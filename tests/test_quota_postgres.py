"""Real PostgreSQL tests. CI must set TEST_QUOTA_DATABASE_URL; no SQLite substitute."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException

from backend.quota import Policy, PostgresQuota, QuotaConnection


@pytest.fixture
async def database():
    dsn = os.getenv("TEST_QUOTA_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_QUOTA_DATABASE_URL is required for real PostgreSQL quota tests")
    scope = f"quota-test-{uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(
        dsn, autocommit=True, connect_timeout=3
    ) as admin:
        sql = (
            Path(__file__).resolve().parents[1] / "backend/migrations/001_request_quota.sql"
        ).read_text()
        await admin.execute(sql)
        try:
            yield dsn, scope, admin
        finally:
            await admin.execute(
                "DELETE FROM public.ai_request_quota_v1 WHERE scope IN (%s, %s)",
                (scope, scope + "-other"),
            )


async def row(admin, scope, resource="model"):
    cursor = await admin.execute(
        "SELECT minute_start, day_start, minute_count, day_count, minute_limit, day_limit FROM public.ai_request_quota_v1 WHERE scope = %s AND resource = %s",
        (scope, resource),
    )
    return await cursor.fetchone()


async def shift_windows(admin, scope, *, future=False):
    # Future windows make concurrency assertions independent of a nearby UTC boundary.
    offset = 86400 if future else -86400
    await admin.execute(
        """UPDATE public.ai_request_quota_v1
           SET minute_start = floor(extract(epoch FROM clock_timestamp()) / 60)::bigint * 60 + %s,
               day_start = floor(extract(epoch FROM clock_timestamp()) / 86400)::bigint * 86400 + %s
           WHERE scope = %s""",
        (offset, offset, scope),
    )


async def test_real_concurrent_instances_share_one_limit_and_rejections_are_atomic(database):
    dsn, scope, admin = database
    policies = {"model": Policy(4, 9)}
    await PostgresQuota(dsn, scope, policies).admit("model")
    await shift_windows(admin, scope, future=True)

    async def attempt():
        # A new store and a new real connection for every attempt.
        try:
            await PostgresQuota(dsn, scope, policies).admit("model")
            return 200
        except HTTPException as error:
            return error.status_code

    results = await asyncio.gather(*(attempt() for _ in range(10)))
    assert results.count(200) == 3 and results.count(429) == 7
    before = await row(admin, scope)
    assert before[2:4] == (4, 4)
    assert await attempt() == 429
    assert await row(admin, scope) == before


async def test_real_first_use_race_creates_exactly_one_shared_counter(database):
    dsn, scope, admin = database
    # Keep both caps above all attempts, so even a clock boundary cannot hide an admission.
    stores = [PostgresQuota(dsn, scope, {"model": Policy(40, 40)}) for _ in range(8)]
    await asyncio.gather(*(store.admit("model") for store in stores))
    current = await row(admin, scope)
    assert current[3] == 8
    cursor = await admin.execute(
        "SELECT count(*) FROM public.ai_request_quota_v1 WHERE scope = %s", (scope,)
    )
    assert (await cursor.fetchone())[0] == 1


async def test_real_daily_denial_does_not_consume_new_minute_and_resources_are_separate(database):
    dsn, scope, admin = database
    policies = {"model": Policy(5, 1), "sandbox": Policy(2, 2)}
    store = PostgresQuota(dsn, scope, policies)
    await store.admit("model")
    await shift_windows(admin, scope, future=True)
    await admin.execute(
        "UPDATE public.ai_request_quota_v1 SET minute_start = 0 WHERE scope = %s", (scope,)
    )
    before = await row(admin, scope)
    with pytest.raises(HTTPException) as error:
        await store.admit("model")
    assert error.value.status_code == 429
    assert int(error.value.headers["Retry-After"]) >= 1
    assert await row(admin, scope) == before
    await store.admit("sandbox")
    await PostgresQuota(dsn, scope + "-other", policies).admit("model")
    assert (await row(admin, scope, "sandbox"))[2:4] == (1, 1)
    assert (await row(admin, scope + "-other"))[2:4] == (1, 1)


async def test_real_windows_reset_forward_and_db_clock_rollback_preserves_counts(database):
    dsn, scope, admin = database
    store = PostgresQuota(dsn, scope, {"model": Policy(2, 5)})
    await store.admit("model")
    await shift_windows(admin, scope)
    await store.admit("model")
    assert (await row(admin, scope))[2:4] == (1, 1)
    await shift_windows(admin, scope, future=True)
    ahead = await row(admin, scope)
    await store.admit("model")
    assert (await row(admin, scope))[:4] == (*ahead[:2], 2, 2)
    with pytest.raises(HTTPException) as error:
        await store.admit("model")
    assert error.value.status_code == 429
    assert (await row(admin, scope))[2:4] == (2, 2)


@pytest.mark.parametrize("policy", [Policy(11, 100), Policy(10, 101), Policy(9, 100)])
async def test_real_policy_mismatch_fails_closed_without_changing_counter(database, policy):
    dsn, scope, admin = database
    await PostgresQuota(dsn, scope).admit("model")
    before = await row(admin, scope)
    with pytest.raises(HTTPException) as error:
        await PostgresQuota(dsn, scope, {"model": policy}).admit("model")
    assert error.value.status_code == 503
    assert await row(admin, scope) == before


async def test_real_cancel_while_waiting_for_lock_closes_connection_without_admission(
    database, monkeypatch
):
    dsn, scope, admin = database
    await PostgresQuota(dsn, scope).admit("model")
    before = await row(admin, scope)
    marked_dsn = psycopg.conninfo.make_conninfo(dsn, application_name=scope)
    opened = []
    original_connect = QuotaConnection.connect
    async with await original_connect(dsn) as blocker:
        await blocker.execute(
            "SELECT scope FROM public.ai_request_quota_v1 WHERE scope = %s FOR UPDATE", (scope,)
        )

        async def tracking_connect(*args, **kwargs):
            connection = await original_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        monkeypatch.setattr(QuotaConnection, "connect", tracking_connect)
        task = asyncio.create_task(PostgresQuota(marked_dsn, scope).admit("model"))
        try:
            async with asyncio.timeout(3):
                while True:
                    cursor = await admin.execute(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE application_name = %s AND wait_event_type = 'Lock')",
                        (scope,),
                    )
                    if (await cursor.fetchone())[0]:
                        break
                    await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    assert opened and all(connection.closed for connection in opened)
    assert await row(admin, scope) == before
    await PostgresQuota(dsn, scope).admit("model")
    assert (await row(admin, scope))[3] == 2


async def test_real_lock_timeout_is_bounded_sanitized_and_leaves_counter_unchanged(database):
    dsn, scope, admin = database
    await PostgresQuota(dsn, scope).admit("model")
    before = await row(admin, scope)
    async with await psycopg.AsyncConnection.connect(dsn) as blocker:
        await blocker.execute(
            "SELECT scope FROM public.ai_request_quota_v1 WHERE scope = %s FOR UPDATE", (scope,)
        )
        async with asyncio.timeout(4):
            with pytest.raises(HTTPException) as error:
                await PostgresQuota(dsn, scope).admit("model")
        assert error.value.status_code == 503
        assert "postgres" not in error.value.detail and "password" not in error.value.detail
    assert await row(admin, scope) == before


async def test_real_process_restart_preserves_admission_counts(database):
    import sys

    dsn, scope, admin = database
    # The child receives only fixed maintainer code and test settings via environment.
    source = """
