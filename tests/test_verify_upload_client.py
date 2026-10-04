"""Critical verifier invariants; no backend, browser, or dependency installation."""

import copy
import json
import os
import shutil
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


def test_post_observation_requires_exact_ticket_headers_and_strict_body():
    url = "http://127.0.0.1:49152/api/documents"
    request_headers = {"content-type": "text/plain", "x-filename": "fixed.txt"}
    response_headers = {
        "content-type": "application/json",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
    }
    metadata = {
        "id": "doc-000001",
        "filename": "fixed.txt",
        "media_type": "text/plain",
        "size_bytes": 1,
        "sha256": "0" * 64,
    }
    record = {
        "ticket": 2,
        "method": "POST",
        "requestUrl": url,
        "responseUrl": url,
        "state": "complete",
        "failure": None,
        "status": 201,
        "requestHeaders": request_headers,
        "responseHeaders": response_headers,
        "bytes": list(json.dumps(metadata).encode()),
    }
    options = {
        "ticket": 2,
        "url": url,
        "status": 201,
        "request_headers": request_headers,
        "response_headers": response_headers,
    }
    assert verifier.parse_post_observation(record, **options) == metadata
    changes = (
        {"ticket": 1},
        {"status": 409},
        {"state": "failed", "failure": "capture_timeout"},
        {"requestUrl": url + "-other"},
        {"responseUrl": url + "-other"},
        {"method": "GET"},
        {"requestHeaders": {**request_headers, "x-filename": "other.txt"}},
        {"responseHeaders": {**response_headers, "cache-control": "public"}},
        {"responseHeaders": {**response_headers, "content-type": "text/html"}},
        {"bytes": [120] * (verifier.POST_CAPTURE_LIMIT + 1)},
        {"bytes": [True]},
        {"bytes": [256]},
        {"bytes": [128]},
        {"bytes": list(b"{broken")},
    )
    for change in changes:
        damaged = copy.deepcopy(record)
        damaged.update(change)
        with pytest.raises(RuntimeError, match="POST response observation"):
            verifier.parse_post_observation(damaged, **options)
    with pytest.raises(RuntimeError, match="header mismatch"):
        verifier.parse_post_observation(record, **{**options, "response_headers": {}})


