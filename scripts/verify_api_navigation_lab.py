"""Verify the same navigable React client against three actual unpacked APIs.

Manual URL/request/result expectations precede implementation. Product parsers
are never an oracle; only the maintainer's fixed ZIPs and one fixed temporary
navigation mutation are executed. All browsers and listeners are owned and reaped.
"""

# ruff: noqa: S101 - assertions express independent acceptance evidence

import argparse
import http.client
import json
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from build_course_labs import DESTINATION, ROOT
from verify_course_labs import ensure_listener_closed
from verify_frontend_state_lab import preview
from verify_memory_lab import canonical, decode
from verify_upload_client import LAUNCHER, checked_command, environment, free_port
from verify_upload_storage import stop_direct_process

LANGUAGES = ("python", "typescript", "go")
CLIENT_FILES = {
    "package.json",
    "pnpm-lock.yaml",
    "tsconfig.json",
    "vite.config.ts",
    "index.html",
    "src/main.tsx",
    "src/App.tsx",
    "src/style.css",
    "src/components/ui/button.tsx",
    "src/navigation.ts",
    "src/navigation.test.ts",
    "src/protocol.ts",
    "src/protocol.test.ts",
    "src/controller.ts",
    "src/controller.test.ts",
}
COMMON = {
    "CLIENT.md",
    "README.md",
    "CONTRACT.md",
    "EVIDENCE.md",
    "AGENTS.md",
    ".gitignore",
    "lessons.json",
    "contract-cases.json",
    "manifest.json",
    ".prettierrc.json",
}
BACKEND_FILES = {
    "python": {"app.py", "service.py", "repository.py", "test_app.py", "pyproject.toml", "uv.lock"},
    "typescript": {
        "src/app.ts",
        "src/service.ts",
        "src/repository.ts",
        "src/server.ts",
        "src/app.test.ts",
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
    },
    "go": {"app.go", "service.go", "repository.go", "main.go", "app_test.go", "go.mod", "go.sum"},
}
TOOL_URL = "/search?q=%E5%B7%A5%E5%85%B7"
DETAIL_URL = "/lessons/tools?q=%E5%B7%A5%E5%85%B7"
TOOL_RESULT = {"question": "工具", "items": [{"id": "tools", "title": "工具契约"}]}
HTTP_RESULT = {"question": "HTTP", "items": [{"id": "http", "title": "HTTP API"}]}
SOURCES = [
    {"id": "tools", "title": "工具契约", "body": "工具应声明参数、返回值与权限边界。"},
    {"id": "http", "title": "HTTP API", "body": "HTTP 请求通过路由返回稳定的 JSON 契约。"},
    {
        "id": "validation",
        "title": "输入校验",
        "body": "使用明确类型，拒绝未知字段、空白与过长问题。",
    },
]
FULL_SEQUENCE = [
    {
        "step": 1,
        "operation": "open-search",
        "url": "/search",
        "new_request": None,
        "http_status": None,
        "response": None,
        "view": "idle",
        "draft": "",
        "submitted_question": None,
        "request_delta": 0,
        "cumulative_full_requests": 0,
    },
    {
        "step": 2,
        "operation": "type-tools-and-enter",
        "url": "/search?q=%E5%B7%A5%E5%85%B7",
        "new_request": {"method": "POST", "path": "/api/search", "body": {"question": "工具"}},
        "http_status": 200,
        "response": {"question": "工具", "items": [{"id": "tools", "title": "工具契约"}]},
        "view": "success",
        "draft": "工具",
        "submitted_question": "工具",
        "request_delta": 1,
        "cumulative_full_requests": 1,
    },
    {
        "step": 3,
        "operation": "edit-http-without-submit",
        "url": "/search?q=%E5%B7%A5%E5%85%B7",
        "new_request": None,
        "http_status": None,
        "response": None,
        "view": "success",
        "draft": "HTTP",
        "submitted_question": "工具",
        "request_delta": 0,
        "cumulative_full_requests": 1,
    },
    {
        "step": 4,
        "operation": "open-tools-detail",
        "url": "/lessons/tools?q=%E5%B7%A5%E5%85%B7",
        "new_request": {"method": "GET", "path": "/api/lessons/tools", "body": None},
        "http_status": 200,
        "response": {"id": "tools", "title": "工具契约"},
        "view": "success",
        "draft": None,
        "submitted_question": None,
        "request_delta": 1,
        "cumulative_full_requests": 2,
    },
    {
        "step": 5,
        "operation": "reload-detail",
        "url": "/lessons/tools?q=%E5%B7%A5%E5%85%B7",
        "new_request": {"method": "GET", "path": "/api/lessons/tools", "body": None},
        "http_status": 200,
        "response": {"id": "tools", "title": "工具契约"},
        "view": "success",
        "draft": None,
        "submitted_question": None,
        "request_delta": 1,
        "cumulative_full_requests": 3,
    },
    {
        "step": 6,
        "operation": "browser-back",
        "url": "/search?q=%E5%B7%A5%E5%85%B7",
        "new_request": {"method": "POST", "path": "/api/search", "body": {"question": "工具"}},
        "http_status": 200,
        "response": {"question": "工具", "items": [{"id": "tools", "title": "工具契约"}]},
        "view": "success",
        "draft": "工具",
        "submitted_question": "工具",
        "request_delta": 1,
        "cumulative_full_requests": 4,
    },
    {
        "step": 7,
        "operation": "browser-forward",
        "url": "/lessons/tools?q=%E5%B7%A5%E5%85%B7",
        "new_request": {"method": "GET", "path": "/api/lessons/tools", "body": None},
        "http_status": 200,
        "response": {"id": "tools", "title": "工具契约"},
        "view": "success",
        "draft": None,
        "submitted_question": None,
        "request_delta": 1,
        "cumulative_full_requests": 5,
    },
    {
        "step": 8,
        "operation": "return-search-results",
        "url": "/search?q=%E5%B7%A5%E5%85%B7",
        "new_request": {"method": "POST", "path": "/api/search", "body": {"question": "工具"}},
        "http_status": 200,
        "response": {"question": "工具", "items": [{"id": "tools", "title": "工具契约"}]},
        "view": "success",
        "draft": "工具",
        "submitted_question": "工具",
        "request_delta": 1,
        "cumulative_full_requests": 6,
    },
    {
        "step": 9,
        "operation": "submit-empty-query",
        "url": "/search?q=%E7%81%AB%E6%98%9F%E7%9B%86%E6%A0%BD",
        "new_request": {"method": "POST", "path": "/api/search", "body": {"question": "火星盆栽"}},
        "http_status": 200,
        "response": {"question": "火星盆栽", "items": []},
        "view": "empty",
        "draft": "火星盆栽",
        "submitted_question": "火星盆栽",
        "request_delta": 1,
        "cumulative_full_requests": 7,
    },
    {
        "step": 10,
        "operation": "direct-missing",
        "url": "/lessons/missing",
        "new_request": {"method": "GET", "path": "/api/lessons/missing", "body": None},
        "http_status": 404,
        "response": {"error": "lesson_not_found"},
        "view": "not_found",
        "draft": None,
        "submitted_question": None,
        "request_delta": 1,
        "cumulative_full_requests": 8,
    },
]
MINIMAL_SEQUENCE = [
    {
        "step": 1,
        "operation": "open-search",
        "url": "/search",
        "new_request": None,
        "http_status": None,
        "response": None,
        "view": "idle",
        "draft": "",
        "submitted_question": None,
        "request_delta": 0,
        "cumulative_full_requests": 0,
    },
    {
        "step": 2,
        "operation": "type-tools-and-enter",
        "url": "/search?q=%E5%B7%A5%E5%85%B7",
        "new_request": {"method": "POST", "path": "/api/search", "body": {"question": "工具"}},
        "http_status": 200,
        "response": {"question": "工具", "items": [{"id": "tools", "title": "工具契约"}]},
        "view": "success",
        "draft": "工具",
        "submitted_question": "工具",
        "request_delta": 1,
        "cumulative_full_requests": 1,
    },
    {
        "step": 3,
        "operation": "edit-http-without-submit",
        "url": "/search?q=%E5%B7%A5%E5%85%B7",
        "new_request": None,
        "http_status": None,
        "response": None,
        "view": "success",
        "draft": "HTTP",
        "submitted_question": "工具",
        "request_delta": 0,
        "cumulative_full_requests": 1,
    },
    {
        "step": 4,
        "operation": "open-tools-detail",
        "url": "/lessons/tools?q=%E5%B7%A5%E5%85%B7",
        "new_request": {"method": "GET", "path": "/api/lessons/tools", "body": None},
        "http_status": 200,
        "response": {"id": "tools", "title": "工具契约"},
        "view": "success",
        "draft": None,
        "submitted_question": None,
        "request_delta": 1,
        "cumulative_full_requests": 2,
    },
    {
        "step": 5,
        "operation": "reload-detail",
        "url": "/lessons/tools?q=%E5%B7%A5%E5%85%B7",
        "new_request": {"method": "GET", "path": "/api/lessons/tools", "body": None},
        "http_status": 200,
        "response": {"id": "tools", "title": "工具契约"},
        "view": "success",
        "draft": None,
        "submitted_question": None,
        "request_delta": 1,
        "cumulative_full_requests": 3,
    },
    {
        "step": 6,
        "operation": "browser-back",
        "url": "/search?q=%E5%B7%A5%E5%85%B7",
        "new_request": {"method": "POST", "path": "/api/search", "body": {"question": "工具"}},
        "http_status": 200,
        "response": {"question": "工具", "items": [{"id": "tools", "title": "工具契约"}]},
        "view": "success",
        "draft": "工具",
        "submitted_question": "工具",
        "request_delta": 1,
        "cumulative_full_requests": 4,
    },
    {
        "step": 9,
        "operation": "submit-empty-query",
        "url": "/search?q=%E7%81%AB%E6%98%9F%E7%9B%86%E6%A0%BD",
        "new_request": {"method": "POST", "path": "/api/search", "body": {"question": "火星盆栽"}},
        "http_status": 200,
        "response": {"question": "火星盆栽", "items": []},
        "view": "empty",
        "draft": "火星盆栽",
        "submitted_question": "火星盆栽",
        "request_delta": 1,
        "cumulative_full_requests": 5,
    },
    {
        "step": 10,
        "operation": "direct-missing",
        "url": "/lessons/missing",
        "new_request": {"method": "GET", "path": "/api/lessons/missing", "body": None},
        "http_status": 404,
        "response": {"error": "lesson_not_found"},
        "view": "not_found",
        "draft": None,
        "submitted_question": None,
        "request_delta": 1,
        "cumulative_full_requests": 6,
    },
]
AUDIT = r"""() => {
  const originalFetch = window.fetch.bind(window);
  const audit = window.__navigationAudit = {ticket:0, pending:0, failures:[], gate:null, holdNext:false, initialHistoryLength:history.length};
  const nativeSet = Storage.prototype.setItem;
  nativeSet.call(localStorage, 'deep-ai-station:v1', 'public-platform-sentinel');
  Storage.prototype.setItem = function() { audit.failures.push('storage_write'); throw new Error('Unexpected storage write'); };
  Storage.prototype.removeItem = function() { audit.failures.push('storage_remove'); throw new Error('Unexpected storage remove'); };
  Storage.prototype.clear = function() { audit.failures.push('storage_clear'); throw new Error('Unexpected storage clear'); };
  async function capture(response, record, body) {
    audit.pending++;
    let reader, timer;
    try {
      const clone = response.clone();
      reader = clone.body?.getReader();
      const chunks = [];
      let size = 0;
      const read = async () => {
        if (reader) while (true) {
          const part = await reader.read();
          if (part.done) break;
          size += part.value.byteLength;
          if (size > 32768) throw new Error('capture_limit');
          chunks.push(...part.value);
        }
        return chunks;
      };
      const bytes = await Promise.race([read(), new Promise((_,reject) => {
        timer = setTimeout(() => reject(new Error('capture_timeout')), 4000);
      })]);
      record.requestBody = await body;
      record.status = response.status;
      record.responseUrl = response.url;
      record.responseHeaders = Object.fromEntries(response.headers);
      record.bytes = bytes;
      record.state = 'complete';
    } catch {
      record.state = 'failed'; audit.failures.push('response_capture_failed');
    } finally {
      clearTimeout(timer);
      if (reader) {
        void reader.cancel().catch(() => {});
        try { reader.releaseLock(); } catch {}
      }
      audit.pending--;
      await window.__navigationEvidence(record);
    }
  }
  window.fetch = function(input, init) {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (!/^\/api(?:\/|$)/.test(url.pathname)) return originalFetch(input, init);
    const ticket = ++audit.ticket;
    const record = {ticket, document:performance.timeOrigin, requestUrl:request.url,
      method:request.method, requestHeaders:Object.fromEntries(request.headers),
      credentials:request.credentials, redirect:request.redirect};
    const body = request.clone().text();
    body.catch(() => {});
    const hold = audit.holdNext;
    audit.holdNext = false;
    const promise = hold ? originalFetch(input, {...init, signal:new AbortController().signal}) : originalFetch(input, init);
    return promise.then(response => {
      void capture(response, record, body);
      if (hold) return new Promise(resolve => {audit.gate = {release:() => resolve(response)};});
      return response;
    }, error => {
      audit.failures.push('unexpected_fetch_failure');
      throw error;
    });
  };
}
"""


