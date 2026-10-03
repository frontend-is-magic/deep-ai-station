"""Verify the independent write-tool ZIP with real HTTP, SQLite and optional React.

Default: frozen installs, both native checks, real HTTP and post-commit recovery.
--browser: frozen installs and only headless React -> real API acceptance.
No provider credentials or platform services are used by either mode.
"""

# ruff: noqa: S101, S106 - assertions and public fake tokens verify the fixed lab contract

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
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

from verify_course_labs import ensure_listener_closed, stop_owned_process_group

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "public/labs/agent-write-safety-python.zip"
AGENT = "fixture-alice-agent"
APPROVER = "fixture-alice-approver"
CONTENT = "发布需要精确批准；响应失败后先查询原 operation_id。<script>fixture</script> `text` 🌱"


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    assert port not in {8000, 5173}
    return port


def child_environment():
    # Use only runtime/cache configuration, never inherit provider/authentication values.
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "CI",
        "UV_CACHE_DIR",
        "PNPM_HOME",
        "PLAYWRIGHT_BROWSERS_PATH",
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


def command(args, folder, env):
    process = subprocess.Popen(  # noqa: S603 - fixed maintainer commands from the downloaded ZIP
        args, cwd=folder, env=env, start_new_session=True
    )
    try:
        status = process.wait(timeout=600)
        if status:
            raise RuntimeError(f"Lab command failed with exit code {status}: {args[1:4]}")
    finally:
        stop_owned_process_group(process)


def read_database(db, operation_id):
    """Inspect the actual committed database, with a new read-only connection each time."""
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=0.25)) as connection:
        publications = connection.execute(
            "SELECT COUNT(*) FROM publications WHERE owner_id = ? AND operation_id = ?",
            ("alice", operation_id),
        ).fetchone()[0]
        operation = connection.execute(
            "SELECT status FROM operations WHERE owner_id = ? AND operation_id = ?",
            ("alice", operation_id),
        ).fetchone()
        document = connection.execute(
            "SELECT content, version FROM documents WHERE owner_id = ? AND id = ?",
            ("alice", "agent-summary"),
        ).fetchone()
    return publications, operation[0] if operation else None, document


@dataclass
class API:
    port: int
    calls: int = 0

    def request(self, method, path, *, token=AGENT, body=None, status=200, error=None, headers=()):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            connection.putrequest(method, path)
            if token is not None:
                connection.putheader("Authorization", "Bearer " + token)
            for name, value in headers:
                connection.putheader(name, value)
            if payload is not None:
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", str(len(payload)))
            connection.endheaders(payload)
            response = connection.getresponse()
            raw = response.read(65537)
            self.calls += 1
            assert response.status == status, (method, path, response.status, status)
            assert len(raw) <= 65536
            assert response.getheader("Cache-Control") == "no-store"
            assert response.getheader("Access-Control-Allow-Origin") is None
            assert response.getheader("Set-Cookie") is None
            assert (response.getheader("Content-Type") or "").split(";", 1)[0] == "application/json"
            if status == 401:
                assert response.getheader("WWW-Authenticate") == 'Bearer realm="agent-write-safety"'
            result = json.loads(raw)
            if error is not None:
                assert result == {"error": error}
            return result
        finally:
            connection.close()


def ready(port, process, *, api):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Owned lab service exited before readiness")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
        try:
            connection.request("GET", "/health" if api else "/")
            response = connection.getresponse()
            body = response.read(65537)
            if response.status == 200:
                if api:
                    assert json.loads(body) == {"status": "ok", "lab": "agent-write-safety-v1"}
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(0.05)
    raise RuntimeError("Owned lab service did not become ready within 15 seconds")


@contextmanager
def owned_service(args, folder, env, port, *, api):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(  # noqa: S603 - fixed, unpacked maintainer entry points
            args, cwd=folder, env=env, stdout=log, stderr=log, start_new_session=True
        )
        try:
            ready(port, process, api=api)
            yield
        finally:
            try:
                stop_owned_process_group(process)
            finally:
                ensure_listener_closed(port)


