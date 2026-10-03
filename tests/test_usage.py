import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from backend import agent_loop, providers
from backend.quota import MemoryQuota, Policy
from backend.usage import UsageTracker, usage_counts

FULL = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
NEXT = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


def frame(value):
    payload = value if isinstance(value, str) else json.dumps(value)
    return f"data: {payload}\n\n".encode()


def completion(*, tool=False, usage=FULL, done=True, repeated=False):
    delta = {"content": "answer"}
    if tool:
        delta = {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "knowledge_search", "arguments": '{"query":"MCP"}'},
                }
            ]
        }
    parts = [
        frame({"choices": [{"delta": delta, "finish_reason": "tool_calls" if tool else "stop"}]})
    ]
    parts.append(frame({"choices": [], "usage": usage}))
    if repeated:
        parts.append(frame({"choices": [], "usage": usage}))
    if done:
        parts.append(frame("[DONE]"))
    return b"".join(parts)


class Remote(httpx.AsyncByteStream):
    def __init__(self, parts, *, failure=None, pause=False):
        self.parts = parts
        self.failure = failure
        self.pause = pause
        self.waiting = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        for part in self.parts:
            yield part
        if self.failure:
            raise self.failure
        if self.pause:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "test-access")
    monkeypatch.setenv("AI_QUOTA_MODE", "memory")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)


def test_usage_filters_untrusted_values_and_preserves_zero():
    assert usage_counts(
        {"prompt_tokens": 0, "completion_tokens": True, "total_tokens": -1, "key": "never"}
    ) == {"prompt_tokens": 0}
    assert usage_counts({"total_tokens": 100_000_001}) == {}
    assert usage_counts(None) == {}


def test_request_snapshots_replace_without_double_counting_and_are_copies():
    tracker = UsageTracker()
    first = tracker.begin()
    tracker.observe(first, FULL)
    tracker.observe(first, FULL)
    tracker.finish(first, {"usage": FULL})
    tracker.finish(first, {"usage": FULL})
    assert tracker.snapshot() == {"usage": FULL, "usage_complete": False}
    assert tracker.snapshot(terminal=True) == {"usage": FULL, "usage_complete": True}
    snapshot = tracker.snapshot()
    snapshot["usage"]["total_tokens"] = 99
    second = tracker.begin()
    tracker.observe(second, {"total_tokens": 7})
    tracker.observe(second, NEXT)
    tracker.finish(second, {"usage": NEXT})
    assert tracker.snapshot(terminal=True) == {
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "usage_complete": True,
    }


def test_unknown_round_is_not_zero_and_zero_round_is_known():
    tracker = UsageTracker()
    assert tracker.snapshot(terminal=True) == {"usage": None, "usage_complete": False}
    first = tracker.begin()
    zero = dict.fromkeys(FULL, 0)
    tracker.finish(first, {"usage": zero})
    assert tracker.snapshot(terminal=True) == {"usage": zero, "usage_complete": True}
    tracker.begin()
    assert tracker.snapshot(terminal=True) == {"usage": zero, "usage_complete": False}


@pytest.mark.parametrize("missing", list(FULL))
def test_each_missing_field_keeps_terminal_usage_incomplete(missing):
    tracker = UsageTracker()
    index = tracker.begin()
    partial = {key: value for key, value in FULL.items() if key != missing}
    tracker.finish(index, {"usage": partial})
    assert tracker.snapshot(terminal=True) == {"usage": partial, "usage_complete": False}


@pytest.mark.parametrize("ending,code", [("eof", 502), ("transport", 502), ("timeout", 504)])
async def test_provider_publishes_usage_before_incomplete_stream_failure(ending, code):
    failures = {
        "eof": None,
        "transport": httpx.ReadError("private transport detail"),
        "timeout": httpx.ReadTimeout("private timeout detail"),
    }
    remote = Remote([completion(done=False)], failure=failures[ending])
    seen = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        with pytest.raises(HTTPException) as error:
            async for event in providers.stream_generate("deepseek", "x", "s", 0.3, client):
                seen.append(event)
    assert error.value.status_code == code and "private" not in error.value.detail
    assert [event for event in seen if event["event"] == "usage"] == [
        {"event": "usage", "usage": FULL, "usage_complete": False}
    ]
    assert not any(event["event"] == "done" for event in seen)
    assert remote.closed