def install_audit(page):
    # add_init_script evaluates source; unlike evaluate, it does not call a function.
    page.add_init_script(script=f"({AUDIT})();")


class NavigationMismatch(AssertionError):
    """Only missing/wrong URL-derived business state counts as mutation detection."""


def same(actual, expected):
    assert canonical(actual) == canonical(expected), "Navigation contract evidence differs"


def unpack(language, folder):
    expected = COMMON | BACKEND_FILES[language] | {"client/" + name for name in CLIENT_FILES}
    with zipfile.ZipFile(DESTINATION / f"api-contract-{language}.zip") as archive:
        entries = archive.infolist()
        assert len(entries) == len(expected) and {entry.filename for entry in entries} == expected
        assert sum(entry.file_size for entry in entries) <= 5_000_000
        assert all(
            not entry.is_dir() and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
            for entry in entries
        )
        content = {entry.filename: archive.read(entry) for entry in entries}
    same(
        decode(content["manifest.json"]),
        {
            "id": "api-contract",
            "version": "api-contract-v1",
            "lessons": ["fullstack-routing", "fullstack-validation"],
            "languages": list(LANGUAGES),
        },
    )
    same(decode(content["lessons.json"]), SOURCES)
    for name, value in content.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    return {name: content["client/" + name] for name in CLIENT_FILES}, content


