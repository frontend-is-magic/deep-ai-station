import asyncio
import json
import socket
from contextlib import asynccontextmanager, nullcontext
from uuid import UUID

import httpx
import pytest
import uvicorn

import streaming
from app import create_app
from streaming import FixtureProducer, load_fixture

FIXTURE = load_fixture()
TEXTS = FIXTURE["texts"]


def events(body):
    result = []
    for block in body.split("\n\n"):
        if block:
            name, data = block.split("\n")
            result.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return result


def assert_identity(frames):
    run_id = frames[0][1]["run_id"]
    parsed = UUID(run_id)
    assert parsed.version == 4 and str(parsed) == run_id
    assert {data["run_id"] for _event, data in frames} == {run_id}
    return run_id


class ObservedProducer(FixtureProducer):
    def __init__(self, scenario, *, block_success=False, close_error=False):
        super().__init__(scenario, TEXTS, 0)
        self.waiting = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cleaned = asyncio.Event()
        self.release = asyncio.Event()
        self.close_count = 0
        self.block_success = block_success
        self.close_error = close_error

    async def next(self):
        try:
            if self.index == 1:
                self.waiting.set()
                if self.block_success and self.scenario == "success":
                    await self.release.wait()
            return await super().next()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def aclose(self):
        self.close_count += 1
        # A real suspension proves cleanup is awaited, not merely scheduled.
        await asyncio.sleep(0)
        await super().aclose()
        self.cleaned.set()
        if self.close_error:
            raise RuntimeError("private-cleanup-diagnostic")


class Factory:
    def __init__(self, **options):
        self.options = options
        self.created = []
        self.started = asyncio.Queue()

    def __call__(self, scenario):
        producer = ObservedProducer(scenario, **self.options)
        self.created.append(producer)
        self.started.put_nowait(producer)
        return producer


class ManualDeadline:
    """Use asyncio's real cancellation machinery, but trigger at an observed boundary."""

    def __init__(self):
        self.calls = []
        self.context = None

    def __call__(self, seconds):
        self.calls.append(seconds)
        self.context = asyncio.timeout(None)
        return self.context

    def expire(self):
        self.context.reschedule(asyncio.get_running_loop().time() - 1)


