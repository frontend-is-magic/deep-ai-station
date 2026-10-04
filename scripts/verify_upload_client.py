"""Verify the identical upload clients from ZIPs through real local backends.

The six language/storage combinations use real HTTP and original-byte downloads.
Additional browser scheduling faults are reported separately from SQLite facts.
"""

# ruff: noqa: S101 - independent assertions over fixed, public teaching fixtures

import argparse
import hashlib
import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

from build_course_labs import DESTINATION, ROOT
from verify_course_labs import ensure_listener_closed
from verify_upload_storage import (
    check_diagnostics,
    cli,
    entry,
    stop_direct_process,
)

LANGUAGES = ("python", "typescript", "go")
BODY = (
    "# 固定资料\r\n<script>window.__uploadSentinel=1</script>\r\n"
    "[假链接](https://example.invalid/) 😀 e\u0301\n"
).encode()
UNKNOWN = (
    "上传结果未确认；服务器可能已保存，请先刷新列表核对。再次上传可能创建重复文档。"
    "此提示仅保留在本页，刷新页面后会丢失。"
)
ACK = "我已核对，允许再次上传（可能重复）"
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}

LAUNCHER = """
import {preview} from 'vite';
const port = Number(process.env.PORT);
if (!Number.isInteger(port) || port < 1 || port > 65535) process.exit(1);
const server = await preview({logLevel:'error', preview:{host:'127.0.0.1',port,strictPort:true,open:false}});
let closing = false;
async function stop() {
  if (closing) return;
  closing = true;
  try { await server.close(); process.exitCode = 0; }
  catch { process.exitCode = 1; }
  process.stdin.pause();
  process.stdin.removeAllListeners('end');
}
process.stdin.resume();
process.stdin.once('end', stop);
process.once('SIGTERM', stop);
process.once('SIGINT', stop);
"""

# Installed before the application. These are observation/scheduling seams, not
# product imports: actual fetch, File bytes, SHA and Blob download APIs still run.
AUDIT = """() => {
  const audit = window.__uploadAudit = {posts:0,violations:[],urls:new Set(),gates:[]};
  const realFetch = window.fetch.bind(window);
  const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
  URL.createObjectURL = blob => {const url=create(blob); audit.urls.add(url); return url;};
  URL.revokeObjectURL = url => {audit.urls.delete(url); return revoke(url);};
  window.fetch = async (input, init) => {
    const request = new Request(input, init), path = new URL(request.url).pathname;
    if (path.startsWith('/api/')) {
      if (request.credentials !== 'omit' || request.cache !== 'no-store' || request.redirect !== 'error')
        audit.violations.push('request_policy');
      if (!/^Bearer lab-(alice|alice-second|alice-readonly|bob|expired|revoked)-session$/.test(request.headers.get('Authorization') || ''))
        audit.violations.push('public_identity');
      if (request.method === 'POST') audit.posts++;
    }
    const gate = window.__fetchGate;
    if (gate && !gate.started && gate.method === request.method && gate.path === path) {
      gate.started=true; gate.signal=request.signal; audit.gates.push(gate);
      const response = await realFetch(request, {signal:new AbortController().signal});
      const bytes = await response.arrayBuffer();
      gate.status=response.status;
      await new Promise(resolve => {gate.release=resolve; gate.ready=true;});
      gate.returned=true;
      return new Response(bytes,{status:response.status,headers:response.headers});
    }
    return realFetch(input,init);
  };
  const read = File.prototype.arrayBuffer;
  File.prototype.arrayBuffer = async function () {
    const gate=window.__fileGate;
    if (!gate || gate.started) return read.call(this);
    gate.started=true; audit.gates.push(gate);
    const bytes=await read.call(this);
    await new Promise(resolve => {gate.release=resolve;gate.ready=true;});
    gate.returned=true; return bytes;
  };
}"""


def identical_clients(archives):
    expected = None
    for archive in archives:
        with zipfile.ZipFile(archive) as package:
            members = [
                item.filename
                for item in package.infolist()
                if item.filename.startswith("client/") and not item.is_dir()
            ]
            if not members or len(members) != len(set(members)):
                raise RuntimeError("Missing or duplicate packaged client members")
            actual = {name: package.read(name) for name in members}
        if expected is not None and actual != expected:
            raise RuntimeError("Upload ZIP clients are not byte-identical")
        expected = actual
    if expected is None:
        raise RuntimeError("No upload archives supplied")
    return len(expected)


