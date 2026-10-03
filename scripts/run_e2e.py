"""CI-owned local server lifecycle for headless browser tests."""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
processes = []
logs = []


def require_available(port):
    try:
        connection = socket.create_connection(("127.0.0.1", port), timeout=0.2)
    except OSError:
        return
    connection.close()
    raise RuntimeError(f"Port {port} already in use; use pytest -m e2e with existing servers")


def ready(url):
    for _ in range(60):
        try:
            with urlopen(url, timeout=1) as response:  # noqa: S310 - fixed localhost
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("Local test server did not start")


try:
    require_available(8000)
    require_available(5173)
    for command in [
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        ["pnpm", "dev"],
    ]:
        log = tempfile.TemporaryFile()
        logs.append(log)
        processes.append(
            subprocess.Popen(  # noqa: S603 - fixed repository-owned commands
                command, cwd=ROOT, stdout=log, stderr=log, start_new_session=True
            )
        )
    ready("http://127.0.0.1:8000/api/health")
    ready("http://127.0.0.1:5173")
    result = subprocess.run([sys.executable, "-m", "pytest", "-m", "e2e"], cwd=ROOT, check=False)  # noqa: S603
    sys.exit(result.returncode)
finally:
    for process in reversed(processes):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    for log in logs:
        log.close()
