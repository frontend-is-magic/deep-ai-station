"""Headless browser -> four maintainer starter APIs, with no model credentials."""

# ruff: noqa: S101 - fixed maintainer verification fixtures, never a production handler

import errno
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
        connection.settimeout(1)
        result = connection.connect_ex(("127.0.0.1", port))
    if result == 0:
        return False
    if result == errno.ECONNREFUSED:
        return True
    raise OSError(result, f"Cannot determine availability of starter port {port}")


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


def demo_answer(page, prompt):
    page.get_by_label("问题", exact=True).fill(prompt)
    with page.expect_response(
        lambda response: (
            response.url == "http://127.0.0.1:5174/api/ask" and response.request.method == "POST"
        )
    ) as request:
        page.get_by_role("button", name="提问", exact=True).click()
    response = request.value
    assert response.status == 200
    return response.json()


def verify_agent_demo(page):
    documents = {
        document["id"]: document
        for document in json.loads((ROOT / "starters/agent/documents.json").read_text())
    }
    expect(page.get_by_role("heading", name="Agent 研究助手", exact=True)).to_be_visible()
    expect(
        page.get_by_text("预设检索、读取和整理顺序，没有模型决策，也没有调用模型。")
    ).to_be_visible()
    answer = demo_answer(page, "API")
    assert answer["workflow"] == "research-agent"
    assert answer["mode"] == "demo"
    assert answer["outcome"] == "complete"
    assert answer["model_calls"] == 0 and answer["tool_calls"] == 2
    assert answer["usage"] is None
    assert answer["citations"]
    source_ids = {source["id"] for source in answer["sources"]}
    citations = page.get_by_role("region", name="引用摘录", exact=True)
    expect(page.get_by_role("region", name="回答")).to_contain_text(
        "实际请求：模型 0 次 · 工具 2 次"
    )
    for citation in answer["citations"]:
        assert citation["document_id"] in source_ids
        assert citation["quote"] and citation["quote"] in documents[citation["document_id"]]["body"]
        expect(citations).to_contain_text(citation["quote"])
    trace = page.get_by_role("region", name="运行轨迹", exact=True)
    assert 0 < len(answer["trace"]) <= 12
    expect(trace.get_by_role("listitem")).to_have_count(len(answer["trace"]))
    for step in answer["trace"]:
        expect(trace).to_contain_text(step["title"])
        expect(trace).to_contain_text(step["detail"])

    answer = demo_answer(page, "zzzz unmatched")
    assert answer["workflow"] == "research-agent"
    assert answer["outcome"] == "insufficient_evidence"
    assert answer["model_calls"] == 0 and answer["tool_calls"] == 1
    assert answer["sources"] == [] and answer["citations"] == []
    expect(page.get_by_role("region", name="回答")).to_contain_text("教学演示 · 证据不足")
    expect(page.get_by_role("region", name="回答")).to_contain_text(
        "实际请求：模型 0 次 · 工具 1 次"
    )
    expect(page.get_by_role("region", name="引用摘录")).to_contain_text(
        "本次没有通过校验的引用摘录。"
    )

    answer = demo_answer(page, "取消计费")
    assert answer["workflow"] == "research-agent"
    assert answer["outcome"] == "conflicting_evidence"
    assert answer["model_calls"] == 0 and answer["tool_calls"] == 2
    sources = {source["id"]: source for source in answer["sources"]}
    conflict_ids = {
        source["id"] for source in answer["sources"] if source["kind"] == "conflict-fixture"
    }
    assert len(conflict_ids) >= 2
    assert conflict_ids.issubset({citation["document_id"] for citation in answer["citations"]})
    result = page.get_by_role("region", name="回答")
    expect(result).to_contain_text("教学演示 · 证据冲突")
    expect(result).to_contain_text("实际请求：模型 0 次 · 工具 2 次")
    for source_id in conflict_ids:
        source = sources[source_id]
        assert source["url"] is None
        expect(result.get_by_role("link", name=source["title"], exact=True)).to_have_count(0)
        expect(result).to_contain_text(source["title"] + " · 冲突练习资料，无外链")
    citations = page.get_by_role("region", name="引用摘录")
    for citation in answer["citations"]:
        assert citation["document_id"] in sources
        assert citation["quote"] and citation["quote"] in documents[citation["document_id"]]["body"]
        expect(citations).to_contain_text(citation["quote"])
    page.set_viewport_size({"width": 375, "height": 812})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


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
    executable = cache / "knowledge-api"
    # A cold CI module cache can outlast HTTP readiness; compile before opening test servers.
    build = subprocess.Popen(  # noqa: S603 - fixed maintainer-owned source
        [go, "build", "-mod=readonly", "-o", str(executable), "."],
        cwd=ROOT / "starters/go",
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        if build.wait(timeout=180) != 0:
            raise RuntimeError("Maintainer Go starter build failed")
    finally:
        stop(build)
    if not all(port_free(port) for port in (8010, 5174)):
        raise RuntimeError("Starter ports acquired during compilation; existing processes retained")
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
        ("go", "gin", [str(executable)]),
        (
            "agent",
            "fastapi",
            [
                str(ROOT / "starters/agent/.venv/bin/python"),
                "-m",
                "uvicorn",
                "app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8010",
            ],
        ),
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
                            health = json.load(response)
                            assert health["framework"] == framework
                            assert health["documents"] == (5 if language == "agent" else 3)
                            if language == "agent":
                                assert health["workflow"] == "research-agent"
                            else:
                                assert "workflow" not in health
                        context = browser.new_context(viewport={"width": 1440, "height": 1000})
                        try:
                            page = context.new_page()
                            page.goto("http://127.0.0.1:5174/")
                            if language == "agent":
                                verify_agent_demo(page)
                            else:
                                demo_answer(page, "API 超时")
                                expect(page.get_by_role("region", name="回答")).to_contain_text(
                                    "教学演示"
                                )
                                expect(
                                    page.get_by_role("link", name="API 契约与失败状态", exact=True)
                                ).to_have_attribute(
                                    "href", "https://fastapi.tiangolo.com/tutorial/handling-errors/"
                                )
                                demo_answer(page, "zzzz unmatched")
                                expect(page.get_by_role("region", name="回答")).to_contain_text(
                                    "证据不足 · 未调用模型"
                                )
                            page.get_by_label("运行模式").select_option("deepseek")
                            page.get_by_label("实验访问码").fill("test-access")
                            page.get_by_label("问题", exact=True).fill("API")
                            with page.expect_response(
                                lambda response: (
                                    response.url == "http://127.0.0.1:5174/api/ask"
                                    and response.request.method == "POST"
                                )
                            ) as request:
                                page.get_by_role("button", name="提问", exact=True).click()
                            assert request.value.status == 401
                            expect(page.get_by_role("alert")).to_have_text("access_required")
                            count = 3 if language == "agent" else 2
                            expect(
                                page.get_by_role(
                                    "heading", name=f"本次页面会话 · 最近 {count} 次完整运行"
                                )
                            ).to_be_visible()
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
                                        "workflow": health.get("workflow"),
                                        "browser_to_api": "passed",
                                        "real_model_called": False,
                                    }
                                )
                            )
                        finally:
                            context.close()
                    finally:
                        stop(api)
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
