"""Fixed MCP teaching scenarios; never accepts a command, URL, or file path."""

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS
from mcp.shared.exceptions import MCPError
from mcp.types import Implementation

from protocol import (
    CONTRACT_VERSION,
    COURSES,
    LESSON_ID,
    PROTOCOL_VERSION,
    SearchArguments,
    SearchOutput,
)

CASES = ("normal", "errors", "timeout", "disconnect")
DISCOVERY_SECONDS = 3
REQUEST_SECONDS = 2
TIMEOUT_SECONDS = 0.25
OVERALL_SECONDS = 30


def safe_environment(home: str) -> dict[str, str]:
    # The SDK merges defaults with env. Cover every pinned POSIX default explicitly.
    values = {
        "HOME": home,
        "LOGNAME": "mcp-lab",
        "USER": "mcp-lab",
        "PATH": "/usr/bin:/bin",
        "SHELL": "/bin/sh",
        "TERM": "dumb",
    }
    if os.name != "posix" or not set(DEFAULT_INHERITED_ENV_VARS).issubset(values):
        raise ValueError("unsupported_environment")
    return {**values, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}


class RecordingRead:
    def __init__(self, stream, frames):
        self.stream, self.frames = stream, frames
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.stream.aclose()

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            item = await self.stream.receive()
        except anyio.EndOfStream:
            self.closed = True
            raise StopAsyncIteration from None
        if not isinstance(item, Exception):
            self.frames.append(
                {
                    "direction": "in",
                    "message": item.message.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                }
            )
        return item


class RecordingWrite:
    def __init__(self, stream, frames):
        self.stream, self.frames = stream, frames

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.stream.aclose()

    async def send(self, item):
        await self.stream.send(item)
        self.frames.append(
            {
                "direction": "out",
                "message": item.message.model_dump(mode="json", by_alias=True, exclude_none=True),
            }
        )


def public_error(error: MCPError) -> dict:
    known = {-32001: "request_timeout", -32000: "transport_closed"}
    if error.code in known:
        return {"code": error.code, "message": known[error.code]}
    if error.code == -32602 and error.message in ("unknown_tool", "resource_not_found"):
        return {"code": error.code, "message": error.message}
    return {"code": -32603, "message": "request_failed"}


async def observe(
    run: dict, frames: list, name: str, method: str, action, deadline=REQUEST_SECONDS
):
    start = len(frames)
    step = {
        "step": name,
        "method": method,
        "request_id": None,
        "outcome": "protocol_error",
        "result": None,
        "error": None,
    }
    try:
        with anyio.fail_after(deadline):
            result = await action()
        step["result"] = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        step["outcome"] = "tool_error" if getattr(result, "is_error", False) else "protocol_result"
    except MCPError as error:
        step["outcome"] = {-32001: "local_timeout", -32000: "transport_closed"}.get(
            error.code, "protocol_error"
        )
        step["error"] = public_error(error)
    except TimeoutError:
        step["outcome"] = "local_timeout"
        step["error"] = {"code": -32001, "message": "request_timeout"}
    finally:
        for frame in frames[start:]:
            message = frame["message"]
            if (
                frame["direction"] == "out"
                and message.get("method") == method
                and type(message.get("id")) is int
            ):
                step["request_id"] = message["id"]
                break
        run["steps"].append(step)
    return step


