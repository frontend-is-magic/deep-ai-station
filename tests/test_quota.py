import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import quota
from backend.app import app
from backend.providers import capabilities
from backend.quota import Counter, MemoryQuota, Policy, PostgresQuota, advance


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    for name in (
        "AI_QUOTA_MODE",
        "AI_QUOTA_DATABASE_URL",
        "AI_QUOTA_SCOPE",
        "VERCEL",
        "APP_ENV",
        "DEEPSEEK_API_KEY",
        "E2B_API_KEY",
        "PLAYGROUND_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    quota._store.cache_clear()
    yield
    quota._store.cache_clear()


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"AI_QUOTA_MODE": "unknown"},
        {"AI_QUOTA_MODE": ""},
        {"AI_QUOTA_MODE": "memory", "VERCEL": "1"},
        {"AI_QUOTA_MODE": "memory", "APP_ENV": "production"},
        {"AI_QUOTA_MODE": "memory", "AI_QUOTA_SCOPE": "a\n"},
        {"AI_QUOTA_MODE": "postgres", "AI_QUOTA_DATABASE_URL": "sqlite:///tmp/a"},
        {"AI_QUOTA_MODE": "postgres", "AI_QUOTA_DATABASE_URL": "postgresql://localhost:bad/x"},
        {"AI_QUOTA_MODE": "postgres", "AI_QUOTA_DATABASE_URL": "postgresql://localhost/x#fragment"},
    ],
)
async def test_invalid_config_disables_live_and_never_connects(values, monkeypatch):
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    for name in ("DEEPSEEK_API_KEY", "E2B_API_KEY", "PLAYGROUND_ACCESS_TOKEN"):
        monkeypatch.setenv(name, "test-only")
    connect = AsyncMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    result = capabilities()
    assert result["providers"][0]["enabled"] is True
    assert result["providers"][1]["enabled"] is False
    assert result["sandbox"]["enabled"] is False
    with pytest.raises(HTTPException) as error:
        await quota.admit("model")
    assert error.value.status_code == 503
    connect.assert_not_awaited()


def test_valid_postgres_capability_does_not_probe_or_expose_database(monkeypatch):
    monkeypatch.setenv("AI_QUOTA_DATABASE_URL", "postgresql://user:fake-password@localhost/test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "fake-access")
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", Mock(side_effect=AssertionError))
    assert capabilities()["providers"][1]["enabled"] is True
    assert "password" not in repr(capabilities())
    assert "password" not in repr(quota.configuration())


async def test_memory_atomic_windows_reset_and_resource_isolation():
    now = [86400 + 10]
    store = MemoryQuota(
        "local", {"model": Policy(2, 3), "sandbox": Policy(1, 2)}, clock=lambda: now[0]
    )
    await asyncio.gather(store.admit("model"), store.admit("model"))
    snapshot = store._counters.copy()
    with pytest.raises(HTTPException) as error:
        await store.admit("model")
    assert error.value.headers == {"Retry-After": "50"}
    assert store._counters == snapshot
    await store.admit("sandbox")
    now[0] += 60
    await store.admit("model")
    assert store._counters["model"].minute_count == 1
    assert store._counters["model"].day_count == 3
    snapshot = store._counters.copy()
    with pytest.raises(HTTPException) as error:
        await store.admit("model")
    assert int(error.value.headers["Retry-After"]) == 86330
    assert store._counters == snapshot
    now[0] = 2 * 86400
    await store.admit("model")
    assert store._counters["model"] == Counter(172800, 172800, 1, 1)
    assert store._counters["sandbox"] == snapshot["sandbox"]


def test_clock_rollback_keeps_windows_and_counts():
    counter = Counter(86460, 86400, 1, 2)
    assert advance(counter, Policy(2, 3), 100) == replace(counter, minute_count=2, day_count=3)
    with pytest.raises(HTTPException) as error:
        advance(replace(counter, minute_count=2), Policy(2, 3), 100)
    assert int(error.value.headers["Retry-After"]) == 86420


def test_same_scope_memory_store_survives_configuration_read(monkeypatch):
    monkeypatch.setenv("AI_QUOTA_MODE", "memory")
    first = quota._store(quota.configuration())
    assert quota._store(quota.configuration()) is first
    monkeypatch.setenv("AI_QUOTA_SCOPE", "other")
    assert quota._store(quota.configuration()) is not first


class FakeConnection:
    def __init__(self, *, fail=None, blocked=False):
        self.closed = False
        self.entered = asyncio.Event()
        self.fail = fail
        self.blocked = blocked
        self.statements = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, error_type, error, traceback):
        raise AssertionError("driver context manager can leak rollback diagnostics")
        await self.close()

    async def commit(self):
        self.committed = True

    async def close(self):
        self.closed = True

    async def execute(self, sql, params=None):
        self.statements.append((sql, params))
        self.entered.set()
        if self.fail:
            raise self.fail
        if self.blocked:
            await asyncio.Event().wait()
        row = (
            (datetime(2026, 1, 1, tzinfo=UTC),)
            if "clock_timestamp" in sql
            else (0, 0, 0, 0, 10, 100)
        )
        return Mock(fetchone=AsyncMock(return_value=row))


