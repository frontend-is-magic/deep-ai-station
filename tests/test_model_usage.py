"""Storage invariants without a database; real PostgreSQL tests live separately."""

import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend import model_usage as ledger
from backend import quota

FULL = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
DAY = date(2026, 10, 4)
NOW = datetime(2026, 10, 4, 12, tzinfo=UTC).timestamp()


def identity(**changes):
    values = dict(run_id=str(uuid4()), request_index=1, provider="deepseek", model="deepseek-chat")
    values.update(changes)
    return ledger.ModelAttemptIdentity(**values)


def assert_unavailable(exc):
    assert exc.value.status_code == 503
    assert exc.value.detail == ledger.UNAVAILABLE


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": "bad"},
        {"run_id": "00000000-0000-1000-8000-000000000000"},
        {"run_id": 1},
        {"request_index": True},
        {"request_index": 0},
        {"request_index": 4},
        {"request_index": 1.0},
        {"provider": "openai"},
        {"model": ""},
        {"model": " \u0085"},
        {"model": "x" * 201},
        {"model": "x\n"},
        {"model": "x\x00"},
        {"model": "x\x7f"},
        {"model": "\ud800"},
        {"model": None},
    ],
)
def test_identity_rejects_invalid_or_unsafe_fields(changes):
    with pytest.raises(HTTPException) as exc:
        identity(**changes)
    assert_unavailable(exc)


def test_identity_canonicalizes_uuid_and_preserves_model():
    run_id = str(uuid4()).upper()
    result = identity(run_id=run_id, model=" 自定义模型 ")
    assert result.run_id == run_id.lower()
    assert result.model == " 自定义模型 "


@pytest.mark.parametrize(
    "changes",
    [
        {"snapshot_seq": 0},
        {"snapshot_seq": True},
        {"snapshot_seq": 2**63},
        {"counts": {"total_tokens": True}},
        {"counts": {"total_tokens": -1}},
        {"counts": {"total_tokens": 100_000_001}},
        {"counts": {"total_tokens": 1.0}},
        {"counts": {"other": 1}},
        {"counts": []},
        {"status": "unknown"},
        {"status": []},
        {"reason": []},
        {"reason": "secret"},
        {"usage_complete": 1},
        {"truncated": 0},
        {"usage_complete": True},
        {"truncated": True},
        {"reason": "completed"},
        {"status": "completed"},
        {"status": "failed", "usage_complete": True, "reason": "upstream_error"},
        {"status": "completed", "usage_complete": True, "reason": "completed", "counts": {}},
    ],
)
def test_low_level_snapshot_rejects_invalid_payload(changes):
    values = dict(snapshot_seq=1, counts=FULL)
    values.update(changes)
    with pytest.raises(HTTPException) as exc:
        ledger.Snapshot.create(**values)
    assert_unavailable(exc)


async def test_memory_duplicate_and_rejection_do_not_partially_charge():
    store = quota.MemoryQuota("local", {"model": quota.Policy(1, 1)}, clock=lambda: NOW)
    first = identity()
    await store.begin_model_attempt(first)
    with pytest.raises(HTTPException) as duplicate:
        await store.begin_model_attempt(first)
    assert_unavailable(duplicate)
    with pytest.raises(HTTPException) as exhausted:
        await store.begin_model_attempt(identity())
    assert exhausted.value.status_code == 429
    assert store._counters["model"].day_count == 1
    report = await store.model_usage_report(DAY)
    assert report["attempts"] == 1
    assert report["statuses"] == dict(admitted=1, completed=0, failed=0, cancelled=0)
    assert report["known_usage"] == dict.fromkeys(ledger.FIELDS)
    assert report["unknown_field_requests"] == dict.fromkeys(ledger.FIELDS, 1)
    assert report["mode"] == "memory" and report["persistent"] is False


async def test_memory_snapshots_replace_and_return_detached_report():
    clock = [NOW]
    store = quota.MemoryQuota("local", clock=lambda: clock[0])
    key = identity()
    await store.begin_model_attempt(key)
    counts = dict(FULL)
    assert await store.write_model_usage(key, snapshot_seq=2, counts=counts) == "applied"
    counts["prompt_tokens"] = 999
    assert await store.write_model_usage(key, snapshot_seq=1, counts={}) == "stale"
    assert await store.write_model_usage(key, snapshot_seq=2, counts=FULL) == "unchanged"
    with pytest.raises(HTTPException):
        await store.write_model_usage(key, snapshot_seq=2, counts={})
    clock[0] -= 3600
    assert (
        await store.write_model_usage(key, snapshot_seq=3, counts={"total_tokens": 0}) == "applied"
    )
    report = await store.model_usage_report(DAY)
    assert report["known_usage"] == dict(prompt_tokens=None, completion_tokens=None, total_tokens=0)
    report["known_usage"]["total_tokens"] = 100
    assert (await store.model_usage_report(DAY))["known_usage"]["total_tokens"] == 0
    record = store._model_attempts[(key.run_id, 1)]
    assert record.updated_at.timestamp() == NOW


