"""Per-run known usage snapshots; no persistence, prices, or quota accounting."""

from dataclasses import dataclass, field

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
