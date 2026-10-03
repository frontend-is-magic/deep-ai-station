import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException

from backend.providers import stream_generate


def frame(value):
    return f"data: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}\r\n\r\n".encode()


class RemoteStream(httpx.AsyncByteStream):
    def __init__(self, parts, pause=False):
        self.parts = parts
        self.pause = pause
        self.closed = False
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        for part in self.parts:
            yield part
        if self.pause:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)


TOOLS = [
    {
        "name": "knowledge_search",
        "description": "Read only course search",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "maxLength": 100}},
            "required": ["query"],
            "additionalProperties": False,
        },
    }
]


async def test_first_delta_arrives_before_completion_and_close_releases_upstream():
    remote = RemoteStream([frame({"choices": [{"delta": {"content": "first"}}]})], pause=True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        stream = stream_generate("deepseek", "x", "system", 0.3, client)
        first = await asyncio.wait_for(anext(stream), timeout=1)
        assert first == {"event": "delta", "text": "first"}
        assert not remote.closed and not remote.waiting.is_set()
        await stream.aclose()
        assert remote.closed


async def test_cancelling_while_waiting_for_next_token_releases_upstream():
    remote = RemoteStream([], pause=True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        stream = stream_generate("deepseek", "x", "system", 0.3, client)
        pending = asyncio.create_task(anext(stream))
        await remote.waiting.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert remote.closed


async def test_split_utf8_crlf_and_usage_only_chunk_are_decoded():
    content = (
        frame({"choices": [{"delta": {"content": "你好"}}]})
        + frame({"choices": [], "usage": {"total_tokens": 6, "untrusted_secret": "never-return"}})
        + frame("[DONE]")
    )
    remote = RemoteStream([content[i : i + 1] for i in range(len(content))])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        result = [event async for event in stream_generate("deepseek", "x", "system", 0.3, client)]
    assert result[0]["text"] == "你好"
    assert result[-1]["usage"] == {"total_tokens": 6}
    assert remote.closed


async def test_deepseek_compatible_request_keeps_budget_and_excludes_reasoning_from_answer():
    remote = RemoteStream(
        [
            frame({"choices": [{"delta": {"reasoning_content": "internal"}}]}),
            frame({"choices": [{"delta": {"content": "answer"}, "finish_reason": "length"}]}),
            frame(
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
                }
            ),
            frame("[DONE]"),
        ]
    )

    def response(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-only"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["messages"] == [
            {"role": "system", "content": "test system"},
            {"role": "user", "content": "test prompt"},
        ]
        assert body["max_tokens"] == 1200 and body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        assert body["thinking"] == {"type": "disabled"}
        assert "parallel_tool_calls" not in body
        return httpx.Response(200, stream=remote)

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        result = [
            event
            async for event in stream_generate(
                "deepseek", "test prompt", "test system", 0.3, client
            )
        ]
    assert [event.get("text") for event in result[:-1]] == ["answer"]
    assert result[-1]["usage"] == {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
    assert result[-1]["model"] == "deepseek-flash" and result[-1]["truncated"] is True
    assert remote.closed


@pytest.mark.parametrize(
    "parts",
    [
        [frame({"choices": [{"delta": {"content": "partial"}}]})],
        [frame({"error": {"message": "private-upstream-message"}})],
        [b"data: invalid private-upstream-message\n\n"],
        [b"data: " + b"x" * 70_000],
        [frame({"choices": [{"delta": {"content": "x" * 20_001}}]})],
        [frame("[DONE]")],
    ],
)
async def test_incomplete_malformed_or_excessive_stream_never_emits_done(parts):
    remote = RemoteStream(parts)
    seen = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        with pytest.raises(HTTPException) as error:
            async for event in stream_generate("deepseek", "x", "system", 0.3, client):
                seen.append(event)
    assert error.value.status_code == 502
    assert "private" not in error.value.detail
    assert all(event["event"] != "done" for event in seen)
    assert remote.closed


async def test_total_timeout_closes_stream_and_returns_fixed_error(monkeypatch):
    import backend.providers as providers

    monkeypatch.setattr(providers, "STREAM_TIMEOUT", 0.01)
    remote = RemoteStream([], pause=True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        with pytest.raises(HTTPException) as error:
            _ = [event async for event in stream_generate("deepseek", "x", "system", 0.3, client)]
    assert error.value.status_code == 504 and remote.closed


@pytest.mark.parametrize("provider", ["deepseek"])
async def test_tool_only_stream_accumulates_json_fragments_and_uses_native_definitions(provider):
    def response(request):
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "knowledge_search"
        assert body["tool_choice"] == "auto"
        assert body["messages"][1]["content"] == "question"
        assert body["thinking"] == {"type": "disabled"}
        assert "strict" not in body["tools"][0]["function"]
        assert "parallel_tool_calls" not in body
        return httpx.Response(200, stream=remote)

    remote = RemoteStream(
        [
            frame(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "knowledge_search",
                                            "arguments": '{"query":',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
            frame(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": '"工具授权"}'}}
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ),
            frame({"choices": [], "usage": {"total_tokens": 10}}),
            frame("[DONE]"),
        ]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        events = [
            event
            async for event in stream_generate(
                provider, "question", "system", 0.3, client, tools=TOOLS
            )
        ]
    assert len(events) == 1 and events[0]["event"] == "done"
    assert events[0]["tool_calls"] == [
        {"id": "call_1", "name": "knowledge_search", "arguments": {"query": "工具授权"}}
    ]
    assert events[0]["usage"] == {"total_tokens": 10} and remote.closed


@pytest.mark.parametrize(
    "case",
    [
        "parallel",
        "malformed",
        "too_large",
        "none",
        "missing_id",
        "wrong_finish",
        "deep_json",
        "array_args",
        "changed_id",
        "invalid_index",
    ],
)
async def test_invalid_tool_streams_are_never_returned_as_executable_calls(case):
    call = {
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {"name": "knowledge_search", "arguments": '{"query":"MCP"}'},
    }
    calls = [call]
    finish = "tool_calls"
    if case == "parallel":
        calls.append({**call, "index": 1, "id": "call_2"})
    elif case == "malformed":
        call["function"]["arguments"] = '{"query":'
    elif case == "too_large":
        call["function"]["arguments"] = "x" * 4097
    elif case == "missing_id":
        call.pop("id")
    elif case == "wrong_finish":
        finish = "stop"
    elif case == "deep_json":
        call["function"]["arguments"] = "[" * 1500 + "0" + "]" * 1500
    elif case == "array_args":
        call["function"]["arguments"] = '["MCP"]'
    elif case == "changed_id":
        calls.append({"index": 0, "id": "call_changed"})
    elif case == "invalid_index":
        call["index"] = True
    remote = RemoteStream(
        [
            frame({"choices": [{"delta": {"tool_calls": calls}, "finish_reason": finish}]}),
            frame("[DONE]"),
        ]
    )
    seen = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        with pytest.raises(HTTPException) as error:
            async for event in stream_generate(
                "deepseek",
                "x",
                "s",
                0.3,
                client,
                tools=TOOLS,
                tool_choice="none" if case == "none" else "auto",
            ):
                seen.append(event)
    assert error.value.status_code == 502 and seen == [] and remote.closed


async def test_truncated_tool_arguments_do_not_become_a_tool_call():
    remote = RemoteStream(
        [
            frame(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "knowledge_search",
                                            "arguments": '{"query":',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "length",
                        }
                    ]
                }
            ),
            frame("[DONE]"),
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        events = [
            event async for event in stream_generate("deepseek", "x", "s", 0.3, client, tools=TOOLS)
        ]
    assert events[-1]["truncated"] is True and events[-1]["tool_calls"] == [] and remote.closed