def environment():
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "CI",
        "UV_CACHE_DIR",
        "GOCACHE",
        "GOMODCACHE",
        "GOPATH",
        "GOTOOLCHAIN",
        "PNPM_HOME",
        "PLAYWRIGHT_BROWSERS_PATH",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
    if port in {8000, 5173}:
        raise RuntimeError("Reserved primary port selected")
    return port


def group_members(pgid):
    result = subprocess.run(  # noqa: S603 - fixed read-only process inventory, never commands/arguments
        ["/bin/ps", "-axo", "pid=,pgid=,stat="],
        capture_output=True,
        text=True,
        timeout=3,
        check=True,
    )
    members = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[1] == str(pgid):
            members.append((int(fields[0]), fields[2]))
    return members


def stop_client(process):
    # The fixed Vite launcher first closes Vite's public server API. Unlike the
    # direct backends it can own an esbuild child, so observe its own session too.
    if process.stdin:
        process.stdin.close()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            os.killpg(process.pid, 15)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, 9)
            process.wait(timeout=3)

    # Observe only this start_new_session group. A zombie is already exited;
    # our direct Popen has separately been waited/reaped above.
    def live_members():
        return [member for member in group_members(process.pid) if not member[1].startswith("Z")]

    for signal_number in (None, 15, 9):
        if not live_members():
            return
        if signal_number is not None:
            try:
                os.killpg(process.pid, signal_number)
            except ProcessLookupError:
                return
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not live_members():
                return
            time.sleep(0.05)
    raise RuntimeError("Owned Vite process group has not exited")
    # No killpg(pgid, 0) after reap, and no permission error is treated as success.


def ready(port, process, api):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Owned upload service exited before readiness")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
        try:
            connection.request("GET", "/health" if api else "/")
            response = connection.getresponse()
            body = response.read(65537)
            if response.status == 200:
                if api:
                    assert json.loads(body) == {"status": "ok", "lab": "text-upload-v1"}
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(0.05)
    raise RuntimeError("Owned upload service readiness exceeded deadline")


