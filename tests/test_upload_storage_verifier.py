"""Reject false success from a fixed CLI and release its actual child process."""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("verify_upload_storage")
    monkeypatch.setattr(module, "CLI_TIMEOUT", 0.3)
    return module


@pytest.mark.parametrize(
    "mode", ["wrong_exit", "duplicate", "boolean", "private_diagnostic", "stalled", "closed_pipes"]
)
def test_cli_output_does_not_replace_correct_exit_or_process_cleanup(verifier, tmp_path, mode):
    child = tmp_path / "fixed_child.py"
    child.write_text("""import os, signal, sys, time
from pathlib import Path
Path('child.pid').write_text(str(os.getpid()))
mode = sys.argv[1]
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode == 'duplicate':
    print('{"schema_version":2,"schema_version":1,"storage_contract":"text-upload-sqlite-v1"}', flush=True)
elif mode == 'boolean':
    print('{"schema_version":true,"storage_contract":"text-upload-sqlite-v1"}', flush=True)
else:
    print('{"schema_version":1,"storage_contract":"text-upload-sqlite-v1"}', flush=True)
if mode == 'private_diagnostic':
    print('private database diagnostic', file=sys.stderr, flush=True)
if mode == 'wrong_exit':
    raise SystemExit(1)
if mode == 'closed_pipes':
    os.close(1)
    os.close(2)
if mode in ('stalled', 'closed_pipes'):
    while True:
        time.sleep(0.1)
""")
    with pytest.raises((ValueError, RuntimeError, subprocess.TimeoutExpired)):
        verifier.cli(
            [sys.executable, "-s", "-E", "-B", str(child), mode],
            tmp_path,
            {"PATH": os.defpath},
            verifier.INIT,
        )
    pid = int((tmp_path / "child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
