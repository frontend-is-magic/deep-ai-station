"""公开共同案例按顺序运行；原生测试另验证注入、原始头和分块边界。"""

import asyncio
import json
from http.cookies import SimpleCookie

import httpx
import pytest
from fastapi.responses import Response
from fastapi.testclient import TestClient

from app import create_app
from auth import COOKIE_NAME, MemorySessionStore, session_cookie
from errors import LabError
from repository import MemoryRepository
from resources import fixture_path, load_fixture

ALICE = {"authorization": "Bearer lab-alice-session"}
READONLY = {"authorization": "Bearer lab-alice-readonly-session"}
COOKIE = {"cookie": "__Host-lab_session=lab-alice-session"}
CSRF = {"origin": "https://lab.example.test", "x-csrf-token": "lab-csrf-a"}


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


def check_cookie(header, *, clear):
    cookie = SimpleCookie()
    cookie.load(header)
    assert set(cookie) == {COOKIE_NAME}
    item = cookie[COOKIE_NAME]
    assert item["secure"] and item["httponly"]
    assert item["samesite"].lower() == "strict"
    assert item["path"] == "/" and not item["domain"]
    if clear:
        assert item["max-age"] == "0" and item.value == ""


def case_request(case):
    headers = list(case.get("headers", {}).items()) + [
        tuple(pair) for pair in case.get("header_pairs", [])
    ]
    body = None
    if "json" in case:
        body = json.dumps(case["json"], ensure_ascii=False).encode("utf-8")
    elif "raw" in case:
        body = case["raw"].encode("utf-8")
    elif "repeat_body" in case:
        repeat = case["repeat_body"]
        body = (repeat["character"] * repeat["count"]).encode("utf-8")
    if body is not None and not any(key.lower() == "content-type" for key, _ in headers):
        headers.append(("content-type", case.get("content_type", "application/json")))
    return {"headers": headers, "content": body}


def test_shared_contract_sequence():
    cases = json.loads(fixture_path("contract-cases.json").read_text(encoding="utf-8"))
    assert cases
    with TestClient(create_app()) as client:
        for case in cases:
            client.cookies.clear()
            response = client.request(case["method"], case["path"], **case_request(case))
            assert response.status_code == case["status"], case["id"]
            try:
                assert_json_exact(response.json(), case["expected"])
            except AssertionError as error:
                raise AssertionError(case["id"]) from error
            assert response.headers["content-type"].startswith("application/json"), case["id"]
            assert response.headers["cache-control"] == "no-store", case["id"]
            for key, value in case.get("response_headers", {}).items():
                assert response.headers.get(key) == value, case["id"]
            if case.get("set_cookie"):
                check_cookie(response.headers["set-cookie"], clear=True)
            else:
                assert "set-cookie" not in response.headers, case["id"]


class RecordingRepository(MemoryRepository):
    def __init__(self):
        super().__init__(load_fixture()["documents"])
        self.calls = []

    def list_owned(self, owner):
        self.calls.append(("list", owner))
        return super().list_owned(owner)

    def find_owned(self, owner, document_id):
        self.calls.append(("find", owner, document_id))
        return super().find_owned(owner, document_id)

    def update_owned(self, owner, document_id, archived, authorize):
        self.calls.append(("update", owner, document_id))
        return super().update_owned(owner, document_id, archived, authorize)


@pytest.mark.parametrize(
    "headers,code",
    [
        ({}, 401),
        ({"authorization": "Basic lab-alice-session"}, 401),
        ({"authorization": "Bearer unknown"}, 401),
        ({"authorization": "Bearer lab-expired-session"}, 401),
        ({"authorization": "Bearer lab-revoked-session"}, 401),
        ({"authorization": "Bearer lab-alice-session "}, 401),
        ({"authorization": "Bearer\tlab-alice-session"}, 401),
        ({"authorization": "Bearer " + "a" * 129}, 401),
        ({"authorization": "Bearer a,b"}, 400),
        ({**ALICE, **COOKIE}, 400),
        ({"cookie": '__Host-lab_session="lab-alice-session"'}, 401),
        ({"cookie": "__Host-lab_session=lab%2Dalice-session"}, 401),
        ({"cookie": "other=lab-alice-session"}, 401),
        ({"cookie": "__Host-lab_session"}, 401),
    ],
)
def test_bad_auth_precedes_invalid_body_and_never_queries_repository(headers, code):
    repository = RecordingRepository()
    with TestClient(create_app(repository=repository)) as client:
        response = client.patch(
            "/documents/alice-notes?owner=bob", headers=headers, content=b"x" * 5000
        )
    assert response.status_code == code
    assert repository.calls == []
    if code == 401:
        assert response.headers["www-authenticate"] == 'Bearer realm="session-authorization"'
    assert "lab-alice-session" not in response.text