def prepare(language, folder, env):
    if language == "python":
        checked_command([shutil.which("uv"), "sync", "--frozen", "--python", "3.12"], folder, env)
    elif language == "typescript":
        checked_command([shutil.which("pnpm"), "install", "--frozen-lockfile"], folder, env)
        checked_command([shutil.which("pnpm"), "build"], folder, env)
    else:
        checked_command(
            [shutil.which("go"), "build", "-mod=readonly", "-o", "lab-server", "."], folder, env
        )


@contextmanager
def backend(language, folder, env, counters):
    port = free_port()
    command = {
        "python": [
            str(folder / ".venv/bin/python"),
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
            "--log-level",
            "error",
        ],
        "typescript": [shutil.which("node"), str(folder / "dist/server.js")],
        "go": [str(folder / "lab-server")],
    }[language]
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(  # noqa: S603 - fixed packaged API entry
            command,
            cwd=folder,
            env={**env, "PORT": str(port)},
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        counters["backend_started"] += 1
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Owned API exited before readiness")
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
                try:
                    connection.request("GET", "/health")
                    response = connection.getresponse()
                    if response.status == 200:
                        same(
                            decode(response.read(4097)), {"status": "ok", "lab": "api-contract-v1"}
                        )
                        break
                except (OSError, http.client.HTTPException):
                    pass
                finally:
                    connection.close()
                if time.monotonic() > deadline:
                    raise TimeoutError("Owned API readiness deadline")
                time.sleep(0.05)
            yield port
        finally:
            failures = []
            for cleanup in (
                lambda: stop_direct_process(process),
                lambda: ensure_listener_closed(port),
            ):
                try:
                    cleanup()
                except Exception as error:
                    failures.append(error)
            log.seek(0)
            if log.read(4097).strip():
                failures.append(RuntimeError("Unexpected API diagnostics"))
            if failures:
                raise RuntimeError("Owned API cleanup failed") from failures[0]
            counters["backend_reaped"] += 1


def check_capture(record, request, response, expected_request, expected_body, status):
    target = urlsplit(response.url)
    assert record["state"] == "complete" and type(record["ticket"]) is int and record["ticket"] > 0
    assert record["requestUrl"] == record["responseUrl"] == response.url == request.url
    assert record["method"] == request.method == expected_request["method"]
    assert target.path == expected_request["path"] and not target.query
    assert record["credentials"] == "omit" and record["redirect"] == "error"
    assert type(record["status"]) is int and record["status"] == response.status == status
    headers = request.all_headers()
    assert "authorization" not in headers and "cookie" not in headers
    raw = request.post_data_buffer or b""
    assert record["requestBody"].encode("utf-8") == raw
    if expected_request["body"] is None:
        assert raw == b""
    else:
        same(decode(raw), expected_request["body"])
        assert headers["content-type"].split(";", 1)[0].lower() == "application/json"
    media = response.headers.get("content-type", "")
    assert record["responseHeaders"].get("content-type") == media
    assert media.split(";", 1)[0].strip(" \t").lower() == "application/json"
    value = record["bytes"]
    assert isinstance(value, list) and 0 < len(value) <= 32768
    assert all(type(byte) is int and 0 <= byte <= 255 for byte in value)
    same(decode(bytes(value)), expected_body)


@contextmanager
def page_case(browser, base, *, mobile=False):
    context = browser.new_context(
        viewport={"width": 375 if mobile else 1280, "height": 900}, service_workers="block"
    )
    page = context.new_page()
    page.set_default_timeout(6000)
    audit = {"requests": [], "responses": [], "captures": [], "blocked": [], "ws": [], "errors": []}
    page.expose_binding(
        "__navigationEvidence", lambda _source, record: audit["captures"].append(record)
    )
    install_audit(page)
    page.on(
        "request",
        lambda request: (
            audit["requests"].append(request)
            if urlsplit(request.url).path.startswith("/api/")
            else None
        ),
    )
    page.on(
        "response",
        lambda response: (
            audit["responses"].append(response)
            if urlsplit(response.url).path.startswith("/api/")
            else None
        ),
    )
    page.on("pageerror", lambda _error: audit["errors"].append("pageerror"))

    def allow(route):
        request = route.request
        url = urlsplit(request.url)
        origin = urlsplit(base)
        allowed = (
            url.scheme == origin.scheme
            and url.netloc == origin.netloc
            and (
                (request.method == "POST" and url.path == "/api/search")
                or (request.method == "GET" and url.path.startswith("/api/lessons/"))
                or (
                    request.method == "GET"
                    and (
                        (
                            request.resource_type == "document"
                            and url.path != "/api"
                            and not url.path.startswith("/api/")
                        )
                        or (
                            url.path.startswith("/assets/")
                            and request.resource_type
                            in {"script", "stylesheet", "image", "font", "other"}
                        )
                        or url.path == "/favicon.ico"
                    )
                )
            )
        )
        if allowed:
            route.continue_()
        else:
            audit["blocked"].append(request.method + " " + url.path)
            route.abort()

    context.route("**/*", allow)

    def socket(socket):
        audit["ws"].append(True)
        socket.close()

    context.route_web_socket("**", socket)
    try:
        yield page, audit
        checkpoint(page, audit)
        assert not audit["blocked"] and not audit["ws"] and not audit["errors"]
        scripts = page.locator('script[type="module"][src]').evaluate_all(
            "nodes=>nodes.map(node=>new URL(node.src).pathname)"
        )
        assert scripts and all(path.startswith("/assets/") for path in scripts)
        assert len(audit["requests"]) == len(audit["responses"]) == len(audit["captures"])
        assert len({(item["document"], item["ticket"]) for item in audit["captures"]}) == len(
            audit["captures"]
        )
        assert page.evaluate(
            "localStorage.length===1 && localStorage.getItem('deep-ai-station:v1')==='public-platform-sentinel' && sessionStorage.length===0"
        )
        assert page.evaluate("indexedDB.databases().then(items=>items.length)") == 0
    finally:
        try:
            if not page.is_closed():
                page.evaluate("window.__navigationAudit?.gate?.release()")
        finally:
            context.close()


def checkpoint(page, audit):
    page.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
    assert page.evaluate("window.__navigationAudit.failures") == []
    assert page.evaluate("window.__navigationAudit.pending") == 0
    assert all(item["state"] == "complete" for item in audit["captures"])


def wait_capture(page, audit, count):
    deadline = time.monotonic() + 7
    while len(audit["captures"]) < count:
        if time.monotonic() > deadline:
            raise TimeoutError("Real HTTP response capture missing")
        page.wait_for_timeout(10)
    assert len(audit["captures"]) == count


def panel(page):
    locator = page.get_by_label("本次请求记录", exact=True)
    if not locator.is_visible():
        page.get_by_text("查看本次请求", exact=True).click()
    return decode(locator.text_content().encode("utf-8"))


def assert_target(page, url, expected_request, response, status, outcome, *, draft=None):
    from playwright.sync_api import expect

    expect(page.get_by_role("heading", name="可导航 API 检索实验", exact=True)).to_be_visible()
    assert page.evaluate("location.pathname+location.search") == url
    if expected_request["method"] == "POST":
        expect(page.get_by_label("已提交查询", exact=True)).to_have_text(
            expected_request["body"]["question"]
        )
        expect(page.get_by_label("检索问题", exact=True)).to_have_value(
            draft if draft is not None else expected_request["body"]["question"]
        )
        region = page.get_by_role("region", name="查询结果", exact=True)
        if outcome == "empty":
            expect(region).to_contain_text("没有匹配资料；这是合法的空结果。")
        else:
            expect(region).to_contain_text(f"找到 {len(response['items'])} 条资料")
        assert region.locator("article").count() == len(response["items"])
        for item in response["items"]:
            expect(region.get_by_role("article", name=item["id"], exact=True)).to_contain_text(
                item["title"]
            )
    elif outcome == "not_found":
        expect(page.get_by_role("alert")).to_contain_text("资料不存在（404）。")
    else:
        region = page.get_by_role("region", name="资料摘要", exact=True)
        expect(region).to_contain_text(response["title"])
        expect(region).to_contain_text(response["id"])
    same(
        panel(page),
        {
            "request": expected_request,
            "response": {"status": status, "body": response},
            "outcome": outcome,
        },
    )


def verify_row(page, audit, row):
    count = row["cumulative_full_requests"]
    if row["request_delta"]:
        wait_capture(page, audit, count)
        check_capture(
            audit["captures"][-1],
            audit["requests"][-1],
            audit["responses"][-1],
            row["new_request"],
            row["response"],
            row["http_status"],
        )
        assert_target(
            page,
            row["url"],
            row["new_request"],
            row["response"],
            row["http_status"],
            row["view"],
            draft=row["draft"],
        )
    checkpoint(page, audit)
    assert len(audit["requests"]) == len(audit["responses"]) == len(audit["captures"]) == count


def core_sequence(browser, base, language):
    from playwright.sync_api import expect

    rows = FULL_SEQUENCE if language == "python" else MINIMAL_SEQUENCE
    with page_case(browser, base) as (page, audit):
        for row in rows:
            action = row["operation"]
            if action == "open-search":
                page.goto(base + "/search")
                expect(page.get_by_label("已提交查询", exact=True)).to_have_text("尚未提交")
                expect(page.get_by_label("检索问题", exact=True)).to_have_value("")
                assert page.get_by_label("本次请求记录", exact=True).count() == 0
            elif action == "type-tools-and-enter":
                page.get_by_label("检索问题", exact=True).fill("工具")
                page.get_by_label("检索问题", exact=True).press("Enter")
            elif action == "edit-http-without-submit":
                before = panel(page)
                page.get_by_label("检索问题", exact=True).fill("HTTP")
                expect(
                    page.get_by_text("草稿尚未提交；当前结果仍对应上次查询。", exact=True)
                ).to_be_visible()
                assert_target(
                    page,
                    TOOL_URL,
                    FULL_SEQUENCE[1]["new_request"],
                    TOOL_RESULT,
                    200,
                    "success",
                    draft="HTTP",
                )
                same(panel(page), before)
            elif action == "open-tools-detail":
                page.get_by_role("link", name="查看资料：工具契约", exact=True).click()
            elif action == "reload-detail":
                page.reload()
            elif action == "browser-back":
                page.go_back()
            elif action == "browser-forward":
                page.go_forward()
            elif action == "return-search-results":
                page.get_by_role("link", name="返回搜索结果", exact=True).click()
            elif action == "submit-empty-query":
                page.get_by_label("检索问题", exact=True).fill("火星盆栽")
                page.get_by_role("button", name="搜索", exact=True).click()
            elif action == "direct-missing":
                page.goto(base + "/lessons/missing")
            verify_row(page, audit, row)
        return len(audit["requests"])


def assert_http_step(page, audit, number, question, response):
    expected = {"method": "POST", "path": "/api/search", "body": {"question": question}}
    wait_capture(page, audit, number)
    check_capture(
        audit["captures"][-1],
        audit["requests"][-1],
        audit["responses"][-1],
        expected,
        response,
        200,
    )
    outcome = "success" if response["items"] else "empty"
    assert_target(
        page, page.evaluate("location.pathname+location.search"), expected, response, 200, outcome
    )
    checkpoint(page, audit)
    assert len(audit["requests"]) == number


def deep_navigation(browser, base, screenshots):
    from playwright.sync_api import expect

    with page_case(browser, base, mobile=True) as (page, audit):
        page.goto(base + "/?%71=%C2%85%E5%B7%A5%E5%85%B7%E3%80%80")
        assert_http_step(page, audit, 1, "工具", TOOL_RESULT)
        assert page.evaluate("location.pathname+location.search") == TOOL_URL
        assert page.evaluate("history.length===window.__navigationAudit.initialHistoryLength")
        before = page.evaluate("history.length")
        field = page.get_by_label("检索问题", exact=True)
        field.focus()
        field.press("ArrowRight")
        field.press("ArrowRight")
        assert field.evaluate("node => [node.selectionStart, node.selectionEnd]") == [2, 2]
        field.press("Shift+Enter")
        expect(field).to_have_value("工具\n")
        checkpoint(page, audit)
        assert len(audit["requests"]) == 1
        field.press("Enter")
        assert_http_step(page, audit, 2, "工具", TOOL_RESULT)
        assert page.evaluate("history.length") == before
        original = panel(page)
        field.fill("\u0085\u00a0")
        page.get_by_role("button", name="搜索", exact=True).click()
        expect(field).to_be_focused()
        expect(field).to_have_attribute("aria-invalid", "true")
        expect(page.get_by_role("alert")).to_contain_text("请输入 1–500 个 Unicode 码点的问题。")
        same(panel(page), original)
        checkpoint(page, audit)
        assert (
            len(audit["requests"]) == 2
            and page.evaluate("location.pathname+location.search") == TOOL_URL
        )
        field.fill("HTTP")
        expect(
            page.get_by_text("草稿尚未提交；当前结果仍对应上次查询。", exact=True)
        ).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        summary = page.get_by_text("查看本次请求", exact=True)
        summary.focus()
        summary.press("Tab")
        expect(page.get_by_label("本次请求记录", exact=True)).to_be_focused()
        if screenshots:
            page.screenshot(path=str(screenshots / "api-navigation-mobile.png"), full_page=True)
        for route, message in (
            ("/search?q=工具&q=工具", "地址参数无效"),
            ("/search?q=工具&view=compact", "地址参数无效"),
            ("/search?q=%FF", "地址参数无效"),
            ("/search/?q=%FF#extra", "页面不存在"),
        ):
            page.goto(base + route)
            expect(page.get_by_text(message, exact=True)).to_be_visible()
            assert page.get_by_label("本次请求记录", exact=True).count() == 0
            checkpoint(page, audit)
            assert len(audit["requests"]) == 2
        page.goto(base + "/search?q=%EF%BB%BF")
        assert_http_step(page, audit, 3, "\ufeff", {"question": "\ufeff", "items": []})
        assert page.evaluate("location.pathname+location.search") == "/search?q=%EF%BB%BF"
        return len(audit["requests"])


def late_response(browser, base):
    from playwright.sync_api import expect

    with page_case(browser, base) as (page, audit):
        page.goto(base + "/search")
        expect(page.get_by_label("检索问题", exact=True)).to_be_visible()
        page.evaluate("window.__navigationAudit.holdNext=true")
        page.get_by_label("检索问题", exact=True).fill("工具")
        page.get_by_role("button", name="搜索", exact=True).click()
        page.wait_for_function("window.__navigationAudit.gate!==null")
        wait_capture(page, audit, 1)
        check_capture(
            audit["captures"][0],
            audit["requests"][0],
            audit["responses"][0],
            FULL_SEQUENCE[1]["new_request"],
            TOOL_RESULT,
            200,
        )
        page.get_by_label("检索问题", exact=True).fill("HTTP")
        page.get_by_role("button", name="搜索", exact=True).click()
        assert_http_step(page, audit, 2, "HTTP", HTTP_RESULT)
        page.evaluate("window.__navigationAudit.gate.release()")
        checkpoint(page, audit)
        assert_target(
            page,
            "/search?q=HTTP",
            {"method": "POST", "path": "/api/search", "body": {"question": "HTTP"}},
            HTTP_RESULT,
            200,
            "success",
        )
        assert len(audit["requests"]) == 2
        return 2


def require_url_query(display, draft, audit, failures, pending, previous_requests):
    if display == "工具":
        return
    # Only the frozen mutation's specific idle target is a valid negative
    # control. Network, capture, schema and application faults must fail normally.
    assert not failures and pending == 0
    assert not audit["blocked"] and not audit["ws"] and not audit["errors"]
    assert all(record["state"] == "complete" for record in audit["captures"])
    assert (
        len(audit["requests"])
        == len(audit["responses"])
        == len(audit["captures"])
        == previous_requests
    )
    assert display == "尚未提交" and draft == "", (
        "Unexpected query state is not the frozen mutation"
    )
    raise NavigationMismatch("Direct URL incorrectly became the idle search target")


def direct_url_assertion(browser, base):
    from playwright.sync_api import expect

    with page_case(browser, base) as (page, audit):
        for count in (1, 2):
            if count == 1:
                page.goto(base + TOOL_URL)
            else:
                page.reload()
            expect(
                page.get_by_role("heading", name="可导航 API 检索实验", exact=True)
            ).to_be_visible()
            submitted = page.get_by_label("已提交查询", exact=True)
            expect(submitted).to_be_visible()
            page.evaluate(
                "new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))"
            )
            assert page.evaluate("location.pathname+location.search") == TOOL_URL
            require_url_query(
                submitted.text_content(),
                page.get_by_label("检索问题", exact=True).input_value(),
                audit,
                page.evaluate("window.__navigationAudit.failures"),
                page.evaluate("window.__navigationAudit.pending"),
                count - 1,
            )
            # Deliberately no catch: unrelated HTTP/observer/protocol/panel errors
            # cannot count as detection of the bad URL derivative.
            assert_http_step(page, audit, count, "工具", TOOL_RESULT)
        return 2


# Frozen with the navigation-module author before running any mutation.
MUTATION_FROM = (
    "if (searchPage) return { kind: 'search', question, canonical: searchHref(question) };"
)
MUTATION_TO = (
    "if (searchPage) return { kind: 'search', question: null, canonical: searchHref(question) };"
)


def make_wrong_navigation(source):
    if source.count(MUTATION_FROM) != 1:
        raise RuntimeError("Fixed navigation mutation target missing or ambiguous")
    return source.replace(MUTATION_FROM, MUTATION_TO, 1)


def check_packaged_sources(folder, contents):
    for name, body in contents.items():
        assert (folder / name).read_bytes() == body, "Original packaged source changed"


def verify(screenshots=None):
    from playwright.sync_api import sync_playwright

    counters = {
        "backend_started": 0,
        "backend_reaped": 0,
        "preview_started": 0,
        "preview_reaped": 0,
    }
    with tempfile.TemporaryDirectory(prefix="deep-ai-api-navigation-") as temporary:
        root = Path(temporary).resolve()
        assert not root.is_relative_to(ROOT)
        env = environment()
        home = root / "home"
        home.mkdir()
        env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
        packages = {}
        contents = {}
        shared_client = None
        for language in LANGUAGES:
            folder = root / language
            folder.mkdir()
            files, original = unpack(language, folder)
            if shared_client is None:
                shared_client = files
            else:
                assert files == shared_client, "ZIP clients differ across backend languages"
            packages[language], contents[language] = folder, original
            prepare(language, folder, env)
        client = packages["python"] / "client"
        checked_command([shutil.which("pnpm"), "install", "--frozen-lockfile"], client, env)
        checked_command([shutil.which("pnpm"), "check"], client, env)
        (client / ".verify-vite.mjs").write_text(LAUNCHER, encoding="utf-8")
        if screenshots:
            screenshots.mkdir(parents=True, exist_ok=True)
        core = {}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for language in LANGUAGES:
                    with backend(language, packages[language], env, counters) as port:
                        runtime = {**env, "LAB_API_PORT": str(port)}
                        with preview(client, runtime, counters) as base:
                            core[language] = core_sequence(browser, base, language)
                            if language == "python":
                                deep = deep_navigation(browser, base, screenshots)
                                late = late_response(browser, base)
                                direct_url_assertion(browser, base)
                        if language == "python":
                            mutant = root / "mutant-client"
                            shutil.copytree(
                                client,
                                mutant,
                                ignore=shutil.ignore_patterns(
                                    "node_modules", "dist", ".verify-vite.mjs"
                                ),
                            )
                            target = mutant / "src/navigation.ts"
                            target.write_text(
                                make_wrong_navigation(target.read_text()), encoding="utf-8"
                            )
                            # The exact same frozen dependencies are shared; only the fixed
                            # temporary source tree/dist differ. No arbitrary code input.
                            (mutant / "node_modules").symlink_to(
                                client / "node_modules", target_is_directory=True
                            )
                            checked_command([shutil.which("pnpm"), "build"], mutant, env)
                            (mutant / ".verify-vite.mjs").write_text(LAUNCHER, encoding="utf-8")
                            rejected = False
                            with preview(mutant, runtime, counters) as base:
                                try:
                                    direct_url_assertion(browser, base)
                                except NavigationMismatch:
                                    rejected = True
                            assert rejected, (
                                "Normal URL assertion accepted the wrong navigation derivative"
                            )
                            # Keep the original client byte-exact and prove its same assertion
                            # succeeds again after the negative control.
                            with preview(client, runtime, counters) as base:
                                direct_url_assertion(browser, base)
            finally:
                browser.close()
        assert core == {"python": 8, "typescript": 6, "go": 6}
        assert counters["backend_started"] == counters["backend_reaped"]
        assert counters["preview_started"] == counters["preview_reaped"]
        for language in LANGUAGES:
            check_packaged_sources(packages[language], contents[language])
        result = {
            "client_contract": "api-navigation-v1",
            "server_contract": "api-contract-v1",
            "core_business_requests": core,
            "core_total": 20,
            "deep_navigation_requests": deep,
            "late_response_actual_requests": late,
            "url_mutation_rejected": True,
            "shared_client_files_identical": len(CLIENT_FILES),
            "client_frozen_check": "passed",
            "processes": counters,
            "model_calls": 0,
        }
    print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(
        description="Verify fixed API navigation ZIPs with owned headless processes"
    )
    parser.add_argument("--screenshots-dir", type=Path)
    args = parser.parse_args()
    verify(args.screenshots_dir.resolve() if args.screenshots_dir else None)


if __name__ == "__main__":
    main()