def exited(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


async def run_case(case: str, total_deadline: float) -> dict:
    if case not in CASES:
        raise ValueError("invalid_case")
    run = {
        "case": case,
        "protocol_version": None,
        "pid": None,
        "steps": [],
        "cancellation_sent": [],
        "server_cleanup_events": [],
        "transport_closed": False,
        "transport_close_evidence": None,
        "child_exited": False,
    }
    frames = []
    reader = None
    # The pinned SDK shields its bounded close/TERM/KILL/reap in __aexit__.
    # Do not shield the request body: external cancellation must reach it.
    with tempfile.TemporaryDirectory(prefix="mcp-lab-") as temporary:
        home = Path(temporary) / "home"
        home.mkdir()
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-s",
                "-E",
                "-B",
                str(Path(__file__).resolve().with_name("server.py")),
                "--fault",
                case if case in ("timeout", "disconnect") else "none",
            ],
            env=safe_environment(str(home)),
            cwd=Path(__file__).resolve().parent,
        )
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr:
            try:
                async with stdio_client(parameters, errlog=stderr) as (read, write):
                    reader = RecordingRead(read, frames)
                    async with ClientSession(
                        reader,
                        RecordingWrite(write, frames),
                        read_timeout_seconds=REQUEST_SECONDS,
                        client_info=Implementation(
                            name="deep-ai-mcp-lab", version=CONTRACT_VERSION
                        ),
                    ) as session:
                        with anyio.fail_after(max(0, total_deadline - anyio.current_time())):
                            discovery = await observe(
                                run,
                                frames,
                                "discover",
                                "server/discover",
                                session.discover,
                                DISCOVERY_SECONDS,
                            )
                            if discovery["outcome"] == "protocol_result":
                                run["protocol_version"] = session.protocol_version
                            if (
                                discovery["outcome"] != "protocol_result"
                                or run["protocol_version"] != PROTOCOL_VERSION
                            ):
                                return run
                            await observe(
                                run, frames, "list_tools", "tools/list", session.list_tools
                            )
                            if case == "normal":
                                await observe(
                                    run,
                                    frames,
                                    "list_resources",
                                    "resources/list",
                                    session.list_resources,
                                )
                                await observe(
                                    run,
                                    frames,
                                    "search",
                                    "tools/call",
                                    lambda: session.call_tool(
                                        "knowledge_search", {"query": " MCP "}
                                    ),
                                )
                                await observe(
                                    run,
                                    frames,
                                    "read_resource",
                                    "resources/read",
                                    lambda: session.read_resource("course://agent-mcp"),
                                )
                                await observe(
                                    run,
                                    frames,
                                    "empty_search",
                                    "tools/call",
                                    lambda: session.call_tool(
                                        "knowledge_search", {"query": "no-match-fixture"}
                                    ),
                                )
                            elif case == "errors":
                                await observe(
                                    run,
                                    frames,
                                    "unknown_tool",
                                    "tools/call",
                                    lambda: session.call_tool("missing_tool", {}),
                                )
                                await observe(
                                    run,
                                    frames,
                                    "unknown_resource",
                                    "resources/read",
                                    lambda: session.read_resource("course://missing"),
                                )
                                await observe(
                                    run,
                                    frames,
                                    "invalid_arguments",
                                    "tools/call",
                                    lambda: session.call_tool(
                                        "knowledge_search", {"query": "MCP", "limit": True}
                                    ),
                                )
                            elif case == "timeout":
                                await observe(
                                    run,
                                    frames,
                                    "timeout_search",
                                    "tools/call",
                                    lambda: session.call_tool(
                                        "knowledge_search",
                                        {"query": "MCP"},
                                        read_timeout_seconds=TIMEOUT_SECONDS,
                                    ),
                                )
                                await observe(
                                    run,
                                    frames,
                                    "recovery_search",
                                    "tools/call",
                                    lambda: session.call_tool("knowledge_search", {"query": "MCP"}),
                                )
                            else:
                                await observe(
                                    run,
                                    frames,
                                    "disconnected_search",
                                    "tools/call",
                                    lambda: session.call_tool("knowledge_search", {"query": "MCP"}),
                                )
            except Exception:
                # The caller's report stays useful on infrastructure failure without leaking diagnostics.
                run["steps"].append(
                    {
                        "step": "session",
                        "method": "local",
                        "request_id": None,
                        "outcome": "protocol_error",
                        "result": None,
                        "error": {"code": -32603, "message": "experiment_failed"},
                    }
                )
            finally:
                stderr.seek(0)
                for line in stderr:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if (
                        set(item) != {"event", "pid"}
                        or type(item["pid"]) is not int
                        or item["pid"] <= 0
                    ):
                        continue
                    if item["event"] == "server_started":
                        run["pid"] = item["pid"]
                    elif item["event"] in ("request_cleanup", "server_cleanup"):
                        run["server_cleanup_events"].append(item)
                if reader and reader.closed:
                    run["transport_close_evidence"] = "read_eof"
                elif any(
                    step["outcome"] == "transport_closed" and step["error"]["code"] == -32000
                    for step in run["steps"]
                ):
                    run["transport_close_evidence"] = "sdk_connection_closed"
                run["transport_closed"] = run["transport_close_evidence"] is not None
                run["child_exited"] = exited(run["pid"])
                run["cancellation_sent"] = [
                    {"request_id": frame["message"]["params"]["requestId"]}
                    for frame in frames
                    if frame["direction"] == "out"
                    and frame["message"].get("method") == "notifications/cancelled"
                ]
    return run


