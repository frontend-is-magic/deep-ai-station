"""Verify the downloaded SSE labs through real HTTP and optional shared React clients."""

# ruff: noqa: S101 - assertions describe fixed teaching fixtures, not production handling

import argparse
import functools
import http.client
import json
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from build_course_labs import STREAM_FILES
from verify_course_labs import command, ensure_listener_closed, stop_owned_process_group, verify

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def loopback_port(base):
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})", base)
    if not match or not 0 < int(match[1]) < 65536:
        raise ValueError("Only a fixed loopback port is allowed")
    return int(match[1])


def request(base, case, *, seen_runs):
    connection = http.client.HTTPConnection("127.0.0.1", loopback_port(base), timeout=8)
    try:
        connection.request("GET", case["path"])
        response = connection.getresponse()
        assert response.status == case["status"], case["id"]
        body = response.read(65537)
        assert len(body) <= 65536, "SSE response exceeded its bounded teaching output"
        media = (response.getheader("Content-Type") or "").split(";", 1)[0]
        if "events" not in case:
            assert media == "application/json"
            assert json.loads(body) == case["expected"], case["id"]
            return
        assert media == "text/event-stream"
        for name, value in {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Accel-Buffering": "no",
        }.items():
            assert response.getheader(name) == value
        assert response.getheader("Access-Control-Allow-Origin") is None
        assert response.getheader("Set-Cookie") is None
        text = body.decode("utf-8", errors="strict")
        assert text.endswith("\n\n")
        parsed, run_id = [], None
        for frame in text[:-2].split("\n\n"):
            lines = frame.split("\n")
            assert (
                len(lines) == 2 and lines[0].startswith("event: ") and lines[1].startswith("data: ")
            )
            data = json.loads(lines[1][6:])
            actual_id = data.pop("run_id")
            assert isinstance(actual_id, str) and UUID.fullmatch(actual_id)
            if run_id is None:
                run_id = actual_id
                assert run_id not in seen_runs, "A previous request's run_id was reused"
                seen_runs.add(run_id)
            assert actual_id == run_id
            parsed.append({"event": lines[0][7:], "data": data})
        assert parsed == case["events"], case["id"]
    finally:
        connection.close()


