"""API -> real model parser lifecycle with controlled storage and no provider network."""

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException

from backend import agent_loop, providers
from backend import app as api
from backend.usage import ModelRequest

ERROR = "模型请求账本暂不可用，请稍后重试"
FULL = {"prompt_tokens": 3, "completion_tokens": 0, "total_tokens": 3}


def frame(data):
    return f"data: {data if isinstance(data, str) else json.dumps(data)}\n\n".encode()


def completion(index=1, *, tool=False, counts=FULL, done=True, repeated=False, truncated=False):
    delta = {"content": "fixed answer"}
    if tool:
        delta = {
            "tool_calls": [
                {
                    "index": 0,
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {"name": "knowledge_search", "arguments": '{"query":"MCP"}'},
                }
            ]
        }
    parts = [
        frame(
            {
                "choices": [
                    {
                        "delta": delta,
                        "finish_reason": "tool_calls"
                        if tool
                        else "length"
                        if truncated
                        else "stop",
                    }
                ]
            }
        )
    ]
    if repeated:
        parts.append(frame({"choices": [], "usage": {"prompt_tokens": 0}}))
    if counts is not None:
        parts.append(frame({"choices": [], "usage": counts}))
        if repeated:
            parts.append(parts[-1])
    if done:
        parts.append(frame("[DONE]"))
    return b"".join(parts)


class Remote(httpx.AsyncByteStream):
    def __init__(self, content, events, *, pause=False, close_failure=False):
        self.content = content
        self.events = events
        self.pause = pause
        self.close_failure = close_failure
        self.waiting = asyncio.Event()
        self.closed = asyncio.Event()
        self.close_count = 0

    async def __aiter__(self):
        yield self.content
        if self.pause:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self):
        self.close_count += 1
        self.events.append("response-close")
        self.closed.set()
        if self.close_failure:
            raise httpx.ReadError("private-close-diagnostic")


class Attempt:
    def __init__(self, owner, index):
        self.owner = owner
        self.index = index
        self.counts = {}
        self.snapshots = []
        self.finishes = []
        self.status = "admitted"
        self.finish_closed = asyncio.Event()
        self.snapshot_waiting = asyncio.Event()
        self.snapshot_closed = asyncio.Event()
        self.complete_waiting = asyncio.Event()

    async def observe(self, counts):
        self.counts = dict(counts)
        self.snapshots.append(dict(counts))
        self.owner.events.append("snapshot")
        if self.owner.block_snapshot:
            self.snapshot_waiting.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.snapshot_closed.set()
        if self.owner.snapshot_failure == self.index:
            raise HTTPException(503, ERROR)

    async def finish(self, status, **values):
        self.finishes.append((status, values))
        self.owner.events.append("finish:" + status)
        try:
            if self.owner.block_cancel and status == "cancelled":
                await asyncio.Event().wait()
            if self.owner.fail_cancel and status == "cancelled":
                raise HTTPException(503, ERROR)
            if self.owner.block_completed and status == "completed":
                self.complete_waiting.set()
                await asyncio.Event().wait()
            self.status = status
            if self.owner.finish_failure == self.index:
                # Simulate an unknown commit: callers cannot overwrite or retry it.
                raise HTTPException(503, ERROR)
        finally:
            self.finish_closed.set()


class Ledger:
    def __init__(self, events):
        self.events = events
        self.records = []
        self.calls = []
        self.reject_index = None
        self.snapshot_failure = None
        self.finish_failure = None
        self.block_cancel = False
        self.fail_cancel = False
        self.mutate_model = None
        self.block_snapshot = False
        self.block_completed = False
        self.admitted = asyncio.Event()

    async def begin(self, run_id, index, provider, model):
        assert UUID(run_id).version == 4
        assert provider == "deepseek"
        self.calls.append((run_id, index, provider, model))
        if self.reject_index == index:
            raise HTTPException(429, "共享请求额度已用完", headers={"Retry-After": "17"})
        self.events.append("begin")
        attempt = Attempt(self, index)
        self.records.append(attempt)
        self.admitted.set()
        if self.mutate_model:
            self.mutate_model()
        return attempt


@pytest.fixture
async def harness(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixed-test-key")
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "test-access")
    monkeypatch.setenv("DEEPSEEK_MODEL", "requested-model")
    events = []
    ledger = Ledger(events)
    state = SimpleNamespace(
        events=events, ledger=ledger, remotes=[], calls=[], clients=[], client_failure=False
    )
    original_client = httpx.AsyncClient

    def response(request):
        assert len(ledger.records) > len(state.calls), "Provider must follow confirmed admission"
        state.calls.append(json.loads(request.content))
        state.events.append("provider")
        return httpx.Response(200, stream=state.remotes[len(state.calls) - 1])

    class OwnedClient(original_client):
        async def aclose(self):
            await asyncio.sleep(0)
            await super().aclose()
            events.append("client-close")
            if state.client_failure:
                raise httpx.ReadError("private-client-diagnostic")

    def client_factory(*_args, **_kwargs):
        client = OwnedClient(transport=httpx.MockTransport(response))
        state.clients.append(client)
        return client

    async with original_client(
        transport=httpx.ASGITransport(app=api.app), base_url="http://test"
    ) as browser:
        state.browser = browser
        monkeypatch.setattr(api, "begin_model_attempt", ledger.begin)
        monkeypatch.setattr(providers.httpx, "AsyncClient", client_factory)
        yield state
    assert all(client.is_closed for client in state.clients)