def test_fetch_observer_uses_real_response_tee_without_waiting_or_extra_post():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the fixed native Response observation test")
    script = r"""
      import assert from 'node:assert/strict';
      globalThis.window=globalThis;
      let nextResponse, fetchCalls=0, timerId=0;
      const timers=new Map();
      // Deterministically fire only the observer deadline, not the product's
      // stream. Response, clone, reader, chunks, and tee cancellation are real.
      globalThis.setTimeout=(callback,ms)=>{assert.equal(ms,5000); const id=++timerId; timers.set(id,callback); return id;};
      globalThis.clearTimeout=id=>timers.delete(id);
      globalThis.fetch=async()=>{fetchCalls++; return nextResponse;};
      const install=__AUDIT__;
      install();
      const audit=window.__uploadAudit;
      const encoder=new TextEncoder();
      const payload=encoder.encode('{"filename":"固定😀.txt","size_bytes":1}');
      const options=name=>({method:'POST',body:'x',credentials:'omit',cache:'no-store',redirect:'error',
        headers:{'Content-Type':'text/plain','X-Filename':name,'Authorization':'Bearer lab-alice-session'}});
      async function finished(record) {
        for(let turn=0;turn<100;turn++) {
          if(!['pending','capturing'].includes(record.state)) return;
          await new Promise(setImmediate);
        }
        assert.fail('observer did not settle');
      }
      const endpoint='http://127.0.0.1:49152/api/documents';
      let controller;
      nextResponse=new Response(new ReadableStream({start(value){controller=value;}}),
        {status:201,headers:{'Content-Type':'application/json','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
      const original=nextResponse;
      const returned=await window.fetch(endpoint,options('first.txt'));
      assert.strictEqual(returned,original);
      assert.equal(returned.bodyUsed,false);
      assert.equal(audit.postResponses[0].state,'capturing');
      // The delayed real stream has not received any bytes when fetch resolves.
      const originalBody=returned.arrayBuffer();
      controller.enqueue(payload.slice(0,16));
      await new Promise(setImmediate);
      controller.enqueue(payload.slice(16)); controller.close();
      assert.deepEqual(new Uint8Array(await originalBody),payload);
      await finished(audit.postResponses[0]);
      assert.deepEqual(audit.postResponses[0].bytes,Array.from(payload));
      assert.equal(audit.postResponses[0].requestHeaders['x-filename'],'first.txt');
      assert.equal(audit.postResponses[0].status,201);
      assert.equal(audit.postResponses[0].responseHeaders['cache-control'],'no-store');
      assert.equal(audit.postResponses[0].ticket,1);
      assert.equal(fetchCalls,1);
      assert.deepEqual(audit.violations,[]);

      // Observer overflow cancels only its clone; the original still yields all bytes.
      const large=new Uint8Array(4097).fill(120);
      nextResponse=new Response(new ReadableStream({start(value){value.enqueue(large);value.close();}}),{status:201});
      const oversized=nextResponse;
      const largeOriginal=await window.fetch(endpoint,options('large.txt'));
      assert.strictEqual(largeOriginal,oversized);
      assert.deepEqual(new Uint8Array(await largeOriginal.arrayBuffer()),large);
      await finished(audit.postResponses[1]);
      assert.equal(audit.postResponses[1].state,'failed');
      assert.equal(audit.postResponses[1].failure,'capture_too_large');
      assert.equal(audit.postResponses[1].bytes,null);
      assert.equal(audit.postResponses[1].ticket,2);

      // The observer deadline also leaves the application's original stream usable.
      nextResponse=new Response(new ReadableStream({start(value){controller=value;}}),{status:201});
      const waitingOriginal=nextResponse;
      const timed=await window.fetch(endpoint,options('timed.txt'));
      assert.strictEqual(timed,waitingOriginal);
      assert.equal(timers.size,1);
      [...timers.values()][0]();
      await finished(audit.postResponses[2]);
      assert.equal(audit.postResponses[2].failure,'capture_timeout');
      const timedBody=timed.arrayBuffer();
      controller.enqueue(payload); controller.close();
      assert.deepEqual(new Uint8Array(await timedBody),payload);

      nextResponse=new Response(new ReadableStream({start(value){controller=value;}}),{status:201});
      const brokenOriginal=nextResponse;
      const broken=await window.fetch(endpoint,options('broken.txt'));
      assert.strictEqual(broken,brokenOriginal);
      const brokenBody=broken.arrayBuffer();
      controller.error(new Error('private-reader-diagnostic'));
      await assert.rejects(brokenBody);
      await finished(audit.postResponses[3]);
      assert.equal(audit.postResponses[3].failure,'capture_failed');
      assert.equal(audit.postResponses[3].bytes,null);
      assert(!JSON.stringify(audit.postResponses).includes('private-reader-diagnostic'));
      nextResponse=new Response(new ReadableStream({start(value){controller=value;}}),{status:201});
      const cleanupOriginal=nextResponse;
      const cleanup=await window.fetch(endpoint,options('cleanup.txt'));
      assert.strictEqual(cleanup,cleanupOriginal);
      audit.cancelCaptures();
      await finished(audit.postResponses[4]);
      assert.equal(audit.postResponses[4].failure,'capture_cancelled');
      const cleanupBody=cleanup.arrayBuffer();
      controller.enqueue(payload); controller.close();
      assert.deepEqual(new Uint8Array(await cleanupBody),payload);
      assert.equal(fetchCalls,5);
      assert.equal(audit.posts,5);
      assert.deepEqual(audit.violations,['capture_too_large','capture_timeout','capture_failed','capture_cancelled']);
      assert.equal(audit.readers.size,0);
      assert.equal(timers.size,0);
      console.log(JSON.stringify({responses:5,original_identity:true,original_bytes:true,no_extra_post:true,readers_closed:true}));
    """.replace("__AUDIT__", verifier.AUDIT)
    result = subprocess.run(  # noqa: S603 - fixed native Response fixture, no server/browser/network
        [node, "--input-type=module", "-"],
        input=script,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
        env=verifier.environment(),
    )
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "responses": 5,
        "original_identity": True,
        "original_bytes": True,
        "no_extra_post": True,
        "readers_closed": True,
    }
