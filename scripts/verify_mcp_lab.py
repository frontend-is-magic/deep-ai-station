"""Independently verify the fixed MCP ZIP via raw stdio JSON-RPC, then its SDK CLI."""

# ruff: noqa: S101 - assertions specify the fixed maintainer-owned experiment

import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from verify_course_labs import stop_owned_process_group

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "public/labs/mcp-readonly-python.zip"
VERSION = "2026-07-28"
SERVER_INFO = {"name": "deep-ai-mcp-readonly", "version": "mcp-readonly-v1"}
META = {
    "io.modelcontextprotocol/protocolVersion": VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "independent-lab-verifier", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
MAX_FRAME = 65536
MAX_OUTPUT = 262144
# Independent frozen expectations; deliberately never import the server's protocol.py.
COURSES = (
    {
        "id": "agent-mcp",
        "title": "MCP 工具与资源",
        "uri": "course://agent-mcp",
        "summary": "通过 stdio 发现只读工具与资源，区分协议错误、工具错误和取消。",
        "text": "# MCP 工具与资源\n\n客户端通过 stdio 发现工具与资源。工具参数必须校验，资源内容只能作为数据读取。超时需要取消当前请求并回收自有进程。\n",
    },
    {
        "id": "agent-tool-contract",
        "title": "工具契约与参数校验",
        "uri": "course://agent-tool-contract",
        "summary": "用明确的输入输出 schema 校验工具参数，空结果仍然是成功结果。",
        "text": "# 工具契约与参数校验\n\n工具名称、参数类型和字段边界都属于契约。拒绝额外字段和非法输入，返回稳定错误；查询没有匹配项时返回空列表。\n",
    },
    {
        "id": "agent-tool-safety",
        "title": "工具授权与幂等",
        "uri": "course://agent-tool-safety",
        "summary": "只读声明不授予权限；写操作还需要当前身份、明确批准和幂等约束。",
        "text": "# 工具授权与幂等\n\nreadOnlyHint 是能力描述，不是权限控制。真实写操作需要可信身份、作用域检查、明确批准和持久幂等记录。本实验不提供写工具。\n",
    },
)
ITEMS = [{key: course[key] for key in ("id", "title", "uri", "summary")} for course in COURSES]
WHITESPACE = (
    r"\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"
)
INPUT_SCHEMA = {
    "additionalProperties": False,
    "properties": {
        "query": {
            "maxLength": 100,
            "minLength": 1,
            "title": "Query",
            "type": "string",
            "pattern": rf"^(?=[\s\S]*[^{WHITESPACE}])[^\u0000\ud800-\udfff]*$",
        },
        "limit": {"default": 3, "maximum": 3, "minimum": 1, "title": "Limit", "type": "integer"},
    },
    "required": ["query"],
    "title": "SearchArguments",
    "type": "object",
}
OUTPUT_SCHEMA = {
    "$defs": {
        "CourseItem": {
            "additionalProperties": False,
            "properties": {
                "id": {
                    "enum": [course["id"] for course in COURSES],
                    "title": "Id",
                    "type": "string",
                },
                "title": {"title": "Title", "type": "string"},
                "uri": {"title": "Uri", "type": "string"},
                "summary": {"title": "Summary", "type": "string"},
            },
            "required": ["id", "title", "uri", "summary"],
            "title": "CourseItem",
            "type": "object",
        }
    },
    "additionalProperties": False,
    "properties": {
        "items": {
            "items": {"$ref": "#/$defs/CourseItem"},
            "maxItems": 3,
            "title": "Items",
            "type": "array",
        },
        "read_only": {"const": True, "default": True, "title": "Read Only", "type": "boolean"},
    },
    "required": ["items"],
    "title": "SearchOutput",
    "type": "object",
}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AssertionError("Duplicate JSON field in protocol evidence")
        result[key] = value
    return result


def reject_constant(_constant):
    raise AssertionError("Nonfinite JSON constant in protocol evidence")


def decode_json(raw):
    return json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )


def assert_json(actual, expected):
    # Python equality equates True/1 and False/0; the wire contract does not.
    options = {
        "ensure_ascii": False,
        "sort_keys": True,
        "allow_nan": False,
        "separators": (",", ":"),
    }
    assert json.dumps(actual, **options) == json.dumps(expected, **options)