@contextmanager
def server(folder, env, uv, db, *flags):
    port = free_port()
    args = [uv, "run", "--frozen", "python", "app.py", "--db", str(db), "--port", str(port), *flags]
    with owned_service(args, folder, env, port, api=True):
        yield API(port)


def prepare(api, *, operation_id=None, content=CONTENT, version=1):
    operation_id = operation_id or str(uuid4())
    body = {
        "operation_id": operation_id,
        "tool": "publish_revision",
        "arguments": {
            "document_id": "agent-summary",
            "expected_version": version,
            "content": content,
        },
    }
    operation = api.request("POST", "/operations", body=body)
    assert operation["operation_id"] == operation_id
    assert operation["status"] == "prepared"
    assert operation["receipt"] is None
    intent = {
        key: operation[key]
        for key in (
            "owner_id",
            "requester_id",
            "tool",
            "document_id",
            "expected_version",
            "content",
        )
    }
    expected_hash = hashlib.sha256(
        json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    assert operation["intent_hash"] == expected_hash
    return operation, body


def act(api, operation, action, *, token=AGENT, status=200, error=None, intent_hash=None):
    return api.request(
        "POST",
        f"/operations/{operation['operation_id']}/{action}",
        token=token,
        body={"intent_hash": intent_hash or operation["intent_hash"]},
        status=status,
        error=error,
    )


def approve(api, operation):
    approved = act(api, operation, "approve", token=APPROVER)
    assert approved["status"] == "approved"
    assert approved["expires_at"] == approved["approved_at"] + 60
    assert act(api, operation, "approve", token=APPROVER) == approved
    return approved


def verify_recovery(api, db, operation, receipt):
    operation_id = operation["operation_id"]
    observed = api.request("GET", f"/operations/{operation_id}")
    assert observed["status"] == "applied"
    assert observed["receipt"] == receipt
    replay = act(api, operation, "execute")
    assert replay == {"operation": observed, "replayed": True}
    assert read_database(db, operation_id) == (
        1,
        "applied",
        (operation["content"], receipt["version"]),
    )
    document = api.request("GET", "/documents/agent-summary")
    assert (document["version"], document["content"]) == (receipt["version"], operation["content"])


def verify_http(folder, env, uv):
    db = folder / "http.sqlite3"
    count = 0
    with server(folder, env, uv, db) as api:
        for token in (None, "fixture-missing", "fixture-expired", "fixture-revoked"):
            api.request("GET", "/me", token=token, status=401, error="authentication_required")
        api.request(
            "GET",
            "/me",
            headers=(("Authorization", "Bearer " + APPROVER),),
            status=400,
            error="ambiguous_credentials",
        )
        api.request(
            "GET",
            "/me",
            token=None,
            headers=(("Cookie", "token=" + AGENT),),
            status=401,
            error="authentication_required",
        )
        assert api.request("GET", "/me") == {
            "owner_id": "alice",
            "requester_id": "agent-alice",
            "capabilities": ["execute", "prepare", "read"],
        }
        api.request("GET", "/documents/bob-summary", status=404, error="document_not_found")
        api.request("GET", "/documents?owner_id=bob", status=422, error="invalid_input")
        operation, body = prepare(api)
        operation_id = operation["operation_id"]
        assert read_database(db, operation_id)[0:2] == (0, "prepared")
        for token in ("fixture-alice-reader", APPROVER):
            api.request(
                "POST", "/operations", token=token, body=body, status=403, error="forbidden"
            )
        for extra in ({"owner_id": "bob"}, {"confirmed": True}, {"role": "approver"}):
            api.request(
                "POST", "/operations", body={**body, **extra}, status=422, error="invalid_input"
            )
        act(api, operation, "approve", status=403, error="forbidden")
        act(api, operation, "execute", status=403, error="approval_required")
        api.request(
            "GET",
            f"/operations/{operation_id}",
            token="fixture-bob-agent",
            status=404,
            error="operation_not_found",
        )
        act(
            api,
            operation,
            "execute",
            token="fixture-bob-agent",
            status=404,
            error="operation_not_found",
        )
        act(
            api,
            operation,
            "approve",
            token=APPROVER,
            intent_hash="0" * 64,
            status=409,
            error="intent_mismatch",
        )
        approved = approve(api, operation)
        act(
            api,
            operation,
            "execute",
            token="fixture-alice-peer",
            status=403,
            error="requester_mismatch",
        )
        act(api, operation, "execute", token=APPROVER, status=403, error="forbidden")
        act(api, operation, "execute", intent_hash="0" * 64, status=409, error="intent_mismatch")
        changed = {**body, "arguments": {**body["arguments"], "content": "批准后修改"}}
        api.request("POST", "/operations", body=changed, status=409, error="operation_conflict")
        assert read_database(db, operation_id)[0:2] == (0, "approved")
        executed = act(api, approved, "execute")
        assert executed["replayed"] is False
        receipt = executed["operation"]["receipt"]
        assert receipt["version"] == 2 and receipt["content"] == CONTENT
        assert api.request("POST", "/operations", body=body) == executed["operation"]
        verify_recovery(api, db, operation, receipt)
        act(api, operation, "revoke", token=APPROVER, status=409, error="already_applied")
        count += api.calls
    with server(folder, env, uv, db) as api:
        verify_recovery(api, db, operation, receipt)
        count += api.calls
    return {
        "http_requests": count,
        "restart_persistence": "passed",
        "authorization_rejections": "passed",
    }


def verify_concurrency(folder, env, uv):
    db = folder / "concurrent.sqlite3"
    with server(folder, env, uv, db) as api:
        operation, _ = prepare(api)
        approve(api, operation)
        barrier = Barrier(2)

        def execute_together():
            barrier.wait(timeout=5)
            return act(API(api.port), operation, "execute")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(execute_together) for _ in range(2)]
            results = [future.result(timeout=8) for future in futures]
        assert sorted(result["replayed"] for result in results) == [False, True]
        assert results[0]["operation"] == results[1]["operation"]
        assert read_database(db, operation["operation_id"])[0:2] == (1, "applied")
    return {"concurrent_same_key_publications": 1, "http_requests": api.calls + len(results)}


