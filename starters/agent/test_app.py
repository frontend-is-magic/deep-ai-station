import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import create_app
from engine import BY_ID, Research, demo, generate

CASES = json.loads(Path(__file__).with_name("eval-cases.json").read_text())
USAGE = {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10}


def call(name, args, identity="call_1"):
    return {
        "id": identity,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def tool_response(*calls, usage=USAGE):
    return {
        "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": list(calls)}}],
        "usage": usage,
    }


def report(ids=("api",), usage=USAGE, finish="stop"):
    value = {
        "answer": "根据实际资料整理。",
        "citations": [{"document_id": key, "quote": BY_ID[key]["body"]} for key in ids],
    }
    return {
        "choices": [{"finish_reason": finish, "message": {"content": json.dumps(value)}}],
        "usage": usage,
    }


def script(payloads):
    requests = []

    def remote(request):
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        requests.append(json.loads(request.content))
        value = payloads[len(requests) - 1]
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(remote), **kwargs)

    return factory, requests


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "test-access")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_demo_cases_retain_real_read_quotes_without_model_calls(case):
    with TestClient(create_app()) as client:
        response = client.post("/api/ask", json={"prompt": case["prompt"]})
    assert response.status_code == 200
    value = response.json()
    assert value["outcome"] == case["outcome"]
    assert [source["id"] for source in value["sources"]] == case["sources"]
    assert value["model_calls"] == 0 and value["tool_calls"] == case["tool_calls"]
    assert value["usage"] is None
    for citation in value["citations"]:
        assert citation["quote"] in BY_ID[citation["document_id"]]["body"]
    assert all(
        source["url"] is None for source in value["sources"] if source["kind"] == "conflict-fixture"
    )


@pytest.mark.parametrize("prompt", [" " * 200 + "API", "x" * 300 + "API", "API 工具 资料 取消"])
def test_demo_long_prefix_and_mixed_topics(prompt):
    value = demo(prompt)
    ids = {source["id"] for source in value["sources"]}
    assert "api" in ids
    if "取消" in prompt:
        assert {"billing-a", "billing-b"} <= ids
        assert value["outcome"] == "conflicting_evidence"


@pytest.mark.parametrize(
    "body",
    [
        {"prompt": " "},
        {"prompt": None},
        {"prompt": "API", "extra": True},
        {"prompt": "API", "mode": "bad"},
    ],
)
def test_invalid_inputs_are_sanitized(body):
    with TestClient(create_app()) as client:
        response = client.post("/api/ask", json=body)
    assert response.status_code == 422 and response.json() == {"error": "invalid_input"}


def test_health_body_limit_and_access_gate(monkeypatch):
    with TestClient(create_app()) as client:
        assert client.get("/api/health").json() == {
            "status": "ok",
            "framework": "fastapi",
            "documents": 5,
            "workflow": "research-agent",
        }
        assert client.post("/api/ask", content=b"x" * 16385).status_code == 413
        assert client.post("/api/ask", json={"prompt": "API", "mode": "openai"}).status_code == 401
        monkeypatch.delenv("OPENAI_API_KEY")
        assert (
            client.post(
                "/api/ask",
                json={"prompt": "API", "mode": "openai"},
                headers={"X-Playground-Token": "test-access"},
            ).status_code
            == 503
        )


def searched(query="API"):
    run = Research()
    value = run.tool("document_search", json.dumps({"query": query}))
    assert all(set(doc) == {"id", "title"} for doc in value["documents"])
    return run


def result_value(ids=("api",)):
    return json.loads(report(ids)["choices"][0]["message"]["content"])


def test_search_only_and_forged_quotes_cannot_be_cited():
    run = searched()
    with pytest.raises(ValueError):
        run.result(result_value(), "demo")
    run.tool("document_read", '{"document_ids":["api"]}')
    value = result_value()
    value["citations"][0]["quote"] = "这是资料里不存在的声明"
    with pytest.raises(ValueError):
        run.result(value, "demo")
    with pytest.raises(ValueError):
        run.result(result_value(()), "demo")
    assert run.result(result_value(), "demo")["sources"][0]["url"] == BY_ID["api"]["url"]


@pytest.mark.parametrize(
    "name,args",
    [
        ("fetch_url", {"url": "https://example.com"}),
        ("document_search", {"query": "API", "path": "../secret"}),
        ("document_read", {"document_ids": ["../secret"]}),
        ("document_read", {"document_ids": ["api", "api"]}),
    ],
)
def test_invalid_tools_do_not_read(name, args):
    run = searched()
    assert run.tool(name, json.dumps(args)) == {"error": "invalid_tool_request"}
    assert not run.read_ids
    with pytest.raises(ValueError):
        run.tool("document_search", '{"query":"API"}')


def test_known_conflict_cannot_be_omitted_even_if_only_other_document_was_read():
    run = searched("API 取消计费")
    run.tool("document_read", '{"document_ids":["api"]}')
    with pytest.raises(ValueError):
        run.result(result_value(), "demo")
    run = searched("取消计费")
    run.tool("document_read", '{"document_ids":["billing-a","billing-b"]}')
    with pytest.raises(ValueError):
        run.result(result_value(("billing-a",)), "demo")
    assert (
        run.result(result_value(("billing-a", "billing-b")), "demo")["outcome"]
        == "conflicting_evidence"
    )