@contextmanager
def service(args, cwd, env, port, counters, *, api):
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(  # noqa: S603 - only fixed unpacked runtimes and Vite launcher
            args,
            cwd=cwd,
            env={**env, "PORT": str(port)},
            stdin=subprocess.PIPE if not api else subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        counters["started_processes"] += 1
        try:
            ready(port, process, api)
            yield process.pid
        finally:
            failures = []
            try:
                (stop_direct_process if api else stop_client)(process)
            except Exception as error:
                failures.append(error)
            try:
                ensure_listener_closed(port)
            except Exception as error:
                failures.append(error)
            try:
                log.seek(0)
                check_diagnostics(log.read(4097))
            except Exception as error:
                failures.append(error)
            if failures:
                raise RuntimeError("Owned upload service cleanup/diagnostics failed") from failures[
                    0
                ]
            counters["reaped_processes"] += 1


def request(port, method, path, *, owner="alice", body=None, filename="notes.md"):
    headers = {"Authorization": f"Bearer lab-{owner}-session", "Connection": "close"}
    if body is not None:
        headers.update(
            {
                "Content-Type": "text/markdown"
                if filename.lower().endswith(".md")
                else "text/plain",
                "X-Filename": filename,
            }
        )
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(65537)
        assert len(raw) <= 65536
        for key, value in HEADERS.items():
            assert response.getheader(key) == value
        result = raw if path.endswith("/content") and response.status == 200 else json.loads(raw)
        return response.status, result
    finally:
        connection.close()


def database_records(work):
    path = (work / ".data/uploads.sqlite3").resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as database:
        rows = database.execute(
            "SELECT id,owner_id,filename,media_type,size_bytes,sha256,content FROM documents ORDER BY id"
        ).fetchall()
        next_id = database.execute("SELECT next_id FROM storage_meta WHERE singleton=1").fetchone()[
            0
        ]
    assert next_id == len(rows) + 1
    assert [row[0] for row in rows] == list(range(1, len(rows) + 1))
    for row in rows:
        assert type(row[6]) is bytes and len(row[6]) == row[4]
        assert hashlib.sha256(row[6]).hexdigest() == row[5]
    return rows


def select_file(page, body=BODY, name="notes.md"):
    page.get_by_label("选择文本文件", exact=True).set_input_files(
        {"name": name, "mimeType": "application/x-untrusted-mime", "buffer": body}
    )


def upload(page, body=BODY, name="notes.md", *, confirmed=True):
    from playwright.sync_api import expect

    select_file(page, body, name)
    if not confirmed:
        page.get_by_role("button", name="上传文件", exact=True).click()
        return None
    with page.expect_response(
        lambda response: (
            response.request.method == "POST" and response.url.endswith("/api/documents")
        )
    ) as pending:
        page.get_by_role("button", name="上传文件", exact=True).click()
    response = pending.value
    assert response.status == 201
    assert response.request.post_data_buffer == body
    headers = response.request.all_headers()
    media = "text/markdown" if name.lower().endswith(".md") else "text/plain"
    assert headers["content-type"] == media and headers["x-filename"] == name
    metadata = response.json()
    assert metadata == {
        "id": metadata["id"],
        "filename": name,
        "media_type": media,
        "size_bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    expect(page.get_by_role("region", name="上传结果", exact=True)).to_contain_text(
        "本次上传已确认。"
    )
    return metadata


def listing(page, count, *, failed=False):
    from playwright.sync_api import expect

    region = page.get_by_role("region", name="我的文档", exact=True)
    expect(region).to_have_attribute("aria-busy", "false")
    expect(region.get_by_role("article")).to_have_count(count)
    if not failed:
        expect(region.get_by_role("alert")).to_have_count(0)
    return region


def document(page, number):
    return page.get_by_role("article", name=f"doc-{number:06d}", exact=True)


def download(page, number, destination, body=BODY, extension="md"):
    from playwright.sync_api import expect

    with page.expect_download() as pending:
        document(page, number).get_by_role("button", name="下载原始附件", exact=True).click()
    file = pending.value
    assert file.suggested_filename == f"upload-doc-{number:06d}.{extension}"
    file.save_as(destination)
    assert destination.read_bytes() == body
    expect(page.get_by_role("region", name="原文预览", exact=True)).to_contain_text(
        "已校验原字节并发起附件下载。"
    )
    assert page.evaluate("window.__uploadAudit.urls.size") == 0


def assert_unknown(page):
    from playwright.sync_api import expect

    expect(page.get_by_role("region", name="未确认上传", exact=True)).to_contain_text(UNKNOWN)


def checkpoint(page):
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )


@contextmanager
def browser_page(browser, base, *, clock=False):
    from playwright.sync_api import expect

    context = browser.new_context(
        viewport={"width": 1280, "height": 1000},
        accept_downloads=True,
        service_workers="block",
    )
    page = context.new_page()
    page.set_default_timeout(10000)
    errors, downloads, destinations = [], [], []
    navigations, websockets = [], []
    page.on(
        "framenavigated",
        lambda frame: navigations.append(True) if frame == page.main_frame else None,
    )
    page.on("websocket", lambda _socket: websockets.append(True))
    page.on("pageerror", lambda error: errors.append(type(error).__name__))
    page.on("download", lambda item: downloads.append(item))

    def allow(route):
        if route.request.url.startswith(base + "/"):
            route.continue_()
        else:
            destinations.append("external")
            route.abort()

    page.route("**/*", allow)
    page.add_init_script("(" + AUDIT + ")()")
    if clock:
        page.clock.install()
    try:
        page.goto(base)
        expect(page.get_by_role("heading", name="受限文本上传实验", exact=True)).to_be_visible()
        listing(page, 0)
        # The exact ZIP was built above. Exercise that output without dev HMR
        # replacing the document while a real POST response is being observed.
        scripts = page.locator('script[type="module"][src]').evaluate_all(
            "nodes => nodes.map(node => new URL(node.src).pathname)"
        )
        assert scripts and all(path.startswith("/assets/") for path in scripts)
        assert len(navigations) == 1 and not websockets
        yield page, downloads
        assert len(navigations) == 1 and not websockets
        assert not errors and not destinations
        assert page.evaluate("window.__uploadAudit.violations") == []
        assert page.evaluate("window.__uploadAudit.urls.size") == 0
        assert page.evaluate("localStorage.length + sessionStorage.length") == 0
        assert page.evaluate("indexedDB.databases().then(items => items.length)") == 0
    finally:
        try:
            page.evaluate("window.__uploadAudit?.gates.forEach(gate => gate.release?.())")
        finally:
            context.close()


