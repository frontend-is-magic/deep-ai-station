"""Real, explicitly configured PostgreSQL ledger tests; never substitute an in-memory DB."""

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, date
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from psycopg import sql
from psycopg.rows import dict_row

from backend.model_usage import ModelAttemptIdentity
from backend.quota import Policy, PostgresQuota, QuotaConnection

ROOT = Path(__file__).resolve().parents[1]
UNAVAILABLE = "模型请求账本暂不可用，请稍后重试"
FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")
PRIVATE_DIAGNOSTIC = "private-ledger-diagnostic-must-not-escape"


@dataclass
class Database:
    dsn: str = field(repr=False)
    scope: str
    admin: psycopg.AsyncConnection = field(repr=False)

    def __iter__(self):
        yield self.dsn
        yield self.scope
        yield self.admin


def identity(*, run_id=None, index=1):
    return ModelAttemptIdentity(run_id or str(uuid4()), index, "deepseek", "deepseek-chat")


async def migrate(connection, names=("001_request_quota.sql", "002_model_usage.sql")):
    for name in names:
        await connection.execute((ROOT / "backend/migrations" / name).read_text())


@pytest.fixture
async def database():
    dsn = os.getenv("TEST_QUOTA_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_QUOTA_DATABASE_URL is required for real PostgreSQL ledger tests")
    scope = f"ledger-test-{uuid4().hex}"
    admin = None
    setup_failed = False
    try:
        admin = await psycopg.AsyncConnection.connect(
            dsn, autocommit=True, connect_timeout=3, row_factory=dict_row
        )
        await migrate(admin)
    except (psycopg.Error, OSError, ValueError):
        setup_failed = True
    if setup_failed:
        if admin is not None:
            await admin.close()
        # Fail outside the exception handler so pytest cannot render the raw DSN cause.
        pytest.fail("Configured PostgreSQL ledger test database is unavailable", pytrace=False)
    try:
        yield Database(dsn, scope, admin)
    finally:
        try:
            for table in ("ai_model_usage_v1", "ai_request_quota_v1"):
                await admin.execute(
                    sql.SQL("DELETE FROM public.{} WHERE scope IN (%s, %s)").format(
                        sql.Identifier(table)
                    ),
                    (scope, scope + "-other"),
                )
        finally:
            await admin.close()


async def quota_row(admin, scope):
    cursor = await admin.execute(
        "SELECT * FROM public.ai_request_quota_v1 WHERE scope = %s AND resource = 'model'",
        (scope,),
    )
    return await cursor.fetchone()


async def usage_rows(admin, scope):
    cursor = await admin.execute(
        "SELECT * FROM public.ai_model_usage_v1 WHERE scope = %s ORDER BY run_id, request_index",
        (scope,),
    )
    return await cursor.fetchall()


async def freeze_windows(admin, scope):
    # Prevent a UTC boundary from changing the quota during a deterministic race.
    await admin.execute(
        """UPDATE public.ai_request_quota_v1
           SET minute_start = floor(extract(epoch FROM clock_timestamp()) / 60)::bigint * 60 + 86400,
               day_start = floor(extract(epoch FROM clock_timestamp()) / 86400)::bigint * 86400 + 86400
           WHERE scope = %s""",
        (scope,),
    )


def child_environment(dsn, scope, **values):
    # Preserve import paths for the isolated test runtime, never the parent's secrets/env.
    return {
        "PATH": os.defpath,
        "PYTHONPATH": os.pathsep.join(path for path in sys.path if path),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TEST_QUOTA_DATABASE_URL": dsn,
        "LEDGER_TEST_SCOPE": scope,
        **values,
    }


async def start_child(source, environment):
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-B",
        "-c",
        source,
        cwd=ROOT,
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def finish_child(process, input_data=None):
    try:
        async with asyncio.timeout(15):
            stdout, stderr = await process.communicate(input_data)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    # Raw stderr/driver exceptions could contain the explicitly supplied test DSN.
    assert process.returncode == 0, "ledger test subprocess failed"
    if stderr:
        pytest.fail("ledger test subprocess produced diagnostics", pytrace=False)
    return json.loads(stdout)