@pytest.mark.parametrize(
    "headers,code",
    [
        ([("authorization", "Bearer lab-alice-session")] * 2, 400),
        ([("cookie", "__Host-lab_session=lab-alice-session")] * 2, 400),
        ([("cookie", "__Host-lab_session=; __Host-lab_session=lab-alice-session")], 400),
        (list(COOKIE.items()) + list(CSRF.items()) + [("origin", "https://lab.example.test")], 403),
        (list(COOKIE.items()) + list(CSRF.items()) + [("x-csrf-token", "lab-csrf-a")], 403),
    ],
)
def test_duplicate_raw_headers_fail_closed(headers, code):
    repository = RecordingRepository()
    with TestClient(create_app(repository=repository)) as client:
        response = client.patch("/documents/alice-notes", headers=headers, json={"archived": True})
    assert response.status_code == code
    assert repository.calls == []


@pytest.mark.parametrize(
    "csrf",
    [
        {},
        {"origin": "https://lab.example.test"},
        {"x-csrf-token": "lab-csrf-a"},
        {**CSRF, "origin": "null"},
        {**CSRF, "origin": "https://lab.example.test.attacker.test"},
        {**CSRF, "origin": "https://lab.example.test/"},
        {**CSRF, "x-csrf-token": "lab-csrf-b"},
        {**CSRF, "x-csrf-token": "lab-csrf-a2"},
    ],
)
def test_failed_csrf_does_not_read_write_or_revoke(csrf):
    repository = RecordingRepository()
    with TestClient(create_app(repository=repository)) as client:
        headers = {**COOKIE, **csrf}
        for path, body in [("/documents/alice-notes", {"archived": True}), ("/logout", {})]:
            method = "PATCH" if path.startswith("/documents") else "POST"
            response = client.request(method, path, headers=headers, json=body)
            assert response.status_code == 403
            assert response.json() == {"error": "csrf_failed"}
        assert client.get("/me", headers=ALICE).status_code == 200
    assert repository.calls == []


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"null",
        b"[]",
        b"true",
        b"{}",
        b'{"archived":1}',
        b'{"archived":"true"}',
        b'{"archived":null}',
        b'{"archived":true,"owner":"bob"}',
        b'{"archived":true,"archived":false}',
        b'{"archived":NaN}',
        b'{"archived":Infinity}',
        b'{"archived":"\xff"}',
        b'\xef\xbb\xbf{"archived":true}',
        b'\xc2\xa0{"archived":true}',
        b'{"archived":true} false',
        b'{"archived":true}\x1c',
    ],
)
def test_bad_json_precedes_csrf_and_never_queries(body):
    repository = RecordingRepository()
    with TestClient(create_app(repository=repository)) as client:
        response = client.patch(
            "/documents/alice-notes",
            headers={**COOKIE, "content-type": "application/json"},
            content=body,
        )
    assert response.status_code == 422
    assert response.json() == {"error": "invalid_input"}
    assert repository.calls == []


def test_owner_lookup_precedes_write_permission_and_client_owner_is_ignored():
    repository = RecordingRepository()
    with TestClient(create_app(repository=repository)) as client:
        headers = {**READONLY, "x-user-id": "bob", "role": "admin", "owner": "bob"}
        missing = client.patch("/documents/bob-notes", headers=headers, json={"archived": True})
        assert missing.status_code == 404
        own = client.patch("/documents/alice-notes", headers=headers, json={"archived": True})
        assert own.status_code == 403
        listing = client.get("/documents", headers=headers).json()["items"]
        assert [item["id"] for item in listing] == ["alice-notes", "alice-plan"]
        assert all(set(item) == {"id", "title", "archived"} for item in listing)
        assert all(not item["archived"] for item in listing)
    assert all(call[1] == "alice" for call in repository.calls)


def test_cookie_disabled_rejects_cookie_and_ambiguity_but_allows_other_cookies():
    with TestClient(create_app(allow_cookie=False)) as client:
        assert client.get("/me", headers=COOKIE).status_code == 401
        assert client.get("/me", headers={**ALICE, **COOKIE}).status_code == 400
        assert client.get("/me", headers={**ALICE, "cookie": "other=value"}).json() == {
            "user_id": "alice"
        }


