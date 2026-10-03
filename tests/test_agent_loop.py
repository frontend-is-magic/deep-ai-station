import asyncio
import json
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException

from backend.agent_loop import execute_tool, stream_agent


def completion(*, name=None, arguments=None, id="call_1", text=None, usage=None):
    delta = (
        {"content": text}
        if text
        else {
            "tool_calls": [
                {
                    "index": 0,
                    "id": id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ]
        }
    )
    payloads = [
        {"choices": [{"delta": delta, "finish_reason": "stop" if text else "tool_calls"}]},
        {"choices": [], "usage": usage},
    ]
    return (
        b"".join(f"data: {json.dumps(value)}\n\n".encode() for value in payloads)
        + b"data: [DONE]\n\n"
    )


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
async def test_native_agent_searches_reads_then_answers_with_bounded_requests_and_partial_usage(
    provider,
):
    responses = [
        completion(name="knowledge_search", arguments={"query": "MCP"}, usage={"total_tokens": 5}),
        completion(name="lesson_read", arguments={"lesson_id": "agent-mcp"}, id="call_2"),
        completion(text="依据实际课程来源回答 MCP", usage={"total_tokens": 7}),
    ]
    bodies = []

    def response(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, content=responses[len(bodies) - 1])

    charge = Mock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        events = [
            event
            async for event in stream_agent(
                provider,
                "MCP",
                "system",
                0.3,
                "agent",
                "agent-mcp",
                "run-1",
                client=client,
                charge=charge,
            )
        ]
    assert len(bodies) == charge.call_count == 3
    assert bodies[2]["tool_choice"] == "none"
    assert all(body["max_tokens"] == 1200 for body in bodies)
    first_observation = json.loads(bodies[1]["messages"][-1]["content"])
    assert first_observation["read_only"] is True
    assert all(item["id"].startswith("agent-") for item in first_observation["items"])
    assert "modelcontextprotocol.io" in json.dumps(first_observation)
    second_observation = json.loads(bodies[2]["messages"][-1]["content"])
    assert second_observation["lesson"]["id"] == "agent-mcp"
    assert len(second_observation["lesson"]["criteria"]) == 3
    assert events[-1]["usage"] == {"total_tokens": 12} and events[-1]["usage_complete"] is False
    assert events[-1]["steps"] == 3 and events[-1]["tool_count"] == 2
    assert [e["text"] for e in events if e["event"] == "delta"] == ["依据实际课程来源回答 MCP"]


def anthropic_completion(*, name=None, arguments=None, id="call_1", text=None):
    block = (
        {"type": "text", "text": text}
        if text
        else {"type": "tool_use", "id": id, "name": name, "input": arguments}
    )
    payloads = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 2}}},
        {"type": "content_block_start", "index": 0, "content_block": block},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn" if text else "tool_use"},
            "usage": {"output_tokens": 3},
        },
        {"type": "message_stop"},
    ]
    return b"".join(f"data: {json.dumps(value)}\n\n".encode() for value in payloads)


