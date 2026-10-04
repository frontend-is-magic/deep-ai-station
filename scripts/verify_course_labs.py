"""Install and verify maintainer-owned lab archives outside the platform checkout."""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from build_course_labs import DESTINATION, FILES, ROOT


def command(args, cwd, env):
    subprocess.run(args, cwd=cwd, env=env, check=True)  # noqa: S603 - fixed maintainer commands


def request(base, case):
    data = None
    headers = {}
    if "json" in case:
        data = json.dumps(case["json"], ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    elif "raw" in case:
        data = case["raw"].encode()
    elif "repeat_body" in case:
        repeat = case["repeat_body"]
        data = (repeat["character"] * repeat["count"]).encode()
    if "content_type" in case:
        headers["Content-Type"] = case["content_type"]
    message = Request(base + case["path"], data=data, headers=headers, method=case["method"])  # noqa: S310 - fixed loopback and repository fixtures
    try:
        response = build_opener(ProxyHandler({})).open(message, timeout=5)  # noqa: S310 - fixed loopback only
    except HTTPError as error:
        response = error
    with response:
        if response.status != case["status"]:
            raise RuntimeError(f"{case['id']}: unexpected HTTP status {response.status}")
        if response.headers.get_content_type() != "application/json":
            raise RuntimeError(f"{case['id']}: expected JSON response")
        if json.loads(response.read()) != case["expected"]:
            raise RuntimeError(f"{case['id']}: JSON response does not match the shared contract")


def stop_owned_process_group(process, *, grace_seconds=5.0, kill_grace_seconds=5.0):
    """Stop only the PGID created by this Popen(start_new_session=True).

    The leader may already be gone while uv/pnpm descendants still own a listener.
    poll()/wait() reap our leader; killpg(..., 0) observes only its recorded group.
    """
    if grace_seconds < 0 or kill_grace_seconds <= 0:
        raise ValueError("process cleanup waits must be bounded and positive")
    pgid = process.pid

    def signal_group(sig):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return False
        return True

    def wait_for_group(seconds):
        deadline = time.monotonic() + seconds
        while True:
            process.poll()
            if not signal_group(0):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.05, remaining))

    try:
        signal_group(signal.SIGTERM)
        if not wait_for_group(grace_seconds):
            signal_group(signal.SIGKILL)
            # An orphan zombie can keep the PGID visible on Linux until init reaps it.
            # SIGKILL cannot be ignored; keep this wait bounded, then let the caller's
            # ensure_listener_closed verify the actual service instead of a zombie group ID.
            wait_for_group(kill_grace_seconds)
    finally:
        process.wait(timeout=kill_grace_seconds)


def ensure_listener_closed(port):
    # Observe closure only; never stop an unrelated process that appears later.
    for _ in range(20):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                time.sleep(0.05)
        except ConnectionRefusedError:
            return
    raise RuntimeError("Owned course lab listener has not closed")


def verify_http(
    args, folder, env, port, request_case=request, cases_override=None, extra_check=None
):
    with socket.socket() as check:
        check.bind(("127.0.0.1", port))
    cases = (
        json.loads((folder / "contract-cases.json").read_text())
        if cases_override is None
        else cases_override
    )
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(  # noqa: S603 - fixed maintainer server entry
            args,
            cwd=folder,
            env={**env, "PORT": str(port)},
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Course lab server exited before health check")
                try:
                    request_case(base, cases[0])
                    break
                except (URLError, TimeoutError, ConnectionError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("Course lab server did not become healthy")
            for case in cases:
                request_case(base, case)
            if extra_check is not None:
                extra_check(base, folder, env)
        finally:
            stop_owned_process_group(process)
            ensure_listener_closed(port)
    return len(cases)


def verify(
    language,
    port,
    lab="api-contract",
    request_case=request,
    restart_cases=None,
    extra_check=None,
    after_verify=None,
):
    # Deliberately omit provider keys and authentication material from child processes.
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "USER",
        "LOGNAME",
        "CI",
        "UV_CACHE_DIR",
        "GOCACHE",
        "GOMODCACHE",
        "GOPATH",
        "GOTOOLCHAIN",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "PNPM_HOME",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    with tempfile.TemporaryDirectory(prefix=f"deep-ai-lab-{language}-") as temporary:
        folder = Path(temporary).resolve()
        if folder.is_relative_to(ROOT):
            raise RuntimeError("Archive checks must run outside the repository")
        with zipfile.ZipFile(DESTINATION / f"{lab}-{language}.zip") as archive:
            archive.extractall(folder)
        if language == "python":
            uv = shutil.which("uv")
            if not uv:
                raise RuntimeError("uv is required")
            command([uv, "sync", "--locked"], folder, env)
            command([uv, "run", "--frozen", "ruff", "check", "."], folder, env)
            command([uv, "run", "--frozen", "ruff", "format", "--check", "."], folder, env)
            command([uv, "run", "--frozen", "pytest", "-q"], folder, env)
            server = [
                uv,
                "run",
                "--frozen",
                "uvicorn",
                "app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ]
        elif language == "typescript":
            command(["pnpm", "install", "--frozen-lockfile"], folder, env)
            config = subprocess.check_output(  # noqa: S603 - fixed local formatter
                [shutil.which("pnpm"), "exec", "prettier", "--find-config-path", "src/app.ts"],
                cwd=folder,
                env=env,
                text=True,
            ).strip()
            if (folder / config).resolve() != folder / ".prettierrc.json":
                raise RuntimeError("Lab did not resolve its own Prettier config")
            command(["pnpm", "check"], folder, env)
            server = ["node", "dist/server.js"]
        else:
            files = sorted(path.name for path in folder.glob("*.go"))
            unformatted = subprocess.check_output(  # noqa: S603 - fixed maintainer source files
                [shutil.which("gofmt"), "-l", *files], cwd=folder, env=env, text=True
            )
            if unformatted.strip():
                raise RuntimeError("Go lab is not formatted")
            go_tests = ["go", "test", "-mod=readonly"]
            if lab in {"session-authorization", "text-upload", "sse-stream"}:
                go_tests.append("-race")
            command([*go_tests, "./..."], folder, env)
            command(["go", "build", "-mod=readonly", "-o", "lab-server", "."], folder, env)
            server = [str(folder / "lab-server")]
        count = verify_http(server, folder, env, port, request_case, extra_check=extra_check)
        restart_count = 0
        if restart_cases:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                restart_port = listener.getsockname()[1]
            restart_server = [str(restart_port) if item == str(port) else item for item in server]
            restart_count = verify_http(
                restart_server, folder, env, restart_port, request_case, restart_cases
            )
        if after_verify is not None:
            after_verify(language, folder, env)
        print(
            json.dumps(
                {
                    "language": language,
                    "lab": lab,
                    "restart_cases": restart_count,
                    "archive": "verified",
                    "http_cases": count,
                    "listener_released": True,
                    "model_calls": 0,
                }
            )
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=FILES)
    args = parser.parse_args()
    for language in [args.language] if args.language else FILES:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        verify(language, port)


if __name__ == "__main__":
    main()