RACE_SOURCE = """
import asyncio, json, os, sys
from uuid import uuid4
from fastapi import HTTPException
from backend.model_usage import ModelAttemptIdentity
from backend.quota import Policy, PostgresQuota
async def main():
    policy = json.loads(os.environ['LEDGER_TEST_POLICY'])
    store = PostgresQuota(os.environ['TEST_QUOTA_DATABASE_URL'], os.environ['LEDGER_TEST_SCOPE'], {'model': Policy(*policy)})
    results = []
    for _ in range(int(os.environ['LEDGER_TEST_ATTEMPTS'])):
        run_id = os.environ.get('LEDGER_TEST_RUN_ID') or str(uuid4())
        attempt = ModelAttemptIdentity(run_id, 1, 'deepseek', 'deepseek-chat')
        try:
            await store.begin_model_attempt(attempt)
        except HTTPException as error:
            results.append(error.status_code)
        else:
            results.append(200)
    print(json.dumps(results), flush=True)
print('ready', flush=True)
sys.stdin.readline()
asyncio.run(main())
"""


async def race_children(dsn, scope, *, policy, attempts, run_id=""):
    processes = []
    try:
        for _ in range(2):
            processes.append(
                await start_child(
                    RACE_SOURCE,
                    child_environment(
                        dsn,
                        scope,
                        LEDGER_TEST_POLICY=json.dumps(policy),
                        LEDGER_TEST_ATTEMPTS=str(attempts),
                        LEDGER_TEST_RUN_ID=run_id,
                    ),
                )
            )
        async with asyncio.timeout(10):
            for process in processes:
                assert await process.stdout.readline() == b"ready\n"
        results = await asyncio.gather(*(finish_child(p, b"go\n") for p in processes))
        return [status for result in results for status in result]
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def test_real_process_race_keeps_permissions_rows_and_both_windows_atomic(database):
    dsn, scope, admin = database
    policy = Policy(3, 3)
    await PostgresQuota(dsn, scope, {"model": policy}).begin_model_attempt(identity())
    await freeze_windows(admin, scope)
    results = await race_children(dsn, scope, policy=(3, 3), attempts=3)
    assert results.count(200) == 2 and results.count(429) == 4
    counter = await quota_row(admin, scope)
    assert counter["minute_count"] == counter["day_count"] == 3
    assert len(await usage_rows(admin, scope)) == 3
    before = counter.copy()
    with pytest.raises(HTTPException) as error:
        await PostgresQuota(dsn, scope, {"model": policy}).begin_model_attempt(identity())
    assert error.value.status_code == 429
    assert await quota_row(admin, scope) == before
    assert len(await usage_rows(admin, scope)) == 3


async def test_real_same_request_key_in_two_processes_grants_only_one_permission(database):
    dsn, scope, admin = database
    attempt = identity()
    results = await race_children(dsn, scope, policy=(10, 10), attempts=1, run_id=attempt.run_id)
    assert sorted(results) == [200, 503]
    counter = await quota_row(admin, scope)
    assert counter["minute_count"] == counter["day_count"] == 1
    assert len(await usage_rows(admin, scope)) == 1
    with pytest.raises(HTTPException) as error:
        await PostgresQuota(dsn, scope, {"model": Policy(10, 10)}).begin_model_attempt(attempt)
    assert error.value.detail == UNAVAILABLE
    assert await quota_row(admin, scope) == counter


@pytest.mark.parametrize("failure_after", ["quota_update", "ledger_insert"])
async def test_real_admission_transaction_rolls_back_both_tables_on_failure(
    database, monkeypatch, caplog, capsys, failure_after
):
    dsn, scope, admin = database
    store = PostgresQuota(dsn, scope)
    await store.begin_model_attempt(identity())
    await freeze_windows(admin, scope)
    before_counter, before_usage = await quota_row(admin, scope), await usage_rows(admin, scope)
    original_execute = QuotaConnection.execute
    injections = []

    async def fail_after_statement(connection, query, params=None, **kwargs):
        cursor = await original_execute(connection, query, params, **kwargs)
        statement = " ".join(str(query).split()).upper()
        target = {
            "quota_update": "UPDATE PUBLIC.AI_REQUEST_QUOTA_V1",
            "ledger_insert": "INSERT INTO PUBLIC.AI_MODEL_USAGE_V1",
        }[failure_after]
        if statement.startswith(target):
            injections.append(failure_after)
            raise psycopg.OperationalError(PRIVATE_DIAGNOSTIC)
        return cursor

    monkeypatch.setattr(QuotaConnection, "execute", fail_after_statement)
    with pytest.raises(HTTPException) as error:
        await store.begin_model_attempt(identity())
    assert error.value.status_code == 503 and error.value.detail == UNAVAILABLE
    assert injections == [failure_after]
    assert await quota_row(admin, scope) == before_counter
    assert await usage_rows(admin, scope) == before_usage
    output = capsys.readouterr()
    assert PRIVATE_DIAGNOSTIC not in caplog.text + output.out + output.err
    assert dsn not in caplog.text + output.out + output.err