def remote(harness, content, **options):
    result = Remote(content, harness.events, **options)
    harness.remotes.append(result)
    return result


async def request(harness, workflow="retrieval", **body):
    return await harness.browser.post(
        "/api/playground/run",
        json={"provider": "deepseek", "prompt": "MCP", "workflow": workflow, **body},
        headers={"X-Playground-Token": "test-access"},
    )


def events(response):
    return [
        {"event": lines[0][7:], "data": json.loads(lines[1][6:])}
        for part in response.text.strip().split("\n\n")
        if part and (lines := part.splitlines())
    ]


@pytest.mark.parametrize(
    "workflow,rounds", [("retrieval", 1), ("agent", 1), ("agent", 2), ("agent", 3)]
)
async def test_each_request_has_one_admission_latest_snapshot_and_post_cleanup_terminal(
    harness, workflow, rounds
):
    for index in range(1, rounds + 1):
        remote(
            harness,
            completion(
                index,
                tool=index < rounds,
                counts={"prompt_tokens": index, "completion_tokens": 0, "total_tokens": index},
                repeated=True,
            ),
        )
    response = await request(harness, workflow)
    observed = events(response)
    assert observed[-1]["event"] == "done"
    assert observed[-1]["data"]["usage"] == {
        "prompt_tokens": sum(range(1, rounds + 1)),
        "completion_tokens": 0,
        "total_tokens": sum(range(1, rounds + 1)),
    }
    assert observed[-1]["data"]["usage_complete"] is True
    assert len(harness.calls) == len(harness.clients) == len(harness.ledger.records) == rounds
    run_id = observed[0]["data"]["run_id"]
    assert harness.ledger.calls == [
        (run_id, index, "deepseek", "requested-model") for index in range(1, rounds + 1)
    ]
    for index, record in enumerate(harness.ledger.records, 1):
        assert record.counts == {
            "prompt_tokens": index,
            "completion_tokens": 0,
            "total_tokens": index,
        }
        assert record.snapshots[0] == {"prompt_tokens": 0}
        assert record.finishes == [("completed", {"usage_complete": True, "truncated": False})]
    order = harness.events
    assert order.count("response-close") == order.count("client-close") == rounds
    for index, entry in enumerate(order):
        if entry == "finish:completed":
            assert order[index - 2 : index] == ["response-close", "client-close"]
    assert all(item.close_count == 1 for item in harness.remotes)


@pytest.mark.parametrize("workflow", ["retrieval", "agent"])
@pytest.mark.parametrize(
    "counts,complete",
    [
        (None, False),
        ({"total_tokens": 0}, False),
        ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, True),
    ],
)
async def test_missing_fields_remain_unknown_and_real_zero_is_preserved(
    harness, workflow, counts, complete
):
    remote(harness, completion(counts=counts, truncated=True))
    result = events(await request(harness, workflow))[-1]
    assert result["event"] == "done" and result["data"]["usage"] == counts
    assert result["data"]["usage_complete"] is complete
    assert harness.ledger.records[0].finishes == [
        ("completed", {"usage_complete": complete, "truncated": True})
    ]


@pytest.mark.parametrize("workflow", ["retrieval", "agent"])
async def test_snapshot_failure_retains_memory_counts_closes_upstream_and_stops_next_round(
    harness, workflow
):
    active = remote(harness, completion(tool=workflow == "agent", done=False), pause=True)
    harness.ledger.snapshot_failure = 1
    result = events(await request(harness, workflow))[-1]
    assert result["event"] == "error" and result["data"]["code"] == 503
    assert result["data"]["usage"] == FULL and result["data"]["usage_complete"] is False
    assert result["data"]["message"] == ERROR
    assert len(harness.calls) == len(harness.ledger.records) == 1
    assert active.close_count == 1 and harness.clients[0].is_closed
    assert harness.ledger.records[0].finishes == [("failed", {"reason": "ledger_error"})]


