"""共同顺序案例与可控并发/流式边界；全部使用公开假资料。"""

import asyncio
import base64
import hashlib
import http.client
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Event, Lock, Thread

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app import create_app, listen_port
from auth import MemorySessionStore
from errors import LabError
from repository import MemoryRepository, QuotaExceeded
from resources import fixture_path, load_fixture

ALICE = {"authorization": "Bearer lab-alice-session"}
HEADERS = {**ALICE, "content-type": "text/plain", "x-filename": "notes.txt"}
BOB = {"authorization": "Bearer lab-bob-session"}


def assert_safe_headers(headers):
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert "set-cookie" not in headers
    assert "access-control-allow-origin" not in headers


def assert_json_exact(actual, expected):
    assert type(actual) is type(expected)
    if isinstance(actual, dict):
        assert actual.keys() == expected.keys()
        for key in actual:
            assert_json_exact(actual[key], expected[key])
    elif isinstance(actual, list):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected, strict=True):
            assert_json_exact(left, right)
    else:
        assert actual == expected


def case_request(case):
    headers = list(case.get("headers", {}).items()) + [
        tuple(pair) for pair in case.get("header_pairs", [])
    ]
    if "content_type" in case:
        headers.append(("content-type", case["content_type"]))
    if "body_base64" in case:
        body = base64.b64decode(case["body_base64"], validate=True)
    elif "raw" in case:
        body = case["raw"].encode("utf-8")
    elif "repeat_body" in case:
        repeat = case["repeat_body"]
        body = (repeat["character"] * repeat["count"]).encode("utf-8")
    else:
        body = None
    return {"headers": headers, "content": body}


@pytest.mark.parametrize("storage", ["memory", "sqlite"])
def test_shared_contract_sequence(storage, tmp_path):
    cases = json.loads(fixture_path("contract-cases.json").read_text(encoding="utf-8"))
    assert cases
    repository = None
    if storage == "sqlite":
        from sqlite_repository import SQLiteRepository, initialize

        initialize(tmp_path)
        repository = SQLiteRepository(tmp_path)
    with TestClient(create_app(repository=repository)) as client:
        for case in cases:
            response = client.request(case["method"], case["path"], **case_request(case))
            assert response.status_code == case["status"], case["id"]
            if "expected_body_base64" in case:
                assert response.content == base64.b64decode(
                    case["expected_body_base64"], validate=True
                ), case["id"]
            else:
                try:
                    assert_json_exact(response.json(), case["expected"])
                except AssertionError as error:
                    raise AssertionError(case["id"]) from error
                assert response.headers["content-type"].startswith("application/json")
            assert_safe_headers(response.headers)
            for name, value in case.get("response_headers", {}).items():
                assert response.headers.get(name) == value, case["id"]


class RecordingRepository(MemoryRepository):
    def __init__(self):
        super().__init__()
        self.calls = []

    def list_owned(self, owner):
        self.calls.append(("list", owner))
        return super().list_owned(owner)

    def find_owned(self, owner, document_id):
        self.calls.append(("find", owner, document_id))
        return super().find_owned(owner, document_id)

    def commit(self, owner, filename, media_type, content, **kwargs):
        self.calls.append(("commit", owner))
        return super().commit(owner, filename, media_type, content, **kwargs)


async def raw_request(application, chunks, headers, method="POST", path="/documents", query=b""):
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    replies = []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        replies.append(message)

    await application(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "root_path": "",
            "headers": headers,
            "server": ("test", 80),
            "client": ("test", 1),
        },
        receive,
        send,
    )
    start = next(item for item in replies if item["type"] == "http.response.start")
    body = b"".join(
        item.get("body", b"") for item in replies if item["type"] == "http.response.body"
    )
    headers = {key.decode(): value.decode() for key, value in start["headers"]}
    assert_safe_headers(headers)
    return start["status"], body, len(messages)


@pytest.mark.parametrize("content_length", [None, b"1", b"999999"])
async def test_chunked_limit_precedes_header_policy_and_stops_reading(content_length):
    repository = RecordingRepository()
    headers = [
        (b"authorization", b"Bearer lab-alice-session"),
        (b"content-type", b"application/json"),
        (b"content-type", b"text/plain"),
    ]
    if content_length is not None:
        headers.append((b"content-length", content_length))
    status, body, unread = await raw_request(
        create_app(repository=repository), [b"a" * 2048, b"b" * 2049, b"never-read"], headers
    )
    assert status == 413 and json.loads(body) == {"error": "request_too_large"}
    assert unread == 1 and repository.calls == []


