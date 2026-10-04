"""Critical verifier invariants; no backend, browser, or dependency installation."""

import os
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify_upload_client as verifier  # noqa: E402


def archive(path, content, *, extra=False):
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("client/src/main.tsx", content)
        package.writestr("client/package.json", '{"private":true}')
        package.writestr("app.py", "print('backend')")
        if extra:
            package.writestr("client/extra.txt", "unexpected")
    return path


def test_clients_require_exact_bytes_and_members(tmp_path):
    first = archive(tmp_path / "first.zip", "same")
    second = archive(tmp_path / "second.zip", "same")
    assert verifier.identical_clients([first, second]) == 2
    archive(second, "changed")
    with pytest.raises(RuntimeError, match="byte-identical"):
        verifier.identical_clients([first, second])
    archive(second, "same", extra=True)
    with pytest.raises(RuntimeError, match="byte-identical"):
        verifier.identical_clients([first, second])


def test_missing_client_is_not_a_success(tmp_path):
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("app.py", "fixed backend")
    with pytest.raises(RuntimeError, match="Missing"):
        verifier.identical_clients([path])
    with pytest.raises(RuntimeError, match="No upload"):
        verifier.identical_clients([])


def test_client_cleanup_reaps_real_stdin_helper():
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        verifier.stop_client(process)
        assert process.returncode == 0
        assert not verifier.group_members(process.pid)
        with pytest.raises(ProcessLookupError):
            os.kill(process.pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_permission_failure_is_not_cleanup_success(monkeypatch):
    process = Mock(pid=12345)
    process.stdin = None
    monkeypatch.setattr(verifier, "group_members", Mock(side_effect=PermissionError("fixed")))
    with pytest.raises(PermissionError):
        verifier.stop_client(process)
    process.wait.assert_called_once_with(timeout=5)


def test_backend_diagnostics_are_not_hidden():
    with pytest.raises(RuntimeError, match="diagnostics"):
        verifier.check_diagnostics(b"private-database-diagnostic")
    with pytest.raises(RuntimeError, match="diagnostics"):
        verifier.check_diagnostics(b" " * 4097)
    verifier.check_diagnostics(b"")