async def test_controlled_lost_commit_ack_on_real_pg_never_grants_second_permission(
    database, monkeypatch
):
    dsn, scope, admin = database
    store, attempt = PostgresQuota(dsn, scope), identity()
    original_commit = QuotaConnection.commit
    commits = []

    async def lost_ack(connection):
        await original_commit(connection)
        commits.append(True)
        raise psycopg.OperationalError(PRIVATE_DIAGNOSTIC)

    # The actual PG commit succeeds; only its acknowledgement is deliberately lost.
    with monkeypatch.context() as patch:
        patch.setattr(QuotaConnection, "commit", lost_ack)
        with pytest.raises(HTTPException) as error:
            await store.begin_model_attempt(attempt)
    assert error.value.status_code == 503 and error.value.detail == UNAVAILABLE
    assert commits == [True]
    rows = await usage_rows(admin, scope)
    assert len(rows) == 1 and rows[0]["status"] == "admitted"
    before = await quota_row(admin, scope)
    with pytest.raises(HTTPException) as duplicate:
        await store.begin_model_attempt(attempt)
    assert duplicate.value.detail == UNAVAILABLE
    assert await quota_row(admin, scope) == before
    assert len(await usage_rows(admin, scope)) == 1


async def test_real_snapshots_use_sequence_and_terminal_state_is_immutable(database):
    dsn, scope, admin = database
    store, attempt = PostgresQuota(dsn, scope), identity()
    await store.begin_model_attempt(attempt)
    assert (
        await store.write_model_usage(attempt, snapshot_seq=1, counts={"prompt_tokens": 10})
        == "applied"
    )
    counts = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
    assert await store.write_model_usage(attempt, snapshot_seq=2, counts=counts) == "applied"
    # Wall-clock rollback does not change snapshot ordering.
    await admin.execute(
        "UPDATE public.ai_model_usage_v1 SET updated_at = admitted_at - interval '1 day' WHERE scope = %s",
        (scope,),
    )
    before = await usage_rows(admin, scope)
    assert (
        await store.write_model_usage(attempt, snapshot_seq=1, counts={"prompt_tokens": 99})
        == "stale"
    )
    assert await store.write_model_usage(attempt, snapshot_seq=2, counts=counts) == "unchanged"
    with pytest.raises(HTTPException) as error:
        await store.write_model_usage(attempt, snapshot_seq=2, counts={"prompt_tokens": 13})
    assert error.value.detail == UNAVAILABLE
    assert await usage_rows(admin, scope) == before
    terminal = dict(
        snapshot_seq=3,
        counts=counts,
        status="completed",
        usage_complete=True,
        reason="completed",
        truncated=False,
    )
    assert await store.write_model_usage(attempt, **terminal) == "applied"
    finished = (await usage_rows(admin, scope))[0]
    assert finished["usage_complete"] is True and finished["finished_at"] is not None
    assert tuple(finished[field] for field in FIELDS) == (12, 3, 15)
    assert await store.write_model_usage(attempt, **terminal) == "unchanged"
    assert (
        await store.write_model_usage(attempt, snapshot_seq=4, counts={"prompt_tokens": 999})
        == "stale"
    )
    with pytest.raises(HTTPException) as conflict:
        await store.write_model_usage(
            attempt, snapshot_seq=4, counts=counts, status="failed", reason="upstream_error"
        )
    assert conflict.value.detail == UNAVAILABLE
    assert (await usage_rows(admin, scope))[0] == finished