@pytest.mark.parametrize(
    "headers,query,status,error",
    [
        ([], b"", 401, "authentication_required"),
        ([(b"authorization", b"Bearer lab-alice-readonly-session")], b"", 403, "forbidden"),
        ([(b"authorization", b"Bearer lab-alice-session")], b"owner=bob", 422, "invalid_input"),
    ],
)
async def test_auth_query_and_permission_rejections_do_not_read_body(headers, query, status, error):
    repository = RecordingRepository()
    actual, body, unread = await raw_request(
        create_app(repository=repository), [b"a" * 4097], headers, query=query
    )
    assert actual == status and json.loads(body) == {"error": error}
    assert unread == 1 and repository.calls == []


async def test_exact_byte_limit_ignores_advertised_length_and_preserves_bytes():
    content = b"a" * 4096
    status, body, _ = await raw_request(
        create_app(),
        [content[:11], content[11:]],
        [
            (key.encode(), value.encode())
            for key, value in {**HEADERS, "content-length": "999999"}.items()
        ],
    )
    assert status == 201
    metadata = json.loads(body)
    assert metadata["size_bytes"] == 4096
    assert metadata["sha256"] == hashlib.sha256(content).hexdigest()


async def test_get_does_not_consume_body():
    status, body, unread = await raw_request(
        create_app(),
        [b"not-an-upload"],
        [(key.encode(), value.encode()) for key, value in ALICE.items()],
        method="GET",
    )
    assert status == 200 and json.loads(body) == {"documents": []} and unread == 1