def test_clock_expiry_is_exact_and_each_app_has_independent_sessions():
    current = [100.0]
    app = create_app(clock=lambda: current[0])
    with TestClient(app) as client:
        current[0] = 3699.999
        assert client.get("/me", headers=ALICE).status_code == 200
        current[0] = 3700.0
        assert client.get("/me", headers=ALICE).status_code == 401
        with TestClient(create_app(clock=lambda: current[0])) as fresh:
            assert fresh.get("/me", headers=ALICE).status_code == 200


def test_logout_revokes_only_current_session_and_restart_resets_fixture():
    with TestClient(create_app()) as client:
        assert (
            client.patch(
                "/documents/alice-notes", headers=ALICE, json={"archived": True}
            ).status_code
            == 200
        )
        response = client.post("/logout", headers={**COOKIE, **CSRF}, json={})
        assert response.json() == {"ok": True}
        check_cookie(response.headers["set-cookie"], clear=True)
        assert client.get("/me", headers=ALICE).status_code == 401
        for token in ["lab-alice-second-session", "lab-bob-session"]:
            assert (
                client.get("/me", headers={"authorization": "Bearer " + token}).status_code == 200
            )
        with TestClient(create_app()) as fresh:
            assert fresh.get("/documents/alice-notes", headers=ALICE).json()["archived"] is False


def test_bearer_logout_does_not_require_write_permission_or_emit_cookie():
    with TestClient(create_app()) as client:
        response = client.post("/logout", headers=READONLY, json={})
        assert response.json() == {"ok": True} and "set-cookie" not in response.headers
        assert client.get("/me", headers=READONLY).status_code == 401


def test_cookie_attribute_helper():
    response = Response()
    session_cookie(response, "lab-alice-session")
    check_cookie(response.headers["set-cookie"], clear=False)


def test_allowed_origin_is_startup_configuration():
    with TestClient(create_app(allowed_origin="https://alternate.example.test")) as client:
        assert (
            client.patch(
                "/documents/alice-notes", headers={**COOKIE, **CSRF}, json={"archived": True}
            ).status_code
            == 403
        )
        response = client.patch(
            "/documents/alice-notes",
            headers={**COOKIE, **CSRF, "origin": "https://alternate.example.test"},
            json={"archived": True},
        )
        assert response.status_code == 200


@pytest.mark.parametrize("method", ["list_owned", "find_owned", "update_owned"])
def test_repository_faults_are_safe_and_not_logged(method, caplog):
    repository = RecordingRepository()

    def fail(*_args):
        raise RuntimeError("private-diagnostic-and-credential")

    setattr(repository, method, fail)
    with TestClient(create_app(repository=repository)) as client:
        if method == "list_owned":
            response = client.get("/documents", headers=ALICE)
        elif method == "find_owned":
            response = client.get("/documents/alice-notes", headers=ALICE)
        else:
            response = client.patch(
                "/documents/alice-notes", headers=ALICE, json={"archived": True}
            )
    assert response.status_code == 503
    assert response.json() == {"error": "repository_unavailable"}
    assert "private-diagnostic" not in response.text + caplog.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("method", ["resolve", "revoke"])
def test_session_store_faults_are_safe(method, caplog):
    store = MemorySessionStore(load_fixture()["sessions"])
    repository = RecordingRepository()

    def fail(*_args):
        raise RuntimeError("private-session-diagnostic")

    setattr(store, method, fail)
    with TestClient(create_app(session_store=store, repository=repository)) as client:
        response = client.post("/logout", headers=ALICE, json={})
    assert response.status_code == 503
    assert response.json() == {"error": "session_store_unavailable"}
    assert "private-session-diagnostic" not in response.text + caplog.text
    assert repository.calls == []


def test_unexpected_internal_error_is_json_no_store_and_not_logged(monkeypatch, caplog):
    import app

    def fail(*_args):
        raise RuntimeError("private-internal-diagnostic")

    monkeypatch.setattr(app, "csrf_guard", fail)
    with TestClient(create_app()) as client:
        response = client.post("/logout", headers=ALICE, json={})
    assert response.status_code == 500
    assert response.json() == {"error": "request_failed"}
    assert response.headers["cache-control"] == "no-store"
    assert "private-internal-diagnostic" not in response.text + caplog.text