def workspace(folder, name):
    work = folder / name
    work.mkdir()
    for asset in ("fixtures.json", "schema.sql"):
        shutil.copyfile(folder / asset, work / asset)
    return work


@contextmanager
def running(browser, folder, client, env, language, work, storage, counters, *, clock=False):
    api_port, client_port = free_port(), free_port()
    args = entry(language, folder)
    if storage == "sqlite":
        cli(
            [*args, "init"],
            work,
            env,
            {"schema_version": 1, "storage_contract": "text-upload-sqlite-v1"},
        )
    launch = [shutil.which("node"), str(client / ".verify-vite.mjs")]
    with service(
        launch,
        client,
        {**env, "LAB_API_PORT": str(api_port)},
        client_port,
        counters,
        api=False,
    ):
        with service(
            [*args, *(["serve", "--storage", "sqlite"] if storage == "sqlite" else [])],
            work,
            env,
            api_port,
            counters,
            api=True,
        ):
            with browser_page(browser, f"http://127.0.0.1:{client_port}", clock=clock) as (
                page,
                downloads,
            ):
                yield page, downloads, api_port


def matrix(browser, folder, client, env, language, storage, counters, temporary, screenshots=None):
    from playwright.sync_api import expect

    work = workspace(folder, "browser-" + storage)
    api_port, client_port = free_port(), free_port()
    args = entry(language, folder)
    extra = ["serve", "--storage", "sqlite"] if storage == "sqlite" else []
    if storage == "sqlite":
        cli(
            [*args, "init"],
            work,
            env,
            {"schema_version": 1, "storage_contract": "text-upload-sqlite-v1"},
        )
    with service(
        [shutil.which("node"), str(client / ".verify-vite.mjs")],
        client,
        {**env, "LAB_API_PORT": str(api_port)},
        client_port,
        counters,
        api=False,
    ):
        with ExitStack() as backend:
            first_pid = backend.enter_context(
                service([*args, *extra], work, env, api_port, counters, api=True)
            )
            with browser_page(browser, f"http://127.0.0.1:{client_port}") as (page, _):
                first = upload(page)
                assert first["id"] == "doc-000001"
                listing(page, 1)
                document(page, 1).get_by_role("button", name="查看纯文本", exact=True).click()
                expect(page.get_by_label("纯文本原文", exact=True)).to_be_visible()
                assert page.get_by_label("纯文本原文", exact=True).text_content() == BODY.decode()
                download(page, 1, temporary / f"{language}-{storage}.bin")
                second = upload(page)
                assert second["id"] == "doc-000002"
                listing(page, 2)
                identity = page.get_by_label("教学身份", exact=True)
                for value in ("alice-second", "alice-readonly"):
                    identity.select_option(value)
                    listing(page, 2)
                expect(page.get_by_role("button", name="上传文件", exact=True)).to_be_disabled()
                identity.select_option("bob")
                listing(page, 0)
                assert page.get_by_label("选择文本文件", exact=True).input_value() == ""
                expect(page.get_by_role("region", name="原文预览", exact=True)).not_to_contain_text(
                    "<script>"
                )
                expect(page.get_by_role("region", name="上传结果", exact=True)).not_to_contain_text(
                    first["sha256"]
                )
                identity.select_option("alice")
                listing(page, 2)
                assert request(api_port, "POST", "/documents", body=b"other client")[0] == 201
                with page.expect_response(
                    lambda response: response.request.method == "POST"
                ) as refused:
                    upload(page, b"over quota", confirmed=False)
                assert refused.value.status == 409 and refused.value.json() == {
                    "error": "quota_exceeded"
                }
                expect(page.get_by_role("region", name="上传结果", exact=True)).to_contain_text(
                    "本次上传被拒绝。"
                )
                page.get_by_role("button", name="刷新我的列表", exact=True).click()
                listing(page, 3)
                visual(page)
                if screenshots is not None:
                    page.screenshot(path=str(screenshots / "upload-desktop.png"), full_page=True)
                if storage == "sqlite":
                    rows = database_records(work)
                    assert len(rows) == 3 and rows[0][6] == BODY and rows[1][6] == BODY
                else:
                    assert not (work / ".data").exists()
                backend.close()
                with service(
                    [*args, *extra], work, env, api_port, counters, api=True
                ) as second_pid:
                    assert second_pid != first_pid
                    page.get_by_role("button", name="刷新我的列表", exact=True).click()
                    listing(page, 3 if storage == "sqlite" else 0)
                    if storage == "sqlite":
                        download(page, 1, temporary / f"{language}-restart.bin")
                        assert request(api_port, "POST", "/documents", body=b"still full") == (
                            409,
                            {"error": "quota_exceeded"},
                        )
                        assert len(database_records(work)) == 3
                    else:
                        assert not (work / ".data").exists()
    return {
        "language": language,
        "storage": storage,
        "original_bytes": True,
        "restart": "persisted" if storage == "sqlite" else "empty",
    }