def verify_fault(folder, env, uv):
    db = folder / "fault.sqlite3"
    with server(folder, env, uv, db, "--fault-after-commit") as api:
        operation, _ = prepare(api)
        approve(api, operation)
        act(api, operation, "execute", status=503, error="result_unconfirmed")
        assert read_database(db, operation["operation_id"])[0:2] == (1, "applied")
        receipt = api.request("GET", f"/operations/{operation['operation_id']}")["receipt"]
        verify_recovery(api, db, operation, receipt)
        count = api.calls
    with server(folder, env, uv, db) as api:
        verify_recovery(api, db, operation, receipt)
        count += api.calls
    return {"post_commit_503_then_restart_publications": 1, "http_requests": count}


def disconnect_after_commit(api, db, operation):
    """Close a real socket only after SQLite confirms COMMIT and before any HTTP response.

    Failure to observe COMMIT, or an already-arrived response, fails the verifier;
    neither is allowed to count as a post-commit disconnect experiment.
    """
    operation_id = operation["operation_id"]
    payload = json.dumps({"intent_hash": operation["intent_hash"]}).encode("ascii")
    wire = (
        f"POST /operations/{operation_id}/execute HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{api.port}\r\nAuthorization: Bearer {AGENT}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n"
    ).encode("ascii") + payload
    with socket.create_connection(("127.0.0.1", api.port), timeout=5) as client:
        client.sendall(wire)
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if read_database(db, operation_id)[0:2] == (1, "applied"):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("Did not observe the committed publication before disconnect")
        client.setblocking(False)
        try:
            response = client.recv(1, socket.MSG_PEEK)
        except BlockingIOError:
            response = None
        assert response is None, (
            "HTTP response arrived before the controlled post-commit disconnect"
        )
        client.shutdown(socket.SHUT_RDWR)
    assert read_database(db, operation_id)[0:2] == (1, "applied")