@pytest.mark.parametrize(
    "change,status", [("expire", 401), ("revoke", 401), ("owner", 401), ("permission", 403)]
)
async def test_body_pause_rechecks_same_session_without_holding_locks(change, status):
    current = [100.0]
    sessions = MemorySessionStore(load_fixture()["sessions"], lambda: current[0])
    repository = RecordingRepository()
    application = create_app(session_store=sessions, repository=repository)
    entered, release = asyncio.Event(), asyncio.Event()

    async def chunks():
        yield b"first "
        entered.set()
        await release.wait()
        yield b"second"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        pending = asyncio.create_task(client.post("/documents", headers=HEADERS, content=chunks()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            # 独立线程要能取得 SessionStore 锁；调用不经任何 HTTP 管理入口。
            if change == "expire":
                current[0] = 3700.0
            elif change == "owner":
                sessions._sessions["lab-alice-session"]["user_id"] = "bob"
            elif change == "revoke":
                await asyncio.wait_for(asyncio.to_thread(sessions.revoke, "lab-alice-session"), 2)
            else:
                await asyncio.wait_for(
                    asyncio.to_thread(sessions.set_write, "lab-alice-session", False), 2
                )
            assert await asyncio.wait_for(asyncio.to_thread(repository.list_owned, "bob"), 2) == []
            repository.calls.clear()
            release.set()
            response = await asyncio.wait_for(pending, 2)
            assert response.status_code == status
            assert response.json() == {
                "error": "authentication_required" if status == 401 else "forbidden"
            }
            assert_safe_headers(response.headers)
            assert repository.calls == []
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    assert repository.list_owned("alice") == []


async def test_aborted_upload_never_commits():
    repository = RecordingRepository()
    entered = asyncio.Event()

    async def chunks():
        yield b"partial"
        entered.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(repository=repository)), base_url="http://test"
    ) as client:
        pending = asyncio.create_task(client.post("/documents", headers=HEADERS, content=chunks()))
        await asyncio.wait_for(entered.wait(), 2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert repository.calls == []
    assert repository.list_owned("alice") == []


@pytest.mark.parametrize("operation", ["list_owned", "find_owned", "commit"])
def test_repository_exceptions_are_fixed_and_not_logged(operation, caplog):
    repository = MemoryRepository()

    def fail(*_args, **_kwargs):
        raise LabError(418, "private-path-filename-and-session")

    setattr(repository, operation, fail)
    with TestClient(create_app(repository=repository)) as client:
        if operation == "list_owned":
            response = client.get("/documents", headers=ALICE)
        elif operation == "find_owned":
            response = client.get("/documents/doc-000001/content", headers=ALICE)
        else:
            response = client.post("/documents", headers=HEADERS, content=b"private-body")
    assert response.status_code == 503
    assert response.json() == {"error": "repository_unavailable"}
    assert "private" not in response.text + caplog.text
    assert_safe_headers(response.headers)


def test_failure_before_publish_preserves_ids_counts_bytes_and_content(monkeypatch):
    repository = MemoryRepository()
    initial = repository.commit("alice", "first.txt", "text/plain", b"a" * 4096)

    def fail():
        raise RuntimeError("private-storage-diagnostic")

    monkeypatch.setattr(repository, "_before_publish", fail)
    with TestClient(create_app(repository=repository)) as client:
        failed = client.post("/documents", headers=HEADERS, content=b"b" * 4096)
        assert failed.status_code == 503
        assert failed.json() == {"error": "repository_unavailable"}
        assert repository.list_owned("alice") == [initial]
        assert repository.find_owned("alice", initial.id).content == b"a" * 4096
        monkeypatch.setattr(repository, "_before_publish", lambda: None)
        saved = client.post("/documents", headers=HEADERS, content=b"c" * 4096)
        assert saved.status_code == 201 and saved.json()["id"] == "doc-000002"
        exceeded = client.post("/documents", headers=HEADERS, content=b"x")
        assert exceeded.status_code == 409
        bob = client.post("/documents", headers={**HEADERS, **BOB}, content=b"bob")
        assert bob.status_code == 201 and bob.json()["id"] == "doc-000003"


class GatedRepository(MemoryRepository):
    def __init__(self):
        super().__init__()
        self.armed = False
        self.first_publish = Event()
        self.second_entered = Event()
        self.release = Event()
        self.entry_lock = Lock()
        self.entries = 0
        self.publications = 0

    def commit(self, *args, **kwargs):
        if self.armed:
            with self.entry_lock:
                self.entries += 1
                if self.entries == 2:
                    self.second_entered.set()
        return super().commit(*args, **kwargs)

    def _before_publish(self):
        if self.armed:
            self.publications += 1
            if self.publications == 1:
                self.first_publish.set()
                if not self.release.wait(2):
                    raise RuntimeError("test synchronization deadline")


@pytest.mark.parametrize("quota", ["count", "bytes"])
def test_competing_uploads_cannot_exceed_last_quota_or_consume_failed_id(quota):
    repository = GatedRepository()
    seeds = [b"a", b"b"] if quota == "count" else [b"a" * 4096]
    for body in seeds:
        repository.commit("alice", "same.txt", "text/plain", body)
    repository.armed = True
    payload = b"c" if quota == "count" else b"c" * 4096

    def upload():
        try:
            return repository.commit("alice", "same.txt", "text/plain", payload)
        except QuotaExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        try:
            first = executor.submit(upload)
            assert repository.first_publish.wait(2)
            second = executor.submit(upload)
            assert repository.second_entered.wait(2)
            repository.release.set()
            results = [first.result(timeout=2), second.result(timeout=2)]
        finally:
            repository.release.set()
    assert sum(item is not None for item in results) == 1
    documents = repository.list_owned("alice")
    assert len(documents) == len(seeds) + 1
    assert sum(len(item.content) for item in documents) <= 8192
    assert len(documents) <= 3
    bob = repository.commit("bob", "same.txt", "text/plain", b"bob")
    assert bob.id == f"doc-{len(seeds) + 2:06d}"


def test_repository_returns_immutable_content_and_metadata_copies():
    repository = MemoryRepository()
    buffer = bytearray(b"original")
    document = repository.commit("alice", "same.txt", "text/plain", buffer)
    buffer[:] = b"modified"
    metadata = document.metadata()
    metadata["filename"] = "other.txt"
    items = repository.list_owned("alice")
    items.clear()
    with pytest.raises(FrozenInstanceError):
        document.owner = "bob"
    with pytest.raises(TypeError):
        document.content[0] = 120
    saved = repository.find_owned("alice", document.id)
    assert saved.content == b"original"
    assert saved.metadata()["filename"] == "same.txt"
    assert repository.find_owned("bob", document.id) is None


def test_same_name_is_new_object_and_new_app_is_empty():
    with TestClient(create_app()) as client:
        first = client.post("/documents", headers=HEADERS, content=b"one").json()
        second = client.post("/documents", headers=HEADERS, content=b"two").json()
        assert [first["id"], second["id"]] == ["doc-000001", "doc-000002"]
        assert first["filename"] == second["filename"]
        assert client.get("/documents/doc-000001/content", headers=ALICE).content == b"one"
        assert client.get("/documents/doc-000002/content", headers=ALICE).content == b"two"
        readonly = {"authorization": "Bearer lab-alice-readonly-session"}
        assert client.get("/documents/doc-000001/content", headers=readonly).content == b"one"
        for suffix in ["", "/content"]:
            assert client.get("/documents/doc-000001" + suffix, headers=BOB).json() == {
                "error": "document_not_found"
            }
        with TestClient(create_app()) as fresh:
            assert fresh.get("/documents", headers=ALICE).json() == {"documents": []}


@pytest.mark.parametrize("status", ["initial", "final"])
def test_session_failure_never_reaches_repository(status, caplog):
    base = MemorySessionStore(load_fixture()["sessions"])
    reads = []

    class FailedStore:
        def resolve(self, token):
            reads.append(token)
            if status == "initial" or len(reads) == 2:
                raise RuntimeError("private-session-details")
            return base.resolve(token)

    repository = RecordingRepository()
    with TestClient(create_app(session_store=FailedStore(), repository=repository)) as client:
        response = client.post("/documents", headers=HEADERS, content=b"body")
    assert response.status_code == 503
    assert response.json() == {"error": "session_store_unavailable"}
    assert "private-session-details" not in response.text + caplog.text
    assert repository.calls == []


def test_all_disallowed_codepoint_classes_and_invalid_utf8_do_not_commit():
    repository = RecordingRepository()
    invalid = [
        chr(value).encode()
        for value in list(range(32)) + list(range(127, 160))
        if value not in (9, 10, 13)
    ]
    invalid += [
        b"\xff",
        b"\xc0\xaf",
        b"\xed\xa0\x80",
        b"\xf4\x90\x80\x80",
        b"a\xef\xbb\xbfb",
        b" \t\r\n",
        b"",
    ]
    with TestClient(create_app(repository=repository)) as client:
        for body in invalid:
            response = client.post("/documents", headers=HEADERS, content=body)
            assert response.status_code == 422
            assert response.json() == {"error": "invalid_text"}
        assert repository.calls == []
        result = client.post("/documents", headers=HEADERS, content="\u00a0\t\r\n👋".encode())
        assert result.status_code == 201 and result.json()["id"] == "doc-000001"


@pytest.mark.parametrize(
    "name",
    [
        "../a.txt",
        "a..txt",
        "a.txt/",
        "a%2etxt",
        "a:txt",
        ".txt",
        "a b.txt",
        "中.txt",
        "a" * 61 + ".txt",
        "a.txt\n",
    ],
)
async def test_filename_policy_never_interprets_paths(name):
    repository = RecordingRepository()
    headers = [
        (key.encode(), value.encode()) for key, value in HEADERS.items() if key != "x-filename"
    ]
    headers.append((b"x-filename", name.encode()))
    status, body, _ = await raw_request(create_app(repository=repository), [b"text"], headers)
    assert status == 422 and json.loads(body) == {"error": "invalid_filename"}
    assert repository.calls == []


@pytest.mark.parametrize(
    "name", ["", "0", "65536", "-1", " 8023", "8023\n", "1.0", "localhost:8023"]
)
def test_port_rejects_invalid_values(name):
    with pytest.raises(ValueError):
        listen_port(name)


def test_port_allows_only_valid_numeric_override():
    assert listen_port("8023") == 8023
    assert listen_port("12345") == 12345


async def test_stream_failure_is_safe_500_without_residual_upload(caplog):
    repository = RecordingRepository()

    async def chunks():
        yield b"partial"
        raise RuntimeError("private-body-path")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(repository=repository)), base_url="http://test"
    ) as client:
        response = await client.post("/documents", headers=HEADERS, content=chunks())
    assert response.status_code == 500
    assert response.json() == {"error": "request_failed"}
    assert "private-body-path" not in response.text + caplog.text
    assert_safe_headers(response.headers)
    assert repository.calls == []