async def test_real_daily_report_preserves_null_zero_latest_rounds_and_utc_boundaries(database):
    dsn, scope, admin = database
    store = PostgresQuota(dsn, scope)
    first = identity()
    second = identity(run_id=first.run_id, index=2)
    pending, cancelled, outside = identity(), identity(), identity()
    for attempt in (first, second, pending, cancelled, outside):
        await store.begin_model_attempt(attempt)
    await store.write_model_usage(first, snapshot_seq=1, counts={"prompt_tokens": 7})
    await store.write_model_usage(
        first,
        snapshot_seq=2,
        counts={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        status="completed",
        usage_complete=True,
        reason="completed",
        truncated=False,
    )
    await store.write_model_usage(
        second,
        snapshot_seq=1,
        counts={"prompt_tokens": 0},
        status="failed",
        reason="upstream_error",
    )
    await store.write_model_usage(
        cancelled, snapshot_seq=1, counts={}, status="cancelled", reason="cancelled"
    )
    await admin.execute(
        """UPDATE public.ai_model_usage_v1 SET admitted_at = CASE
             WHEN run_id = %s THEN '2030-01-02 00:00:00+00'::timestamptz
             WHEN run_id = %s THEN '2030-01-01 23:59:59.999999+00'::timestamptz
             ELSE '2030-01-01 00:00:00+00'::timestamptz END WHERE scope = %s""",
        (outside.run_id, cancelled.run_id, scope),
    )
    other = PostgresQuota(dsn, scope + "-other")
    await other.begin_model_attempt(identity())
    await admin.execute(
        "UPDATE public.ai_model_usage_v1 SET admitted_at = '2030-01-01 12:00:00+00' WHERE scope = %s",
        (scope + "-other",),
    )
    report = await store.model_usage_report(date(2030, 1, 1))
    assert report == {
        "scope": scope,
        "date": "2030-01-01",
        "mode": "postgres",
        "persistent": True,
        "attempts": 4,
        "statuses": {"admitted": 1, "completed": 1, "failed": 1, "cancelled": 1},
        "known_usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        "unknown_field_requests": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 3},
        "complete_requests": 1,
        "incomplete_requests": 3,
    }
    assert (await store.model_usage_report(date(2030, 1, 2)))["attempts"] == 1
    empty = await store.model_usage_report(date(2029, 12, 31))
    assert empty["attempts"] == 0 and empty["known_usage"] == dict.fromkeys(FIELDS)
    partial = [row for row in await usage_rows(admin, scope) if row["request_index"] == 2][0]
    assert partial["prompt_tokens"] == 0 and partial["completion_tokens"] is None
    assert partial["total_tokens"] is None and partial["usage_complete"] is False


async def test_real_process_exit_keeps_unfinished_snapshot_after_restart(database):
    dsn, scope, admin = database
    attempt = identity()
    writer = """
import asyncio, json, os
from backend.model_usage import ModelAttemptIdentity
from backend.quota import PostgresQuota
async def main():
    store = PostgresQuota(os.environ['TEST_QUOTA_DATABASE_URL'], os.environ['LEDGER_TEST_SCOPE'])
    attempt = ModelAttemptIdentity(os.environ['LEDGER_TEST_RUN_ID'], 1, 'deepseek', 'deepseek-chat')
    await store.begin_model_attempt(attempt)
    await store.write_model_usage(attempt, snapshot_seq=1, counts={'prompt_tokens': 0})
    print(json.dumps({'persisted': True}), flush=True)
asyncio.run(main())
os._exit(0)
"""
    environment = child_environment(dsn, scope, LEDGER_TEST_RUN_ID=attempt.run_id)
    assert await finish_child(await start_child(writer, environment)) == {"persisted": True}
    reader = """
import asyncio, json, os
import psycopg
async def main():
    async with await psycopg.AsyncConnection.connect(os.environ['TEST_QUOTA_DATABASE_URL']) as connection:
        cursor = await connection.execute('SELECT status, snapshot_seq, prompt_tokens, completion_tokens, total_tokens, finished_at FROM public.ai_model_usage_v1 WHERE scope = %s AND run_id = %s', (os.environ['LEDGER_TEST_SCOPE'], os.environ['LEDGER_TEST_RUN_ID']))
        print(json.dumps(await cursor.fetchone()), flush=True)
asyncio.run(main())
"""
    assert await finish_child(await start_child(reader, environment)) == [
        "admitted",
        1,
        0,
        None,
        None,
        None,
    ]
    report = await PostgresQuota(dsn, scope).model_usage_report(
        (await usage_rows(admin, scope))[0]["admitted_at"].astimezone(UTC).date()
    )
    assert report["statuses"]["admitted"] == 1 and report["incomplete_requests"] == 1
    assert report["known_usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": None,
        "total_tokens": None,
    }


@pytest.mark.parametrize("operation", ["admit", "snapshot"])
async def test_real_lock_wait_timeout_is_bounded_sanitized_and_atomic(
    database, caplog, capsys, operation
):
    dsn, scope, admin = database
    store, attempt = PostgresQuota(dsn, scope), identity()
    await store.begin_model_attempt(attempt)
    before_counter, before_usage = await quota_row(admin, scope), await usage_rows(admin, scope)
    async with await psycopg.AsyncConnection.connect(dsn) as blocker:
        table = "ai_request_quota_v1" if operation == "admit" else "ai_model_usage_v1"
        await blocker.execute(
            sql.SQL("SELECT scope FROM public.{} WHERE scope = %s FOR UPDATE").format(
                sql.Identifier(table)
            ),
            (scope,),
        )
        async with asyncio.timeout(4):
            with pytest.raises(HTTPException) as error:
                if operation == "admit":
                    await store.begin_model_attempt(identity())
                else:
                    await store.write_model_usage(
                        attempt, snapshot_seq=1, counts={"prompt_tokens": 9}
                    )
    assert error.value.status_code == 503 and error.value.detail == UNAVAILABLE
    assert await quota_row(admin, scope) == before_counter
    assert await usage_rows(admin, scope) == before_usage
    output = capsys.readouterr()
    assert dsn not in caplog.text + output.out + output.err
    assert "psycopg" not in caplog.text + output.err


@pytest.mark.parametrize("operation", ["admit", "snapshot"])
async def test_real_cancel_during_sql_lock_wait_closes_connection_and_rolls_back(
    database, monkeypatch, caplog, capsys, operation
):
    dsn, scope, admin = database
    store, attempt = PostgresQuota(dsn, scope), identity()
    await store.begin_model_attempt(attempt)
    before_counter, before_usage = await quota_row(admin, scope), await usage_rows(admin, scope)
    original_connect, opened = QuotaConnection.connect, []

    async def tracked_connect(*args, **kwargs):
        connection = await original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    marked_dsn = psycopg.conninfo.make_conninfo(dsn, application_name=scope)
    store = PostgresQuota(marked_dsn, scope)
    async with await psycopg.AsyncConnection.connect(dsn) as blocker:
        table = "ai_request_quota_v1" if operation == "admit" else "ai_model_usage_v1"
        await blocker.execute(
            sql.SQL("SELECT scope FROM public.{} WHERE scope = %s FOR UPDATE").format(
                sql.Identifier(table)
            ),
            (scope,),
        )
        monkeypatch.setattr(QuotaConnection, "connect", tracked_connect)
        task = asyncio.create_task(
            store.begin_model_attempt(identity())
            if operation == "admit"
            else store.write_model_usage(attempt, snapshot_seq=1, counts={"prompt_tokens": 9})
        )
        try:
            async with asyncio.timeout(3):
                while True:
                    cursor = await admin.execute(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE application_name = %s AND wait_event_type = 'Lock') AS waiting",
                        (scope,),
                    )
                    if (await cursor.fetchone())["waiting"]:
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
    assert await quota_row(admin, scope) == before_counter
    assert await usage_rows(admin, scope) == before_usage
    # The lock and cancellation must not poison subsequent independent requests.
    await store.write_model_usage(attempt, snapshot_seq=1, counts={"prompt_tokens": 1})
    output = capsys.readouterr()
    assert dsn not in caplog.text + output.out + output.err
    assert "psycopg" not in caplog.text + output.err