class RawPeer:
    """Own one fixed server process and bound every pipe frame, output and wait."""

    def __init__(self, python, folder, env, *, fault="none", overall_seconds=15):
        assert fault in {"none", "timeout", "disconnect"}
        self.process = subprocess.Popen(  # noqa: S603 - fixed package entry, no external command input
            [str(python), "-s", "-E", "-B", str(folder / "server.py"), "--fault", fault],
            cwd=folder,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )
        self.deadline = time.monotonic() + overall_seconds
        self.selector = selectors.DefaultSelector()
        self.buffers = {"stdout": b"", "stderr": b""}
        self.closed = set()
        self.frames, self.events = [], []
        self.seen_ids = set()
        self.total = self.sequence = 0
        self.selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _error, _traceback):
        try:
            if exc_type is None:
                self.finish()
        finally:
            try:
                # The fixed raw server creates no child processes. Reap an already
                # exited leader before signalling: macOS can reject killpg on its zombie.
                if self.process.poll() is None:
                    try:
                        stop_owned_process_group(self.process)
                    except PermissionError:
                        if self.process.poll() is None:
                            raise
                self.process.wait(timeout=1)
            finally:
                for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                    pipe.close()
                self.selector.close()
        return False

    def _line(self, name, raw):
        assert len(raw) <= MAX_FRAME, "Oversized MCP line"
        value = decode_json(raw)
        assert isinstance(value, dict), "Expected object on the protocol/event channel"
        if name == "stderr":
            assert set(value) == {"event", "pid"}
            assert value["event"] in {
                "server_started",
                "request_started",
                "request_cleanup",
                "server_cleanup",
            }
            assert type(value["pid"]) is int and value["pid"] == self.process.pid
            self.events.append(value)
            return
        assert value.get("jsonrpc") == "2.0"
        assert set(value) in ({"jsonrpc", "id", "result"}, {"jsonrpc", "id", "error"})
        assert type(value["id"]) is int and 0 < value["id"] <= self.sequence
        assert value["id"] not in self.seen_ids, "Duplicate response ID"
        self.seen_ids.add(value["id"])
        self.frames.append(value)

    def _pump(self, wait):
        for key, _mask in self.selector.select(timeout=max(0, min(wait, 0.1))):
            name = key.data
            chunk = os.read(key.fileobj.fileno(), 8192)
            if not chunk:
                assert not self.buffers[name], "EOF with an incomplete JSON line"
                self.closed.add(name)
                self.selector.unregister(key.fileobj)
                continue
            self.total += len(chunk)
            assert self.total <= MAX_OUTPUT, "MCP process exceeded the total output bound"
            pending = self.buffers[name] + chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                assert line, "Blank/non-protocol line on a private pipe"
                self._line(name, line)
            assert len(pending) <= MAX_FRAME, "Oversized unterminated MCP line"
            self.buffers[name] = pending

    def wait(self, condition, *, seconds=3):
        deadline = min(self.deadline, time.monotonic() + seconds)
        while not condition():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Bounded raw MCP observation timed out")
            self._pump(remaining)

    def _send(self, value):
        data = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
        assert len(data) <= 4096 and time.monotonic() < self.deadline
        assert self.process.stdin.write(data) == len(data)

    def send(self, method, params=None):
        self.sequence += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": self.sequence,
                "method": method,
                "params": {**(params or {}), "_meta": META},
            }
        )
        return self.sequence

    def response(self, request_id):
        self.wait(lambda: bool(self.frames) or "stdout" in self.closed)
        assert self.frames, "Transport closed before its expected response"
        frame = self.frames.pop(0)
        assert frame["id"] == request_id, "Response does not belong to the current request"
        return frame

    def request(self, method, params=None):
        return self.response(self.send(method, params))

    def event_count(self, name):
        return sum(event["event"] == name for event in self.events)

    def wait_event(self, name):
        self.wait(lambda: self.event_count(name) > 0 or "stderr" in self.closed)
        assert self.event_count(name) == 1, f"Missing or duplicate {name} event"

    def observe_silence(self, *, seconds=0.15):
        deadline = min(self.deadline, time.monotonic() + seconds)
        while time.monotonic() < deadline:
            self._pump(deadline - time.monotonic())
            assert not self.frames, "Invalid wire message unexpectedly produced a business result"
            assert "stdout" not in self.closed, "Invalid wire message closed the reusable transport"

    def cancel(self, request_id):
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": request_id, "reason": "fixed verifier deadline"},
            }
        )

    def finish(self):
        self.process.stdin.close()
        self.wait(lambda: self.closed == {"stdout", "stderr"} and self.process.poll() is not None)
        assert self.process.wait(timeout=0.5) == 0
        assert self.event_count("server_started") == 1
        assert self.event_count("server_cleanup") == 1
        assert not self.frames, "Unexpected or late response remained at process exit"