def test_real_http_keeps_duplicate_auth_cookie_and_upload_headers_distinct():
    application = create_app()
    started = Event()

    async def observed_app(scope, receive, send):
        async def observed_send(message):
            await send(message)
            if message["type"] == "lifespan.startup.complete":
                started.set()

        await application(scope, receive, observed_send)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                observed_app, log_level="critical", access_log=False, timeout_graceful_shutdown=2
            )
        )
        thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        try:
            assert started.wait(3), "ASGI startup must complete before sending requests"
            cases = [
                (
                    [*(HEADERS.items()), ("Authorization", "Bearer lab-alice-session")],
                    "ambiguous_credentials",
                ),
                ([*(HEADERS.items()), ("Cookie", "__Host-lab_session=")], "ambiguous_credentials"),
                (
                    [
                        ("Cookie", "__Host-lab_session=lab-alice-session"),
                        ("Cookie", "__Host-lab_session=lab-alice-session"),
                    ],
                    "ambiguous_credentials",
                ),
                ([*(HEADERS.items()), ("Content-Type", "text/plain")], "ambiguous_upload_headers"),
                ([*(HEADERS.items()), ("X-Filename", "notes.txt")], "ambiguous_upload_headers"),
            ]
            for headers, error in cases:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    connection.putrequest("POST", "/documents")
                    for name, value in headers:
                        connection.putheader(name, value)
                    connection.putheader("Content-Length", "4")
                    connection.endheaders(b"text")
                    response = connection.getresponse()
                    assert response.status == 400
                    assert json.loads(response.read()) == {"error": error}
                    assert_safe_headers(
                        {name.lower(): value for name, value in response.getheaders()}
                    )
                finally:
                    connection.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive(), "test-owned Uvicorn must release its ephemeral socket"
