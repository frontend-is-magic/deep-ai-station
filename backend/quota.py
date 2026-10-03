"""Atomic admission counts, not a token ledger or a monetary budget."""

import asyncio
import math
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

import psycopg
from fastapi import HTTPException

from backend import model_usage

Resource = Literal["model", "sandbox"]
UNAVAILABLE = "共享请求配额暂不可用，请检查服务端配置"


@dataclass(frozen=True)
class Policy:
    minute: int
    day: int

    def __post_init__(self):
        if any(
            type(value) is not int or not 1 <= value <= 1_000_000
            for value in (self.minute, self.day)
        ):
            raise ValueError("invalid quota policy")


POLICIES = {"model": Policy(10, 100), "sandbox": Policy(2, 20)}


@dataclass(frozen=True)
class Config:
    mode: str
    scope: str
    database_url: str = field(repr=False)


def configuration() -> Config:
    mode = os.getenv("AI_QUOTA_MODE", "postgres")
    scope = os.getenv("AI_QUOTA_SCOPE", "deep-ai-station")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", scope):
        raise HTTPException(503, UNAVAILABLE)
    if mode == "memory":
        if os.getenv("VERCEL") == "1" or os.getenv("APP_ENV") == "production":
            raise HTTPException(503, UNAVAILABLE)
        return Config(mode, scope, "")
    if mode != "postgres":
        raise HTTPException(503, UNAVAILABLE)
    database_url = os.getenv("AI_QUOTA_DATABASE_URL", "")
    try:
        url = urlsplit(database_url)
        if (
            not database_url
            or len(database_url) > 4096
            or any(char.isspace() for char in database_url)
            or url.scheme not in {"postgres", "postgresql"}
            or not url.hostname
            or url.fragment
        ):
            raise ValueError("invalid database URL")
        _ = url.port
        psycopg.conninfo.conninfo_to_dict(database_url)
    except (ValueError, psycopg.Error):
        raise HTTPException(503, UNAVAILABLE) from None
    return Config(mode, scope, database_url)


def quota_configured() -> bool:
    """Configuration readiness only: never contacts a database."""
    try:
        configuration()
        return True
    except HTTPException:
        return False


@dataclass(frozen=True)
class Counter:
    minute_start: int = 0
    day_start: int = 0
    minute_count: int = 0
    day_count: int = 0


def advance(counter: Counter, policy: Policy, now: float) -> Counter:
    """Only move windows forward, retaining counts if the DB clock moves back."""
    minute = math.floor(now / 60) * 60
    day = math.floor(now / 86400) * 86400
    minute_start = max(minute, counter.minute_start)
    day_start = max(day, counter.day_start)
    minute_count = 0 if minute > counter.minute_start else counter.minute_count
    day_count = 0 if day > counter.day_start else counter.day_count
    waits = []
    if minute_count >= policy.minute:
        waits.append(minute_start + 60 - now)
    if day_count >= policy.day:
        waits.append(day_start + 86400 - now)
    if waits:
        retry_after = max(1, math.ceil(max(waits)))
        raise HTTPException(
            429,
            "共享请求额度已用完，请稍后重试",
            headers={"Retry-After": str(retry_after)},
        )
    return Counter(minute_start, day_start, minute_count + 1, day_count + 1)