async def test_real_001_upgrade_and_reinitialization_preserve_quota_and_ledger(database):
    dsn, _, admin = database
    database_name = "ledger_upgrade_" + uuid4().hex
    scope = "upgrade-check"
    await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    try:
        isolated_dsn = psycopg.conninfo.make_conninfo(dsn, dbname=database_name)
        async with await psycopg.AsyncConnection.connect(
            isolated_dsn, autocommit=True, row_factory=dict_row
        ) as isolated:
            await migrate(isolated, ("001_request_quota.sql",))
            store = PostgresQuota(isolated_dsn, scope)
            await store.admit("model")
            before = await quota_row(isolated, scope)
            await migrate(isolated, ("002_model_usage.sql",))
            assert await quota_row(isolated, scope) == before
            attempt = identity()
            await store.begin_model_attempt(attempt)
            await store.write_model_usage(attempt, snapshot_seq=1, counts={"prompt_tokens": 0})
            expected_quota, expected_usage = (
                await quota_row(isolated, scope),
                await usage_rows(isolated, scope),
            )
            await migrate(isolated)
            assert await quota_row(isolated, scope) == expected_quota
            assert await usage_rows(isolated, scope) == expected_usage
    finally:
        await admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))


async def test_real_runtime_role_needs_only_dml_and_no_ddl(database, monkeypatch):
    dsn, scope, admin = database
    role_name = "ledger_runtime_" + uuid4().hex
    role = sql.Identifier(role_name)
    await admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(role))
    try:
        await admin.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        await admin.execute(
            sql.SQL(
                "GRANT SELECT, INSERT, UPDATE ON public.ai_request_quota_v1, public.ai_model_usage_v1 TO {}"
            ).format(role)
        )
        cursor = await admin.execute(
            "SELECT has_schema_privilege(%s, 'public', 'CREATE') AS can_create", (role_name,)
        )
        assert (await cursor.fetchone())["can_create"] is False
        original_connect = QuotaConnection.connect

        async def runtime_connect(*args, **kwargs):
            connection = await original_connect(*args, **kwargs)
            await connection.execute(sql.SQL("SET ROLE {}").format(role))
            return connection

        with monkeypatch.context() as patch:
            patch.setattr(QuotaConnection, "connect", runtime_connect)
            store, attempt = PostgresQuota(dsn, scope), identity()
            await store.begin_model_attempt(attempt)
            await store.write_model_usage(
                attempt,
                snapshot_seq=1,
                counts={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                status="completed",
                usage_complete=True,
                reason="completed",
            )
            admitted_date = (
                (await usage_rows(admin, scope))[0]["admitted_at"].astimezone(UTC).date()
            )
            report = await store.model_usage_report(admitted_date)
            assert report["complete_requests"] == 1 and report["known_usage"] == dict.fromkeys(
                FIELDS, 0
            )
    finally:
        await admin.execute(
            sql.SQL(
                "REVOKE ALL ON public.ai_request_quota_v1, public.ai_model_usage_v1 FROM {}"
            ).format(role)
        )
        await admin.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(role))
        await admin.execute(sql.SQL("DROP ROLE {}").format(role))