def protocol_result(frame):
    assert "error" not in frame
    result = frame["result"]
    assert result["resultType"] == "complete"
    assert result["_meta"] == {"io.modelcontextprotocol/serverInfo": SERVER_INFO}
    return result


def discover(peer):
    result = protocol_result(peer.request("server/discover"))
    assert result["supportedVersions"] == [VERSION]
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["capabilities"]["resources"] == {"listChanged": False, "subscribe": False}
    assert result["cacheScope"] == "private" and result["ttlMs"] == 0
    peer.wait_event("server_started")


def tool_success(frame, expected_items):
    result = protocol_result(frame)
    expected = {"items": expected_items, "read_only": True}
    assert result["isError"] is False
    assert_json(result["structuredContent"], expected)
    assert len(result["content"]) == 1 and set(result["content"][0]) == {"type", "text"}
    content = result["content"][0]
    assert content["type"] == "text"
    actual = decode_json(content["text"].encode("utf-8", errors="strict"))
    assert_json(actual, expected)
    # Preserve the contract's compact UTF-8 form without imposing object key order.
    assert content["text"] == json.dumps(actual, ensure_ascii=False, separators=(",", ":"))


def tool_error(frame):
    result = protocol_result(frame)
    assert result["isError"] is True
    assert "structuredContent" not in result
    assert result["content"] == [{"type": "text", "text": "invalid_arguments"}]


def protocol_error(frame, message):
    assert "result" not in frame
    assert frame["error"] == {"code": -32602, "message": message}