def verify_disconnect(folder, env, uv):
    db = folder / "disconnect.sqlite3"
    with server(folder, env, uv, db, "--response-delay-ms", "2000") as api:
        operation, _ = prepare(api)
        approve(api, operation)
        disconnect_after_commit(api, db, operation)
        receipt = api.request("GET", f"/operations/{operation['operation_id']}")["receipt"]
        verify_recovery(api, db, operation, receipt)
        count = (
            api.calls + 1
        )  # Includes the raw-socket execute whose response is deliberately unread.
    with server(folder, env, uv, db) as api:
        verify_recovery(api, db, operation, receipt)
        count += api.calls
    return {
        "http_requests": count,
        "disconnect_phase": "SQLite COMMIT observed, HTTP response not received",
        "post_commit_disconnect_then_restart_publications": 1,
    }


def install(folder, env, *, browser):
    uv, pnpm = shutil.which("uv"), shutil.which("pnpm")
    if not uv or not pnpm:
        raise RuntimeError("uv and pnpm are required on PATH")
    command([uv, "sync", "--locked"], folder, env)
    command([pnpm, "install", "--frozen-lockfile"], folder / "client", env)
    if not browser:
        command([uv, "run", "--frozen", "ruff", "check", "."], folder, env)
        command([uv, "run", "--frozen", "ruff", "format", "--check", "."], folder, env)
        command([uv, "run", "--frozen", "pytest", "-q"], folder, env)
        command([pnpm, "check"], folder / "client", env)
    return uv, pnpm