async def test_anthropic_agent_replays_native_tool_results_and_aggregates_actual_round_usage():
    bodies = []
    responses = [
        anthropic_completion(name="knowledge_search", arguments={"query": "MCP"}),
        anthropic_completion(name="lesson_read", arguments={"lesson_id": "agent-mcp"}, id="call_2"),
        anthropic_completion(text="依据 MCP 课程的验收项回答"),
    ]

    def response(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, content=responses[len(bodies) - 1])

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        events = [
            event
            async for event in stream_agent(
                "anthropic", "MCP", "system", 0.3, "agent", "agent-mcp", "r", client=client
            )
        ]
    for body in bodies[:2]:
        assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert bodies[2]["tool_choice"] == {"type": "none"}
    assert [m["role"] for m in bodies[2]["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    for n, body in enumerate(bodies[1:], 1):
        result = body["messages"][-1]["content"][0]
        use = body["messages"][-2]["content"][0]
        assert result["type"] == "tool_result" and use["type"] == "tool_use"
        assert result["tool_use_id"] == use["id"] == f"call_{n}"
        observation = json.loads(result["content"])
        assert observation["read_only"] is True and result["is_error"] is False
    assert json.loads(bodies[2]["messages"][-1]["content"][0]["content"])["lesson"]["criteria"]
    assert events[-1]["usage"] == {"input_tokens": 6, "output_tokens": 9}
    assert events[-1]["usage_complete"] is True
    assert events[-1]["steps"] == 3 and events[-1]["tool_count"] == 2


@pytest.mark.parametrize(
    "name,arguments,error",
    [
        ("delete_file", {"path": "/files/a"}, "unknown_tool"),
        ("knowledge_search", {"query": "MCP", "url": "https://example.com"}, "invalid_arguments"),
        ("knowledge_search", {"query": " "}, "invalid_arguments"),
        ("knowledge_search", {"query": "x" * 101}, "invalid_arguments"),
        ("lesson_read", {"lesson_id": "fullstack-http"}, "lesson_not_in_track"),
        ("lesson_read", {"lesson_id": "../../etc/passwd"}, "lesson_not_in_track"),
    ],
)
def test_tool_permission_and_arguments_are_enforced_before_dispatch(
    monkeypatch, name, arguments, error
):
    import backend.agent_loop as agent

    retrieve = Mock(side_effect=AssertionError("Must not search invalid inputs"))
    monkeypatch.setattr(agent, "retrieve", retrieve)
    result = execute_tool(name, arguments, "agent", "run:1")
    assert result["error"] == error and result["read_only"] is True
    retrieve.assert_not_called()


async def test_unknown_tool_is_an_error_observation_without_dispatch_and_can_recover():
    bodies = []

    def response(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(
                200, content=completion(name="publish", arguments={"target": "arbitrary"})
            )
        assert json.loads(body["messages"][-1]["content"])["error"] == "unknown_tool"
        return httpx.Response(200, content=completion(text="该工具未被允许，只能使用课程资料"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        events = [
            e
            async for e in stream_agent("openai", "x", "s", 0.3, "agent", None, "r", client=client)
        ]
    assert events[-1]["steps"] == 2 and len(bodies) == 2
    assert any(e.get("data", {}).get("status") == "error" for e in events)


async def test_same_read_only_arguments_reuse_observation_without_repeating_dispatch(monkeypatch):
    import backend.agent_loop as agent

    dispatch = Mock(wraps=agent.execute_tool)
    monkeypatch.setattr(agent, "execute_tool", dispatch)
    bodies = []

    def response(request):
        bodies.append(json.loads(request.content))
        n = len(bodies)
        return httpx.Response(
            200,
            content=completion(name="knowledge_search", arguments={"query": "MCP"}, id=f"call_{n}")
            if n < 3
            else completion(text="课程来源已找到"),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        events = [
            e
            async for e in stream_agent("openai", "x", "s", 0.3, "agent", None, "r", client=client)
        ]
    assert dispatch.call_count == 1 and events[-1]["tool_count"] == 2
    assert bodies[1]["messages"][-1]["content"] == bodies[2]["messages"][-1]["content"]


async def test_model_cannot_request_another_tool_on_the_last_round(monkeypatch):
    import backend.agent_loop as agent

    dispatch = Mock(wraps=agent.execute_tool)
    monkeypatch.setattr(agent, "execute_tool", dispatch)
    requests = []

    def response(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            content=completion(
                name="knowledge_search", arguments={"query": "MCP"}, id=f"call_{len(requests)}"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(HTTPException) as error:
            _ = [
                e
                async for e in stream_agent(
                    "openai", "x", "s", 0.3, "agent", None, "r", client=client
                )
            ]
    assert error.value.status_code == 502 and len(requests) == 3 and dispatch.call_count == 1
    assert requests[-1]["tool_choice"] == "none"


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


async def test_cancelled_agent_closes_the_active_model_stream():
    remote = WaitingStream()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        stream = stream_agent("openai", "x", "s", 0.3, "agent", None, "r", client=client)
        assert (await anext(stream))["event"] == "trace"
        pending = asyncio.create_task(anext(stream))
        await remote.waiting.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert remote.closed


async def test_agent_total_deadline_closes_the_stream(monkeypatch):
    import backend.agent_loop as agent

    monkeypatch.setattr(agent, "TOTAL_TIMEOUT", 0.01)
    remote = WaitingStream()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        with pytest.raises(HTTPException) as error:
            _ = [
                e
                async for e in stream_agent(
                    "openai", "x", "s", 0.3, "agent", None, "r", client=client
                )
            ]
    assert error.value.status_code == 504 and remote.closed
