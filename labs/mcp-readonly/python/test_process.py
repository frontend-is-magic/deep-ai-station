import asyncio
import copy
import json
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import jsonschema
import pytest
from mcp import StdioServerParameters

import client
from protocol import COURSES, PROTOCOL_VERSION, SearchOutput
from test_protocol import INVALID

ROOT = Path(__file__).resolve().parent
META = {
    "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "native-test", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


@asynccontextmanager
async def raw_server(fault="none"):
    with (
        tempfile.TemporaryDirectory(prefix="mcp-native-") as home,
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr,
    ):
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-s",
            "-E",
            "-B",
            str(ROOT / "server.py"),
            "--fault",
            fault,
            env=client.safe_environment(home),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            yield proc, stderr
        finally:
            if proc.returncode is None:
                proc.stdin.close()
                try:
                    await asyncio.wait_for(proc.wait(), 3)
                except TimeoutError:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), 2)
                    except TimeoutError:
                        proc.kill()
                        await asyncio.wait_for(proc.wait(), 2)
            assert client.exited(proc.pid)


async def rpc(proc, request_id, method, params=None):
    request = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {**(params or {}), "_meta": META},
    }
    proc.stdin.write((json.dumps(request, ensure_ascii=True) + "\n").encode())
    await proc.stdin.drain()
    line = await asyncio.wait_for(proc.stdout.readline(), 3)
    assert line.endswith(b"\n")
    response = json.loads(line.decode("utf-8"))
    assert response["jsonrpc"] == "2.0" and response["id"] == request_id
    return response