def raw_normal(python, folder, env):
    with RawPeer(python, folder, env) as peer:
        discover(peer)
        listed = protocol_result(peer.request("tools/list"))
        assert len(listed["tools"]) == 1
        tool = listed["tools"][0]
        assert tool["name"] == "knowledge_search"
        assert_json(tool["inputSchema"], INPUT_SCHEMA)
        assert_json(tool["outputSchema"], OUTPUT_SCHEMA)
        assert tool["annotations"] == {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
        resources = protocol_result(peer.request("resources/list"))["resources"]
        assert resources == [
            {"name": course["title"], "uri": course["uri"], "mimeType": "text/plain"}
            for course in COURSES
        ]
        digests = {}
        for course in COURSES:
            result = protocol_result(peer.request("resources/read", {"uri": course["uri"]}))
            assert len(result["contents"]) == 1
            resource = result["contents"][0]
            assert resource["uri"] == course["uri"] and resource["mimeType"] == "text/plain"
            actual = resource["text"].encode("utf-8", errors="strict")
            assert actual == course["text"].encode("utf-8")
            digests[course["uri"]] = hashlib.sha256(actual).hexdigest()
        for arguments, expected in (
            ({"query": "MCP"}, [ITEMS[0]]),
            ({"query": "agent-", "limit": 2}, ITEMS[:2]),
            ({"query": "\u3000AgEnT-\u3000"}, ITEMS),
            ({"query": "no-match-fixture"}, []),
            ({"query": "\ufeff"}, []),
        ):
            tool_success(
                peer.request("tools/call", {"name": "knowledge_search", "arguments": arguments}),
                expected,
            )
        for arguments in (
            {},
            {"query": 42},
            {"query": True},
            {"query": ""},
            {"query": " \t\u3000"},
            {"query": "a" * 101},
            {"query": "a\u0000b"},
            {"query": "MCP", "extra": True},
            {"query": "MCP", "limit": True},
            {"query": "MCP", "limit": 1.0},
            {"query": "MCP", "limit": 0},
            {"query": "MCP", "limit": 4},
            {"query": "MCP", "limit": "2"},
        ):
            tool_error(
                peer.request("tools/call", {"name": "knowledge_search", "arguments": arguments})
            )
        # The official stdio decoder rejects an escaped lone surrogate before
        # the tool handler. Verify bounded silence and a real next response instead
        # of misclassifying a transport parse rejection as a business error.
        invalid_id = peer.send(
            "tools/call", {"name": "knowledge_search", "arguments": {"query": "\ud800"}}
        )
        peer.observe_silence()
        tool_success(
            peer.request("tools/call", {"name": "knowledge_search", "arguments": {"query": "MCP"}}),
            [ITEMS[0]],
        )
        assert invalid_id not in peer.seen_ids
        protocol_error(
            peer.request("tools/call", {"name": "missing_tool", "arguments": {}}), "unknown_tool"
        )
        protocol_error(
            peer.request("resources/read", {"uri": "course://missing"}), "resource_not_found"
        )
        # Exercise SDK-level validation before the business handler. Its original
        # error.data echoed this sentinel; the package's output guard must remove it.
        unknown = peer.request("private-input-fixture")
        assert unknown["error"] == {"code": -32601, "message": "method_not_found"}
        assert "private-input-fixture" not in json.dumps(unknown)
        for arguments in (["private-input-fixture"], "private-input-fixture"):
            protocol_error(
                peer.request("tools/call", {"name": "knowledge_search", "arguments": arguments}),
                "invalid_request_parameters",
            )
        tool_success(
            peer.request("tools/call", {"name": "knowledge_search", "arguments": {"query": "MCP"}}),
            [ITEMS[0]],
        )
        count = peer.sequence
    return {
        "raw_requests": count,
        "resource_sha256": digests,
        "raw_normal_and_errors": "passed",
        "raw_sdk_error_redaction_then_reuse": "passed",
        "raw_unicode_wire_rejection_then_reuse": "passed",
    }


def raw_cancel(python, folder, env):
    with RawPeer(python, folder, env, fault="timeout") as peer:
        discover(peer)
        request_id = peer.send(
            "tools/call", {"name": "knowledge_search", "arguments": {"query": "MCP"}}
        )
        peer.wait_event("request_started")
        assert not peer.frames, "Blocked fixture unexpectedly produced a response"
        peer.cancel(request_id)
        peer.wait_event("request_cleanup")
        tool_success(
            peer.request("tools/call", {"name": "knowledge_search", "arguments": {"query": "MCP"}}),
            [ITEMS[0]],
        )
        assert request_id not in peer.seen_ids, "Cancelled request sent a late result"
    assert peer.event_count("request_cleanup") == 1
    assert request_id not in peer.seen_ids
    return {
        "raw_requests": peer.sequence,
        "raw_cancel_request_id": request_id,
        "raw_cancel_cleanup_then_reuse": "passed",
    }


def raw_disconnect(python, folder, env):
    with RawPeer(python, folder, env, fault="disconnect") as peer:
        discover(peer)
        request_id = peer.send(
            "tools/call", {"name": "knowledge_search", "arguments": {"query": "MCP"}}
        )
        peer.wait_event("request_started")
        # Keep our input pipe open: the server itself must end its transport.
        # The dedicated disconnect fixture exits after its real cleanup callbacks;
        # its SDK terminal frame may or may not flush before the actual process exit.
        assert not peer.process.stdin.closed
        peer.wait(lambda: peer.closed == {"stdout", "stderr"} and peer.process.poll() is not None)
        assert not peer.process.stdin.closed
        terminal_frame_observed = bool(peer.frames)
        if terminal_frame_observed:
            assert len(peer.frames) == 1
            assert peer.frames.pop() == {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": "transport_closed"},
            }
        assert peer.process.wait(timeout=0.5) == 0
        assert peer.event_count("request_cleanup") == 1
    return {
        "raw_requests": peer.sequence,
        "raw_disconnect_eof_and_reap": "passed",
        "raw_disconnect_terminal_frame_observed": terminal_frame_observed,
    }


def clean_environment(folder):
    home = folder / "home"
    home.mkdir()
    runtime = {"PATH": os.defpath, "HOME": str(home), "LANG": "C.UTF-8"}
    install = {
        key: os.environ[key]
        for key in ("PATH", "UV_CACHE_DIR", "TMPDIR", "CI")
        if key in os.environ
    }
    return {**runtime, **install}, runtime


def owned_command(args, folder, env, *, timeout=600):
    process = subprocess.Popen(args, cwd=folder, env=env, start_new_session=True)  # noqa: S603 - fixed commands
    try:
        if process.wait(timeout=timeout):
            raise RuntimeError("Frozen MCP lab command failed")
    finally:
        stop_owned_process_group(process)


def capture_cli(python, folder, env):
    process = subprocess.Popen(  # noqa: S603 - fixed package CLI
        [str(python), "-s", "-E", "-B", "client.py", "--case", "all"],
        cwd=folder,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    output = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        deadline = time.monotonic() + 60
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("The fixed CLI exceeded its overall observation deadline")
            for key, _mask in selector.select(timeout=min(remaining, 0.1)):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                output[key.data].extend(data)
                assert sum(map(len, output.values())) <= MAX_OUTPUT, "CLI output exceeded its bound"
        assert process.wait(timeout=3) == 0, "CLI did not validate all four scenarios"
        assert not output["stderr"], "CLI emitted unexpected diagnostics"
        raw = bytes(output["stdout"])
        assert raw.endswith(b"\n") and raw.count(b"\n") == 1
        return decode_json(raw)
    finally:
        try:
            if process.poll() is None:
                # SIGINT lets anyio unwind the SDK context and reap its separate server group.
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    pass
            stop_owned_process_group(process)
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()


def validate_cli(report):
    assert set(report) == {
        "contract_version",
        "lesson_id",
        "protocol_version",
        "case",
        "model_calls",
        "passed",
        "runs",
    }
    assert report["contract_version"] == "mcp-readonly-v1" and report["lesson_id"] == "agent-mcp"
    assert report["protocol_version"] == VERSION and report["case"] == "all"
    assert report["model_calls"] == 0 and type(report["model_calls"]) is int
    assert report["passed"] is True
    assert [run["case"] for run in report["runs"]] == ["normal", "errors", "timeout", "disconnect"]
    pids = set()
    for run in report["runs"]:
        assert set(run) == {
            "case",
            "protocol_version",
            "pid",
            "steps",
            "cancellation_sent",
            "server_cleanup_events",
            "transport_closed",
            "transport_close_evidence",
            "child_exited",
        }
        assert run["protocol_version"] == VERSION
        pid = run["pid"]
        assert type(pid) is int and pid > 0 and pid not in pids
        pids.add(pid)
        assert run["child_exited"] is True
        assert run["transport_close_evidence"] in (None, "read_eof", "sdk_connection_closed")
        assert run["transport_closed"] is (run["transport_close_evidence"] is not None)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("A CLI-reported server process still exists")
        assert all(event["pid"] == pid for event in run["server_cleanup_events"])
        names = [event["event"] for event in run["server_cleanup_events"]]
        assert names.count("server_cleanup") == 1
        request_ids = []
        for step in run["steps"]:
            assert set(step) == {"step", "method", "request_id", "outcome", "result", "error"}
            assert type(step["request_id"]) is int
            request_ids.append(step["request_id"])
            if step["outcome"] in {"protocol_result", "tool_error"}:
                assert type(step["result"]) is dict and step["error"] is None
            else:
                assert step["result"] is None and set(step["error"]) == {"code", "message"}
        assert len(request_ids) == len(set(request_ids))
        outcomes = [step["outcome"] for step in run["steps"]]
        expected_steps = {
            "normal": [
                ("discover", "server/discover", "protocol_result"),
                ("list_tools", "tools/list", "protocol_result"),
                ("list_resources", "resources/list", "protocol_result"),
                ("search", "tools/call", "protocol_result"),
                ("read_resource", "resources/read", "protocol_result"),
                ("empty_search", "tools/call", "protocol_result"),
            ],
            "errors": [
                ("discover", "server/discover", "protocol_result"),
                ("list_tools", "tools/list", "protocol_result"),
                ("unknown_tool", "tools/call", "protocol_error"),
                ("unknown_resource", "resources/read", "protocol_error"),
                ("invalid_arguments", "tools/call", "tool_error"),
            ],
            "timeout": [
                ("discover", "server/discover", "protocol_result"),
                ("list_tools", "tools/list", "protocol_result"),
                ("timeout_search", "tools/call", "local_timeout"),
                ("recovery_search", "tools/call", "protocol_result"),
            ],
            "disconnect": [
                ("discover", "server/discover", "protocol_result"),
                ("list_tools", "tools/list", "protocol_result"),
                ("disconnected_search", "tools/call", "transport_closed"),
            ],
        }
        assert [
            (step["step"], step["method"], step["outcome"]) for step in run["steps"]
        ] == expected_steps[run["case"]]
        discovered = protocol_result({"result": run["steps"][0]["result"]})
        assert discovered["supportedVersions"] == [VERSION]
        tools = protocol_result({"result": run["steps"][1]["result"]})["tools"]
        assert len(tools) == 1 and tools[0]["name"] == "knowledge_search"
        assert_json(tools[0]["inputSchema"], INPUT_SCHEMA)
        assert_json(tools[0]["outputSchema"], OUTPUT_SCHEMA)
        if run["transport_close_evidence"] == "sdk_connection_closed":
            assert "transport_closed" in outcomes
        if run["case"] == "normal":
            assert set(outcomes) == {"protocol_result"}
            assert not run["cancellation_sent"] and names.count("request_cleanup") == 0
            resources = protocol_result({"result": run["steps"][2]["result"]})["resources"]
            assert resources == [
                {"name": course["title"], "uri": course["uri"], "mimeType": "text/plain"}
                for course in COURSES
            ]
            tool_success({"result": run["steps"][3]["result"]}, [ITEMS[0]])
            assert run["steps"][4]["result"]["contents"] == [
                {"uri": COURSES[0]["uri"], "mimeType": "text/plain", "text": COURSES[0]["text"]}
            ]
            tool_success({"result": run["steps"][5]["result"]}, [])
        elif run["case"] == "errors":
            assert "tool_error" in outcomes and "protocol_error" in outcomes
            assert not run["cancellation_sent"] and names.count("request_cleanup") == 0
            for step in run["steps"]:
                if step["outcome"] == "tool_error":
                    tool_error({"result": step["result"]})
                if step["outcome"] == "protocol_error":
                    assert step["error"] in (
                        {"code": -32602, "message": "unknown_tool"},
                        {"code": -32602, "message": "resource_not_found"},
                    )
        elif run["case"] == "timeout":
            timed_out = [step for step in run["steps"] if step["outcome"] == "local_timeout"]
            assert len(timed_out) == 1 and timed_out[0]["error"]["code"] == -32001
            assert run["cancellation_sent"] == [{"request_id": timed_out[0]["request_id"]}]
            assert names.count("request_cleanup") == 1
            assert run["steps"][-1]["outcome"] == "protocol_result"
            tool_success({"result": run["steps"][-1]["result"]}, [ITEMS[0]])
        else:
            assert outcomes[-1] == "transport_closed" and run["transport_closed"] is True
            assert run["steps"][-1]["error"] == {"code": -32000, "message": "transport_closed"}
            assert names.count("request_cleanup") == 1
            assert not run["cancellation_sent"]
    return {"sdk_cli_cases": 4, "sdk_cli_servers_observed_exited": len(pids)}


def verify():
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required on PATH")
    temp_root = "/private/tmp" if Path("/private/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="deep-ai-mcp-verify-", dir=temp_root) as temporary:
        folder = Path(temporary).resolve()
        assert not folder.is_relative_to(ROOT)
        with zipfile.ZipFile(ARCHIVE) as archive:
            archive.extractall(folder)
        install_env, runtime_env = clean_environment(folder)
        owned_command([uv, "sync", "--locked", "--python", "3.12"], folder, install_env)
        python = folder / ".venv/bin/python"
        for args in (("ruff", "check", "."), ("ruff", "format", "--check", "."), ("pytest", "-q")):
            owned_command([uv, "run", "--frozen", *args], folder, install_env)
        result = {"raw_requests": 0}
        for check in (raw_normal, raw_cancel, raw_disconnect):
            checked = check(python, folder, runtime_env)
            result["raw_requests"] += checked.pop("raw_requests")
            result.update(checked)
        result.update(validate_cli(capture_cli(python, folder, runtime_env)))
    print(
        json.dumps(
            {
                "lab": "mcp-readonly-v1",
                "protocol_version": VERSION,
                "archive": "verified",
                "native_checks": "passed",
                **result,
                "raw_servers_reaped": 3,
                "model_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    verify()