@asynccontextmanager
async def client_for(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.parametrize("scenario", ["success", "error"])
async def test_fixtures_and_terminal_contract(scenario):
    factory = Factory()
    async with client_for(create_app(producer_factory=factory)) as client:
        response = await client.get("/stream", params={"scenario": scenario})
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == "text/event-stream"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-accel-buffering"] == "no"
    assert "content-length" not in response.headers
    assert "set-cookie" not in response.headers
    assert "access-control-allow-origin" not in response.headers
    frames = events(response.text)
    run_id = assert_identity(frames)
    assert frames[0] == ("start", {"run_id": run_id, "scenario": scenario})
    count = 3 if scenario == "success" else 1
    assert frames[1 : count + 1] == [
        ("delta", {"run_id": run_id, "seq": index, "text": text})
        for index, text in enumerate(TEXTS[:count], 1)
    ]
    if scenario == "success":
        assert frames[-1] == ("done", {"run_id": run_id, "seq": 3})
        assert "🌱" in response.text and "usage" not in response.text
    else:
        assert frames[-1] == (
            "error",
            {"run_id": run_id, "code": "producer_failed", "message": "教学数据源失败"},
        )
        assert "fixed teaching producer failure" not in response.text
    assert len(frames) == count + 2
    assert factory.created[0].close_count == 1
    assert factory.created[0].cleaned.is_set()


@pytest.mark.parametrize("scenario", ["timeout", "hold"])
async def test_total_deadline_closes_blocked_producer_without_done(scenario):
    factory, deadline = Factory(), ManualDeadline()
    app = create_app(producer_factory=factory, timeout_factory=deadline, deadline_seconds=5)
    async with client_for(app) as client:
        task = asyncio.create_task(client.get("/stream", params={"scenario": scenario}))
        try:
            producer = await asyncio.wait_for(factory.started.get(), 2)
            await asyncio.wait_for(producer.waiting.wait(), 2)
            deadline.expire()
            response = await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    frames = events(response.text)
    assert [name for name, _data in frames] == ["start", "delta", "error"]
    run_id = assert_identity(frames)
    assert frames[-1][1] == {
        "run_id": run_id,
        "code": "deadline_exceeded",
        "message": "教学运行超时",
    }
    assert deadline.calls == [5]
    assert producer.cancelled.is_set() and producer.cleaned.is_set()
    assert producer.close_count == 1


async def test_one_deadline_remains_in_force_after_another_fragment():
    factory, deadline = Factory(block_success=True), ManualDeadline()
    app = create_app(producer_factory=factory, timeout_factory=deadline, deadline_seconds=5)
    messages = []
    third_fragment = asyncio.Event()
    hold_send = asyncio.Event()

    async def send(message):
        messages.append(message)
        if b'"seq": 3' in message.get("body", b""):
            third_fragment.set()
            await hold_send.wait()

    task = asyncio.create_task(app(scope("scenario=success"), asyncio.Queue().get, send))
    try:
        producer = await asyncio.wait_for(factory.started.get(), 2)
        await asyncio.wait_for(producer.waiting.wait(), 2)
        original_deadline = deadline.context
        producer.release.set()
        await asyncio.wait_for(third_fragment.wait(), 2)
        assert deadline.context is original_deadline and deadline.calls == [5]
        deadline.expire()
        await asyncio.wait_for(task, 2)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    frames = events(b"".join(message.get("body", b"") for message in messages).decode())
    assert [name for name, _data in frames] == ["start", "delta", "delta", "delta", "error"]
    assert frames[-1][1]["code"] == "deadline_exceeded"
    assert producer.close_count == 1


@pytest.mark.parametrize(
    "query",
    [
        "",
        "scenario",
        "scenario=",
        "scenario=unknown",
        "scenario=SUCCESS",
        "scenario=success%20",
        "scenario=success&scenario=success",
        "scenario=success&scenario=error",
        "other=success",
        "scenario=success&other=",
        "scenario=success&other=value",
        "scenario=success%00",
        "scenario=success%0A",
        "scenario[]=success",
        "scenario=timeout&scenario=hold",
    ],
)
async def test_invalid_query_never_creates_producer(query):
    factory = Factory()
    async with client_for(create_app(producer_factory=factory)) as client:
        response = await client.get("/stream?" + query)
    assert response.status_code == 400
    assert response.json() == {
        "error": {"code": "invalid_request", "message": "仅支持一个有效的 scenario 参数"}
    }
    assert factory.created == []


async def test_health_and_unique_request_identity():
    async with client_for(create_app(step_seconds=0)) as client:
        assert (await client.get("/health")).json() == {"ok": True, "lab": "sse-stream"}
        responses = await asyncio.gather(
            *(client.get("/stream?scenario=success") for _ in range(3))
        )
    identities = [assert_identity(events(response.text)) for response in responses]
    assert len(set(identities)) == 3


@pytest.mark.parametrize("failure", ["factory", "next", "close"])
async def test_internal_failures_are_sanitized_and_never_done(failure, caplog):
    factory = Factory(close_error=failure == "close")
    original_factory = factory

    if failure == "factory":

        def factory(_scenario):
            raise RuntimeError("private-factory-diagnostic")
    elif failure == "next":

        class Broken(ObservedProducer):
            async def next(self):
                raise RuntimeError("private-next-diagnostic")

        factory = lambda scenario: Broken(scenario)  # noqa: E731
    async with client_for(create_app(producer_factory=factory)) as client:
        response = await client.get("/stream?scenario=success")
    frames = events(response.text)
    assert frames[-1][0] == "error" and frames[-1][1]["code"] == "producer_failed"
    assert all(name != "done" for name, _data in frames)
    assert "private-" not in response.text + caplog.text
    if failure == "close":
        assert original_factory.created[0].close_count == 1


def scope(query, version="2.4"):
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": version},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/stream",
        "raw_path": b"/stream",
        "query_string": query.encode(),
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8024),
    }