@pytest.mark.parametrize(
    "column,value",
    [
        ("snapshot_seq", -1),
        ("prompt_tokens", 100_000_001),
        ("request_index", 4),
        ("status", "completed"),
        ("usage_complete", True),
    ],
)
async def test_real_schema_rejects_invalid_counts_and_inconsistent_terminal_rows(
    database, column, value
):
    dsn, scope, admin = database
    await PostgresQuota(dsn, scope).begin_model_attempt(identity())
    before = await usage_rows(admin, scope)
    with pytest.raises(psycopg.IntegrityError):
        await admin.execute(
            sql.SQL("UPDATE public.ai_model_usage_v1 SET {} = %s WHERE scope = %s").format(
                sql.Identifier(column)
            ),
            (value, scope),
        )
    assert await usage_rows(admin, scope) == before


async def test_real_report_cli_matches_scope_summary_and_sanitizes_config_failure(database):
    dsn, scope, admin = database
    store, attempt = PostgresQuota(dsn, scope), identity()
    await store.begin_model_attempt(attempt)
    await store.write_model_usage(attempt, snapshot_seq=1, counts={"prompt_tokens": 0})
    day = (await usage_rows(admin, scope))[0]["admitted_at"].astimezone(UTC).date()
    expected = await store.model_usage_report(day)
    assert expected["attempts"] == 1

    async def invoke(selected_scope):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-B",
            str(ROOT / "scripts/report_model_usage.py"),
            "--date",
            day.isoformat(),
            cwd=ROOT,
            env=child_environment(
                dsn,
                scope,
                AI_QUOTA_MODE="postgres",
                AI_QUOTA_SCOPE=selected_scope,
                AI_QUOTA_DATABASE_URL=dsn,
            ),
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
        return process.returncode, stdout.decode(), stderr.decode()

    code, stdout, stderr = await invoke(scope)
    assert code == 0, "configured ledger CLI failed"
    assert stderr == ""
    assert json.loads(stdout) == expected
    code, stdout, stderr = await invoke("invalid scope " + PRIVATE_DIAGNOSTIC)
    assert code == 1 and stdout == ""
    assert stderr == "模型请求用量汇总失败，请检查托管配置与数据库权限。\n"
    assert dsn not in stderr and PRIVATE_DIAGNOSTIC not in stderr