class MemoryQuota:
    """Explicit local development only; shares nothing across processes."""

    def __init__(
        self,
        scope: str,
        policies: Mapping[str, Policy] | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.scope = scope
        self.policies = dict(POLICIES if policies is None else policies)
        self.clock = clock
        self._counters: dict[str, Counter] = {}
        self._model_attempts: dict[tuple[str, int], model_usage.MemoryRecord] = {}
        self._lock = threading.Lock()

    async def admit(self, resource: Resource):
        policy = self.policies[resource]
        with self._lock:
            self._counters[resource] = advance(
                self._counters.get(resource, Counter()), policy, self.clock()
            )

    async def begin_model_attempt(self, identity: model_usage.ModelAttemptIdentity):
        key = (identity.run_id, identity.request_index)
        with self._lock:
            if key in self._model_attempts:
                raise model_usage.unavailable()
            now = self.clock()
            counter = advance(self._counters.get("model", Counter()), self.policies["model"], now)
            timestamp = datetime.fromtimestamp(now, UTC)
            record = model_usage.MemoryRecord(
                identity, model_usage.Snapshot(0, (None, None, None)), timestamp, timestamp
            )
            self._counters["model"] = counter
            self._model_attempts[key] = record

    async def write_model_usage(
        self,
        identity,
        *,
        snapshot_seq,
        counts,
        status="admitted",
        usage_complete=False,
        truncated=None,
        reason=None,
    ):
        incoming = model_usage.Snapshot.create(
            snapshot_seq, counts, status, usage_complete, truncated, reason
        )
        key = (identity.run_id, identity.request_index)
        with self._lock:
            record = self._model_attempts.get(key)
            if record is None or record.identity != identity:
                raise model_usage.unavailable()
            result = model_usage.transition(record.snapshot, incoming)
            if result == "applied":
                self._model_attempts[key] = record.update(
                    incoming, datetime.fromtimestamp(self.clock(), UTC)
                )
            return result

    async def model_usage_report(self, day: date):
        with self._lock:
            return model_usage.memory_report(self.scope, day, self._model_attempts.values())


class QuotaConnection(psycopg.AsyncConnection):
    async def _try_cancel(self, *, timeout: float = 5.0) -> None:
        # psycopg 3.3.3's wait() invokes this hook after interruption. These short-lived
        # connections are never reused: close instead of logging raw cancel diagnostics
        # or waiting another five seconds. PostgreSQL rolls back an uncommitted session.
        await self.close()
        raise asyncio.CancelledError from None


class PostgresQuota:
    def __init__(self, database_url: str, scope: str, policies: Mapping[str, Policy] | None = None):
        self._database_url = database_url
        self.scope = scope
        self.policies = dict(POLICIES if policies is None else policies)

    async def admit(self, resource: Resource):
        policy = self.policies[resource]
        connection = None
        try:
            async with asyncio.timeout(5):
                connection = await QuotaConnection.connect(
                    self._database_url,
                    connect_timeout=3,
                    options="-c statement_timeout=2000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=3000",
                )
                await self._admit_in_transaction(connection, resource, policy)
                await connection.commit()
                # Commit must finish before a caller may invoke its provider.
                # On failure/cancellation, closing below rolls back without raw driver logs.
        except HTTPException:
            raise
        except (psycopg.Error, TimeoutError, OSError, ValueError):
            # Never retry an uncertain commit or fall back to memory.
            raise HTTPException(503, UNAVAILABLE) from None
        finally:
            if connection is not None and not connection.closed:
                await connection.close()

    async def _admit_in_transaction(self, connection, resource, policy):
        # The unique key serializes first use; FOR UPDATE protects both windows.
        await connection.execute(
            """INSERT INTO public.ai_request_quota_v1
               (scope, resource, minute_limit, day_limit)
               VALUES (%s, %s, %s, %s) ON CONFLICT (scope, resource) DO NOTHING""",
            (self.scope, resource, policy.minute, policy.day),
        )
        cursor = await connection.execute(
            """SELECT minute_start, day_start, minute_count, day_count, minute_limit, day_limit
               FROM public.ai_request_quota_v1 WHERE scope = %s AND resource = %s FOR UPDATE""",
            (self.scope, resource),
        )
        row = await cursor.fetchone()
        if row is None or (row[4], row[5]) != (policy.minute, policy.day):
            raise HTTPException(503, UNAVAILABLE)
        # Take DB time only after acquiring the row lock, never application time.
        cursor = await connection.execute("SELECT clock_timestamp()")
        now = (await cursor.fetchone())[0].timestamp()
        updated = advance(Counter(*row[:4]), policy, now)
        await connection.execute(
            """UPDATE public.ai_request_quota_v1
               SET minute_start = %s, day_start = %s, minute_count = %s, day_count = %s
               WHERE scope = %s AND resource = %s""",
            (
                updated.minute_start,
                updated.day_start,
                updated.minute_count,
                updated.day_count,
                self.scope,
                resource,
            ),
        )

    async def _ledger_transaction(self, operation):
        connection = None
        try:
            async with asyncio.timeout(5):
                connection = await QuotaConnection.connect(
                    self._database_url,
                    connect_timeout=3,
                    options="-c statement_timeout=2000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=3000",
                )
                result = await operation(connection)
                await connection.commit()
                return result
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except HTTPException as exc:
            if exc.status_code == 429:
                raise
            raise model_usage.unavailable() from None
        except Exception:
            # Includes unknown commit outcomes. No retries and no in-memory fallback.
            raise model_usage.unavailable() from None
        finally:
            interrupted = sys.exc_info()[0] is not None
            if connection is not None and not connection.closed:
                try:
                    await connection.close()
                except BaseException:
                    # Cleanup diagnostics must not replace the original cancellation/error.
                    if not interrupted:
                        if isinstance(sys.exception(), (asyncio.CancelledError, GeneratorExit)):
                            raise
                        raise model_usage.unavailable() from None

    async def begin_model_attempt(self, identity: model_usage.ModelAttemptIdentity):
        async def operation(connection):
            await self._admit_in_transaction(connection, "model", self.policies["model"])
            await model_usage.insert_postgres(connection, self.scope, identity)

        await self._ledger_transaction(operation)

    async def write_model_usage(
        self,
        identity,
        *,
        snapshot_seq,
        counts,
        status="admitted",
        usage_complete=False,
        truncated=None,
        reason=None,
    ):
        incoming = model_usage.Snapshot.create(
            snapshot_seq, counts, status, usage_complete, truncated, reason
        )

        async def operation(connection):
            return await model_usage.write_postgres(connection, self.scope, identity, incoming)

        return await self._ledger_transaction(operation)

    async def model_usage_report(self, day: date):
        model_usage.day_bounds(day)

        async def operation(connection):
            return await model_usage.report_postgres(connection, self.scope, day)

        return await self._ledger_transaction(operation)


@lru_cache(maxsize=8)
def _store(config: Config):
    if config.mode == "memory":
        return MemoryQuota(config.scope)
    return PostgresQuota(config.database_url, config.scope)


async def admit(resource: Resource):
    await _store(configuration()).admit(resource)
