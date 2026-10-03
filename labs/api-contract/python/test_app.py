"""共享 HTTP 契约与 Python 特有的依赖替换、流式边界验证。"""

import json

import pytest
from fastapi.testclient import TestClient

from app import app, get_repository
from repository import FixedRepository, Lesson, fixture_path

CASES = json.loads(fixture_path("contract-cases.json").read_text(encoding="utf-8"))


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def case_request(case):
    if "json" in case:
        # json=None 在 httpx 中表示无 body；显式编码保证测试的是 JSON null。
        content = json.dumps(case["json"], ensure_ascii=False).encode("utf-8")
    elif "raw" in case:
        content = case["raw"].encode("utf-8")
    elif "repeat_body" in case:
        repeat = case["repeat_body"]
        content = (repeat["character"] * repeat["count"]).encode("utf-8")
    else:
        return {}
    return {
        "content": content,
        "headers": {"content-type": case.get("content_type", "application/json")},
    }


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_shared_http_contract(client, case):
    response = client.request(case["method"], case["path"], **case_request(case))
    assert response.status_code == case["status"]
    assert response.json() == case["expected"]
    assert response.headers["content-type"].startswith("application/json")


class EmptyRepository:
    def find(self, lesson_id):
        return None

    def search(self, question):
        return []


class FailedRepository:
    def find(self, lesson_id):
        raise RuntimeError("private-repository-diagnostic")

    def search(self, question):
        raise RuntimeError("private-repository-diagnostic")


class RecordingRepository(EmptyRepository):
    def __init__(self):
        self.queries = []

    def search(self, question):
        self.queries.append(question)
        return super().search(question)


def test_repository_can_be_replaced_without_changing_routes(client):
    app.dependency_overrides[get_repository] = EmptyRepository
    missing = client.get("/lessons/tools")
    assert missing.status_code == 404
    assert missing.json() == {"error": "lesson_not_found"}
    result = client.post("/search", json={"question": "工具"})
    assert result.status_code == 200
    assert result.json() == {"question": "工具", "items": []}


@pytest.mark.parametrize("path", ["/lessons/tools", "/search"])
def test_repository_failure_is_safe_json_503(client, path):
    app.dependency_overrides[get_repository] = FailedRepository
    if path == "/search":
        response = client.post(path, json={"question": "工具"})
    else:
        response = client.get(path)
    assert response.status_code == 503
    assert response.json() == {"error": "repository_unavailable"}
    assert "private-repository-diagnostic" not in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case["status"] in {413, 415, 422}],
    ids=lambda case: case["id"],
)
def test_invalid_requests_never_query_repository(client, case):
    repository = RecordingRepository()
    app.dependency_overrides[get_repository] = lambda: repository
    response = client.request(case["method"], case["path"], **case_request(case))
    assert response.status_code == case["status"]
    assert repository.queries == []


def test_service_passes_trimmed_query_to_repository(client):
    repository = RecordingRepository()
    app.dependency_overrides[get_repository] = lambda: repository
    response = client.post("/search", json={"question": " \n工具\t "})
    assert response.status_code == 200
    assert repository.queries == ["工具"]


@pytest.mark.parametrize(
    "body",
    [
        b'{"question":"\xff"}',
        b'{"question":"\\ud800"}',
        b'{"question":NaN}',
        b'{"question":Infinity}',
        b'{"question":-Infinity}',
        b"",
    ],
    ids=[
        "invalid-utf8",
        "unpaired-surrogate",
        "nan",
        "infinity",
        "negative-infinity",
        "empty-body",
    ],
)
def test_invalid_bytes_and_non_json_values_do_not_query_or_echo(client, body):
    repository = RecordingRepository()
    app.dependency_overrides[get_repository] = lambda: repository
    response = client.post("/search", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert response.json() == {"error": "invalid_input"}
    assert repository.queries == []


def test_repository_search_preserves_order_and_does_not_match_across_fields():
    repository = FixedRepository(
        [
            Lesson("first", "aB", "unrelated"),
            Lesson("second", "title", "AB"),
            Lesson("cross", "a", "b"),
        ]
    )
    assert [item.id for item in repository.search("ab")] == ["first", "second"]


async def raw_asgi_request(chunks, headers):
    """直接发 ASGI 分块；TestClient 可能合并 Python 生成器，无法证明分块边界。"""
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    received = []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        received.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/search",
            "raw_path": b"/search",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "server": ("test", 80),
            "client": ("test", 1),
        },
        receive,
        send,
    )
    status = next(item["status"] for item in received if item["type"] == "http.response.start")
    body = b"".join(
        item.get("body", b"") for item in received if item["type"] == "http.response.body"
    )
    return status, json.loads(body), len(messages)


@pytest.mark.parametrize("content_length", [None, b"1", b"999999"])
async def test_chunked_limit_precedes_media_json_and_repository(client, content_length):
    repository = RecordingRepository()
    app.dependency_overrides[get_repository] = lambda: repository
    headers = [(b"content-type", b"text/plain")]
    if content_length is not None:
        headers.append((b"content-length", content_length))
    status, body, unread = await raw_asgi_request([b"x" * 2048, b"x" * 2049, b"unused"], headers)
    assert status == 413
    assert body == {"error": "request_too_large"}
    assert unread == 1
    assert repository.queries == []


async def test_actual_limit_accepts_4096_bytes_despite_false_content_length(client):
    body = b'{"question":"HTTP"}'
    body += b" " * (4096 - len(body))
    status, payload, _ = await raw_asgi_request(
        [body[:11], body[11:]],
        [(b"content-type", b"application/json"), (b"content-length", b"999999")],
    )
    assert status == 200
    assert payload == {"question": "HTTP", "items": [{"id": "http", "title": "HTTP API"}]}


async def test_body_limit_counts_utf8_bytes_instead_of_characters(client):
    status, payload, _ = await raw_asgi_request(
        [("界" * 1366).encode("utf-8")], [(b"content-type", b"application/json")]
    )
    assert status == 413
    assert payload == {"error": "request_too_large"}
