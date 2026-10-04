"""Small guards for independent API navigation evidence and its fetch observer."""

import importlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("verify_api_navigation_lab")


@pytest.mark.parametrize(
    "damage", [None, "wrong-query", "wrong-status", "boolean-byte", "credentials"]
)
def test_capture_is_bound_to_actual_request_and_response(verifier, damage):
    expected = {"method": "POST", "path": "/api/search", "body": {"question": "工具"}}
    raw = json.dumps(expected["body"], ensure_ascii=False).encode()
    url = "http://127.0.0.1:12345/api/search"
    request = SimpleNamespace(
        url=url,
        method="POST",
        post_data_buffer=raw,
        all_headers=lambda: {"content-type": "application/json"},
    )
    response = SimpleNamespace(
        url=url, status=200, headers={"content-type": "application/json; charset=utf-8"}
    )
    record = {
        "state": "complete",
        "ticket": 1,
        "requestUrl": url,
        "responseUrl": url,
        "method": "POST",
        "credentials": "omit",
        "redirect": "error",
        "status": 200,
        "requestBody": raw.decode(),
        "responseHeaders": response.headers,
        "bytes": list(json.dumps(verifier.TOOL_RESULT, ensure_ascii=False).encode()),
    }
    if damage == "wrong-query":
        record["requestBody"] = '{"question":"HTTP"}'
    elif damage == "wrong-status":
        record["status"] = 201
    elif damage == "boolean-byte":
        record["bytes"][0] = True
    elif damage == "credentials":
        record["credentials"] = "include"
    if damage is None:
        verifier.check_capture(record, request, response, expected, verifier.TOOL_RESULT, 200)
    else:
        with pytest.raises(AssertionError):
            verifier.check_capture(record, request, response, expected, verifier.TOOL_RESULT, 200)


def test_manual_sequence_counts_and_same_url_mutation_are_explicit(verifier):
    assert sum(row["request_delta"] for row in verifier.FULL_SEQUENCE) == 8
    assert sum(row["request_delta"] for row in verifier.MINIMAL_SEQUENCE) == 6
    assert [row["step"] for row in verifier.MINIMAL_SEQUENCE] == [1, 2, 3, 4, 5, 6, 9, 10]
    source = "prefix\n" + verifier.MUTATION_FROM + "\nsuffix"
    changed = verifier.make_wrong_navigation(source)
    assert changed == "prefix\n" + verifier.MUTATION_TO + "\nsuffix"
    assert changed != source
    for invalid in ("unrelated source", source + source):
        with pytest.raises(RuntimeError):
            verifier.make_wrong_navigation(invalid)


def test_observer_returns_original_response_before_delayed_body(verifier, tmp_path):
    from verify_memory_lab import Child, assert_reaped

    node = shutil.which("node")
    assert node, "Node 24 is required for the actual Response stream probe"
    installed = []
    verifier.install_audit(
        SimpleNamespace(add_init_script=lambda *, script: installed.append(script))
    )
    assert len(installed) == 1
    probe = tmp_path / "fixed-capture.mjs"
    probe.write_text(
        """import assert from 'node:assert/strict';
class Storage {
  constructor(){this.values=new Map();}
  setItem(k,v){this.values.set(k,v);}
  removeItem(k){this.values.delete(k);}
  clear(){this.values.clear();}
}
globalThis.Storage=Storage;
globalThis.localStorage=new Storage();
globalThis.window=globalThis;
globalThis.history={length:2};
let stream, finishEvidence;
let calls=0;
const records=[];
const evidence=new Promise(resolve=>{finishEvidence=resolve;});
globalThis.__navigationEvidence=async record=>{records.push(record);finishEvidence();};
const original=new Response(new ReadableStream({start(controller){stream=controller;}}),{headers:{'content-type':'application/json'}});
globalThis.fetch=async()=>{calls++;return original;};
"""
        + installed[0]
        + """

const returned=await fetch('http://127.0.0.1:12345/api/search', {method:'POST',body:'{"question":"工具"}',headers:{'content-type':'application/json'},credentials:'omit',redirect:'error'});
assert.equal(returned,original);
assert.equal(records.length,0);
assert.equal(__navigationAudit.pending,1);
const text=returned.text();
stream.enqueue(new TextEncoder().encode('{"question":"工'));
await new Promise(resolve=>setImmediate(resolve));
assert.equal(records.length,0);
stream.enqueue(new TextEncoder().encode('具","items":[]}'));
stream.close();
assert.equal(await text,'{"question":"工具","items":[]}');
await evidence;
assert.equal(records.length,1);
assert.equal(records[0].state,'complete');
assert.equal(new TextDecoder().decode(Uint8Array.from(records[0].bytes)),'{"question":"工具","items":[]}');
assert.equal(records[0].requestBody,'{"question":"工具"}');
assert.equal(records[0].ticket,1);
assert.equal(calls,1);
assert.equal(__navigationAudit.pending,0);
assert.deepEqual(__navigationAudit.failures,[]);
console.log('original-response-delayed-body-and-one-fetch-verified');
""",
        encoding="utf-8",
    )
    with Child([node, str(probe)], tmp_path, {"PATH": str(Path(node).parent)}, timeout=8) as child:
        code, stdout, stderr = child.finish()
        assert code == 0 and stderr == b""
        assert stdout == b"original-response-delayed-body-and-one-fetch-verified\n"
    assert_reaped(child)


def test_only_frozen_idle_query_mutation_counts_as_detection(verifier):
    audit = {"requests": [], "responses": [], "captures": [], "blocked": [], "ws": [], "errors": []}
    with pytest.raises(verifier.NavigationMismatch):
        verifier.require_url_query("尚未提交", "", audit, [], 0, 0)
    for area in ("blocked", "errors", "captures"):
        changed = {key: list(value) for key, value in audit.items()}
        changed[area].append({"state": "failed"} if area == "captures" else "failure")
        with pytest.raises(AssertionError) as raised:
            verifier.require_url_query("尚未提交", "", changed, [], 0, 0)
        assert not isinstance(raised.value, verifier.NavigationMismatch)
    with pytest.raises(AssertionError) as raised:
        verifier.require_url_query("尚未提交", "", audit, ["response_capture_failed"], 0, 0)
    assert not isinstance(raised.value, verifier.NavigationMismatch)