async def test_terminal_is_immutable_and_replays_are_idempotent():
    store = quota.MemoryQuota("local", clock=lambda: NOW)
    key = identity()
    await store.begin_model_attempt(key)
    terminal = dict(
        counts=FULL, status="completed", usage_complete=True, truncated=True, reason="completed"
    )
    assert await store.write_model_usage(key, snapshot_seq=1, **terminal) == "applied"
    assert await store.write_model_usage(key, snapshot_seq=2, **terminal) == "unchanged"
    assert await store.write_model_usage(key, snapshot_seq=99, counts={}) == "stale"
    with pytest.raises(HTTPException) as conflict:
        await store.write_model_usage(
            key, snapshot_seq=3, **(terminal | {"status": "failed", "usage_complete": False})
        )
    assert_unavailable(conflict)
    report = await store.model_usage_report(DAY)
    assert report["complete_requests"] == 1 and report["incomplete_requests"] == 0
    assert report["known_usage"] == FULL


async def test_memory_missing_identity_and_mismatching_model_cannot_write():
    store = quota.MemoryQuota("local", clock=lambda: NOW)
    key = identity()
    await store.begin_model_attempt(key)
    for other in (identity(), identity(run_id=key.run_id, model="different")):
        with pytest.raises(HTTPException) as exc:
            await store.write_model_usage(other, snapshot_seq=1, counts=FULL)
        assert_unavailable(exc)


async def test_memory_day_filter_and_request_rounds_sum_once():
    clock = [NOW]
    store = quota.MemoryQuota("local", clock=lambda: clock[0])
    run_id = str(uuid4())
    for index in range(1, 4):
        key = identity(run_id=run_id, request_index=index)
        await store.begin_model_attempt(key)
        attempt = ledger.ModelAttempt(store, key)
        await attempt.observe(FULL)
        await attempt.observe(FULL)
        await attempt.finish("completed", usage_complete=True)
    clock[0] += 86400
    await store.begin_model_attempt(identity())
    report = await store.model_usage_report(DAY)
    assert report["attempts"] == 3
    assert report["known_usage"] == {key: value * 3 for key, value in FULL.items()}
    assert (await store.model_usage_report(date(2026, 10, 5)))["attempts"] == 1
    assert (await store.model_usage_report(date(2026, 10, 3)))["known_usage"] == dict.fromkeys(
        ledger.FIELDS
    )


async def test_cached_begin_shares_quota_and_requires_explicit_local_mode(monkeypatch):
    quota._store.cache_clear()
    monkeypatch.setenv("AI_QUOTA_MODE", "memory")
    monkeypatch.setenv("AI_QUOTA_SCOPE", "unit-test")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    try:
        first = await ledger.begin_model_attempt(str(uuid4()), 1, "deepseek", "deepseek-chat")
        second = await ledger.begin_model_attempt(str(uuid4()), 1, "deepseek", "deepseek-chat")
        assert first._store is second._store
        monkeypatch.setenv("APP_ENV", "production")
        with pytest.raises(HTTPException) as exc:
            await ledger.begin_model_attempt(str(uuid4()), 1, "deepseek", "deepseek-chat")
        assert_unavailable(exc)
    finally:
        quota._store.cache_clear()


class RecordingStore:
    def __init__(self):
        self.calls = []
        self.failure = None

    async def write_model_usage(self, identity, **payload):
        self.calls.append(payload)
        if self.failure is not None:
            raise self.failure
        return "applied"


async def test_handle_normalizes_and_deduplicates_cumulative_snapshots():
    store = RecordingStore()
    attempt = ledger.ModelAttempt(store, identity())
    await attempt.observe({"total_tokens": True})
    await attempt.observe(None)
    await attempt.observe(FULL | {"ignored": "private"})
    await attempt.observe(FULL)
    await attempt.observe({"total_tokens": 0})
    await attempt.finish("failed")
    await attempt.finish("failed")
    await attempt.observe(FULL)
    assert [call["snapshot_seq"] for call in store.calls] == [1, 2, 3]
    assert store.calls[-1]["counts"] == {"total_tokens": 0}
    assert store.calls[-1]["usage_complete"] is False
    assert store.calls[-1]["reason"] == "upstream_error"


async def test_observe_failure_retains_latest_known_counts_for_failure_terminal():
    store = RecordingStore()
    attempt = ledger.ModelAttempt(store, identity())
    store.failure = ledger.unavailable()
    with pytest.raises(HTTPException):
        await attempt.observe(FULL)
    store.failure = None
    await attempt.finish("failed", reason="ledger_error")
    assert store.calls[-1]["counts"] == FULL
    assert store.calls[-1]["snapshot_seq"] == 2
    assert store.calls[-1]["usage_complete"] is False


