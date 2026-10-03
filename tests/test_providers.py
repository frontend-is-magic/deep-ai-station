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
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")


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
        stream = stream_generate("openai", "x", "system", 0.3, client)
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
        stream = stream_generate("openai", "x", "system", 0.3, client)
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
        result = [event async for event in stream_generate("openai", "x", "system", 0.3, client)]
    assert result[0]["text"] == "你好"
    assert result[-1]["usage"] == {"total_tokens": 6}
    assert remote.closed


async def test_anthropic_usage_is_cumulative_and_thinking_is_not_answer_text():
    parts = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10, "output_tokens": 1}}},
        {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "internal"},
        },
        {"type": "future_event"},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "answer"}},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "max_tokens"},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    remote = RemoteStream([frame(value) for value in parts])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        result = [event async for event in stream_generate("anthropic", "x", "system", 0.3, client)]
    assert [event.get("text") for event in result[:-1]] == ["answer"]
    assert result[-1]["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert result[-1]["truncated"] is True


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
            async for event in stream_generate("openai", "x", "system", 0.3, client):
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
            _ = [event async for event in stream_generate("openai", "x", "system", 0.3, client)]
    assert error.value.status_code == 504 and remote.closed


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
async def test_tool_only_stream_accumulates_json_fragments_and_uses_native_definitions(provider):
    def response(request):
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "knowledge_search"
        assert body["tool_choice"] == "auto"
        assert body["messages"][1]["content"] == "question"
        if provider == "openai":
            assert body["parallel_tool_calls"] is False
        else:
            assert body["thinking"] == {"type": "disabled"}
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


async def test_anthropic_tool_stream_and_followup_history_match_the_native_protocol():
    messages = [
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "toolu_1",
                    "type": "function",
                    "function": {"name": "knowledge_search", "arguments": '{"query":"MCP"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "toolu_1",
            "content": '{"error":"invalid_arguments"}',
            "is_error": True,
        },
    ]

    def response(request):
        body = json.loads(request.content)
        assert body["tools"][0]["input_schema"] == TOOLS[0]["parameters"]
        assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
        assert body["messages"][1]["content"][0]["type"] == "tool_use"
        assert body["messages"][1]["content"][0]["input"] == {"query": "MCP"}
        result = body["messages"][2]["content"][0]
        assert (
            result["type"] == "tool_result"
            and result["tool_use_id"] == "toolu_1"
            and result["is_error"] is True
        )
        return httpx.Response(200, stream=remote)

    remote = RemoteStream(
        [
            frame(
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_2",
                        "name": "knowledge_search",
                        "input": {},
                    },
                }
            ),
            frame(
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "input_json_delta", "partial_json": '{"query":"MCP"}'},
                }
            ),
            frame({"type": "content_block_stop", "index": 1}),
            frame(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use"},
                    "usage": {"output_tokens": 12},
                }
            ),
            frame({"type": "message_stop"}),
        ]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        events = [
            event
            async for event in stream_generate(
                "anthropic", "", "system", 0.3, client, messages=messages, tools=TOOLS
            )
        ]
    assert events[-1]["tool_calls"][0]["arguments"] == {"query": "MCP"} and remote.closed


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
                "openai",
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
            event async for event in stream_generate("openai", "x", "s", 0.3, client, tools=TOOLS)
        ]
    assert events[-1]["truncated"] is True and events[-1]["tool_calls"] == [] and remote.closed


@pytest.mark.parametrize("close_index", [None, True])
async def test_anthropic_incomplete_or_invalid_tool_block_never_becomes_a_call(close_index):
    blocks = [
        {"type": "message_start", "message": {}},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "call_1",
                "name": "knowledge_search",
                "input": {"query": "MCP"},
            },
        },
        *(
            [{"type": "content_block_stop", "index": close_index}]
            if close_index is not None
            else []
        ),
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    ]
    remote = RemoteStream([frame(block) for block in blocks])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        with pytest.raises(HTTPException) as error:
            _ = [
                event
                async for event in stream_generate("anthropic", "x", "s", 0.3, client, tools=TOOLS)
            ]
    assert error.value.status_code == 502 and remote.closed