async def test_real_wire_discovery_structured_output_resources_and_fixed_errors():
    async with raw_server() as (proc, stderr):
        assert (
            PROTOCOL_VERSION
            in (await rpc(proc, 1, "server/discover"))["result"]["supportedVersions"]
        )
        tool = (await rpc(proc, 2, "tools/list"))["result"]["tools"][0]
        resources = (await rpc(proc, 3, "resources/list"))["result"]["resources"]
        assert [r["uri"] for r in resources] == [c["uri"] for c in COURSES]
        for index, course in enumerate(COURSES, 4):
            contents = (await rpc(proc, index, "resources/read", {"uri": course["uri"]}))["result"][
                "contents"
            ]
            assert contents == [
                {"uri": course["uri"], "mimeType": "text/plain", "text": course["text"]}
            ]
        result = (
            await rpc(
                proc, 7, "tools/call", {"name": "knowledge_search", "arguments": {"query": " mCp "}}
            )
        )["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["items"][0]["id"] == "agent-mcp"
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        jsonschema.validate(result["structuredContent"], tool["outputSchema"])
        for request_id, args in enumerate(
            [
                *[
                    value
                    for value in INVALID
                    if not any(0xD800 <= ord(c) <= 0xDFFF for c in str(value.get("query", "")))
                ],
                {"query": "MCP", "limit": 1.0},
            ],
            8,
        ):
            failed = (
                await rpc(
                    proc, request_id, "tools/call", {"name": "knowledge_search", "arguments": args}
                )
            )["result"]
            assert failed["isError"] is True
            assert failed["content"] == [{"type": "text", "text": "invalid_arguments"}]
            assert "structuredContent" not in failed
        assert (
            await rpc(proc, 50, "tools/call", {"name": "PRIVATE_TEST_MARKER", "arguments": {}})
        )["error"] == {"code": -32602, "message": "unknown_tool"}
        assert (await rpc(proc, 51, "resources/read", {"uri": "course://PRIVATE_TEST_MARKER"}))[
            "error"
        ] == {"code": -32602, "message": "resource_not_found"}


@pytest.mark.parametrize("case", client.CASES)
async def test_actual_sdk_case_report_and_pid_exit(case):
    report = await client.run_experiment(case)
    assert report["passed"] is True, report
    assert report["protocol_version"] == PROTOCOL_VERSION
    run = report["runs"][0]
    assert client.exited(run["pid"])
    assert [s["request_id"] for s in run["steps"]] == list(range(1, len(run["steps"]) + 1))
    if case == "disconnect":
        assert run["transport_closed"] is True
        assert run["transport_close_evidence"] in ("read_eof", "sdk_connection_closed")
    if case == "timeout":
        assert run["cancellation_sent"] == [{"request_id": 3}]
        assert [e["event"] for e in run["server_cleanup_events"]] == [
            "request_cleanup",
            "server_cleanup",
        ]


async def test_normal_report_rejects_empty_match_and_changed_schema():
    run = (await client.run_experiment("normal"))["runs"][0]
    altered = copy.deepcopy(run)
    payload = SearchOutput(items=[]).model_dump()
    altered["steps"][3]["result"]["structuredContent"] = payload
    altered["steps"][3]["result"]["content"][0]["text"] = json.dumps(payload)
    assert client.passed(altered) is False
    altered = copy.deepcopy(run)
    altered["steps"][1]["result"]["tools"][0]["annotations"]["readOnlyHint"] = False
    assert client.passed(altered) is False


async def test_real_disconnect_reaches_stdout_eof_and_reaps_process():
    async with raw_server("disconnect") as (proc, stderr):
        # asyncio may auto-close its StreamWriter when the peer exits. Keep an
        # independent duplicate of our write end open until EOF and wait finish.
        held_stdin = os.dup(proc.stdin.transport.get_extra_info("pipe").fileno())
        try:
            await rpc(proc, 1, "server/discover")
            proc.stdin.write(
                (
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {
                                "name": "knowledge_search",
                                "arguments": {"query": "MCP"},
                                "_meta": META,
                            },
                        }
                    )
                    + "\n"
                ).encode()
            )
            await proc.stdin.drain()
            closing = await asyncio.wait_for(proc.stdout.readline(), 3)
            if closing:
                assert json.loads(closing) == {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "error": {"code": -32000, "message": "transport_closed"},
                }
                assert await asyncio.wait_for(proc.stdout.readline(), 3) == b""
            os.fstat(held_stdin)
            await asyncio.wait_for(proc.wait(), 3)
            assert proc.returncode == 0
            stderr.seek(0)
            events = [json.loads(line)["event"] for line in stderr]
            assert events == [
                "server_started",
                "request_started",
                "request_cleanup",
                "server_cleanup",
            ]
        finally:
            os.close(held_stdin)


async def test_child_environment_excludes_fake_secret_and_overrides_sdk_defaults(monkeypatch):
    monkeypatch.setenv("MCP_FAKE_SECRET", "PRIVATE_TEST_MARKER")
    monkeypatch.setenv("HOME", "/fake-parent-home")
    monkeypatch.setenv("PATH", "/fake-parent-bin")
    original = client.stdio_client
    # A fixed test-only process reports key names/booleans, never any environment values.
    code = "import os,json,sys;print(json.dumps({'secret_present':'MCP_FAKE_SECRET' in os.environ,'parent_home':os.environ.get('HOME')=='/fake-parent-home','parent_path':os.environ.get('PATH')=='/fake-parent-bin'}),file=sys.stderr,flush=True)"
    with (
        tempfile.TemporaryDirectory(prefix="mcp-env-") as home,
        tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr,
    ):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-s", "-E", "-B", "-c", code],
            env=client.safe_environment(home),
        )
        async with original(params, errlog=stderr):
            pass
        stderr.seek(0)
        assert json.loads(stderr.read()) == {
            "secret_present": False,
            "parent_home": False,
            "parent_path": False,
        }