async def test_native_three_rounds_two_tools_and_partial_usage():
    factory, requests = script(
        [
            tool_response(call("document_search", {"query": "API"})),
            tool_response(call("document_read", {"document_ids": ["api"]}, "call_2"), usage=None),
            report(),
        ]
    )
    charged = []
    value = await generate("API", lambda: charged.append(1), factory)
    assert len(charged) == len(requests) == value["model_calls"] == 3
    assert value["tool_calls"] == 2 and value["outcome"] == "complete"
    assert value["usage"] == {"prompt_tokens": 16, "completion_tokens": 4, "total_tokens": 20}
    assert value["usage_complete"] is False
    assert requests[2]["tool_choice"] == "none"
    assert all(
        request["parallel_tool_calls"] is False and request["max_completion_tokens"] == 800
        for request in requests
    )
    assert requests[2]["messages"][-1]["role"] == "tool"
    assert "body" in json.loads(requests[2]["messages"][-1]["content"])["documents"][0]


async def test_real_abstention_retains_model_call_and_unknown_usage():
    factory, _ = script([report((), usage=None)])
    value = await generate("missing", lambda: None, factory)
    assert value["mode"] == "openai" and value["model_calls"] == 1
    assert value["outcome"] == "insufficient_evidence" and value["usage"] is None
    assert value["usage_complete"] is False


@pytest.mark.parametrize(
    "payloads",
    [
        [
            tool_response(
                call("document_search", {"query": "API"}),
                call("document_search", {"query": "API"}, "parallel"),
            )
        ],
        [
            tool_response(call("document_search", {"query": "API"})),
            tool_response(call("document_read", {"document_ids": ["api"]})),
        ],
        [
            tool_response(call("document_search", {"query": "API"})),
            tool_response(call("document_read", {"document_ids": ["api"]}, "call_2")),
            tool_response(call("document_search", {"query": "API"}, "call_3")),
        ],
        [report(finish="length")],
        [report()],
        [tool_response(call("document_search", {"query": "x" * 5000}))],
        [httpx.Response(200, content=b"x" * 1_000_001)],
        [httpx.Response(200, content=b'{"choices": [],"choices": []}')],
    ],
)
async def test_invalid_native_output_is_rejected(payloads):
    factory, requests = script(payloads)
    with pytest.raises(HTTPException) as error:
        await generate("API", lambda: None, factory)
    assert error.value.status_code == 502 and error.value.detail == "provider_invalid_response"
    assert len(requests) <= 3


async def test_invalid_tool_observation_can_recover_to_abstention():
    factory, requests = script([tool_response(call("unknown", {"path": "../secret"})), report(())])
    value = await generate("missing", lambda: None, factory)
    assert value["tool_calls"] == 1 and value["model_calls"] == 2
    assert json.loads(requests[1]["messages"][-1]["content"]) == {"error": "invalid_tool_request"}
    assert any(step["status"] == "error" for step in value["trace"])
    assert "../secret" not in json.dumps(value)


def test_quota_counts_every_http_request():
    payloads = [
        tool_response(call("document_search", {"query": "API"})),
        tool_response(call("document_read", {"document_ids": ["api"]}, "call_2")),
        report(),
    ]
    factory, requests = script(payloads * 4)
    with TestClient(create_app(factory)) as client:
        for _ in range(3):
            assert (
                client.post(
                    "/api/ask",
                    json={"prompt": "API", "mode": "openai"},
                    headers={"X-Playground-Token": "test-access"},
                ).status_code
                == 200
            )
        response = client.post(
            "/api/ask",
            json={"prompt": "API", "mode": "openai"},
            headers={"X-Playground-Token": "test-access"},
        )
    assert response.status_code == 429 and response.json() == {"error": "rate_limited"}
    assert len(requests) == 10


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


async def test_cancel_closes_upstream():
    stream = WaitingStream()
    factory, _ = script([httpx.Response(200, stream=stream)])
    task = asyncio.create_task(generate("API", lambda: None, factory))
    await asyncio.wait_for(stream.waiting.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


async def test_timeout_and_upstream_429_are_sanitized():
    def timeout(request):
        raise httpx.ReadTimeout("private provider detail", request=request)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(timeout), **kwargs)

    with pytest.raises(HTTPException) as error:
        await generate("API", lambda: None, factory)
    assert error.value.status_code == 504 and error.value.detail == "provider_timeout"
    factory, _ = script([httpx.Response(429, text="private provider detail")])
    with pytest.raises(HTTPException) as error:
        await generate("API", lambda: None, factory)
    assert error.value.status_code == 429 and error.value.detail == "provider_unavailable"


async def test_overall_deadline_closes_waiting_stream(monkeypatch):
    import engine

    original_timeout = asyncio.timeout
    monkeypatch.setattr(engine.asyncio, "timeout", lambda _seconds: original_timeout(0.02))
    stream = WaitingStream()
    factory, _ = script([httpx.Response(200, stream=stream)])
    with pytest.raises(HTTPException) as error:
        await generate("API", lambda: None, factory)
    assert error.value.status_code == 504 and stream.closed


async def test_disconnect_retrieves_simultaneous_provider_failure(monkeypatch):
    import gc
    import app as api_module

    async def fail(*_args):
        raise HTTPException(502, "provider_invalid_response")

    class Disconnected:
        async def is_disconnected(self):
            return True

    monkeypatch.setattr(api_module, "generate", fail)
    endpoint = next(route.endpoint for route in create_app().routes if route.path == "/api/ask")
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        with pytest.raises(HTTPException) as error:
            await endpoint(
                api_module.Question(prompt="API", mode="openai"), Disconnected(), "test-access"
            )
        assert error.value.status_code == 499
        gc.collect()
        await asyncio.sleep(0)
        assert unhandled == []
    finally:
        loop.set_exception_handler(previous)
