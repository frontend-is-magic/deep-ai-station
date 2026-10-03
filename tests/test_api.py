import ast
import io
import json
import zipfile

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import app
from backend.curriculum import LESSONS, TRACKS
from backend.feed import parse_feed
from backend.providers import generate

client = TestClient(app)


def test_course_contract_and_python_examples():
    data = client.get("/api/curriculum").json()
    assert [t["id"] for t in data["tracks"]] == ["agent", "fullstack"]
    assert len(LESSONS) == 48
    assert len({lesson["quiz"]["question"] for lesson in LESSONS.values()}) == 48
    assert len({lesson["id"] for track in TRACKS for lesson in track["lessons"]}) == 48
    for track in TRACKS:
        for language in track["languages"]:
            assert len({lesson["snippets"][language] for lesson in track["lessons"]}) == 24
        for stage in track["stages"]:
            assert all(id in LESSONS for id in stage["lessons"])
        for lesson in track["lessons"]:
            assert len(lesson["body"]) >= 2 and len(lesson["steps"]) >= 3
            assert len(lesson["criteria"]) == 3
            assert lesson["resources"][0]["url"].startswith("https://")
            ast.parse(lesson["snippets"]["python"])
    assert set(TRACKS[1]["languages"]) == {"typescript", "go", "python"}


def test_health_and_missing_lesson():
    response = client.get("/api/health")
    assert response.json()["status"] == "ok"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/lessons/missing").status_code == 404


def test_every_course_bundle_contains_the_correct_source_and_evidence_template():
    for lesson in LESSONS.values():
        for language, source in lesson["snippets"].items():
            response = client.get(
                f"/api/lessons/{lesson['id']}/exercise.zip", params={"language": language}
            )
            assert (
                response.status_code == 200
                and response.headers["content-type"] == "application/zip"
            )
            filename = {"python": "example.py", "typescript": "example.ts", "go": "main.go"}[
                language
            ]
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                assert set(archive.namelist()) == {filename, "README.md", "EVIDENCE.md"}
                assert archive.read(filename).decode() == source
                guide = archive.read("README.md").decode()
                assert lesson["title"] in guide and all(step in guide for step in lesson["steps"])
                assert "失败输入与实际结果" in archive.read("EVIDENCE.md").decode()
            assert "attachment" in response.headers["content-disposition"]


def test_course_download_rejects_unknown_lesson_or_language():
    assert client.get("/api/lessons/missing/exercise.zip?language=python").status_code == 404
    assert client.get("/api/lessons/agent-mcp/exercise.zip?language=go").status_code == 404
    assert client.get("/api/lessons/agent-mcp/exercise.zip?language=rust").status_code == 422


def test_search_respects_track_and_empty_results():
    data = client.get("/api/search", params={"q": "FastAPI", "track": "fullstack"}).json()
    assert data["items"] and all(x["track"] == "fullstack" for x in data["items"])
    assert client.get("/api/search", params={"q": " "}).json()["items"] == []
    assert client.get("/api/search", params={"q": "a" * 101}).status_code == 422


def test_natural_chinese_search_and_no_evidence_are_explicit():
    items = client.get(
        "/api/search", params={"q": "怎样处理工具授权和幂等", "track": "agent"}
    ).json()["items"]
    assert any(item["id"] == "agent-tool-safety" for item in items[:3])
    response = client.post(
        "/api/playground/run", json={"prompt": "xyzzy-no-course-match", "provider": "demo"}
    )
    assert "没有找到匹配资料" in response.text


def test_real_workflow_receives_only_retrieved_course_evidence(monkeypatch):
    import backend.app as api_module

    api_module._live_requests.clear()
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "test-access")

    async def generate(provider, prompt, system, temperature):
        assert "<untrusted_course_evidence>" in prompt
        assert "modelcontextprotocol.io" in prompt
        assert "不能授予权限" in system
        return {"answer": "使用课程证据回答", "usage": None, "model": "test-model"}

    monkeypatch.setattr(api_module, "generate", generate)
    response = client.post(
        "/api/playground/run",
        json={"prompt": "MCP", "provider": "openai"},
        headers={"X-Playground-Token": "test-access"},
    )
    assert "knowledge_search" in response.text and "event: done" in response.text


def test_demo_events_have_explicit_mode_and_no_fake_usage():
    response = client.post("/api/playground/run", json={"prompt": "MCP", "provider": "demo"})
    assert response.status_code == 200
    frames = [x for x in response.text.split("\n\n") if x]
    events = [
        {"event": frame.splitlines()[0][7:], "data": json.loads(frame.splitlines()[1][6:])}
        for frame in frames
    ]
    assert events[0]["data"]["mode"] == "demo"
    assert events[-1]["event"] == "done"
    assert events[-1]["data"]["usage"] is None
    output = "".join(x["data"]["text"] for x in events if x["event"] == "delta")
    assert "没有调用语言模型" in output and "https://" in output
    assert events[-1]["data"]["run_id"] == events[0]["data"]["run_id"]