def passed(run: dict) -> bool:
    if (
        run["protocol_version"] != PROTOCOL_VERSION
        or not run["child_exited"]
        or any(step["request_id"] is None for step in run["steps"])
    ):
        return False
    expected = {
        "normal": ["protocol_result"] * 6,
        "errors": [
            "protocol_result",
            "protocol_result",
            "protocol_error",
            "protocol_error",
            "tool_error",
        ],
        "timeout": ["protocol_result", "protocol_result", "local_timeout", "protocol_result"],
        "disconnect": ["protocol_result", "protocol_result", "transport_closed"],
    }
    if [step["outcome"] for step in run["steps"]] != expected[run["case"]]:
        return False
    events = run["server_cleanup_events"]
    if sum(e["event"] == "server_cleanup" and e["pid"] == run["pid"] for e in events) != 1:
        return False
    steps = run["steps"]
    if PROTOCOL_VERSION not in steps[0]["result"].get("supportedVersions", []):
        return False
    tools = steps[1]["result"].get("tools", [])
    if (
        len(tools) != 1
        or tools[0]["name"] != "knowledge_search"
        or tools[0].get("outputSchema") != SearchOutput.model_json_schema()
        or tools[0].get("inputSchema") != SearchArguments.contract_schema()
        or tools[0].get("annotations")
        != {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    ):
        return False
    for step in steps:
        if step["method"] == "tools/call" and step["outcome"] == "protocol_result":
            value = SearchOutput.model_validate(step["result"].get("structuredContent"))
            if json.loads(step["result"]["content"][0]["text"]) != value.model_dump(mode="json"):
                return False
    expected_items = [{key: COURSES[0][key] for key in ("id", "title", "uri", "summary")}]
    case = run["case"]
    if case == "normal":
        return (
            [r["uri"] for r in steps[2]["result"]["resources"]] == [c["uri"] for c in COURSES]
            and steps[3]["result"]["structuredContent"]["items"] == expected_items
            and steps[4]["result"]["contents"]
            == [{"uri": COURSES[0]["uri"], "mimeType": "text/plain", "text": COURSES[0]["text"]}]
            and steps[5]["result"]["structuredContent"]["items"] == []
            and not run["cancellation_sent"]
        )
    if case == "errors":
        return (
            steps[2]["error"] == {"code": -32602, "message": "unknown_tool"}
            and steps[3]["error"] == {"code": -32602, "message": "resource_not_found"}
            and steps[4]["result"]["content"] == [{"type": "text", "text": "invalid_arguments"}]
        )
    cleaned = sum(e["event"] == "request_cleanup" and e["pid"] == run["pid"] for e in events)
    if case == "timeout":
        return (
            cleaned == 1
            and run["cancellation_sent"] == [{"request_id": steps[2]["request_id"]}]
            and steps[2]["error"]["code"] == -32001
            and steps[3]["result"]["structuredContent"]["items"] == expected_items
        )
    return cleaned == 1 and run["transport_closed"] and steps[2]["error"]["code"] == -32000


async def run_experiment(case: str = "all") -> dict:
    if case not in (*CASES, "all"):
        raise ValueError("invalid_case")
    report = {
        "contract_version": CONTRACT_VERSION,
        "lesson_id": LESSON_ID,
        "protocol_version": None,
        "case": case,
        "model_calls": 0,
        "passed": False,
        "runs": [],
    }
    deadline = anyio.current_time() + OVERALL_SECONDS
    try:
        for selected in CASES if case == "all" else (case,):
            if anyio.current_time() >= deadline:
                return report
            report["runs"].append(await run_case(selected, deadline))
        versions = {run["protocol_version"] for run in report["runs"]}
        report["protocol_version"] = versions.pop() if len(versions) == 1 else None
        report["passed"] = all(passed(run) for run in report["runs"])
    except Exception:
        report["passed"] = False
    return report


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "invalid_cli_arguments\n")


def main() -> int:
    parser = SafeParser(description="Run fixed local MCP teaching scenarios")
    parser.add_argument("--case", choices=(*CASES, "all"), default="all")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    report = anyio.run(run_experiment, args.case)
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
