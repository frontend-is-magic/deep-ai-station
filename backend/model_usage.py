"""Per-request usage snapshots. No prompts, generated output, or credentials are stored."""

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID

from fastapi import HTTPException

from backend.usage import usage_counts

UNAVAILABLE = "模型请求账本暂不可用，请稍后重试"
FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")
STATUSES = ("admitted", "completed", "failed", "cancelled")
REASONS = {"completed", "upstream_error", "timeout", "cancelled", "ledger_error"}
MAX_SEQUENCE = 2**63 - 1
Status = Literal["completed", "failed", "cancelled"]
WriteResult = Literal["applied", "unchanged", "stale"]


def unavailable() -> HTTPException:
    return HTTPException(503, UNAVAILABLE)


@dataclass(frozen=True)
class ModelAttemptIdentity:
    run_id: str
    request_index: int
    provider: str
    model: str

    def __post_init__(self):
        try:
            value = UUID(self.run_id) if type(self.run_id) is str else None
            if value is None or value.version != 4:
                raise ValueError
            if type(self.request_index) is not int or not 1 <= self.request_index <= 3:
                raise ValueError
            if self.provider != "deepseek":
                raise ValueError
            if (
                type(self.model) is not str
                or not 1 <= len(self.model) <= 200
                or not self.model.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in self.model)
            ):
                raise ValueError
            self.model.encode("utf-8")
            object.__setattr__(self, "run_id", str(value))
        except (ValueError, TypeError, AttributeError, UnicodeError):
            raise unavailable() from None


@dataclass(frozen=True)
class Snapshot:
    sequence: int
    counts: tuple[int | None, int | None, int | None]
    status: str = "admitted"
    usage_complete: bool = False
    truncated: bool | None = None
    reason: str | None = None

    @classmethod
    def create(
        cls,
        snapshot_seq,
        counts,
        status="admitted",
        usage_complete=False,
        truncated=None,
        reason=None,
    ):
        if (
            type(snapshot_seq) is not int
            or not 1 <= snapshot_seq <= MAX_SEQUENCE
            or type(counts) is not dict
            or any(key not in FIELDS for key in counts)
            or any(
                type(value) is not int or not 0 <= value <= 100_000_000 for value in counts.values()
            )
            or type(status) is not str
            or status not in STATUSES
            or type(usage_complete) is not bool
            or (truncated is not None and type(truncated) is not bool)
            or (reason is not None and (type(reason) is not str or reason not in REASONS))
        ):
            raise unavailable()
        if status == "admitted":
            if usage_complete or truncated is not None or reason is not None:
                raise unavailable()
        elif reason is None:
            raise unavailable()
        if usage_complete and (status != "completed" or len(counts) != 3):
            raise unavailable()
        return cls(
            snapshot_seq,
            tuple(counts.get(field) for field in FIELDS),
            status,
            usage_complete,
            truncated,
            reason,
        )

    def payload(self):
        return self.counts, self.status, self.usage_complete, self.truncated, self.reason


def transition(current: Snapshot, incoming: Snapshot) -> WriteResult:
    # Terminals are immutable even when a delayed writer carries a higher sequence.
    if current.status != "admitted":
        if incoming.status == "admitted":
            return "stale"
        if current.payload() == incoming.payload():
            return "unchanged"
        raise unavailable()
    if incoming.sequence < current.sequence:
        return "stale"
    if incoming.sequence == current.sequence:
        if incoming.payload() == current.payload():
            return "unchanged"
        raise unavailable()
    return "applied"


@dataclass(frozen=True)
class MemoryRecord:
    identity: ModelAttemptIdentity
    snapshot: Snapshot
    admitted_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None

    def update(self, snapshot: Snapshot, now: datetime):
        timestamp = max(self.updated_at, now)
        return replace(
            self,
            snapshot=snapshot,
            updated_at=timestamp,
            finished_at=timestamp if snapshot.status != "admitted" else None,
        )


def report_result(scope: str, day: date, mode: str, row) -> dict:
    return {
        "scope": scope,
        "date": day.isoformat(),
        "mode": mode,
        "persistent": mode == "postgres",
        "attempts": row[0],
        "statuses": dict(zip(STATUSES, row[1:5], strict=True)),
        "known_usage": {
            field: None if value is None else int(value)
            for field, value in zip(FIELDS, row[5:8], strict=True)
        },
        "unknown_field_requests": dict(zip(FIELDS, row[8:11], strict=True)),
        "complete_requests": row[11],
        "incomplete_requests": row[0] - row[11],
    }


def day_bounds(day: date):
    if type(day) is not date or day == date.max:
        raise unavailable()
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def memory_report(scope: str, day: date, records) -> dict:
    start, end = day_bounds(day)
    snapshots = [record.snapshot for record in records if start <= record.admitted_at < end]
    known = []
    for index in range(3):
        values = [item.counts[index] for item in snapshots if item.counts[index] is not None]
        known.append(sum(values) if values else None)
    row = (
        len(snapshots),
        *(sum(item.status == status for item in snapshots) for status in STATUSES),
        *known,
        *(sum(item.counts[index] is None for item in snapshots) for index in range(3)),
        sum(item.usage_complete for item in snapshots),
    )
    return report_result(scope, day, "memory", row)


async def insert_postgres(connection, scope: str, identity: ModelAttemptIdentity):
    cursor = await connection.execute(
        """INSERT INTO public.ai_model_usage_v1
           (scope, run_id, request_index, provider, model)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (scope, run_id, request_index) DO NOTHING RETURNING run_id""",
        (scope, identity.run_id, identity.request_index, identity.provider, identity.model),
    )
    if await cursor.fetchone() is None:
        raise unavailable()


