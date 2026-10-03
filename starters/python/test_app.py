import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import create_app, generate

CASES = json.loads(Path(__file__).with_name("contract-cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_shared_contract(case, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("PLAYGROUND_ACCESS_TOKEN", raising=False)
    with TestClient(create_app()) as client:
        response = client.post("/api/ask", json=case["body"])
    assert response.status_code == case["status"]
    if case["status"] == 200:
        result = response.json()
        assert result["mode"] == case["mode"] and result["run_id"]
        assert result["usage"] is None
        if case.get("source"):
            assert any(source["id"] == case["source"] for source in result["sources"])
        else:
            assert result["sources"] == []


@pytest.mark.parametrize("payload", [b"{", b'"bad"', b"[]", b'{"prompt":"' + b"x" * 17000 + b'"}'])
def test_invalid_wire_input_is_bounded(payload):
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/ask", content=payload, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == (413 if len(payload) > 16384 else 422)
    assert set(response.json()) == {"error"}


def test_live_request_uses_fixed_endpoint_and_known_counts_and_enforces_quota(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "test-access")
    requests = []

    def remote(request):
        requests.append(request)
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        assert json.loads(request.content)["max_completion_tokens"] == 800
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "固定资料中的 API 契约"}}
                ],
                "usage": {"total_tokens": 12, "unexpected": "discard"},
            },
        )

    api = create_app(
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(remote), **kwargs)
    )
    with TestClient(api) as client:
        for _ in range(10):
            response = client.post(
                "/api/ask",
                json={"prompt": "API", "mode": "openai"},
                headers={"X-Playground-Token": "test-access"},
            )
            assert response.status_code == 200 and response.json()["usage"] == {"total_tokens": 12}
        assert (
            client.post(
                "/api/ask",
                json={"prompt": "API", "mode": "openai"},
                headers={"X-Playground-Token": "test-access"},
            ).status_code
            == 429
        )
    assert len(requests) == 10


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b'{"choices":[]}',
        b'{"choices":[{"finish_reason":"length","message":{"content":"partial"}}]}',
        b"x" * 1000001,
    ],
)
async def test_incomplete_or_oversized_provider_output_is_sanitized(monkeypatch, payload):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")

    def factory(**kwargs):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload)), **kwargs
        )

    with pytest.raises(HTTPException) as error:
        await generate("API", [], factory)
    assert error.value.status_code == 502 and error.value.detail == "provider_invalid_response"


class WaitingStream(httpx.AsyncByteStream):
    def __init__(self):
        self.waiting = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        self.waiting.set()
        await asyncio.Event().wait()
        yield b""

    async def aclose(self):
        self.closed = True


async def test_cancel_closes_the_mock_provider_stream(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    remote = WaitingStream()

    def factory(**kwargs):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote)), **kwargs
        )

    task = asyncio.create_task(generate("API", [], factory))
    await remote.waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert remote.closed