@pytest.mark.parametrize("delta", [{"content": None}, {}])
@pytest.mark.parametrize("tool", [False, True])
async def test_deepseek_documented_last_content_chunk_carries_usage(delta, tool):
    if tool:
        initial = {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "knowledge_search", "arguments": '{"query":"MCP"}'},
                }
            ]
        }
    else:
        initial = {"content": "answer"}
    remote = Remote(
        [
            frame({"choices": [{"delta": initial, "finish_reason": None}]}),
            frame(
                {
                    "choices": [
                        {"delta": delta, "finish_reason": "tool_calls" if tool else "stop"}
                    ],
                    "usage": FULL,
                }
            ),
            frame("[DONE]"),
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        events = [
            event
            async for event in providers.stream_generate(
                "deepseek", "x", "s", 0.3, client, tools=agent_loop.TOOLS if tool else None
            )
        ]
    assert events[-2] == {"event": "usage", "usage": FULL, "usage_complete": False}
    assert events[-1]["event"] == "done" and events[-1]["usage_complete"] is True
    assert bool(events[-1].get("tool_calls")) is tool
    assert [event["text"] for event in events if event["event"] == "delta"] == (
        [] if tool else ["answer"]
    )
    assert remote.closed


async def test_usage_only_stream_is_never_a_success_without_output_or_tool():
    seen = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=frame({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": FULL})
                + frame("[DONE]"),
            )
        )
    ) as client:
        with pytest.raises(HTTPException) as error:
            async for event in providers.stream_generate("deepseek", "x", "s", 0.3, client):
                seen.append(event)
    assert error.value.status_code == 502
    assert seen == [{"event": "usage", "usage": FULL, "usage_complete": False}]


async def test_provider_updates_and_partial_fields_merge_without_inventing_missing_counts():
    data = [frame({"choices": [{"delta": {"content": "answer"}, "finish_reason": "stop"}]})]
    for counts in (
        {"prompt_tokens": 0},
        {"prompt_tokens": 0},
        {"completion_tokens": 2},
        {"completion_tokens": 3},
        {"unexpected": "private"},
    ):
        data.append(frame({"choices": [], "usage": counts}))
    data.append(frame("[DONE]"))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"".join(data)))
    ) as client:
        events = [
            event async for event in providers.stream_generate("deepseek", "x", "s", 0.3, client)
        ]
    snapshots = [event["usage"] for event in events if event["event"] == "usage"]
    assert snapshots == [
        {"prompt_tokens": 0},
        {"prompt_tokens": 0},
        {"prompt_tokens": 0, "completion_tokens": 2},
        {"prompt_tokens": 0, "completion_tokens": 3},
    ]
    assert events[-1]["usage"] == snapshots[-1] and events[-1]["usage_complete"] is False


@pytest.mark.parametrize("ending", ["cancel", "close"])
async def test_provider_cancel_or_generator_close_retains_sent_snapshot_without_final_yield(ending):
    remote = Remote([completion(done=False)], pause=True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        stream = providers.stream_generate("deepseek", "x", "s", 0.3, client)
        assert (await anext(stream))["event"] == "delta"
        snapshot = await anext(stream)
        if ending == "cancel":
            pending = asyncio.create_task(anext(stream))
            await remote.waiting.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        await stream.aclose()
    assert snapshot == {"event": "usage", "usage": FULL, "usage_complete": False}
    assert remote.closed


def parse_events(response):
    events = []
    for frame_text in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in frame_text.splitlines())
        events.append({"event": fields["event"], "data": json.loads(fields["data"])})
    return events