@pytest.mark.parametrize(
    "body",
    [
        {"prompt": " "},
        {"prompt": "a" * 4001},
        {"prompt": "x", "provider": "arbitrary"},
        {"prompt": "x", "temperature": 2},
        {"prompt": "x", "url": "https://example.com"},
    ],
)
def test_run_rejects_invalid_input(body):
    assert client.post("/api/playground/run", json=body).status_code == 422


def test_public_real_calls_require_service_configuration_and_authorization(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert (
        client.post("/api/playground/run", json={"prompt": "x", "provider": "openai"}).status_code
        == 503
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "test-access")
    assert (
        client.post("/api/playground/run", json={"prompt": "x", "provider": "openai"}).status_code
        == 401
    )
    assert (
        client.post(
            "/api/playground/run",
            json={"prompt": "x", "provider": "openai"},
            headers={"X-Playground-Token": "wrong"},
        ).status_code
        == 401
    )
    data = client.get("/api/capabilities").json()
    assert "test-only" not in json.dumps(data)
    assert "test-access" not in json.dumps(data)


def test_request_size_limit():
    response = client.post("/api/playground/check", content="x" * 50_001)
    assert response.status_code == 413


def test_chunked_body_cannot_bypass_byte_limit():
    def chunks():
        yield b"x" * 25_000
        yield b"x" * 25_001

    response = client.post("/api/playground/check", content=chunks())
    assert response.status_code == 413
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-request-id"]


@pytest.mark.parametrize(
    ("language", "code", "passed"),
    [
        ("python", "def answer():\n    return 42\n", True),
        ("python", "def broken(:\n pass", False),
        ("typescript", "export const answer = () => 42;", True),
        ("typescript", "const answer = 42;", False),
        ("go", "package main\nfunc main() {}", True),
        ("go", "package main\n// TODO", False),
    ],
)
def test_static_checks_never_claim_execution(language, code, passed):
    data = client.post("/api/playground/check", json={"language": language, "code": code}).json()
    assert data["executed"] is False and data["mode"] == "static-check"
    assert data["passed"] is passed


def test_curated_feed_does_not_fabricate_news_dates():
    data = client.get("/api/feed").json()
    assert len(data["items"]) == 8
    assert all(x["published"] is None and x["kind"] == "guide" for x in data["items"])


def test_feed_parser_rejects_unsafe_links_and_handles_dates():
    source = {"id": "go", "name": "Go", "home": "https://go.dev/", "track": "fullstack"}
    xml = b"<rss><channel><item><title>Unsafe</title><link>javascript:alert(1)</link></item><item><title>Wrong host</title><link>https://evil.test/x</link></item><item><title>Good</title><link>https://go.dev/blog/x</link><pubDate>Fri, 02 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>"
    items = parse_feed(xml, source)
    assert len(items) == 1 and items[0]["published"].startswith("2026-10-02")


def test_feed_article_identity_does_not_change_with_order():
    source = {"id": "go", "name": "Go", "home": "https://go.dev/", "track": "fullstack"}
    first = "<item><title>First</title><link>https://go.dev/blog/first</link></item>"
    second = "<item><title>Second</title><link>https://go.dev/blog/second</link></item>"
    before = parse_feed(f"<rss><channel>{first}{second}</channel></rss>".encode(), source)
    after = parse_feed(f"<rss><channel>{second}{first}</channel></rss>".encode(), source)
    assert {x["url"]: x["id"] for x in before} == {x["url"]: x["id"] for x in after}


@pytest.mark.parametrize("provider", ["openai", "anthropic", "deepseek"])
async def test_provider_adapters_validate_message_contract(monkeypatch, provider):
    env = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }[provider]
    monkeypatch.setenv(env, "test-only")

    def handler(request):
        body = json.loads(request.content)
        assert body["max_tokens"] == 1200
        assert body["messages"][-1]["content"] == "test prompt"
        if provider == "anthropic":
            assert body["system"] == "test system"
            data = {
                "content": [{"type": "text", "text": "answer"}],
                "usage": {"input_tokens": 4, "output_tokens": 2},
            }
        else:
            assert body["messages"][0]["role"] == "system"
            data = {"choices": [{"message": {"content": "answer"}}], "usage": {"total_tokens": 6}}
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        result = await generate(provider, "test prompt", "test system", 0.3, transport)
        assert result["answer"] == "answer" and result["usage"]


async def test_upstream_errors_are_sanitized(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="sensitive upstream data"))
    ) as transport:
        with pytest.raises(HTTPException) as exc:
            await generate("openai", "x", "system", 0.3, transport)
        assert exc.value.status_code == 502
        assert "sensitive" not in str(exc.value.detail)