def visual(page):
    from playwright.sync_api import expect

    document(page, 1).get_by_role("button", name="查看纯文本", exact=True).click()
    expect(page.get_by_label("纯文本原文", exact=True)).to_be_visible()
    assert page.get_by_label("纯文本原文", exact=True).text_content() == BODY.decode()
    preview = page.get_by_role("region", name="原文预览", exact=True)
    assert preview.locator("script,a").count() == 0
    assert page.evaluate("typeof window.__uploadSentinel") == "undefined"
    page.set_viewport_size({"width": 375, "height": 812})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("button", name="刷新我的列表", exact=True).focus()
    page.keyboard.press("Enter")
    listing(page, 3)
    page.set_viewport_size({"width": 1280, "height": 1000})


def unknown_response(browser, folder, client, env, language, counters, screenshots=None):
    from playwright.sync_api import expect

    work = workspace(folder, "browser-unknown")
    with running(browser, folder, client, env, language, work, "sqlite", counters) as (
        page,
        _,
        _port,
    ):
        dropped = []

        def lose_confirmed(route):
            if route.request.method != "POST":
                route.fallback()
                return
            response = route.fetch(timeout=5000, max_retries=0, max_redirects=0)
            try:
                assert response.status == 201
                metadata = response.json()
                assert metadata["id"] == "doc-000001"
                rows = database_records(work)
                assert len(rows) == 1 and rows[0][6] == BODY
                dropped.append(metadata)
            finally:
                response.dispose()
            route.abort("failed")

        page.route("**/api/documents", lose_confirmed)
        upload(page, confirmed=False)
        assert_unknown(page)
        page.unroute("**/api/documents", lose_confirmed)
        assert len(dropped) == 1 and page.evaluate("window.__uploadAudit.posts") == 1
        select_file(page, b"not automatically sent")
        expect(page.get_by_role("button", name="上传文件", exact=True)).to_be_disabled()
        identity = page.get_by_label("教学身份", exact=True)
        identity.select_option("alice-second")
        listing(page, 1)
        assert_unknown(page)
        page.route(
            "**/api/documents",
            lambda route: route.fulfill(
                status=503,
                headers={**HEADERS, "Content-Type": "application/json"},
                json={"error": "repository_unavailable"},
            ),
            times=1,
        )
        page.get_by_role("button", name="刷新我的列表", exact=True).click()
        expect(
            page.get_by_role("region", name="我的文档", exact=True).get_by_role("alert")
        ).to_be_visible()
        assert_unknown(page)
        page.get_by_role("button", name="刷新我的列表", exact=True).click()
        listing(page, 1)
        assert_unknown(page)
        identity.select_option("bob")
        listing(page, 0)
        expect(page.get_by_role("region", name="未确认上传", exact=True)).to_have_count(0)
        expect(page.get_by_role("region", name="上传结果", exact=True)).not_to_contain_text(
            "notes.md"
        )
        identity.select_option("alice")
        listing(page, 1)
        assert_unknown(page)
        page.get_by_role("button", name=ACK, exact=True).click()
        assert page.evaluate("window.__uploadAudit.posts") == 1
        assert page.get_by_label("选择文本文件", exact=True).input_value() == ""
        expect(page.get_by_role("button", name="上传文件", exact=True)).to_be_disabled()
        assert_unknown(page)
        result = upload(page)
        assert result["id"] == "doc-000002"
        listing(page, 2)
        assert_unknown(page)
        select_file(page)
        expect(page.get_by_role("button", name="上传文件", exact=True)).to_be_disabled()
        assert page.evaluate("window.__uploadAudit.posts") == 2
        rows = database_records(work)
        assert len(rows) == 2 and rows[0][6] == rows[1][6] == BODY
        if screenshots is not None:
            page.set_viewport_size({"width": 375, "height": 812})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(screenshots / "upload-unknown-mobile.png"), full_page=True)
    return {
        "real_201_then_browser_response_dropped": True,
        "posts_before_permission": 1,
        "posts_after_explicit_new_upload": 2,
        "old_uncertainty_retained": True,
    }