@pytest.mark.parametrize(
    "ending,code,complete,calls",
    [
        ("quota", 429, True, 1),
        ("provider-429", 429, False, 2),
        ("provider-502", 502, False, 2),
        ("incomplete", 502, False, 2),
        ("success", None, True, 2),
    ],
)
async def test_agent_api_preserves_prior_and_current_known_usage_without_false_success(
    monkeypatch, ending, code, complete, calls
):
    import backend.app as api

    provider_calls = []

    def response(request):
        provider_calls.append(request)
        if len(provider_calls) == 1:
            return httpx.Response(200, content=completion(tool=True, repeated=True))
        if ending.startswith("provider-"):
            return httpx.Response(int(ending.split("-")[1]), text="private body")
        return httpx.Response(200, content=completion(usage=NEXT, done=ending == "success"))

    quota = MemoryQuota(
        "test", {"model": Policy(1 if ending == "quota" else 10, 100)}, clock=lambda: 86401
    )

    async def begin_attempt(*_args):
        await quota.admit("model")
        return AsyncMock()

    monkeypatch.setattr(api, "begin_model_attempt", begin_attempt)
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as provider_client:

        def stream(*args, **kwargs):
            return agent_loop.stream_agent(*args, **kwargs, client=provider_client)

        monkeypatch.setattr(api, "stream_agent", stream)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            result = await client.post(
                "/api/playground/run",
                json={"prompt": "MCP", "provider": "deepseek", "workflow": "agent"},
                headers={"X-Playground-Token": "test-access"},
            )
    assert result.status_code == 200 and len(provider_calls) == calls
    events = parse_events(result)
    snapshots = [event["data"] for event in events if event["event"] == "usage"]
    assert snapshots and all(snapshot["usage_complete"] is False for snapshot in snapshots)
    assert all(set(snapshot) == {"run_id", "usage", "usage_complete"} for snapshot in snapshots)
    assert all(snapshot["run_id"] == events[0]["data"]["run_id"] for snapshot in snapshots)
    expected = (
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if ending in {"success", "incomplete"}
        else FULL
    )
    assert events[-1]["data"]["usage"] == expected
    assert events[-1]["data"]["usage_complete"] is complete
    assert events[-1]["event"] == ("done" if ending == "success" else "error")
    if code:
        assert events[-1]["data"]["code"] == code
        assert not any(event["event"] == "done" for event in events)
    if ending == "quota":
        assert events[-1]["data"]["retry_after"] == 59
    assert "private body" not in result.text


@pytest.mark.parametrize("ending", ["success", "incomplete", "truncated", "legacy"])
async def test_retrieval_has_the_same_snapshot_and_terminal_contract(monkeypatch, ending):
    import backend.app as api

    monkeypatch.setattr(api, "begin_model_attempt", AsyncMock())
    payload = completion(done=ending != "incomplete")
    if ending == "truncated":
        payload = payload.replace(b'"finish_reason": "stop"', b'"finish_reason": "length"')
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload))
    ) as provider_client:

        def stream(*args, **kwargs):
            return providers.stream_generate(*args, **kwargs, client=provider_client)

        if ending == "legacy":

            async def stream(*args, **kwargs):
                yield {"event": "delta", "text": "legacy answer"}
                yield {"event": "done", "model": "mock", "usage": FULL}

        monkeypatch.setattr(api, "stream_generate", stream)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            result = await client.post(
                "/api/playground/run",
                json={"prompt": "MCP", "provider": "deepseek"},
                headers={"X-Playground-Token": "test-access"},
            )
    events = parse_events(result)
    assert events[-1]["data"]["usage"] == FULL
    assert events[-1]["data"]["usage_complete"] is (ending != "incomplete")
    assert events[-1]["event"] == ("error" if ending == "incomplete" else "done")
    if ending == "truncated":
        assert events[-1]["data"]["truncated"] is True
    if ending != "legacy":
        snapshot = next(event for event in events if event["event"] == "usage")
        assert snapshot["data"]["usage"] == FULL and snapshot["data"]["usage_complete"] is False