import asyncio
import os
from fastapi import HTTPException
from backend.quota import Policy, PostgresQuota
async def main():
    store = PostgresQuota(os.environ['TEST_QUOTA_DATABASE_URL'], os.environ['QUOTA_TEST_SCOPE'], {'model': Policy(10, 3)})
    for _ in range(int(os.environ['QUOTA_TEST_ATTEMPTS'])):
        try:
            await store.admit('model')
        except HTTPException as error:
            print(error.status_code)
        else:
            print(200)
asyncio.run(main())
"""

    async def child(attempts):
        environment = {
            **os.environ,
            "TEST_QUOTA_DATABASE_URL": dsn,
            "QUOTA_TEST_SCOPE": scope,
            "QUOTA_TEST_ATTEMPTS": str(attempts),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-B",
            "-c",
            source,
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(15):
                stdout, stderr = await process.communicate()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        # Do not include raw driver diagnostics/DSN in a failing pytest assertion.
        assert process.returncode == 0, "quota test subprocess failed"
        if stderr:
            pytest.fail("quota test subprocess produced diagnostics", pytrace=False)
        return stdout.decode().splitlines()

    assert await child(2) == ["200", "200"]
    await shift_windows(admin, scope, future=True)
    await PostgresQuota(dsn, scope, {"model": Policy(10, 3)}).admit("model")
    assert await child(1) == ["429"]
    assert (await row(admin, scope))[2:4] == (3, 3)