def gate_fetch(page, method, path):
    page.evaluate("value => {window.__fetchGate=value}", {"method": method, "path": path})


def release_gate(page, name="__fetchGate"):
    page.evaluate("name => window[name].release()", name)
    page.wait_for_function("name => window[name].returned === true", arg=name)
    checkpoint(page)


def late_identity(browser, folder, client, env, language, counters):
    from playwright.sync_api import expect

    work = workspace(folder, "browser-late")
    with running(browser, folder, client, env, language, work, "sqlite", counters) as (
        page,
        downloads,
        port,
    ):
        gate_fetch(page, "POST", "/api/documents")
        upload(page, confirmed=False)
        page.wait_for_function("window.__fetchGate.ready === true")
        page.evaluate("window.__heldAlice=window.__fetchGate")
        page.get_by_label("教学身份", exact=True).select_option("bob")
        listing(page, 0)
        gate_fetch(page, "POST", "/api/documents")
        upload(page, b"Bob only", name="bob.txt", confirmed=False)
        page.wait_for_function("window.__fetchGate.ready === true")
        release_gate(page, "__heldAlice")
        expect(page.get_by_role("region", name="上传结果", exact=True)).to_contain_text(
            "正在等待上传结果"
        )
        expect(page.get_by_role("button", name="停止等待", exact=True)).to_be_enabled()
        expect(page.get_by_role("region", name="未确认上传", exact=True)).to_have_count(0)
        release_gate(page)
        expect(page.get_by_role("region", name="上传结果", exact=True)).to_contain_text(
            "本次上传已确认。"
        )
        listing(page, 1)
        expect(document(page, 2)).to_contain_text("bob.txt")
        expect(page.get_by_role("region", name="我的文档", exact=True)).not_to_contain_text(
            "notes.md"
        )
        page.get_by_label("教学身份", exact=True).select_option("alice")
        listing(page, 1)
        assert_unknown(page)
        gate_fetch(page, "GET", "/api/documents")
        page.get_by_role("button", name="刷新我的列表", exact=True).click()
        page.wait_for_function("window.__fetchGate.ready === true")
        assert request(port, "POST", "/documents", body=b"newer list")[0] == 201
        page.get_by_role("button", name="刷新我的列表", exact=True).click()
        listing(page, 2)
        release_gate(page)
        listing(page, 2)
        gate_fetch(page, "GET", "/api/documents/doc-000001/content")
        document(page, 1).get_by_role("button", name="下载原始附件", exact=True).click()
        page.wait_for_function("window.__fetchGate.ready === true")
        page.get_by_label("教学身份", exact=True).select_option("bob")
        listing(page, 1)
        release_gate(page)
        assert not downloads and page.evaluate("window.__uploadAudit.urls.size") == 0
        assert len(database_records(work)) == 3
    return {"late_post_list_download_isolation": True}