@pytest.mark.parametrize("version", ["2.3", "2.4"])
async def test_disconnect_cancels_wait_without_forced_terminal(version):
    factory, inbox, messages = Factory(), asyncio.Queue(), []

    async def send(message):
        messages.append(message)

    task = asyncio.create_task(
        create_app(producer_factory=factory)(scope("scenario=hold", version), inbox.get, send)
    )
    try:
        producer = await asyncio.wait_for(factory.started.get(), 2)
        await asyncio.wait_for(producer.waiting.wait(), 2)
        inbox.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(task, 2)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    frames = events(b"".join(message.get("body", b"") for message in messages).decode())
    assert [name for name, _data in frames] == ["start", "delta"]
    assert producer.cancelled.is_set() and producer.cleaned.is_set()
    assert producer.close_count == 1


@pytest.mark.parametrize("close_error", [False, True])
async def test_task_cancellation_preserves_original_interrupt_and_cleanup(close_error, caplog):
    factory, messages = Factory(close_error=close_error), []

    async def send(message):
        messages.append(message)

    task = asyncio.create_task(
        create_app(producer_factory=factory)(scope("scenario=hold"), asyncio.Queue().get, send)
    )
    try:
        producer = await asyncio.wait_for(factory.started.get(), 2)
        await asyncio.wait_for(producer.waiting.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert producer.close_count == 1 and producer.cleaned.is_set()
    assert [
        name
        for name, _data in events(
            b"".join(message.get("body", b"") for message in messages).decode()
        )
    ] == ["start", "delta"]
    assert "private-cleanup-diagnostic" not in caplog.text


async def test_send_failure_still_closes_producer():
    factory = Factory()

    async def send(message):
        if b"event: delta" in message.get("body", b""):
            raise OSError("private-send-diagnostic")

    await create_app(producer_factory=factory)(scope("scenario=success"), asyncio.Queue().get, send)
    assert factory.created[0].close_count == 1 and factory.created[0].cleaned.is_set()


class ReadyServer(uvicorn.Server):
    def __init__(self, app):
        super().__init__(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=0,
                access_log=False,
                log_config=None,
                timeout_graceful_shutdown=2,
            )
        )
        self.ready = asyncio.Event()

    def capture_signals(self):
        return nullcontext()

    async def startup(self, sockets=None):
        await super().startup(sockets)
        self.ready.set()


@asynccontextmanager
async def live_server(app):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        address = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = ReadyServer(app)
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            await asyncio.wait_for(server.ready.wait(), 2)
            yield address
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(task, 4)
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert not server.server_state.connections
        assert not server.server_state.tasks


async def read_first_delta(response):
    lines = response.aiter_lines()
    chunks = []
    async with asyncio.timeout(2):
        async for line in lines:
            chunks.append(line)
            if line == "" and len(chunks) > 3:
                break
    frames = events("\n".join(chunks) + "\n")
    assert [name for name, _data in frames] == ["start", "delta"]
    assert frames[1][1]["text"] == TEXTS[0]
    return frames[0][1]["run_id"]


async def test_real_http_disconnect_cancels_and_cleans_once_before_server_shutdown():
    factory = Factory()
    async with live_server(create_app(producer_factory=factory, deadline_seconds=30)) as base:
        async with httpx.AsyncClient(base_url=base, trust_env=False) as client:
            async with client.stream("GET", "/stream?scenario=hold") as response:
                await read_first_delta(response)
                producer = await asyncio.wait_for(factory.started.get(), 2)
                await asyncio.wait_for(producer.waiting.wait(), 2)
                assert not producer.cleaned.is_set()
                await response.aclose()
                await asyncio.wait_for(producer.cancelled.wait(), 2)
                await asyncio.wait_for(producer.cleaned.wait(), 2)
                assert producer.close_count == 1 and producer.index == 1
            assert (await client.get("/health")).status_code == 200
            assert producer.close_count == 1