@pytest.mark.parametrize("failure", [ledger.unavailable(), asyncio.CancelledError()])
async def test_unknown_terminal_commit_never_retries_or_downgrades(failure):
    store = RecordingStore()
    attempt = ledger.ModelAttempt(store, identity())
    await attempt.observe(FULL)
    store.failure = failure
    with pytest.raises(type(failure)):
        await attempt.finish("completed", usage_complete=True)
    store.failure = None
    for status, complete in (("completed", True), ("failed", False), ("cancelled", False)):
        with pytest.raises(HTTPException) as exc:
            await attempt.finish(status, usage_complete=complete)
        assert_unavailable(exc)
    await attempt.observe({"total_tokens": 900})
    assert len(store.calls) == 2


async def test_zero_complete_and_unknown_incomplete_are_distinct():
    store = RecordingStore()
    attempt = ledger.ModelAttempt(store, identity())
    with pytest.raises(HTTPException):
        await attempt.finish("completed", usage_complete=True)
    assert not store.calls
    await attempt.observe(dict.fromkeys(ledger.FIELDS, 0))
    await attempt.finish("completed", usage_complete=True)
    assert store.calls[-1]["usage_complete"] is True
    unknown = ledger.ModelAttempt(store, identity())
    await unknown.finish("cancelled")
    assert store.calls[-1]["counts"] == {}
    assert store.calls[-1]["usage_complete"] is False


def test_pg_bigint_sum_decimal_becomes_json_integer_without_losing_null_or_zero():
    result = ledger.report_result(
        "scope", DAY, "postgres", (2, 1, 1, 0, 0, Decimal(2**80), None, Decimal(0), 0, 2, 0, 1)
    )
    decoded = json.loads(json.dumps(result))
    assert decoded["known_usage"] == dict(
        prompt_tokens=2**80, completion_tokens=None, total_tokens=0
    )


class Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class Connection:
    def __init__(self, *, fail_on=None, close_failure=False):
        self.statements = []
        self.closed = False
        self.commits = 0
        self.fail_on = fail_on
        self.close_failure = close_failure

    async def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("diagnostic-must-stay-private")
        if "SELECT clock_timestamp()" in sql:
            return Cursor((datetime.fromtimestamp(NOW, UTC),))
        if "SELECT minute_start" in sql:
            return Cursor((0, 0, 0, 0, 10, 100))
        if "RETURNING run_id" in sql:
            return Cursor((params[1],))
        return Cursor(None)

    async def commit(self):
        self.commits += 1
        if self.fail_on == "commit":
            raise RuntimeError("diagnostic-must-stay-private")
        if self.fail_on == "cancel":
            raise asyncio.CancelledError

    async def close(self):
        self.closed = True
        if self.close_failure:
            raise RuntimeError("close-diagnostic-private")


async def connect_store(monkeypatch, connection):
    calls = []

    async def connect(*args, **kwargs):
        calls.append((args, kwargs))
        return connection

    monkeypatch.setattr(quota.QuotaConnection, "connect", connect)
    return quota.PostgresQuota("postgresql://invalid/unit", "unit"), calls


async def test_begin_uses_one_connection_one_commit_and_quota_before_insert(monkeypatch):
    connection = Connection()
    store, calls = await connect_store(monkeypatch, connection)
    await store.begin_model_attempt(identity())
    assert len(calls) == connection.commits == 1 and connection.closed
    statements = [sql for sql, _ in connection.statements]
    assert "UPDATE public.ai_request_quota_v1" in statements[-2]
    assert "INSERT INTO public.ai_model_usage_v1" in statements[-1]
    assert "FOR UPDATE" in statements[1]


@pytest.mark.parametrize(
    "stage", ["UPDATE public.ai_request_quota_v1", "INSERT INTO public.ai_model_usage_v1", "commit"]
)
async def test_storage_failure_is_fixed_and_never_retries(monkeypatch, stage):
    connection = Connection(fail_on=stage)
    store, calls = await connect_store(monkeypatch, connection)
    with pytest.raises(HTTPException) as exc:
        await store.begin_model_attempt(identity())
    assert_unavailable(exc)
    assert len(calls) == 1 and connection.closed
    assert connection.commits == (1 if stage == "commit" else 0)


async def test_cancelled_commit_and_failed_close_preserve_cancellation(monkeypatch):
    connection = Connection(fail_on="cancel", close_failure=True)
    store, _ = await connect_store(monkeypatch, connection)
    with pytest.raises(asyncio.CancelledError):
        await store.begin_model_attempt(identity())
    assert connection.closed and connection.commits == 1


async def test_close_failure_after_commit_fails_closed(monkeypatch):
    connection = Connection(close_failure=True)
    store, _ = await connect_store(monkeypatch, connection)
    with pytest.raises(HTTPException) as exc:
        await store.begin_model_attempt(identity())
    assert_unavailable(exc)
    assert connection.commits == 1


async def test_uncertain_observation_invalidates_previous_acknowledgement():
    store = RecordingStore()
    attempt = ledger.ModelAttempt(store, identity())
    await attempt.observe(FULL)
    store.failure = ledger.unavailable()
    with pytest.raises(HTTPException):
        await attempt.observe({"total_tokens": 1})
    store.failure = None
    await attempt.observe(FULL)
    assert len(store.calls) == 3
    assert store.calls[-1]["counts"] == FULL
    assert store.calls[-1]["snapshot_seq"] == 3