@pytest.mark.parametrize("workflow", ["retrieval", "agent"])
async def test_unknown_terminal_commit_never_overwrites_completed_or_sends_next_request(
    harness, workflow
):
    remote(harness, completion(tool=workflow == "agent"))
    harness.ledger.finish_failure = 1
    result = events(await request(harness, workflow))[-1]
    assert result["event"] == "error" and result["data"]["code"] == 503
    assert result["data"]["usage"] == FULL and result["data"]["usage_complete"] is False
    assert len(harness.calls) == 1
    record = harness.ledger.records[0]
    assert len(record.finishes) == 1 and record.status == "completed"


@pytest.mark.parametrize("workflow", ["retrieval", "agent"])
@pytest.mark.parametrize("failure", ["incomplete", "response-close", "client-close"])
async def test_upstream_failures_keep_counts_in_failed_row_with_incomplete_usage(
    harness, workflow, failure
):
    active = remote(
        harness, completion(done=failure != "incomplete"), close_failure=failure == "response-close"
    )
    harness.client_failure = failure == "client-close"
    response = await request(harness, workflow)
    result = events(response)[-1]
    assert result["event"] == "error" and result["data"]["code"] == 502
    assert result["data"]["usage"] == FULL and result["data"]["usage_complete"] is False
    assert "private-" not in response.text
    assert active.close_count == 1
    assert harness.ledger.records[0].finishes == [("failed", {"reason": "upstream_error"})]


async def test_later_quota_rejection_does_not_create_unknown_round_or_refund_prior_request(harness):
    remote(harness, completion(tool=True))
    harness.ledger.reject_index = 2
    result = events(await request(harness, "agent"))[-1]
    assert result["event"] == "error" and result["data"]["code"] == 429
    assert result["data"]["retry_after"] == 17
    assert result["data"]["usage"] == FULL and result["data"]["usage_complete"] is True
    assert len(harness.calls) == len(harness.ledger.records) == 1


async def test_single_request_quota_rejection_is_http_before_any_provider(harness):
    harness.ledger.reject_index = 1
    response = await request(harness)
    assert response.status_code == 429 and response.headers["retry-after"] == "17"
    assert not harness.calls and not harness.ledger.records