async def raw_request(application, chunks, headers, path="/documents/alice-notes", query=b""):
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    responses = []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        responses.append(message)

    await application(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "PATCH",
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
    start = next(message for message in responses if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in responses if message["type"] == "http.response.body"
    )
    assert dict(start["headers"])[b"cache-control"] == b"no-store"
    return start["status"], json.loads(body), len(messages)


@pytest.mark.parametrize("content_length", [None, b"1", b"999999"])
async def test_chunked_limit_precedes_media_and_stops_reading(content_length):
    repository = RecordingRepository()
    headers = [(b"authorization", b"Bearer lab-alice-session"), (b"content-type", b"text/plain")]
    if content_length is not None:
        headers.append((b"content-length", content_length))
    status, body, unread = await raw_request(
        create_app(repository=repository), [b"x" * 2048, b"x" * 2049, b"never-consumed"], headers
    )
    assert status == 413 and body == {"error": "request_too_large"}
    assert unread == 1 and repository.calls == []


async def test_missing_authentication_does_not_consume_any_body_chunk():
    status, body, unread = await raw_request(create_app(), [b"x" * 5000, b"x"], [])
    assert status == 401 and body == {"error": "authentication_required"}
    assert unread == 2


async def test_query_rejection_precedes_body_read():
    status, body, unread = await raw_request(
        create_app(),
        [b"x" * 5000],
        [(b"authorization", b"Bearer lab-alice-session")],
        query=b"owner=alice",
    )
    assert status == 422 and body == {"error": "invalid_input"} and unread == 1


async def test_exact_4096_bytes_accepts_ascii_whitespace_and_ignores_length_header():
    body = b'{"archived":true}'
    body += b" " * (4096 - len(body))
    status, payload, _ = await raw_request(
        create_app(),
        [body[:4], body[4:]],
        [
            (b"authorization", b"bEaReR   lab-alice-session"),
            (b"content-type", b" Application/JSON\t; broken parameter"),
            (b"content-length", b"9000"),
        ],
    )
    assert status == 200 and payload["archived"] is True


async def test_body_limit_counts_utf8_bytes():
    status, body, _ = await raw_request(
        create_app(),
        [("界" * 1366).encode()],
        [(b"authorization", b"Bearer lab-alice-session"), (b"content-type", b"application/json")],
    )
    assert status == 413 and body == {"error": "request_too_large"}


def test_repository_cannot_choose_an_http_error_or_echo_diagnostics():
    repository = RecordingRepository()

    def fail(*_args):
        raise LabError(418, "private-diagnostic")

    repository.find_owned = fail
    with TestClient(create_app(repository=repository)) as client:
        response = client.get("/documents/alice-notes", headers=ALICE)
    assert response.status_code == 503
    assert response.json() == {"error": "repository_unavailable"}


@pytest.mark.parametrize("stop", ["expire", "revoke"])
@pytest.mark.parametrize("cookie", [False, True])
@pytest.mark.parametrize("path", ["/documents/alice-notes", "/logout"])
async def test_expiry_or_logout_during_body_upload_is_rechecked(stop, cookie, path):
    current = [100.0]
    store = MemorySessionStore(load_fixture()["sessions"], lambda: current[0])
    repository = RecordingRepository()
    application = create_app(session_store=store, repository=repository)
    uploading, resume = asyncio.Event(), asyncio.Event()
    content = b'{"archived":true}' if path.startswith("/documents") else b"{}"

    async def chunks():
        yield content[:1]
        uploading.set()
        await resume.wait()
        yield content[1:]

    # 即使 Cookie CSRF 无效，上传期间撤销/到期的会话仍先返回 401。
    headers = {**(COOKIE if cookie else ALICE), "content-type": "application/json"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        method = "PATCH" if path.startswith("/documents") else "POST"
        pending = asyncio.create_task(
            client.request(method, path, headers=headers, content=chunks())
        )
        try:
            await asyncio.wait_for(uploading.wait(), 2)
            if stop == "expire":
                current[0] = 3700.0
            else:
                # 并行注销必须能够完成，不能被尚未上传完的正文占有 SessionStore 锁阻塞。
                response = await asyncio.wait_for(client.post("/logout", headers=ALICE, json={}), 2)
                assert response.status_code == 200
            resume.set()
            response = await asyncio.wait_for(pending, 2)
            assert response.status_code == 401
            assert response.json() == {"error": "authentication_required"}
            assert response.headers["www-authenticate"] == 'Bearer realm="session-authorization"'
            assert repository.calls == []
        finally:
            resume.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
