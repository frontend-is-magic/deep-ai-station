"""Per-run usage snapshots and bounded per-request ledger lifecycle coordination."""

import asyncio
import sys
from dataclasses import dataclass, field

import anyio
from fastapi import HTTPException

USAGE_FIELDS = frozenset({"prompt_tokens", "completion_tokens", "total_tokens"})


def usage_counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: count
        for key, count in value.items()
        if key in USAGE_FIELDS and type(count) is int and 0 <= count <= 100_000_000
    }


@dataclass
class RequestUsage:
    counts: dict[str, int] = field(default_factory=dict)
    finished: bool = False
    complete: bool = False


class UsageTracker:
    """Replace each request's latest snapshot, then sum only known fields."""

    def __init__(self):
        self._requests: list[RequestUsage] = []
        self._cleanup_failed = False

    def begin(self) -> int:
        self._requests.append(RequestUsage())
        return len(self._requests) - 1

    def observe(self, request: int, value: object):
        counts = usage_counts(value)
        if counts:
            self._requests[request].counts = counts

    def finish(self, request: int, result: dict):
        self.observe(request, result.get("usage"))
        record = self._requests[request]
        record.finished = True
        record.complete = result.get("usage_complete", True) is True

    def cleanup_failed(self):
        self._cleanup_failed = True

    def snapshot(self, *, terminal: bool = False) -> dict:
        counts: dict[str, int] = {}
        for request in self._requests:
            for key, value in request.counts.items():
                counts[key] = counts.get(key, 0) + value
        complete = (
            terminal
            and not self._cleanup_failed
            and bool(self._requests)
            and all(
                request.finished and request.complete and USAGE_FIELDS <= request.counts.keys()
                for request in self._requests
            )
        )
        return {"usage": counts or None, "usage_complete": complete}


class ModelRequest:
    """Observe memory first; persist a single request, never a run's summed counters."""

    CANCEL_FINISH_TIMEOUT = 1.0

    def __init__(self, tracker: UsageTracker, attempt=None, *, deadline=None):
        self.tracker = tracker
        self.attempt = attempt
        self.deadline = deadline
        self.request = None
        self.finish_started = False
        self.ledger_failed = False

    async def __aenter__(self):
        self.request = self.tracker.begin()
        return self

    async def observe(self, counts):
        self.tracker.observe(self.request, counts)
        if self.attempt is not None:
            try:
                await self.attempt.observe(dict(self.tracker._requests[self.request].counts))
            except Exception:
                self.ledger_failed = True
                raise

    async def complete(self, result):
        record = self.tracker._requests[self.request]
        complete = (
            result.get("usage_complete", True) is True and USAGE_FIELDS <= record.counts.keys()
        )
        self.finish_started = True
        if self.attempt is not None:
            await self.attempt.finish(
                "completed", usage_complete=complete, truncated=result.get("truncated", False)
            )
        self.tracker.finish(self.request, result)

    async def cancel_if_open(self):
        if self.finish_started:
            return
        self.finish_started = True
        if self.attempt is None:
            return
        interrupted = sys.exception()
        timed_out = self.deadline is not None and self.deadline.expired()
        try:
            # Shield the enclosing ASGI cancel scope, not a detached task. The shorter
            # deadline interrupts the store and its finally closes the connection.
            with anyio.CancelScope(shield=True):
                async with asyncio.timeout(self.CANCEL_FINISH_TIMEOUT):
                    await self.attempt.finish(
                        "failed" if timed_out else "cancelled",
                        reason="timeout" if timed_out else "cancelled",
                    )
        except (asyncio.CancelledError, GeneratorExit):
            if interrupted is None:
                raise
        except Exception:
            # A cancelled request may retain an unconfirmed admitted row. Cleanup
            # cannot change the original interruption or invent a successful terminal.
            return

    async def __aexit__(self, _type, exc, _traceback):
        if isinstance(exc, (asyncio.CancelledError, GeneratorExit)) or exc is None:
            await self.cancel_if_open()
        elif not self.finish_started:
            self.finish_started = True
            if self.attempt is not None:
                reason = (
                    "ledger_error"
                    if self.ledger_failed
                    else "timeout"
                    if isinstance(exc, TimeoutError)
                    or isinstance(exc, HTTPException)
                    and exc.status_code == 504
                    else "upstream_error"
                )
                await self.attempt.finish("failed", reason=reason)
        return False