@pytest.mark.parametrize("workflow", ["retrieval", "agent"])
@pytest.mark.parametrize("ending", ["cancel", "generator-close"])
@pytest.mark.parametrize("close_failure", [False, True])
async def test_interruptions_close_upstream_once_keep_known_counts_and_never_yield_terminal(
    harness, workflow, ending, close_failure
):
    active = remote(harness, completion(done=False), pause=True, close_failure=close_failure)
    harness.ledger.fail_cancel = close_failure
    if ending == "cancel":
        task = asyncio.create_task(request(harness, workflow))
        await asyncio.wait_for(active.waiting.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    else:
        response = await api.run(
            api.RunInput(prompt="MCP", provider="deepseek", workflow=workflow),
            SimpleNamespace(is_disconnected=lambda: asyncio.sleep(0, result=False)),
            "test-access",
        )
        stream = response.body_iterator
        seen = []
        while True:
            item = await anext(stream)
            seen.append(item)
            if item.startswith("event: usage"):
                break
        await stream.aclose()
        assert not any(item.startswith(("event: done", "event: error")) for item in seen)
    record = harness.ledger.records[0]
    assert record.counts == FULL and record.finishes == [("cancelled", {"reason": "cancelled"})]
    assert record.finish_closed.is_set() and active.close_count == 1
    assert len(harness.calls) == 1


async def test_cancel_finish_is_bounded_and_no_background_write_survives(harness, monkeypatch):
    monkeypatch.setattr(ModelRequest, "CANCEL_FINISH_TIMEOUT", 0.02)
    remote_stream = remote(harness, completion(done=False), pause=True)
    harness.ledger.block_cancel = True
    task = asyncio.create_task(request(harness))
    await asyncio.wait_for(remote_stream.waiting.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    record = harness.ledger.records[0]
    assert record.finish_closed.is_set() and record.status == "admitted"
    assert record.finishes == [("cancelled", {"reason": "cancelled"})]
    assert remote_stream.close_count == 1


async def test_agent_deadline_is_failed_timeout_not_user_cancel(harness, monkeypatch):
    monkeypatch.setattr(agent_loop, "TOTAL_TIMEOUT", 0.02)
    remote(harness, completion(done=False), pause=True)
    result = events(await request(harness, "agent"))[-1]
    assert result["event"] == "error" and result["data"]["code"] == 504
    assert harness.ledger.records[0].finishes == [("failed", {"reason": "timeout"})]


@pytest.mark.parametrize("workflow", ["retrieval", "agent"])
async def test_requested_model_is_frozen_for_admission_and_upstream(harness, monkeypatch, workflow):
    remote(harness, completion())
    harness.ledger.mutate_model = lambda: monkeypatch.setenv(
        "DEEPSEEK_MODEL", "changed-after-admit"
    )
    await request(harness, workflow)
    assert harness.ledger.calls[0][3] == harness.calls[0]["model"] == "requested-model"


@pytest.mark.parametrize("workflow", ["retrieval", "agent"])
async def test_demo_and_input_rejections_never_create_model_records(harness, monkeypatch, workflow):
    assert (await request(harness, workflow, provider="demo")).status_code == 200
    assert (await request(harness, workflow, prompt=" ")).status_code == 422
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "different-access")
    assert (await request(harness, workflow)).status_code == 401
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert (await request(harness, workflow)).status_code == 503
    assert not harness.ledger.calls and not harness.calls


@pytest.mark.parametrize("model", ["", " ", "a" * 201, "model\nname", "model\x7fname", "\ud800"])
async def test_invalid_model_configuration_rejects_before_admission(harness, monkeypatch, model):
    original = providers.os.getenv
    monkeypatch.setattr(
        providers.os,
        "getenv",
        lambda key, default=None: model if key == "DEEPSEEK_MODEL" else original(key, default),
    )
    assert (await request(harness)).status_code == 503
    assert not harness.ledger.calls and not harness.calls


async def test_disconnect_detected_after_usage_frame_preserves_the_already_parsed_counts(harness):
    active = remote(harness, completion(done=False), pause=True)
    checks = []

    async def disconnected():
        checks.append(True)
        return len(checks) == 2  # delta is sent, then a usage frame arrives with disconnect.

    response = await api.run(
        api.RunInput(prompt="MCP", provider="deepseek"),
        SimpleNamespace(is_disconnected=disconnected),
        "test-access",
    )
    seen = [item async for item in response.body_iterator]
    assert not any(
        item.startswith(("event: usage", "event: done", "event: error")) for item in seen
    )
    record = harness.ledger.records[0]
    assert record.counts == FULL and record.status == "cancelled"
    assert record.finishes == [("cancelled", {"reason": "cancelled"})]
    assert active.close_count == 1


async def test_closing_initial_sse_before_provider_marks_admitted_attempt_cancelled(harness):
    response = await api.run(
        api.RunInput(prompt="MCP", provider="deepseek"),
        SimpleNamespace(is_disconnected=lambda: asyncio.sleep(0, result=False)),
        "test-access",
    )
    stream = response.body_iterator
    assert (await anext(stream)).startswith("event: start")
    await stream.aclose()
    assert not harness.calls and not harness.clients
    record = harness.ledger.records[0]
    assert record.counts == {} and record.status == "cancelled"
    assert record.finishes == [("cancelled", {"reason": "cancelled"})]


@pytest.mark.parametrize("workflow", ["retrieval", "agent"])
@pytest.mark.parametrize("phase", ["snapshot", "completed"])
async def test_cancellation_during_ledger_io_preserves_counts_and_does_not_start_a_new_round(
    harness, workflow, phase
):
    active = remote(harness, completion(tool=workflow == "agent"))
    harness.ledger.block_snapshot = phase == "snapshot"
    harness.ledger.block_completed = phase == "completed"
    task = asyncio.create_task(request(harness, workflow))
    await asyncio.wait_for(harness.ledger.admitted.wait(), 1)
    record = harness.ledger.records[0]
    pending = record.snapshot_waiting if phase == "snapshot" else record.complete_waiting
    await asyncio.wait_for(pending.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert active.close_count == 1 and len(harness.calls) == 1
    assert record.counts == FULL
    if phase == "snapshot":
        assert record.snapshot_closed.is_set() and record.status == "cancelled"
        assert record.finishes == [("cancelled", {"reason": "cancelled"})]
    else:
        assert record.finish_closed.is_set() and record.status == "admitted"
        # The interrupted terminal commit is unknown: never overwrite or retry it.
        assert record.finishes == [("completed", {"usage_complete": True, "truncated": False})]


@pytest.mark.parametrize("workflow", ["retrieval", "agent"])
async def test_actual_asgi_disconnect_allows_awaiting_provider_cleanup(harness, workflow):
    class AwaitingClose(Remote):
        async def aclose(self):
            await asyncio.sleep(0)
            await super().aclose()

    active = AwaitingClose(completion(done=False), harness.events, pause=True)
    harness.remotes.append(active)
    body = json.dumps({"provider": "deepseek", "prompt": "MCP", "workflow": workflow}).encode()
    delivered = False
    sent = []

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await active.waiting.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/playground/run",
        "raw_path": b"/api/playground/run",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            (b"x-playground-token", b"test-access"),
        ],
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 80),
    }
    await asyncio.wait_for(api.app(scope, receive, send), 2)
    assert active.closed.is_set() and active.close_count == 1
    record = harness.ledger.records[0]
    assert record.counts == FULL and record.status == "cancelled"
    assert record.finish_closed.is_set()
    assert not any(b"event: done" in message.get("body", b"") for message in sent)