def verify(*, browser=False, screenshots=None):
    env = child_environment()
    # Keep all installs, databases and child log files outside either checkout.
    temp_root = "/private/tmp" if Path("/private/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="deep-ai-agent-write-", dir=temp_root) as temporary:
        folder = Path(temporary).resolve()
        assert not folder.is_relative_to(ROOT)
        with zipfile.ZipFile(ARCHIVE) as archive:
            archive.extractall(folder)
        uv, pnpm = install(folder, env, browser=browser)
        if browser:
            result = verify_browser(folder, env, uv, pnpm, screenshots=screenshots)
        else:
            result = {"native_checks": "passed", "http_requests": 0}
            for check in (verify_http, verify_concurrency, verify_fault, verify_disconnect):
                checked = check(folder, env, uv)
                result["http_requests"] += checked.pop("http_requests")
                result.update(checked)
    print(
        json.dumps(
            {
                "lab": "agent-write-safety-v1",
                "archive": "verified",
                **result,
                "listeners_released": True,
                "model_calls": 0,
            },
            ensure_ascii=False,
        )
    )


def browser_publish(page, *, fault):
    from playwright.sync_api import expect

    page.get_by_label("新摘要内容").fill(CONTENT)
    page.get_by_role("button", name="准备新操作", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("意图已准备，尚未发布。")
    operation_id = page.get_by_label("当前操作 ID").input_value()
    assert UUID(operation_id).version == 4 and str(UUID(operation_id)) == operation_id
    review = page.get_by_role("region", name="待审阅意图", exact=True)
    expect(review).to_contain_text(CONTENT)
    assert review.locator("script").count() == 0
    expect(page.get_by_role("button", name="批准当前意图", exact=True)).to_be_disabled()
    page.get_by_label("教学身份").select_option(APPROVER)
    expect(page.get_by_role("button", name="批准当前意图", exact=True)).to_be_enabled()
    expect(review).to_contain_text(CONTENT)
    page.get_by_role("button", name="批准当前意图", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("当前意图已批准，请切回原申请者执行。")
    page.get_by_label("教学身份").select_option(AGENT)
    expect(page.get_by_role("button", name="执行已批准操作", exact=True)).to_be_enabled()
    page.get_by_role("button", name="执行已批准操作", exact=True).click()
    if fault:
        expect(page.get_by_role("status")).to_have_text(
            "结果尚未确认。请保留原操作 ID 并查询原操作；不要用新 ID 重试。"
        )
        expect(page.get_by_label("当前操作 ID")).to_have_value(operation_id)
        expect(page.get_by_role("button", name="准备新操作", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="执行已批准操作", exact=True)).to_be_disabled()
        # A different, missing ID must not release the original uncertain publication.
        page.get_by_label("查询操作 ID").fill(str(uuid4()))
        page.get_by_role("button", name="查询原操作", exact=True).click()
        expect(page.get_by_role("status")).to_have_text("当前 owner 内找不到该操作。")
        expect(page.get_by_role("button", name="准备新操作", exact=True)).to_be_disabled()
        page.get_by_label("查询操作 ID").fill(operation_id)
        page.get_by_role("button", name="查询原操作", exact=True).click()
    else:
        expect(page.get_by_role("status")).to_have_text("发布已确认。请回读文档核对新版本。")
    receipt = page.get_by_role("region", name="操作回执", exact=True)
    expect(receipt).to_contain_text(operation_id)
    expect(receipt).to_contain_text(CONTENT)
    assert receipt.locator("script").count() == 0
    expect(page.get_by_role("button", name="重放原操作", exact=True)).to_be_enabled()
    page.get_by_role("button", name="重放原操作", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("已返回原回执，本次没有重复发布。")
    page.get_by_role("button", name="刷新文档", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("已回读服务端当前文档；编辑草稿保持原样。")
    expect(page.get_by_role("region", name="当前文档", exact=True)).to_contain_text(CONTENT)
    return operation_id


def browser_draft_and_identity(page):
    from playwright.sync_api import expect

    page.get_by_role("button", name="载入文档当前版本", exact=True).click()
    page.get_by_label("新摘要内容").fill("第二个草稿，需要自己的批准。")
    page.get_by_role("button", name="准备新操作", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("意图已准备，尚未发布。")
    page.get_by_label("教学身份").select_option(APPROVER)
    page.get_by_role("button", name="批准当前意图", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("当前意图已批准，请切回原申请者执行。")
    page.get_by_label("教学身份").select_option(AGENT)
    expect(page.get_by_role("button", name="执行已批准操作", exact=True)).to_be_enabled()
    approved_id = page.get_by_label("当前操作 ID").input_value()
    page.reload()
    expect(page.get_by_role("region", name="当前身份", exact=True)).to_contain_text("agent-alice")
    expect(page.get_by_label("新摘要内容")).to_have_value(CONTENT)
    page.get_by_label("查询操作 ID").fill(approved_id)
    page.get_by_role("button", name="查询原操作", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("原操作已查询，请审阅当前状态与意图。")
    expect(page.get_by_label("新摘要内容")).to_have_value(CONTENT)
    expect(page.get_by_role("button", name="执行已批准操作", exact=True)).to_be_disabled()
    page.get_by_role("button", name="载入当前意图草稿", exact=True).click()
    expect(page.get_by_role("button", name="执行已批准操作", exact=True)).to_be_enabled()
    page.get_by_label("新摘要内容").fill("批准之后改写，旧批准不适用于新草稿。")
    expect(page.get_by_role("button", name="执行已批准操作", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="重放原操作", exact=True)).to_be_disabled()
    page.get_by_label("教学身份").select_option("fixture-bob-agent")
    identity = page.get_by_role("region", name="当前身份", exact=True)
    expect(identity).to_contain_text("agent-bob")
    expect(page.get_by_role("region", name="当前文档", exact=True)).to_contain_text(
        "另一位学习者的摘要"
    )
    # Delay a real Alice /me response after reading it, deliberately ignoring its AbortSignal.
    # No fabricated success payloads or model/sandbox calls are involved.
    page.evaluate("""() => {
      const realFetch = window.fetch.bind(window);
      window.heldIdentity = null;
      window.fetch = async (input, init) => {
        const request = new Request(input, init);
        if (new URL(request.url).pathname === '/api/me' &&
            request.headers.get('Authorization') === 'Bearer fixture-alice-agent' &&
            !window.heldIdentity) {
          const held = {signal: request.signal, ready: false, returned: false};
          window.heldIdentity = held;
          const response = await realFetch(request, {signal: new AbortController().signal});
          await response.clone().text();
          await new Promise(resolve => { held.release = resolve; held.ready = true; });
          held.returned = true;
          return response;
        }
        return realFetch(input, init);
      };
    }""")
    page.get_by_label("教学身份").select_option(AGENT)
    page.wait_for_function("window.heldIdentity?.ready === true")
    page.get_by_label("教学身份").select_option("fixture-bob-agent")
    expect(identity).to_contain_text("agent-bob")
    page.wait_for_function("window.heldIdentity.signal.aborted === true")
    page.evaluate("window.heldIdentity.release()")
    page.wait_for_function("window.heldIdentity.returned === true")
    page.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    expect(identity).to_contain_text("agent-bob")
    expect(identity).not_to_contain_text("agent-alice")
    expect(page.get_by_role("region", name="当前文档", exact=True)).to_contain_text(
        "另一位学习者的摘要"
    )
    expect(page.get_by_role("region", name="待审阅意图", exact=True)).not_to_contain_text(CONTENT)


def verify_browser(folder, env, uv, pnpm, *, screenshots):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for fault in (False, True):
                label = "fault" if fault else "normal"
                db = folder / f"browser-{label}.sqlite3"
                flags = ["--fault-after-commit"] if fault else []
                with server(folder, env, uv, db, *flags) as api:
                    port = free_port()
                    base = f"http://127.0.0.1:{port}"
                    args = [
                        pnpm,
                        "exec",
                        "vite",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--strictPort",
                    ]
                    with owned_service(
                        args,
                        folder / "client",
                        {**env, "LAB_API_PORT": str(api.port)},
                        port,
                        api=False,
                    ):
                        context = browser.new_context(viewport={"width": 1280, "height": 1000})
                        try:
                            page = context.new_page()
                            page.set_default_timeout(10000)
                            errors, requests = [], []
                            page.on(
                                "pageerror", lambda error, errors=errors: errors.append(str(error))
                            )
                            page.on(
                                "request",
                                lambda request, requests=requests: requests.append(request.url),
                            )
                            # Fail closed for unexpected destinations before they can leave loopback.
                            page.route(
                                "**/*",
                                lambda route, _request, base=base: (
                                    route.continue_()
                                    if route.request.url.startswith(base + "/")
                                    else route.abort()
                                ),
                            )
                            page.goto(base)
                            expect(
                                page.get_by_role("region", name="当前身份", exact=True)
                            ).to_contain_text("agent-alice")
                            operation_id = browser_publish(page, fault=fault)
                            assert read_database(db, operation_id) == (1, "applied", (CONTENT, 2))
                            if screenshots:
                                screenshots.mkdir(parents=True, exist_ok=True)
                                page.evaluate("document.fonts.ready")
                                page.screenshot(
                                    path=str(screenshots / f"agent-write-{label}-desktop.png"),
                                    full_page=True,
                                )
                            if not fault:
                                browser_draft_and_identity(page)
                            page.set_viewport_size({"width": 375, "height": 812})
                            page.reload()
                            expect(
                                page.get_by_role("region", name="当前身份", exact=True)
                            ).to_contain_text("agent-alice")
                            mobile_id = browser_publish(page, fault=fault)
                            assert read_database(db, mobile_id) == (1, "applied", (CONTENT, 3))
                            assert page.evaluate(
                                "document.documentElement.scrollWidth <= innerWidth"
                            )
                            if screenshots:
                                screenshots.mkdir(parents=True, exist_ok=True)
                                page.evaluate("document.fonts.ready")
                                page.screenshot(
                                    path=str(screenshots / f"agent-write-{label}-mobile.png"),
                                    full_page=True,
                                )
                            assert not errors, errors
                            assert all(url.startswith(base + "/") for url in requests)
                            assert not any(
                                "/execute-code" in url or "/agent/" in url or "/models" in url
                                for url in requests
                            )
                        finally:
                            context.close()
        finally:
            browser.close()
    return {
        "browser_publish_flows": 4,
        "shared_react_to_api": "passed",
        "browser_post_commit_503_recovery": "passed",
        "late_identity_response_isolation": "passed",
        "viewport_375px": "passed",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--browser", action="store_true", help="Run only headless React/API acceptance"
    )
    parser.add_argument("--screenshots", type=Path, help="Optional headless screenshots directory")
    args = parser.parse_args()
    verify(browser=args.browser, screenshots=args.screenshots)
