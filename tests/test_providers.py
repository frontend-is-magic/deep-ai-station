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
