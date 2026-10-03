import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import _live_requests, _sandbox_requests, app
from backend.sandbox import OUTPUT_LIMIT, execute_code, sandbox_capabilities


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("E2B_API_KEY", "test-service-key")
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "test-access-code")
    monkeypatch.delenv("E2B_GO_TEMPLATE", raising=False)
    _live_requests.clear()
    _sandbox_requests.clear()


def factory_for(run_code):
    sandbox = SimpleNamespace(
        run_code=run_code,
        kill=AsyncMock(return_value=True),
        files=SimpleNamespace(write=AsyncMock()),
        commands=SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(exit_code=0))),
    )
    factory = SimpleNamespace(create=AsyncMock(return_value=sandbox))
    return factory, sandbox


@pytest.mark.parametrize("language", ["python", "typescript"])
async def test_execution_is_remote_network_denied_and_secrets_not_in_environment(
    configured, language
):
    async def run(source, **kwargs):
        kwargs["on_stdout"](SimpleNamespace(line="42\n"))
        return SimpleNamespace(error=None, results=[])

    factory, sandbox = factory_for(run)
    result = await execute_code(language, "print(42)", factory)
    options = factory.create.call_args.kwargs
    assert options["allow_internet_access"] is False and options["secure"] is True
    assert options["timeout"] == 45 and options["envs"] == {}
    assert set(options["metadata"]) == {"project", "language", "run_id"}
    assert result["passed"] is True and result["stdout"] == "42\n"
    assert result["cleanup"] == "destroyed"
    sandbox.kill.assert_awaited_once()


async def test_go_source_is_a_file_never_shell_interpolation(configured, monkeypatch):
    monkeypatch.setenv("E2B_GO_TEMPLATE", "trusted-go-template")
    factory, sandbox = factory_for(AsyncMock())
    source = "package main\n// $(touch /tmp/unwanted)\nfunc main() {}"
    result = await execute_code("go", source, factory)
    assert result["passed"] is True
    assert factory.create.call_args.kwargs["template"] == "trusted-go-template"
    assert sandbox.files.write.call_args.args == ("/home/user/main.go", source)
    assert "$(" not in sandbox.commands.run.call_args.args[0]
    sandbox.kill.assert_awaited_once()


async def test_output_limit_stops_and_destroys_sandbox(configured):
    async def run(source, **kwargs):
        kwargs["on_stdout"](SimpleNamespace(line="x" * (OUTPUT_LIMIT + 1)))

    factory, sandbox = factory_for(run)
    result = await execute_code("python", "print('x')", factory)
    assert result["status"] == "output-limit" and result["truncated"] is True
    assert result["passed"] is False and len(result["stdout"]) == OUTPUT_LIMIT
    sandbox.kill.assert_awaited_once()


async def test_timeout_always_destroys_sandbox(configured):
    factory, sandbox = factory_for(AsyncMock(side_effect=TimeoutError))
    result = await execute_code("python", "while True: pass", factory)
    assert result["status"] == "timeout" and result["passed"] is False
    sandbox.kill.assert_awaited_once()


async def test_sdk_error_is_sanitized_and_cleanup_still_runs(configured):
    factory, sandbox = factory_for(AsyncMock(side_effect=RuntimeError("test-service-key")))
    with pytest.raises(HTTPException) as error:
        await execute_code("python", "print(42)", factory)
    assert error.value.status_code == 503
    assert "test-service-key" not in error.value.detail
    sandbox.kill.assert_awaited_once()


async def test_cancellation_destroys_active_sandbox(configured):
    started = asyncio.Event()

    async def run(source, **kwargs):
        started.set()
        await asyncio.Event().wait()

    factory, sandbox = factory_for(run)
    task = asyncio.create_task(execute_code("python", "while True: pass", factory))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    sandbox.kill.assert_awaited_once()


async def test_cleanup_failure_is_not_reported_as_destroyed(configured):
    factory, sandbox = factory_for(AsyncMock(return_value=SimpleNamespace(error=None, results=[])))
    sandbox.kill.side_effect = RuntimeError("network unavailable")
    result = await execute_code("python", "print(42)", factory)
    assert result["cleanup"] == "expiry-fallback"


def test_configuration_and_access_code_are_required(configured, monkeypatch):
    client = TestClient(app)
    monkeypatch.delenv("E2B_API_KEY")
    assert sandbox_capabilities()["languages"] == []
    assert (
        client.post(
            "/api/playground/execute", json={"language": "python", "code": "print(42)"}
        ).status_code
        == 503
    )
    monkeypatch.setenv("E2B_API_KEY", "test-service-key")
    assert (
        client.post(
            "/api/playground/execute", json={"language": "python", "code": "print(42)"}
        ).status_code
        == 401
    )
    assert "go" not in sandbox_capabilities()["languages"]


def test_execution_endpoint_rate_limit_is_separate_and_bounded(configured, monkeypatch):
    import backend.app as api_module

    executor = AsyncMock(return_value={"mode": "isolated-sandbox", "passed": True})
    monkeypatch.setattr(api_module, "execute_code", executor)
    client = TestClient(app)
    for index in range(3):
        response = client.post(
            "/api/playground/execute",
            json={"language": "python", "code": "print(42)"},
            headers={"X-Playground-Token": "test-access-code"},
        )
        assert response.status_code == (200 if index < 2 else 429)
    assert executor.await_count == 2