async def write_postgres(
    connection, scope: str, identity: ModelAttemptIdentity, incoming: Snapshot
) -> WriteResult:
    key = (scope, identity.run_id, identity.request_index)
    cursor = await connection.execute(
        """SELECT provider, model, snapshot_seq, prompt_tokens, completion_tokens, total_tokens,
                  status, usage_complete, truncated, reason
           FROM public.ai_model_usage_v1
           WHERE scope = %s AND run_id = %s AND request_index = %s FOR UPDATE""",
        key,
    )
    row = await cursor.fetchone()
    if row is None or (row[0], row[1]) != (identity.provider, identity.model):
        raise unavailable()
    current = Snapshot(row[2], tuple(row[3:6]), *row[6:10])
    result = transition(current, incoming)
    if result == "applied":
        await connection.execute(
            """UPDATE public.ai_model_usage_v1
               SET snapshot_seq = %s, prompt_tokens = %s, completion_tokens = %s,
                   total_tokens = %s, status = %s, usage_complete = %s,
                   truncated = %s, reason = %s,
                   updated_at = GREATEST(updated_at, clock_timestamp()),
                   finished_at = CASE WHEN %s = 'admitted' THEN NULL
                       ELSE GREATEST(updated_at, clock_timestamp()) END
               WHERE scope = %s AND run_id = %s AND request_index = %s""",
            (
                incoming.sequence,
                *incoming.counts,
                incoming.status,
                incoming.usage_complete,
                incoming.truncated,
                incoming.reason,
                incoming.status,
                *key,
            ),
        )
    return result


async def report_postgres(connection, scope: str, day: date) -> dict:
    start, end = day_bounds(day)
    cursor = await connection.execute(
        """SELECT COUNT(*),
                  COUNT(*) FILTER (WHERE status = 'admitted'),
                  COUNT(*) FILTER (WHERE status = 'completed'),
                  COUNT(*) FILTER (WHERE status = 'failed'),
                  COUNT(*) FILTER (WHERE status = 'cancelled'),
                  SUM(prompt_tokens), SUM(completion_tokens), SUM(total_tokens),
                  COUNT(*) FILTER (WHERE prompt_tokens IS NULL),
                  COUNT(*) FILTER (WHERE completion_tokens IS NULL),
                  COUNT(*) FILTER (WHERE total_tokens IS NULL),
                  COUNT(*) FILTER (WHERE usage_complete)
           FROM public.ai_model_usage_v1
           WHERE scope = %s AND admitted_at >= %s AND admitted_at < %s""",
        (scope, start, end),
    )
    row = await cursor.fetchone()
    if row is None:
        raise unavailable()
    return report_result(scope, day, "postgres", row)


class ModelAttempt:
    """Serializes local writes; uncertain terminal commits are never retried."""

    def __init__(self, store, identity: ModelAttemptIdentity):
        self.identity = identity
        self._store = store
        self._sequence = 0
        self._counts: dict[str, int] = {}
        self._confirmed_counts: dict[str, int] | None = None
        self._terminal: Snapshot | None = None
        self._terminal_confirmed = False
        self._lock = asyncio.Lock()

    async def observe(self, counts: object) -> None:
        normalized = usage_counts(counts)
        if not normalized:
            return
        async with self._lock:
            if self._terminal is not None:
                return
            self._counts = normalized.copy()
            if self._confirmed_counts == normalized:
                return
            self._sequence += 1
            # A failed write may have committed. Do not reuse an older acknowledgement.
            self._confirmed_counts = None
            await self._store.write_model_usage(
                self.identity, snapshot_seq=self._sequence, counts=normalized.copy()
            )
            self._confirmed_counts = normalized.copy()

    async def finish(
        self,
        status: Status,
        usage_complete: bool = False,
        truncated: bool | None = None,
        reason: str | None = None,
    ) -> None:
        async with self._lock:
            if type(status) is not str or status not in STATUSES:
                raise unavailable()
            if reason is None:
                reason = {
                    "completed": "completed",
                    "failed": "upstream_error",
                    "cancelled": "cancelled",
                }.get(status)
            incoming = Snapshot.create(
                self._sequence + 1, self._counts, status, usage_complete, truncated, reason
            )
            if status == "admitted":
                raise unavailable()
            if self._terminal is not None:
                if self._terminal_confirmed and self._terminal.payload() == incoming.payload():
                    return
                raise unavailable()
            # Set before awaiting: unknown commit must not trigger another terminal write.
            self._terminal = incoming
            self._sequence = incoming.sequence
            await self._store.write_model_usage(
                self.identity,
                snapshot_seq=incoming.sequence,
                counts=self._counts.copy(),
                status=status,
                usage_complete=usage_complete,
                truncated=truncated,
                reason=reason,
            )
            self._terminal_confirmed = True


async def begin_model_attempt(
    run_id: str, request_index: int, provider: str, model: str
) -> ModelAttempt:
    # Lazy import avoids a cycle: quota owns the shared cached storage instance.
    from backend.quota import _store, configuration

    identity = ModelAttemptIdentity(run_id, request_index, provider, model)
    try:
        store = _store(configuration())
        await store.begin_model_attempt(identity)
    except HTTPException as exc:
        if exc.status_code == 429:
            raise
        raise unavailable() from None
    return ModelAttempt(store, identity)