def stopped_and_deadline(browser, folder, client, env, language, counters):
    from playwright.sync_api import expect

    for outcome in ("stop", "deadline"):
        work = workspace(folder, "browser-" + outcome)
        with running(
            browser,
            folder,
            client,
            env,
            language,
            work,
            "sqlite",
            counters,
            clock=outcome == "deadline",
        ) as (page, _, _port):
            gate_fetch(page, "POST", "/api/documents")
            upload(page, confirmed=False)
            page.wait_for_function("window.__fetchGate.ready === true")
            assert len(database_records(work)) == 1
            if outcome == "stop":
                page.get_by_role("button", name="停止等待", exact=True).click()
            else:
                page.clock.fast_forward(10001)
            assert_unknown(page)
            release_gate(page)
            assert_unknown(page)
            assert page.evaluate("window.__uploadAudit.posts") == 1
            expect(page.get_by_role("button", name="上传文件", exact=True)).to_be_disabled()
    return {"stop_after_dispatch": True, "deadline_with_browser_clock": True}


def local_read_selection(browser, folder, client, env, language, counters):
    from playwright.sync_api import expect

    work = workspace(folder, "browser-local-read")
    with running(browser, folder, client, env, language, work, "memory", counters) as (
        page,
        _,
        _port,
    ):
        page.evaluate("window.__fileGate={}")
        upload(page, confirmed=False)
        page.wait_for_function("window.__fileGate.ready === true")
        page.get_by_role("button", name="停止等待", exact=True).click()
        expect(page.get_by_role("region", name="上传结果", exact=True)).to_contain_text(
            "已停止本次等待。"
        )
        expect(page.get_by_role("region", name="未确认上传", exact=True)).to_have_count(0)
        assert page.evaluate("window.__uploadAudit.posts") == 0
        select_file(page, b"new file", name="new.txt")
        release_gate(page, "__fileGate")
        expect(page.get_by_label("已选文件", exact=True)).to_contain_text("new.txt")
        page.get_by_role("button", name="上传文件", exact=True).click()
        expect(page.get_by_role("region", name="上传结果", exact=True)).to_contain_text(
            "本次上传已确认。"
        )
        listing(page, 1)
        expect(document(page, 1)).to_contain_text("new.txt")
        assert page.evaluate("window.__uploadAudit.posts") == 1
    return {"stopped_before_post": True, "late_file_read_isolation": True}


def refusals_and_integrity(browser, folder, client, env, language, counters):
    from playwright.sync_api import expect

    work = workspace(folder, "browser-refusals")
    with running(browser, folder, client, env, language, work, "sqlite", counters) as (
        page,
        downloads,
        port,
    ):
        select_file(page, b"x" * 4097, name="large.txt")
        expect(
            page.get_by_role("region", name="上传文件设置", exact=True).get_by_role("alert")
        ).to_be_visible()
        expect(page.get_by_role("button", name="上传文件", exact=True)).to_be_disabled()
        assert page.evaluate("window.__uploadAudit.posts") == 0
        with page.expect_response(lambda response: response.request.method == "POST") as rejected:
            upload(page, b"\x80", name="bad.txt", confirmed=False)
        assert rejected.value.status == 422 and rejected.value.json() == {"error": "invalid_text"}
        expect(page.get_by_role("region", name="上传结果", exact=True)).to_contain_text(
            "本次上传被拒绝。"
        )
        assert database_records(work) == []
        upload(page)
        listing(page, 1)

        def corrupt_content(route):
            response = route.fetch(timeout=5000, max_retries=0, max_redirects=0)
            try:
                assert response.status == 200 and response.body() == BODY
                route.fulfill(response=response, body=b"x" + BODY[1:])
            finally:
                response.dispose()

        page.route("**/api/documents/doc-000001/content", corrupt_content, times=1)
        document(page, 1).get_by_role("button", name="下载原始附件", exact=True).click()
        expect(
            page.get_by_role("region", name="原文预览", exact=True).get_by_role("alert")
        ).to_be_visible()
        assert not downloads
        for identity in ("expired", "revoked"):
            page.get_by_label("教学身份", exact=True).select_option(identity)
            expect(
                page.get_by_role("region", name="我的文档", exact=True).get_by_role("alert")
            ).to_be_visible()
            listing(page, 0, failed=True)
        page.get_by_label("教学身份", exact=True).select_option("bob")
        listing(page, 0)
        upload(page, b"a" * 4096, name="full.txt")
        listing(page, 1)
        upload(page, b"b" * 4096, name="full.txt")
        listing(page, 2)
        with page.expect_response(lambda response: response.request.method == "POST") as rejected:
            upload(page, b"over", name="full.txt", confirmed=False)
        assert rejected.value.status == 409 and rejected.value.json() == {"error": "quota_exceeded"}
        expect(page.get_by_role("region", name="上传结果", exact=True)).to_contain_text(
            "本次上传被拒绝。"
        )
        assert len(database_records(work)) == 3
        status, data = request(port, "GET", "/documents", owner="bob")
        assert status == 200 and sum(item["size_bytes"] for item in data["documents"]) == 8192
    return {
        "local_size_and_real_utf8_rejection": True,
        "byte_quota_and_public_identity_rejection": True,
        "tampered_download_refused": True,
    }