async def test_postgres_time_is_read_after_row_lock_and_commit_precedes_return(monkeypatch):
    connection = FakeConnection()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    await PostgresQuota("not-printed", "test").admit("model")
    statements = [item[0] for item in connection.statements]
    assert "FOR UPDATE" in statements[1] and "clock_timestamp" in statements[2]
    assert "UPDATE" in statements[3]
    assert connection.closed and connection.committed
    assert connect.call_args.kwargs["connect_timeout"] == 3
    assert "lock_timeout=1000" in connect.call_args.kwargs["options"]


@pytest.mark.parametrize(
    "failure", [psycopg.OperationalError("secret-DSN"), TimeoutError("secret-DSN")]
)
async def test_database_failure_closes_connection_and_is_sanitized(monkeypatch, failure):
    connection = FakeConnection(fail=failure)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", AsyncMock(return_value=connection))
    with pytest.raises(HTTPException) as error:
        await PostgresQuota("secret-DSN", "test").admit("model")
    assert error.value.status_code == 503 and "secret" not in error.value.detail
    assert connection.closed


async def test_cancellation_during_database_wait_closes_connection(monkeypatch):
    connection = FakeConnection(blocked=True)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", AsyncMock(return_value=connection))
    task = asyncio.create_task(PostgresQuota("unused", "test").admit("model"))
    await connection.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert connection.closed and not connection.committed


@pytest.mark.parametrize("workflow", ["retrieval", "agent"])
def test_missing_protection_prevents_every_real_model_call(monkeypatch, workflow):
    import backend.agent_loop as agent
    import backend.app as api

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "test-access")
    provider = Mock(side_effect=AssertionError("must not call upstream"))
    monkeypatch.setattr(api, "stream_generate", provider)
    monkeypatch.setattr(agent, "stream_generate", provider)
    response = TestClient(app).post(
        "/api/playground/run",
        json={"prompt": "MCP", "provider": "deepseek", "workflow": workflow},
        headers={"X-Playground-Token": "test-access"},
    )
    if workflow == "retrieval":
        assert response.status_code == 503
    else:
        assert response.status_code == 200 and '"code": 503' in response.text
    provider.assert_not_called()


@pytest.mark.parametrize("status,headers", [(429, {"Retry-After": "42"}), (503, None)])
def test_quota_http_error_preserves_status_headers_and_zero_model_calls(
    monkeypatch, status, headers
):
    import backend.app as api

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "test-access")
    admission = AsyncMock(side_effect=HTTPException(status, quota.UNAVAILABLE, headers=headers))
    provider = Mock(side_effect=AssertionError)
    monkeypatch.setattr(api, "admit", admission)
    monkeypatch.setattr(api, "stream_generate", provider)
    response = TestClient(app).post(
        "/api/playground/run",
        json={"prompt": "MCP", "provider": "deepseek"},
        headers={"X-Playground-Token": "test-access"},
    )
    assert response.status_code == status
    if status == 429:
        assert response.headers["Retry-After"] == "42"
    provider.assert_not_called()


@pytest.mark.parametrize(
    "body,access,status",
    [
        ({"prompt": " ", "provider": "deepseek"}, "test-access", 422),
        ({"prompt": "MCP", "provider": "deepseek"}, "wrong", 401),
        ({"prompt": "MCP", "provider": "deepseek", "lesson_id": "missing"}, "test-access", 404),
        ({"prompt": "MCP", "provider": "demo"}, None, 200),
        ({"prompt": "MCP", "provider": "demo", "workflow": "agent"}, None, 200),
    ],
)
def test_validation_auth_and_demo_never_consume_quota(monkeypatch, body, access, status):
    import backend.app as api

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "test-access")
    admission = AsyncMock(side_effect=AssertionError)
    monkeypatch.setattr(api, "admit", admission)
    response = TestClient(app).post(
        "/api/playground/run", json=body, headers={"X-Playground-Token": access} if access else {}
    )
    assert response.status_code == status
    admission.assert_not_awaited()


