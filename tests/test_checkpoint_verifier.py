"""Real bounded subprocesses prevent CLI flags/closed pipes from faking process exit."""

import importlib
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = r"""
import json
import os
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
if MODE == "wrong_pid":
    print(json.dumps({"event":"fault_reached", "fault":"pause-before-commit", "node":"draft", "pid":os.getpid()+1}), file=__import__("sys").stderr, flush=True)
elif MODE == "duplicate":
    print('{"ok":true,"ok":false}', flush=True)
elif MODE == "oversized":
    os.write(1, b'x' * 300000)
else:
    print('{"ok":true}', flush=True)
if MODE == "nonzero":
    raise SystemExit(1)
if MODE == "duplicate":
    raise SystemExit(0)
if MODE == "closed_pipes":
    os.close(1)
    os.close(2)
while True:
    time.sleep(0.1)
"""


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("verify_checkpoint_lab")


def child_fixture(verifier, tmp_path, mode):
    assert mode in {"stalled", "closed_pipes", "wrong_pid", "nonzero", "duplicate", "oversized"}
    helper = tmp_path / "fixed_child.py"
    helper.write_text("MODE = " + repr(mode) + "\n" + HELPER)
    return verifier.Child(
        [sys.executable, "-s", "-E", "-B", str(helper)],
        tmp_path,
        {"PATH": os.defpath, "HOME": str(tmp_path)},
        timeout=0.5,
        grace=0.05,
    )


def assert_reaped(child):
    assert child.reaped and child.process.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(child.process.pid, 0)


@pytest.mark.parametrize("mode", ["stalled", "closed_pipes"])
def test_claimed_success_or_pipe_eof_without_exit_fails_then_kills_and_reaps(
    verifier, tmp_path, mode
):
    child = child_fixture(verifier, tmp_path, mode)
    with pytest.raises((TimeoutError, subprocess.TimeoutExpired)):
        with child:
            verifier.report(child)
    assert bytes(child.output["stdout"]) == b'{"ok":true}\n'
    assert child.process.returncode == -signal.SIGKILL
    assert_reaped(child)


@pytest.mark.parametrize("mode", ["nonzero", "duplicate", "oversized"])
def test_failure_status_ambiguous_json_or_unbounded_output_cannot_pass(verifier, tmp_path, mode):
    child = child_fixture(verifier, tmp_path, mode)
    with pytest.raises((AssertionError, ValueError)):
        with child:
            verifier.report(child)
    assert_reaped(child)


def test_fault_marker_from_wrong_pid_is_rejected_and_never_used_as_kill_target(verifier, tmp_path):
    child = child_fixture(verifier, tmp_path, "wrong_pid")
    with pytest.raises(AssertionError, match="Fixed checkpoint evidence differs"):
        with child:
            child.fault_reached("pause-before-commit", "draft")
    assert child.process.returncode == -signal.SIGKILL
    assert_reaped(child)