def checked_command(args, cwd, env):
    process = subprocess.Popen(args, cwd=cwd, env=env, start_new_session=True)  # noqa: S603 - fixed package install/build commands
    try:
        status = process.wait(timeout=600)
        if status != 0:
            raise RuntimeError("Frozen package command failed")
    finally:
        stop_client(process)


def prepare(language, folder, env):
    with zipfile.ZipFile(DESTINATION / f"text-upload-{language}.zip") as package:
        package.extractall(folder)
    if language == "python":
        checked_command([shutil.which("uv"), "sync", "--locked"], folder, env)
    elif language == "typescript":
        checked_command(["pnpm", "install", "--frozen-lockfile"], folder, env)
        checked_command(["pnpm", "build"], folder, env)
    else:
        checked_command(["go", "build", "-mod=readonly", "-o", "lab-server", "."], folder, env)


def verify(selected, screenshots=None):
    from playwright.sync_api import sync_playwright

    archives = [DESTINATION / f"text-upload-{language}.zip" for language in LANGUAGES]
    members = identical_clients(archives)
    env = environment()
    counters = {"started_processes": 0, "reaped_processes": 0}
    temp_root = "/private/tmp" if Path("/private/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="deep-ai-upload-client-", dir=temp_root) as temporary:
        root = Path(temporary).resolve()
        if root.is_relative_to(ROOT):
            raise RuntimeError("Verification must be outside the checkout")
        folders = {}
        for language in selected:
            folder = root / language
            folder.mkdir()
            prepare(language, folder, env)
            folders[language] = folder
        client = folders[selected[0]] / "client"

        checked_command(["pnpm", "install", "--frozen-lockfile"], client, env)
        checked_command(["pnpm", "check"], client, env)
        (client / ".verify-vite.mjs").write_text(LAUNCHER, encoding="utf-8")
        matrix_results = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for language in selected:
                    for storage in ("memory", "sqlite"):
                        matrix_results.append(
                            matrix(
                                browser,
                                folders[language],
                                client,
                                env,
                                language,
                                storage,
                                counters,
                                root,
                                screenshots
                                if language == selected[0] and storage == "memory"
                                else None,
                            )
                        )
                representative = selected[0]
                folder = folders[representative]
                details = {}
                for check in (
                    unknown_response,
                    late_identity,
                    stopped_and_deadline,
                    local_read_selection,
                    refusals_and_integrity,
                ):
                    options = {"screenshots": screenshots} if check is unknown_response else {}
                    details.update(
                        check(browser, folder, client, env, representative, counters, **options)
                    )
            finally:
                browser.close()
        assert counters["started_processes"] == counters["reaped_processes"]
    print(
        json.dumps(
            {
                "lab": "text-upload-client-v1",
                "client_archive_members_equal": members,
                "matrix": matrix_results,
                "detailed_backend": representative,
                "checks": details,
                **counters,
                "client_serving": "built_dist_no_hmr",
                "documents_stable": True,
                "headless_closed": True,
                "listeners_released": True,
                "model_calls": 0,
            },
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=LANGUAGES)
    parser.add_argument("--screenshots-dir", type=Path)
    args = parser.parse_args()
    screenshots = args.screenshots_dir.resolve() if args.screenshots_dir else None
    if screenshots is not None:
        if screenshots.is_relative_to(ROOT):
            parser.error("Screenshot output must be outside the checkout")
        screenshots.mkdir(parents=True, exist_ok=True)
    verify([args.language] if args.language else list(LANGUAGES), screenshots)


if __name__ == "__main__":
    main()