def browser_check(base, folder, env, *, language, screenshots):
    from playwright.sync_api import expect, sync_playwright

    client = folder / "client"
    pnpm = shutil.which("pnpm")
    if not pnpm:
        raise RuntimeError("pnpm is required for the shared client")
    command(["pnpm", "install", "--frozen-lockfile"], client, env)
    command(["pnpm", "check"], client, env)
    port = free_port()
    page_base = f"http://127.0.0.1:{port}"
    opener = build_opener(ProxyHandler({}))
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(  # noqa: S603 - bundled fixed client only
            [pnpm, "exec", "vite", "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
            cwd=client,
            env={**env, "LAB_API_PORT": str(loopback_port(base))},
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            for _ in range(150):
                if process.poll() is not None:
                    raise RuntimeError("Owned SSE client exited before readiness")
                try:
                    with opener.open(page_base, timeout=1) as response:  # noqa: S310 - fixed loopback
                        if response.status == 200:
                            break
                except (URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("Owned SSE client did not start")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1280, "height": 1000})
                try:
                    page = context.new_page()
                    errors, requests = [], []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("request", lambda message: requests.append(message.url))
                    page.goto(page_base)
                    a = page.get_by_role("region", name="运行 A", exact=True)
                    b = page.get_by_role("region", name="运行 B", exact=True)
                    expect(a.get_by_role("status")).to_have_text("等待开始")
                    for scenario, status, output in [
                        ("success", "已完成", "理解 流式 响应 🌱"),
                        ("error", "教学数据源失败", "理解 "),
                        ("timeout", "教学运行超时", "理解 "),
                    ]:
                        a.get_by_label("实验场景", exact=True).select_option(scenario)
                        a.get_by_role("button", name="开始运行", exact=True).click()
                        expect(a.get_by_role("status")).to_have_text(status, timeout=10000)
                        expect(a.get_by_role("region", name="增量文字", exact=True)).to_have_text(
                            output
                        )
                        expect(a.get_by_role("list", name="事件顺序")).to_contain_text(
                            "done" if scenario == "success" else "error"
                        )
                    a.get_by_label("实验场景", exact=True).select_option("hold")
                    a.get_by_role("button", name="开始运行", exact=True).click()
                    expect(a.get_by_role("region", name="增量文字")).to_have_text("理解 ")
                    b.get_by_role("button", name="开始运行", exact=True).click()
                    a.get_by_role("button", name="停止", exact=True).click()
                    expect(a.get_by_role("status")).to_have_text("已停止，保留已收到的文字")
                    expect(a.get_by_role("region", name="增量文字")).to_have_text("理解 ")
                    expect(b.get_by_role("status")).to_have_text("已完成")
                    expect(b.get_by_role("region", name="增量文字")).to_have_text(
                        "理解 流式 响应 🌱"
                    )
                    a.get_by_role("button", name="开始运行", exact=True).click()
                    expect(a.get_by_role("region", name="增量文字")).to_have_text("理解 ")
                    a.get_by_label("实验场景", exact=True).select_option("success")
                    a.get_by_role("button", name="重新运行", exact=True).click()
                    expect(a.get_by_role("status")).to_have_text("已完成")
                    expect(a.get_by_role("region", name="增量文字")).to_have_text(
                        "理解 流式 响应 🌱"
                    )
                    if language == "typescript":
                        verify_client_rejections(page, a)
                    assert not errors
                    assert all(url.startswith(page_base + "/") for url in requests)
                    if screenshots:
                        screenshots.mkdir(parents=True, exist_ok=True)
                        page.evaluate("document.fonts.ready")
                        page.screenshot(
                            path=str(screenshots / f"sse-{language}-desktop.png"), full_page=True
                        )
                    page.set_viewport_size({"width": 375, "height": 812})
                    page.reload()
                    expect(page.get_by_role("heading", level=1)).to_be_visible()
                    a.get_by_role("button", name="开始运行", exact=True).click()
                    expect(a.get_by_role("status")).to_have_text("已完成")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    if screenshots:
                        page.evaluate("document.fonts.ready")
                        page.evaluate(
                            "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
                        )
                        page.screenshot(
                            path=str(screenshots / f"sse-{language}-mobile.png"), full_page=True
                        )
                finally:
                    context.close()
                    browser.close()
        finally:
            stop_owned_process_group(process)
            ensure_listener_closed(port)
    print(
        json.dumps(
            {
                "language": language,
                "shared_react_to_sse": "passed",
                "client_listener_released": True,
                "model_calls": 0,
            }
        )
    )


def verify_client_rejections(page, panel):
    from playwright.sync_api import expect

    run_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    def wire(event, value):
        return (
            "event: "
            + event
            + "\ndata: "
            + json.dumps({"run_id": run_id, **value}, ensure_ascii=False)
            + "\n\n"
        )

    # A controlled prematurely closed response exercises the client EOF boundary.
    incomplete = wire("start", {"scenario": "success"}) + wire("delta", {"seq": 1, "text": "理解 "})
    page.route(
        "**/api/stream*",
        lambda route: route.fulfill(content_type="text/event-stream", body=incomplete),
        times=1,
    )
    panel.get_by_role("button", name="开始运行", exact=True).click()
    expect(panel.get_by_role("status")).to_have_text("流协议无效或未完成")
    expect(panel.get_by_role("region", name="增量文字")).to_have_text("理解 ")

    # Ignore AbortSignal once on purpose: identity checks must also reject late work.
    late = (
        wire("start", {"scenario": "hold"})
        + wire("delta", {"seq": 1, "text": "不能覆盖新运行的旧文字"})
        + wire("error", {"code": "deadline_exceeded", "message": "教学运行超时"})
    )
    page.evaluate(
        """wire => {
      const original = window.fetch.bind(window);
      window.__originalFetch = window.fetch;
      let first = true;
      window.fetch = (...args) => {
        if (first && String(args[0]).startsWith('/api/stream')) {
          first = false;
          return new Promise(resolve => {
            window.__releaseLate = () => resolve(new Response(new ReadableStream({
              start(controller) { controller.enqueue(new TextEncoder().encode(wire)); },
              pull(controller) { window.__lateClosed = true; controller.close(); },
            }), {headers:{'Content-Type':'text/event-stream'}}));
          });
        }
        return original(...args);
      };
    }""",
        late,
    )
    try:
        panel.get_by_label("实验场景", exact=True).select_option("hold")
        panel.get_by_role("button", name="开始运行", exact=True).click()
        page.wait_for_function("typeof window.__releaseLate === 'function'")
        panel.get_by_label("实验场景", exact=True).select_option("success")
        panel.get_by_role("button", name="重新运行", exact=True).click()
        expect(panel.get_by_role("status")).to_have_text("已完成")
        page.evaluate("window.__releaseLate()")
        page.wait_for_function("window.__lateClosed === true")
        page.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        expect(panel.get_by_role("status")).to_have_text("已完成")
        expect(panel.get_by_role("region", name="增量文字")).to_have_text("理解 流式 响应 🌱")
        expect(panel).not_to_contain_text(run_id)
    finally:
        page.evaluate(
            "window.fetch = window.__originalFetch; delete window.__originalFetch; delete window.__releaseLate; delete window.__lateClosed;"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=STREAM_FILES)
    parser.add_argument(
        "--browser", action="store_true", help="Also verify the bundled React client headlessly"
    )
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    if args.screenshots and not args.browser:
        parser.error("--screenshots requires --browser")
    for language in [args.language] if args.language else STREAM_FILES:
        extra = (
            functools.partial(browser_check, language=language, screenshots=args.screenshots)
            if args.browser
            else None
        )
        verify(
            language,
            free_port(),
            "sse-stream",
            functools.partial(request, seen_runs=set()),
            extra_check=extra,
        )


if __name__ == "__main__":
    main()