@pytest.mark.parametrize("reason", ["missing-key", "unsupported-language", "busy"])
async def test_sandbox_preconditions_do_not_consume_quota(monkeypatch, reason):
    import backend.sandbox as sandbox

    monkeypatch.setenv("E2B_API_KEY", "test-key")
    if reason == "missing-key":
        monkeypatch.delenv("E2B_API_KEY")
    admission = AsyncMock(side_effect=AssertionError)
    monkeypatch.setattr(sandbox, "admit", admission)
    slots = asyncio.Semaphore(0 if reason == "busy" else 2)
    monkeypatch.setattr(sandbox, "_slots", slots)
    with pytest.raises(HTTPException):
        await sandbox.execute_code(
            "go" if reason == "unsupported-language" else "python", "print(1)"
        )
    admission.assert_not_awaited()


async def test_sandbox_admission_failure_never_creates_and_releases_local_slot(monkeypatch):
    from types import SimpleNamespace

    import backend.sandbox as sandbox

    monkeypatch.setenv("E2B_API_KEY", "test-key")
    admission = AsyncMock(side_effect=HTTPException(429, "quota", headers={"Retry-After": "5"}))
    monkeypatch.setattr(sandbox, "admit", admission)
    slots = asyncio.Semaphore(1)
    monkeypatch.setattr(sandbox, "_slots", slots)
    factory = SimpleNamespace(create=AsyncMock(side_effect=AssertionError))
    with pytest.raises(HTTPException) as error:
        await sandbox.execute_code("python", "print(1)", factory)
    assert error.value.status_code == 429 and not slots.locked()
    admission.assert_awaited_once_with("sandbox")
    factory.create.assert_not_awaited()


async def test_sandbox_cancel_during_admission_never_creates_and_releases_slot(monkeypatch):
    from types import SimpleNamespace

    import backend.sandbox as sandbox

    monkeypatch.setenv("E2B_API_KEY", "test-key")
    entered = asyncio.Event()

    async def blocked(resource):
        assert resource == "sandbox"
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(sandbox, "admit", blocked)
    slots = asyncio.Semaphore(1)
    monkeypatch.setattr(sandbox, "_slots", slots)
    factory = SimpleNamespace(create=AsyncMock(side_effect=AssertionError))
    task = asyncio.create_task(sandbox.execute_code("python", "print(1)", factory))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not slots.locked()
    factory.create.assert_not_awaited()


async def test_sandbox_provider_failure_keeps_attempt_in_separate_bucket(monkeypatch):
    from types import SimpleNamespace

    import backend.sandbox as sandbox

    monkeypatch.setenv("E2B_API_KEY", "test-key")
    store = MemoryQuota("local", clock=lambda: 86401)
    monkeypatch.setattr(sandbox, "admit", store.admit)
    factory = SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("fake provider failure")))
    with pytest.raises(HTTPException) as error:
        await sandbox.execute_code("python", "print(1)", factory)
    assert error.value.status_code == 503
    assert store._counters["sandbox"].day_count == 1
    assert "model" not in store._counters


def test_model_failure_after_admission_is_not_refunded(monkeypatch):
    import backend.app as api

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "test-access")
    store = MemoryQuota("local", clock=lambda: 86401)
    monkeypatch.setattr(api, "admit", store.admit)

    async def failure(*args):
        raise HTTPException(502, "model failed")
        yield

    monkeypatch.setattr(api, "stream_generate", failure)
    response = TestClient(app).post(
        "/api/playground/run",
        json={"prompt": "MCP", "provider": "deepseek"},
        headers={"X-Playground-Token": "test-access"},
    )
    assert '"code": 502' in response.text
    assert store._counters["model"].day_count == 1


async def test_agent_cancel_during_admission_never_calls_provider(monkeypatch):
    import backend.agent_loop as agent

    entered = asyncio.Event()

    async def charge():
        entered.set()
        await asyncio.Event().wait()

    provider = Mock(side_effect=AssertionError)
    monkeypatch.setattr(agent, "stream_generate", provider)
    stream = agent.stream_agent(
        "deepseek", "MCP", "system", 0.3, "agent", None, "test", charge=charge
    )
    task = asyncio.create_task(anext(stream))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await stream.aclose()
    provider.assert_not_called()