async def test_discovery_timeout_has_no_cancel_and_force_reaps_only_owned_child(monkeypatch):
    original = client.stdio_client
    code = "import os,json,sys,time;print(json.dumps({'event':'server_started','pid':os.getpid()}),file=sys.stderr,flush=True);time.sleep(60)"

    @asynccontextmanager
    async def stalled(parameters, errlog):
        parameters = parameters.model_copy(update={"args": ["-s", "-E", "-B", "-c", code]})
        async with original(parameters, errlog=errlog) as streams:
            yield streams

    monkeypatch.setattr(client, "stdio_client", stalled)
    monkeypatch.setattr(client, "DISCOVERY_SECONDS", 0.3)
    report = await client.run_experiment("normal")
    run = report["runs"][0]
    assert report["passed"] is False and report["protocol_version"] is None
    assert run["steps"][0]["outcome"] == "local_timeout"
    assert run["cancellation_sent"] == []
    assert run["server_cleanup_events"] == []
    assert run["child_exited"] is True and client.exited(run["pid"])


async def test_external_cancellation_reaches_request_and_child_cleanup(monkeypatch):
    started = anyio.Event()
    checked = []
    original_send, original_exited = client.RecordingWrite.send, client.exited

    async def send(self, item):
        await original_send(self, item)
        if item.message.model_dump().get("method") == "tools/call":
            started.set()

    def exited(pid):
        result = original_exited(pid)
        checked.append((pid, result))
        return result

    monkeypatch.setattr(client.RecordingWrite, "send", send)
    monkeypatch.setattr(client, "exited", exited)
    async with anyio.create_task_group() as group:
        group.start_soon(client.run_case, "timeout", anyio.current_time() + 10)
        with anyio.fail_after(4):
            await started.wait()
        group.cancel_scope.cancel()
    assert len(checked) == 1 and checked[0][0] is not None and checked[0][1] is True


async def test_expired_overall_budget_does_not_spawn(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("must not spawn")

    monkeypatch.setattr(client, "OVERALL_SECONDS", 0)
    monkeypatch.setattr(client, "run_case", forbidden)
    report = await client.run_experiment()
    assert report["passed"] is False and report["runs"] == []


@pytest.mark.parametrize("entry", ["client.py", "server.py"])
async def test_cli_rejects_arbitrary_options_without_echo(entry):
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-B",
        str(ROOT / entry),
        "--command",
        "PRIVATE_TEST_MARKER",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), 3)
    assert proc.returncode == 2 and stdout == b""
    assert stderr == b"invalid_cli_arguments\n"


@pytest.mark.parametrize("query", ["\ud800", "\udfff"])
async def test_sdk_rejects_wire_surrogate_without_business_result_and_connection_recovers(query):
    async with raw_server() as (proc, stderr):
        await rpc(proc, 1, "server/discover")
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                rpc(
                    proc,
                    2,
                    "tools/call",
                    {"name": "knowledge_search", "arguments": {"query": query}},
                ),
                0.15,
            )
        result = await rpc(
            proc, 3, "tools/call", {"name": "knowledge_search", "arguments": {"query": "MCP"}}
        )
        assert result["result"]["structuredContent"]["items"][0]["id"] == "agent-mcp"
        proc.stdin.close()
        await asyncio.wait_for(proc.wait(), 3)
        stderr.seek(0)
        assert [json.loads(line)["event"] for line in stderr] == [
            "server_started",
            "server_cleanup",
        ]


async def test_sdk_protocol_errors_cannot_echo_unknown_methods_or_invalid_envelopes():
    async with raw_server() as (proc, stderr):
        await rpc(proc, 1, "server/discover")
        assert (await rpc(proc, 2, "PRIVATE_TEST_MARKER"))["error"] == {
            "code": -32601,
            "message": "method_not_found",
        }
        result = await rpc(
            proc, 3, "tools/call", {"name": {"PRIVATE_TEST_MARKER": True}, "arguments": {}}
        )
        assert result["error"] == {"code": -32602, "message": "invalid_request_parameters"}
        proc.stdin.close()
        await asyncio.wait_for(proc.wait(), 3)
        stderr.seek(0)
        assert "PRIVATE_TEST_MARKER" not in stderr.read()
