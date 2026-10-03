"""Headless browser -> three maintainer starter APIs, with no model credentials."""

# ruff: noqa: S101 - fixed maintainer verification fixtures, never a production handler

import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def port_free(port):
    with socket.socket() as connection:
        return connection.connect_ex(("127.0.0.1", port)) != 0


def wait_ready(url, process):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Owned starter process exited with status {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:  # noqa: S310 - fixed localhost callers
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("Starter readiness timed out")


def stop(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if process.poll() is None:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def main():
    if not all(port_free(port) for port in (8010, 5174)):
        raise SystemExit("Starter ports 8010/5174 are occupied; existing processes retained")
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG") if key in os.environ}
    cache = ROOT / ".tools" / "starter-e2e-cache"
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    env.update(
        {
            "GOCACHE": str(cache / "build"),
            "GOPATH": str(cache / "packages"),
            "GOTOOLCHAIN": "local",
        }
    )
    node = shutil.which("node")
    go = shutil.which("go") or str(ROOT / ".tools/go/bin/go")
    commands = [
        (
            "python",
            "fastapi",
            [
                str(ROOT / "starters/python/.venv/bin/python"),
                "-m",
                "uvicorn",
                "app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8010",
            ],
        ),
        ("typescript", "hono", [node, "dist/server.js"]),
        ("go", "gin", [go, "run", "-mod=readonly", "."]),
    ]
    frontend = subprocess.Popen(  # noqa: S603 - fixed maintainer-owned source
        [
            node,
            "node_modules/vite/bin/vite.js",
            "--host",
            "127.0.0.1",
            "--port",
            "5174",
            "--strictPort",
        ],
        cwd=ROOT / "starters/frontend",
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        wait_ready("http://127.0.0.1:5174/", frontend)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for language, framework, command in commands:
                    if not port_free(8010):
                        raise RuntimeError("Starter API port acquired by another process")
                    api = subprocess.Popen(  # noqa: S603 - fixed maintainer-owned source
                        command,
                        cwd=ROOT / "starters" / language,
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    try:
                        wait_ready("http://127.0.0.1:8010/api/health", api)
                        with urllib.request.urlopen(
                            "http://127.0.0.1:5174/api/health", timeout=3
                        ) as response:  # noqa: S310 - fixed localhost
                            assert json.load(response)["framework"] == framework
                        context = browser.new_context(viewport={"width": 1440, "height": 1000})
                        try:
                            page = context.new_page()
                            page.goto("http://127.0.0.1:5174/")
                            page.get_by_label("问题", exact=True).fill("API 超时")
                            page.get_by_role("button", name="提问", exact=True).click()
                            expect(page.get_by_role("region", name="回答")).to_contain_text(
                                "教学演示"
                            )
                            expect(
                                page.get_by_role("link", name="API 契约与失败状态", exact=True)
                            ).to_have_attribute(
                                "href", "https://fastapi.tiangolo.com/tutorial/handling-errors/"
                            )
                            page.get_by_label("问题", exact=True).fill("zzzz unmatched")
                            page.get_by_role("button", name="提问", exact=True).click()
                            expect(page.get_by_role("region", name="回答")).to_contain_text(
                                "证据不足 · 未调用模型"
                            )
                            page.get_by_label("运行模式").select_option("openai")
                            page.get_by_label("实验访问码").fill("test-access")
                            page.get_by_label("问题", exact=True).fill("API")
                            page.get_by_role("button", name="提问", exact=True).click()
                            expect(page.get_by_role("alert")).to_have_text("access_required")
                            page.set_viewport_size({"width": 375, "height": 812})
                            assert page.evaluate(
                                "document.documentElement.scrollWidth <= innerWidth"
                            )
                            page.reload()
                            expect(page.get_by_label("运行模式")).to_have_value("demo")
                            expect(
                                page.get_by_role("heading", name="本次页面会话 · 最近 0 次完整运行")
                            ).to_be_visible()
                            print(
                                json.dumps(
                                    {
                                        "language": language,
                                        "framework": framework,
                                        "browser_to_api": "passed",
                                        "real_model_called": False,
                                    }
                                )
                            )
                        finally:
                            context.close()
                    finally:
                        stop(api)
                        # go run owns a compiler wrapper and a serving child; killing the wrapper alone is insufficient.
                        if not port_free(8010):
                            raise RuntimeError("Owned starter API did not release its port")
            finally:
                browser.close()
    finally:
        stop(frontend)
    if not all(port_free(port) for port in (8010, 5174)):
        raise RuntimeError("Starter test ports were not released")
    print("Owned starter servers and headless browser closed; ports 8010/5174 released")


if __name__ == "__main__":
    main()
