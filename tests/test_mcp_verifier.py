"""Real child processes prove bounded raw-wire checks reject false protocol evidence."""

import importlib
import os
import signal
import sys
import time
from functools import partial
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = r"""
import json
import os
import signal
import signal
import sys
import time

if MODE == "uncooperative":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
print(json.dumps({"event": "server_started", "pid": os.getpid() + (MODE == "wrong_pid")}), file=sys.stderr, flush=True)
if MODE == "invalid_stdout":
    print("not a JSON-RPC frame", flush=True)
elif MODE == "incomplete":
    os.write(1, b'{"jsonrpc":"2.0","id":1')
    sys.exit(0)
elif MODE == "oversized":
    os.write(1, b'x' * 70000)
for line in sys.stdin:
    request = json.loads(line)
    if MODE == "uncooperative":
        while True:
            time.sleep(1)
    if MODE == "cancel_ignored":
        if request.get("method") == "tools/call":
            print(json.dumps({"event": "request_started", "pid": os.getpid()}), file=sys.stderr, flush=True)
        continue
    if "id" not in request:
        continue
    frame = {"jsonrpc": "2.0", "id": request["id"] + (MODE == "wrong_id"), "result": {"fixture": True}}
    wire = json.dumps(frame).encode() + b'\n'
    for start, end in ((0, 5), (5, 17), (17, len(wire))):
        os.write(1, wire[start:end])
print(json.dumps({"event": "server_cleanup", "pid": os.getpid()}), file=sys.stderr, flush=True)
"""


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    module = importlib.import_module("verify_mcp_lab")
    # This fixed helper deliberately ignores TERM; make its bounded kill phase fast.
    monkeypatch.setattr(
        module,
        "stop_owned_process_group",
        partial(module.stop_owned_process_group, grace_seconds=0.1, kill_grace_seconds=1),
    )
    return module


def peer_fixture(verifier, tmp_path, mode, *, deadline=2):
    assert mode in {
        "normal",
        "invalid_stdout",
        "incomplete",
        "oversized",
        "wrong_pid",
        "wrong_id",
        "uncooperative",
        "cancel_ignored",
    }
    (tmp_path / "server.py").write_text("MODE = " + repr(mode) + "\n" + HELPER)
    return verifier.RawPeer(
        Path(sys.executable),
        tmp_path,
        {"PATH": os.defpath, "HOME": str(tmp_path)},
        overall_seconds=deadline,
    )


def assert_reaped(peer):
    assert peer.process.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(peer.process.pid, 0)


def test_fragmented_protocol_and_stderr_lifecycle_are_independently_observed(verifier, tmp_path):
    peer = peer_fixture(verifier, tmp_path, "normal")
    with peer:
        assert peer.request("server/discover")["result"] == {"fixture": True}
        assert peer.request("tools/list")["id"] == 2
    assert peer.closed == {"stdout", "stderr"}
    assert peer.event_count("server_started") == peer.event_count("server_cleanup") == 1
    assert_reaped(peer)


@pytest.mark.parametrize(
    "mode", ["invalid_stdout", "incomplete", "oversized", "wrong_pid", "wrong_id"]
)
def test_invalid_wire_or_pid_evidence_fails_and_reaps_only_owned_child(verifier, tmp_path, mode):
    peer = peer_fixture(verifier, tmp_path, mode)
    with pytest.raises((AssertionError, ValueError)):
        with peer:
            peer.request("server/discover")
    assert_reaped(peer)


def test_stalled_uncooperative_process_has_bounded_failure_and_is_reaped(verifier, tmp_path):
    start = time.monotonic()
    peer = peer_fixture(verifier, tmp_path, "uncooperative", deadline=0.3)
    with pytest.raises(TimeoutError, match="Bounded raw MCP observation"):
        with peer:
            peer.request("server/discover")
    assert time.monotonic() - start < 3
    assert peer.event_count("server_started") == 1
    assert peer.process.returncode == -signal.SIGKILL
    assert_reaped(peer)


def test_sending_cancel_without_server_cleanup_is_not_accepted_as_cancellation(verifier, tmp_path):
    peer = peer_fixture(verifier, tmp_path, "cancel_ignored", deadline=0.3)
    with pytest.raises(TimeoutError, match="Bounded raw MCP observation"):
        with peer:
            request_id = peer.send(
                "tools/call", {"name": "knowledge_search", "arguments": {"query": "MCP"}}
            )
            peer.wait_event("request_started")
            peer.cancel(request_id)
            peer.wait_event("request_cleanup")
    assert peer.event_count("request_cleanup") == 0
    assert_reaped(peer)