async def test_agent_cancellation_after_admission_keeps_attempt(monkeypatch):
    import backend.agent_loop as agent

    entered = asyncio.Event()
    store = MemoryQuota("local", clock=lambda: 86401)

    async def stream(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
        yield

    monkeypatch.setattr(agent, "stream_generate", stream)
    generator = agent.stream_agent(
        "deepseek", "MCP", "system", 0.3, "agent", None, "test", charge=lambda: store.admit("model")
    )
    assert (await anext(generator))["event"] == "trace"
    task = asyncio.create_task(anext(generator))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await generator.aclose()
    assert store._counters["model"].day_count == 1


def test_setup_failure_output_is_fixed_and_has_no_traceback(monkeypatch, capsys):
    from scripts import setup_quota

    async def fail():
        raise psycopg.OperationalError("postgresql://private-password@private-host/db")

    monkeypatch.setattr(setup_quota, "setup", fail)
    assert setup_quota.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "配额表初始化失败，请检查托管配置与数据库权限。\n"


async def test_setup_requires_postgres_and_uses_only_fixed_migration(monkeypatch):
    from scripts import setup_quota

    monkeypatch.setenv("AI_QUOTA_MODE", "memory")
    connect = AsyncMock(side_effect=AssertionError)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    with pytest.raises(ValueError):
        await setup_quota.setup()
    connect.assert_not_awaited()
    monkeypatch.setenv("AI_QUOTA_MODE", "postgres")
    monkeypatch.setenv("AI_QUOTA_DATABASE_URL", "postgresql://localhost/unused")
    connection = FakeConnection()
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", AsyncMock(return_value=connection))
    await setup_quota.setup()
    assert len(connection.statements) == 1
    assert "CREATE TABLE IF NOT EXISTS public.ai_request_quota_v1" in connection.statements[0][0]
    assert connection.closed


async def test_driver_cancel_hook_closes_without_raw_diagnostics(monkeypatch, caplog):
    from types import SimpleNamespace

    from psycopg import waiting

    class InterruptedConnection(FakeConnection):
        wait = psycopg.AsyncConnection.wait
        _try_cancel = quota.QuotaConnection._try_cancel
        pgconn = SimpleNamespace(socket=1, transaction_status=psycopg.pq.TransactionStatus.ACTIVE)
        cancel_safe = AsyncMock(side_effect=psycopg.OperationalError("private-diagnostic-marker"))

    connection = InterruptedConnection()
    waiter = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(waiting, "wait_async", waiter)
    with pytest.raises(asyncio.CancelledError):
        await connection.wait(iter(()))
    assert connection.closed
    connection.cancel_safe.assert_not_awaited()
    assert waiter.await_count == 1
    assert "private-diagnostic-marker" not in caplog.text


@pytest.mark.parametrize("cancel", [False, True])
async def test_uncertain_commit_never_retries_or_calls_provider(monkeypatch, cancel):
    connection = FakeConnection()
    entered = asyncio.Event()

    async def uncertain_commit():
        connection.committed = True
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        raise psycopg.OperationalError("secret commit acknowledgement lost")

    connection.commit = uncertain_commit
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    provider = Mock()

    async def caller():
        await PostgresQuota("unused", "test").admit("model")
        provider()

    task = asyncio.create_task(caller())
    await entered.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(HTTPException) as error:
            await task
        assert error.value.status_code == 503
    assert connection.closed
    connect.assert_awaited_once()
    provider.assert_not_called()


async def test_initial_failure_never_enters_raw_rollback_logger(monkeypatch, caplog):
    class RollbackConnection(FakeConnection):
        __aexit__ = psycopg.AsyncConnection.__aexit__

    connection = RollbackConnection(fail=psycopg.OperationalError("initial failure"))
    connection.rollback = AsyncMock(side_effect=psycopg.OperationalError("private-rollback-marker"))
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", AsyncMock(return_value=connection))
    with pytest.raises(HTTPException) as error:
        await PostgresQuota("unused", "test").admit("model")
    assert error.value.status_code == 503 and connection.closed
    connection.rollback.assert_not_awaited()
    assert "private-rollback-marker" not in caplog.text