async def test_real_http_cancel_one_request_does_not_cancel_other_request():
    factory = Factory(block_success=True)
    async with live_server(create_app(producer_factory=factory, deadline_seconds=30)) as base:
        async with httpx.AsyncClient(base_url=base, trust_env=False) as client:
            async with client.stream("GET", "/stream?scenario=hold") as response_a:
                run_a = await read_first_delta(response_a)
                producer_a = await asyncio.wait_for(factory.started.get(), 2)
                await asyncio.wait_for(producer_a.waiting.wait(), 2)
                request_b = asyncio.create_task(client.get("/stream?scenario=success"))
                try:
                    producer_b = await asyncio.wait_for(factory.started.get(), 2)
                    await asyncio.wait_for(producer_b.waiting.wait(), 2)
                    await response_a.aclose()
                    await asyncio.wait_for(producer_a.cleaned.wait(), 2)
                    assert producer_a.cancelled.is_set() and producer_a.close_count == 1
                    assert not producer_b.cancelled.is_set() and not producer_b.cleaned.is_set()
                    producer_b.release.set()
                    response_b = await asyncio.wait_for(request_b, 2)
                finally:
                    if not request_b.done():
                        request_b.cancel()
                    await asyncio.gather(request_b, return_exceptions=True)
            frames_b = events(response_b.text)
            assert assert_identity(frames_b) != run_a
            assert [name for name, _data in frames_b] == [
                "start",
                "delta",
                "delta",
                "delta",
                "done",
            ]
            assert producer_b.close_count == 1 and producer_b.cleaned.is_set()
            assert not producer_b.cancelled.is_set()
            assert producer_a.close_count == 1


def test_packaged_fixture_takes_precedence_over_repository_shared_fixture(tmp_path, monkeypatch):
    package = tmp_path / "python"
    package.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "fixtures.json").write_text(json.dumps(FIXTURE))
    monkeypatch.setattr(streaming, "__file__", str(package / "streaming.py"))
    assert load_fixture() == FIXTURE
    (package / "fixtures.json").write_text(json.dumps({**FIXTURE, "step_ms": 17}))
    assert load_fixture()["step_ms"] == 17


@pytest.mark.parametrize("scenario", ["timeout", "hold"])
async def test_default_timeout_factory_enforces_configured_deadline(scenario):
    factory = Factory()
    async with client_for(create_app(producer_factory=factory, deadline_seconds=0.01)) as client:
        response = await asyncio.wait_for(client.get("/stream", params={"scenario": scenario}), 2)
    frames = events(response.text)
    assert [name for name, _data in frames] == ["start", "delta", "error"]
    assert frames[-1][1]["code"] == "deadline_exceeded"
    assert factory.created[0].cancelled.is_set()
    assert factory.created[0].close_count == 1


async def test_producer_timeout_exception_is_not_confused_with_our_deadline():
    class Broken(ObservedProducer):
        async def next(self):
            raise TimeoutError("private-producer-timeout")

    producer = Broken("success")
    async with client_for(create_app(producer_factory=lambda _scenario: producer)) as client:
        response = await client.get("/stream?scenario=success")
    frames = events(response.text)
    assert [name for name, _data in frames] == ["start", "error"]
    assert frames[-1][1]["code"] == "producer_failed"
    assert producer.close_count == 1
    assert "private-producer-timeout" not in response.text


async def test_disconnect_during_async_cleanup_finishes_it_once_without_done():
    class Closing(ObservedProducer):
        def __init__(self):
            super().__init__("success")
            self.closing = asyncio.Event()
            self.release_close = asyncio.Event()

        async def aclose(self):
            self.close_count += 1
            self.closing.set()
            await self.release_close.wait()
            self.closed = True
            self.cleaned.set()

    producer = Closing()
    response = streaming.StreamResponse("success", lambda _scenario: producer, 30)
    inbox, messages = asyncio.Queue(), []

    async def send(message):
        messages.append(message)

    task = asyncio.create_task(response(scope("scenario=success"), inbox.get, send))
    try:
        await asyncio.wait_for(producer.closing.wait(), 2)
        inbox.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(response.disconnected.wait(), 2)
        producer.release_close.set()
        await asyncio.wait_for(task, 2)
    finally:
        producer.release_close.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert producer.close_count == 1 and producer.cleaned.is_set()
    frames = events(b"".join(message.get("body", b"") for message in messages).decode())
    assert [name for name, _data in frames] == ["start", "delta", "delta", "delta"]


async def test_cleanup_cancellation_is_deferred_until_owned_resource_is_closed():
    entered, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    class Resource:
        async def aclose(self):
            calls.append("close")
            entered.set()
            await release.wait()
            cleaned.set()

    task = asyncio.create_task(streaming.close_producer(Resource()))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        # cancel() immediately cancels an unshielded current wait. No scheduling guess is needed.
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert cleaned.is_set() and calls == ["close"]
