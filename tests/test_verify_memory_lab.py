"""Guard independent memory-lab evidence and actual bounded child cleanup."""

import importlib
import json
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("verify_memory_lab")


@pytest.mark.parametrize("mode", ["hang", "closed-pipes", "flood"])
def test_real_cli_output_cannot_replace_exit_and_cleanup(verifier, tmp_path, mode):
    child = tmp_path / "fixed_child.py"
    child.write_text("""import os, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if sys.argv[1] == 'flood':
    print('x' * 8192, flush=True)
else:
    print('{"contract_version":"memory-policy-v1","error":"invalid_input"}', flush=True)
if sys.argv[1] == 'closed-pipes':
    os.close(1)
    os.close(2)
while True:
    time.sleep(0.01)
""")
    with pytest.raises((TimeoutError, RuntimeError)):
        with verifier.Child(
            [sys.executable, "-s", "-E", "-B", str(child), mode],
            tmp_path,
            {"PATH": os.defpath},
            timeout=0.4,
            max_output=1024,
        ) as process:
            process.finish()
    verifier.assert_reaped(process)
    with pytest.raises(ProcessLookupError):
        os.kill(process.process.pid, 0)


def test_real_success_requires_valid_report_and_waited_exit(verifier, tmp_path):
    child = tmp_path / "fixed_child.py"
    expected = verifier.error("invalid_input")
    child.write_text("print(" + repr(json.dumps(expected)) + ")\nraise SystemExit(1)\n")
    with verifier.Child(
        [sys.executable, "-s", "-E", "-B", str(child)], tmp_path, {"PATH": os.defpath}
    ) as process:
        assert verifier.check_report(*process.finish(), expected, 1) == expected
    verifier.assert_reaped(process)


@pytest.mark.parametrize("damage", ["exit", "duplicate", "boolean", "diagnostic", "foreign"])
def test_report_rejects_false_evidence(verifier, damage):
    expected = {"contract_version": "memory-policy-v1", "model_calls": 0, "read_only": True}
    code, stderr = 0, b""
    result = dict(expected)
    if damage == "exit":
        code = 1
    elif damage == "boolean":
        result["model_calls"] = False
    elif damage == "diagnostic":
        stderr = b"private-memory-rejected-input\n"
    elif damage == "foreign":
        result["extra"] = "foreign-owner-private-text-fixture"
    stdout = json.dumps(result).encode() + b"\n"
    if damage == "duplicate":
        stdout = b'{"model_calls":2,' + stdout[1:]
    with pytest.raises((AssertionError, ValueError)):
        verifier.check_report(code, stdout, stderr, expected, 0)


def test_read_only_snapshot_includes_new_runtime_cache_files(verifier, tmp_path):
    (tmp_path / "memories.json").write_text("{}")
    before = verifier.snapshot(tmp_path)
    cache = tmp_path / ".pytest_cache"
    cache.mkdir()
    (cache / "changed").write_text("runtime must not write")
    assert verifier.snapshot(tmp_path) != before