async def test_agent_cancel_later_round_keeps_earlier_snapshot_without_final_event():
    remote = Remote([], pause=True)
    calls = []

    def response(request):
        calls.append(request)
        return (
            httpx.Response(200, content=completion(tool=True))
            if len(calls) == 1
            else httpx.Response(200, stream=remote)
        )

    tracker = UsageTracker()
    seen = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        stream = agent_loop.stream_agent(
            "deepseek", "MCP", "s", 0.3, "agent", None, "test", client=client, usage_tracker=tracker
        )

        async def consume():
            async for event in stream:
                seen.append(event)

        task = asyncio.create_task(consume())
        await remote.waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await stream.aclose()
    assert len(calls) == 2 and remote.closed
    assert any(event.get("usage") == FULL for event in seen if event["event"] == "usage")
    assert tracker.snapshot(terminal=True) == {"usage": FULL, "usage_complete": False}
    assert not any(event["event"] == "done" for event in seen)


@pytest.mark.parametrize("workflow", ["retrieval", "agent"])
async def test_response_close_failure_keeps_known_usage_but_never_marks_it_complete(
    monkeypatch, workflow
):
    import backend.app as api

    class CloseFailure(Remote):
        async def aclose(self):
            self.closed = True
            raise httpx.ReadError("private close diagnostic")

    remote = CloseFailure([completion()])
    monkeypatch.setattr(api, "begin_model_attempt", AsyncMock())
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as provider_client:

        def model_stream(*args, **kwargs):
            return providers.stream_generate(*args, **kwargs, client=provider_client)

        def agent_stream(*args, **kwargs):
            return agent_loop.stream_agent(*args, **kwargs, client=provider_client)

        monkeypatch.setattr(api, "stream_generate", model_stream)
        monkeypatch.setattr(api, "stream_agent", agent_stream)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            result = await client.post(
                "/api/playground/run",
                json={"prompt": "MCP", "provider": "deepseek", "workflow": workflow},
                headers={"X-Playground-Token": "test-access"},
            )
    events = parse_events(result)
    assert events[-1]["event"] == "error"
    assert events[-1]["data"]["code"] == 502
    assert events[-1]["data"]["usage"] == FULL
    assert events[-1]["data"]["usage_complete"] is False
    assert not any(event["event"] == "done" for event in events)
    assert "private close diagnostic" not in result.text and remote.closed


@pytest.mark.parametrize("ending", ["cancel", "close"])
async def test_agent_interruption_survives_response_close_failure_without_yield(ending):
    class CloseFailure(Remote):
        async def aclose(self):
            self.closed = True
            raise httpx.ReadError("private close diagnostic")

    remote = CloseFailure([completion(done=False)], pause=True)
    tracker = UsageTracker()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=remote))
    ) as client:
        stream = agent_loop.stream_agent(
            "deepseek", "MCP", "s", 0.3, "agent", None, "test", client=client, usage_tracker=tracker
        )
        while (await anext(stream))["event"] != "usage":
            pass
        if ending == "cancel":
            pending = asyncio.create_task(anext(stream))
            await remote.waiting.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        await stream.aclose()
    assert remote.closed
    assert tracker.snapshot(terminal=True) == {"usage": FULL, "usage_complete": False}


@pytest.mark.parametrize("interrupted", [True, False])
async def test_owned_client_cleanup_is_sanitized_and_preserves_cancellation(interrupted):
    from types import SimpleNamespace

    client = SimpleNamespace(aclose=AsyncMock(side_effect=httpx.ReadError("private client close")))
    if interrupted:
        with pytest.raises(asyncio.CancelledError):
            try:
                raise asyncio.CancelledError
            finally:
                await providers.close_client(client)
    else:
        with pytest.raises(HTTPException) as error:
            await providers.close_client(client)
        assert error.value.status_code == 502
        assert "private" not in error.value.detail
    client.aclose.assert_awaited_once()
