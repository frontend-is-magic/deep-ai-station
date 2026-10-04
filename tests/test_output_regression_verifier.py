"""Exercise only false success and owned child cleanup boundaries."""

import importlib
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = r"""
import os
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
if MODE == "duplicate":
    print('{"passed":true,"passed":false}', flush=True)
    raise SystemExit(0)
if MODE == "oversized":
    os.write(1, b'x' * 300000)
else:
    print('{"passed":true}', flush=True)
if MODE == "wrong_exit":
    raise SystemExit(1)
if MODE == "closed_pipes":
    os.close(1)
    os.close(2)
while True:
    time.sleep(0.1)
"""


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("verify_output_regression_lab")


@pytest.mark.parametrize(
    "mode", ["stalled", "closed_pipes", "duplicate", "oversized", "wrong_exit"]
)
def test_valid_looking_output_does_not_replace_real_exit_and_cleanup(verifier, tmp_path, mode):
    helper = tmp_path / "fixed_child.py"
    helper.write_text("MODE = " + repr(mode) + "\n" + HELPER)
    child = verifier.Child(
        [sys.executable, "-s", "-E", "-B", str(helper)],
        tmp_path,
        {"PATH": os.defpath, "HOME": str(tmp_path)},
        timeout=0.5,
        grace=0.05,
    )
    with pytest.raises((AssertionError, ValueError, TimeoutError, subprocess.TimeoutExpired)):
        with child:
            verifier.report(child)
    assert child.reaped and child.process.returncode is not None
    if mode in {"stalled", "closed_pipes"}:
        assert child.process.returncode == -signal.SIGKILL
    with pytest.raises(ProcessLookupError):
        os.kill(child.process.pid, 0)


def test_gate_exit_two_is_distinct_from_successful_and_invalid_invocation(verifier, tmp_path):
    helper = tmp_path / "fixed_rejection.py"
    helper.write_text('print(\'{"gate":{"passed":false}}\')\nraise SystemExit(2)\n')
    for expected_code in (0, 1, 2):
        child = verifier.Child(
            [sys.executable, "-s", "-E", "-B", str(helper)],
            tmp_path,
            {"PATH": os.defpath, "HOME": str(tmp_path)},
            timeout=1,
        )
        with child:
            if expected_code == 2:
                assert verifier.report(child, exit_code=expected_code) == {
                    "gate": {"passed": False}
                }
            else:
                with pytest.raises(AssertionError, match="exit status"):
                    verifier.report(child, exit_code=expected_code)
        assert child.reaped and child.process.returncode == 2
        with pytest.raises(ProcessLookupError):
            os.kill(child.process.pid, 0)


def test_nonfinite_surrogate_and_boolean_count_cannot_become_evidence(verifier):
    for raw in (b'{"value":1e999}', b'{"value":NaN}', b'{"value":"\\ud800"}'):
        with pytest.raises((ValueError, UnicodeError)):
            verifier.decode(raw)
    with pytest.raises(AssertionError):
        verifier.same({"case_count": True}, {"case_count": 1})
