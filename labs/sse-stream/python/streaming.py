"""Fixed teaching producers and one explicitly owned ASGI stream lifecycle."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from starlette.requests import ClientDisconnect
from starlette.responses import Response

SCENARIOS = frozenset({"success", "error", "timeout", "hold"})
TimeoutFactory = Callable[[float], asyncio.Timeout]


def load_fixture() -> dict:
    local = Path(__file__).with_name("fixtures.json")
    path = local if local.is_file() else Path(__file__).parent.parent / "shared" / "fixtures.json"
    return json.loads(path.read_text(encoding="utf-8"))


class Producer(Protocol):
    """Each request owns one producer; waits must propagate task cancellation."""

    async def next(self) -> str:
        """Return one fragment or raise StopAsyncIteration."""
        ...

    async def aclose(self) -> None: ...


ProducerFactory = Callable[[str], Producer]


class FixtureProducer:
    def __init__(self, scenario: str, texts: list[str], step_seconds: float):
        self.scenario = scenario
        self.texts = tuple(texts)
        self.step_seconds = step_seconds
        self.index = 0
        self.closed = False

    async def next(self) -> str:
        if self.closed or self.index == len(self.texts):
            raise StopAsyncIteration
        if self.index:
            if self.scenario == "error":
                raise RuntimeError("fixed teaching producer failure")
            if self.scenario in {"timeout", "hold"}:
                await asyncio.Event().wait()
            await asyncio.sleep(self.step_seconds)
        text = self.texts[self.index]
        self.index += 1
        return text

    async def aclose(self) -> None:
        self.closed = True


async def close_producer(producer: Producer) -> None:
    # A disconnect can arrive during async cleanup. Keep ownership until it finishes,
    # then propagate the original cancellation instead of leaving a detached cleanup task.
    cleanup = asyncio.create_task(producer.aclose())
    interrupted = None
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:
            interrupted = exc
        except Exception:
            break
    if interrupted is not None:
        if not cleanup.cancelled():
            cleanup.exception()
        raise interrupted
    cleanup.result()


def frame(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


class StreamResponse(Response):
    """Listen for disconnect even while the producer has no next bytes to send."""

    def __init__(
        self,
        scenario: str,
        producer_factory: ProducerFactory,
        deadline_seconds: float,
        timeout_factory: TimeoutFactory = asyncio.timeout,
    ):
        super().__init__(
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Accel-Buffering": "no",
            },
        )
        self.raw_headers = [
            (key, value) for key, value in self.raw_headers if key != b"content-length"
        ]
        self.scenario = scenario
        self.producer_factory = producer_factory
        self.deadline_seconds = deadline_seconds
        self.timeout_factory = timeout_factory
        self.run_id = str(uuid4())
        self.disconnected = asyncio.Event()

    async def emit(self, send, event: str, data: dict) -> None:
        if self.disconnected.is_set():
            raise ClientDisconnect
        try:
            await send(
                {
                    "type": "http.response.body",
                    "body": frame(event, {"run_id": self.run_id, **data}),
                    "more_body": True,
                }
            )
        except OSError:
            raise ClientDisconnect from None

    async def produce(self, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
        await self.emit(send, "start", {"scenario": self.scenario})
        producer = None
        seq = 0
        terminal = ("done", {})
        deadline = None
        try:
            producer = self.producer_factory(self.scenario)
            # One timeout surrounds the entire production loop, never one timeout per fragment.
            deadline = self.timeout_factory(self.deadline_seconds)
            async with deadline:
                while True:
                    try:
                        text = await producer.next()
                    except StopAsyncIteration:
                        break
                    seq += 1
                    await self.emit(send, "delta", {"seq": seq, "text": text})
            terminal = ("done", {"seq": seq})
        except ClientDisconnect:
            raise
        except TimeoutError:
            terminal = (
                ("error", {"code": "deadline_exceeded", "message": "教学运行超时"})
                if deadline is not None and deadline.expired()
                else ("error", {"code": "producer_failed", "message": "教学数据源失败"})
            )
        except Exception:
            terminal = ("error", {"code": "producer_failed", "message": "教学数据源失败"})
        finally:
            if producer is not None:
                try:
                    await close_producer(producer)
                except Exception:
                    # Preserve cancellation/GeneratorExit, but never report successful failed cleanup.
                    terminal = ("error", {"code": "producer_failed", "message": "教学数据源失败"})
        # CancelledError is intentionally not caught: a disconnected client gets no forced terminal.
        await self.emit(send, *terminal)
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def listen(self, receive) -> None:
        while True:
            if (await receive())["type"] == "http.disconnect":
                self.disconnected.set()
                return

    async def __call__(self, scope, receive, send) -> None:
        # ASGI 2.4 send failures alone cannot interrupt an upstream wait. Own both tasks explicitly.
        producer_task = asyncio.create_task(self.produce(send))
        disconnect_task = asyncio.create_task(self.listen(receive))
        tasks = (producer_task, disconnect_task)
        try:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if disconnect_task not in done:
                try:
                    await producer_task
                except (ClientDisconnect, OSError):
                    pass
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
