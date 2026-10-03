"""Real, fixed process trees prove cleanup owns the whole group, not just its leader."""

import importlib
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHILD = r"""
import json
import os
import signal
import socket
import sys

if sys.argv[1] == 'ignore-term':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
with socket.socket() as listener:
    listener.bind(('127.0.0.1', 0))
    listener.listen(8)
    ready = {'port': listener.getsockname()[1], 'pid': os.getpid(), 'pgid': os.getpgrp()}
    os.write(1, (json.dumps(ready) + '\n').encode())
    while True:
        signal.pause()
"""
LEADER = r"""
import signal
import subprocess
import sys

subprocess.Popen([sys.executable, '-u', '-c', sys.argv[1], sys.argv[2]], stdin=subprocess.DEVNULL)
if sys.argv[2] == 'leader-exits':
    sys.exit(0)
while True:
    signal.pause()
"""


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("verify_course_labs")


def read_ready(process):
    # The child writes one small atomic record only after binding and setting its signal handler.
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=5):
            raise AssertionError("Owned child did not announce readiness")
        line = process.stdout.readline()
    result = json.loads(line)
    assert result["pgid"] == process.pid
    return result


@pytest.mark.parametrize("mode", ["leader-exits", "ignore-term"])
def test_cleanup_stops_owned_descendants_and_releases_listener(verifier, monkeypatch, mode):
    real_killpg = os.killpg
    delivered = []
    process = subprocess.Popen(  # noqa: S603 - fixed maintainer code, no shell or external input
        [sys.executable, "-u", "-c", LEADER, CHILD, mode],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"PYTHONUNBUFFERED": "1"},
        bufsize=0,
        start_new_session=True,
    )
    ready = None

    def record_signal(pgid, sig):
        assert pgid == process.pid, "Cleanup must only signal its own recorded process group"
        delivered.append(sig)
        return real_killpg(pgid, sig)

    try:
        ready = read_ready(process)
        if mode == "leader-exits":
            assert process.wait(timeout=5) == 0
        else:
            assert process.poll() is None
        with socket.create_connection(("127.0.0.1", ready["port"]), timeout=1):
            pass
        monkeypatch.setattr(verifier.os, "killpg", record_signal)
        verifier.stop_owned_process_group(process, grace_seconds=0.1, kill_grace_seconds=3)
        verifier.ensure_listener_closed(ready["port"])
        assert process.returncode is not None
        assert signal.SIGTERM in delivered
        if mode == "ignore-term":
            assert process.returncode == -signal.SIGTERM
            assert signal.SIGKILL in delivered
        # Reap the leader and verify the listener, not killpg(0): an orphan zombie may
        # keep its old PGID observable on Linux despite holding no socket or live work.
    finally:
        # Independent fallback ensures a failed assertion/helper never abandons our descendants.
        try:
            real_killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        process.stdout.close()
        if ready is not None:
            verifier.ensure_listener_closed(ready["port"])


@pytest.mark.parametrize("zombie_group", [False, True])
def test_missing_or_zombie_group_has_bounded_cleanup_and_reaps_leader(
    verifier, monkeypatch, zombie_group
):
    process = Mock(pid=12345)
    process.poll.return_value = 0
    delivered = []
    ticks = iter(index / 10 for index in range(20))
    monkeypatch.setattr(verifier.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(verifier.time, "sleep", lambda _seconds: None)

    def signal_only_owned_group(pgid, sig):
        assert pgid == 12345
        delivered.append(sig)
        if not zombie_group:
            raise ProcessLookupError

    monkeypatch.setattr(verifier.os, "killpg", signal_only_owned_group)
    verifier.stop_owned_process_group(process, grace_seconds=0.1, kill_grace_seconds=0.1)
    process.wait.assert_called_once_with(timeout=0.1)
    assert signal.SIGTERM in delivered
    assert (signal.SIGKILL in delivered) is zombie_group
    assert len(delivered) <= 6
