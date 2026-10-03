"""Real SQLite files and deterministic concurrency, no models or external services."""

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app

AGENT = "fixture-alice-agent"
APPROVER = "fixture-alice-approver"
PEER = "fixture-alice-peer"
READER = "fixture-alice-reader"
BOB = "fixture-bob-agent"
BOB_APPROVER = "fixture-bob-approver"
PRIVATE = "private-diagnostic-must-not-escape"
ROOT = Path(__file__).resolve().parent


def headers(token=AGENT):
    return {"Authorization": "Bearer " + token}


def payload(operation_id=None, **arguments):
    return {
        "operation_id": operation_id or str(uuid4()),
        "tool": "publish_revision",
        "arguments": {
            "document_id": "agent-summary",
            "expected_version": 1,
            "content": "新的摘要 \n 保留空白",
            **arguments,
        },
    }


@pytest.fixture
def harness(tmp_path):
    clock = [1000.0]
    db = tmp_path / "lab.db"
    app = create_app(db, clock=lambda: clock[0])
    with TestClient(app) as client:
        yield app, client, db, clock


def post(client, path, body, token=AGENT):
    return client.post(path, json=body, headers=headers(token))


def prepare(client, body=None, token=AGENT):
    response = post(client, "/operations", body or payload(), token)
    assert response.status_code == 200, response.text
    return response.json()


def action(client, item, verb, token=None, **changes):
    token = token or (APPROVER if verb in {"approve", "revoke"} else AGENT)
    return post(
        client,
        f"/operations/{item['operation_id']}/{verb}",
        {"intent_hash": item["intent_hash"], **changes},
        token,
    )


def approved(client, body=None):
    item = prepare(client, body)
    response = action(client, item, "approve")
    assert response.status_code == 200, response.text
    return response.json()


def rows(db, table):
    assert table in {"documents", "operations", "publications"}
    with sqlite3.connect(db) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM " + table)]  # noqa: S608


def error(response, status, code):
    assert response.status_code == status
    assert response.json() == {"error": code}
    assert response.headers["cache-control"] == "no-store"
    assert PRIVATE not in response.text
    if status == 401:
        assert response.headers["www-authenticate"] == 'Bearer realm="agent-write-safety"'


def test_prepare_approve_execute_and_replay_preserve_actual_intent(harness):
    _app, client, db, clock = harness
    body = payload()
    item = prepare(client, body)
    expected = {
        "owner_id": "alice",
        "requester_id": "agent-alice",
        "tool": body["tool"],
        **body["arguments"],
    }
    digest = hashlib.sha256(
        json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert item["intent_hash"] == digest and item["status"] == "prepared"
    assert item["approved_at"] is item["expires_at"] is item["receipt"] is None
    assert item["before_content"] == rows(db, "documents")[0]["content"]
    assert rows(db, "publications") == []
    error(action(client, item, "execute"), 403, "approval_required")
    grant = action(client, item, "approve").json()
    assert grant["approved_at"] == 1000 and grant["expires_at"] == 1060
    clock[0] += 5
    assert action(client, item, "approve").json() == grant
    executed = action(client, item, "execute").json()
    assert executed["replayed"] is False
    assert executed["operation"]["receipt"]["version"] == 2
    assert executed["operation"]["receipt"]["content"] == body["arguments"]["content"]
    assert len(rows(db, "publications")) == 1
    clock[0] = 1100
    replay = action(client, item, "execute").json()
    assert replay == {"operation": executed["operation"], "replayed": True}
    assert prepare(client, body) == executed["operation"]
    assert len(rows(db, "publications")) == 1
    error(action(client, item, "approve"), 409, "already_applied")
    error(action(client, item, "revoke"), 409, "already_applied")


@pytest.mark.parametrize("token", ["missing", "fixture-expired", "fixture-revoked", "Bearer"])
def test_bad_sessions_are_rejected_before_body_or_database(harness, token):
    app, client, db, _clock = harness
    app.state.repository.transaction = lambda *_: pytest.fail("must not reach database")
    response = client.post(
        "/operations",
        content=b"invalid" * 900,
        headers={**headers(token), "content-type": "text/plain"},
    )
    error(response, 401, "authentication_required")
    assert rows(db, "operations") == []


@pytest.mark.parametrize(
    "auth_headers,code",
    [
        (
            [("Authorization", "Bearer " + AGENT), ("Authorization", "Bearer " + AGENT)],
            "ambiguous_credentials",
        ),
        ([("Authorization", "Bearer " + AGENT + ", Bearer " + AGENT)], "ambiguous_credentials"),
        ([("Cookie", "session=" + AGENT)], "authentication_required"),
        ([("Authorization", "Bearer\t" + AGENT)], "authentication_required"),
    ],
)
def test_auth_header_ambiguity_never_falls_back(harness, auth_headers, code):
    _app, client, _db, _clock = harness
    error(
        client.get("/me", headers=auth_headers),
        400 if code == "ambiguous_credentials" else 401,
        code,
    )
    response = client.get("/me", headers={"Authorization": "bEaReR   " + AGENT})
    assert response.json() == {
        "owner_id": "alice",
        "requester_id": "agent-alice",
        "capabilities": ["execute", "prepare", "read"],
    }


def test_owner_capability_requester_and_hash_are_independent(harness):
    _app, client, db, _clock = harness
    item = prepare(client)
    error(action(client, item, "approve", AGENT), 403, "forbidden")
    error(action(client, item, "execute", READER), 403, "forbidden")
    error(action(client, item, "execute", PEER), 403, "requester_mismatch")
    error(action(client, item, "approve", BOB_APPROVER), 404, "operation_not_found")
    error(client.get("/documents/bob-summary", headers=headers()), 404, "document_not_found")
    error(
        client.get(f"/operations/{item['operation_id']}", headers=headers(BOB)),
        404,
        "operation_not_found",
    )
    error(action(client, item, "approve", intent_hash="0" * 64), 409, "intent_mismatch")
    assert (
        client.get(f"/operations/{item['operation_id']}", headers=headers(READER)).status_code
        == 200
    )
    assert rows(db, "publications") == []
    body = payload(item["operation_id"], document_id="bob-summary")
    bob = prepare(client, body, BOB)
    assert bob["owner_id"] == "bob" and bob["intent_hash"] != item["intent_hash"]


def test_same_key_conflict_and_same_version_competition(harness):
    _app, client, db, _clock = harness
    body = payload()
    first = approved(client, body)
    error(
        post(
            client,
            "/operations",
            {**body, "arguments": {**body["arguments"], "content": "changed"}},
        ),
        409,
        "operation_conflict",
    )
    error(post(client, "/operations", body, PEER), 409, "operation_conflict")
    second = approved(client)
    unapproved = prepare(client)
    assert action(client, first, "execute").status_code == 200
    error(action(client, second, "execute"), 409, "stale_document")
    assert action(client, second, "approve").json() == second
    error(action(client, unapproved, "approve"), 409, "stale_document")
    error(post(client, "/operations", payload()), 409, "stale_document")
    assert len(rows(db, "publications")) == 1
    # A same-key preparation reads its original difference, not a refreshed document.
    assert prepare(client, body)["before_content"] == first["before_content"]


@pytest.mark.parametrize("observer", ["get", "prepare", "approve", "revoke", "execute"])
def test_observed_expiry_is_persistent_after_clock_rollback(harness, observer):
    _app, client, db, clock = harness
    body = payload()
    item = approved(client, body)
    clock[0] = item["expires_at"]
    if observer == "get":
        assert (
            client.get(f"/operations/{item['operation_id']}", headers=headers()).json()["status"]
            == "expired"
        )
    elif observer == "prepare":
        assert prepare(client, body)["status"] == "expired"
    else:
        error(action(client, item, observer), 409, "operation_expired")
    assert rows(db, "operations")[0]["status"] == "expired"
    clock[0] = 1001
    error(action(client, item, "execute"), 409, "operation_expired")
    error(action(client, item, "approve"), 409, "operation_expired")
    assert rows(db, "publications") == []


@pytest.mark.parametrize("with_approval", [False, True])
def test_revocation_is_terminal_and_repeat_is_idempotent(harness, with_approval):
    _app, client, db, _clock = harness
    item = approved(client) if with_approval else prepare(client)
    revoked = action(client, item, "revoke").json()
    assert revoked["status"] == "revoked"
    assert action(client, item, "revoke").json() == revoked
    error(action(client, item, "approve"), 409, "operation_revoked")
    error(action(client, item, "execute"), 409, "operation_revoked")
    assert not rows(db, "publications")


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b"null",
        b"{}{}",
        b"\xef\xbb\xbf{}",
        b"\xff",
        b'{"x":NaN}',
        b'{"x":"\\ud800"}',
        b'{"x":1,"x":2}',
        b'{"x":' + b"[" * 33 + b"0" + b"]" * 33 + b"}",
    ],
)
def test_strict_json_rejects_invalid_raw_values(harness, raw):
    _app, client, db, _clock = harness
    response = client.post(
        "/operations", content=raw, headers={**headers(), "Content-Type": "application/json"}
    )
    error(response, 422, "invalid_input")
    assert not rows(db, "operations")


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_version", True),
        ("expected_version", 1.0),
        ("expected_version", 0),
        ("expected_version", 2147483648),
        ("content", ""),
        ("content", "x" * 2001),
        ("content", "a\x00b"),
        ("document_id", "Agent"),
        ("document_id", "a" * 81),
        ("document_id", "agent-summary\n"),
    ],
)
def test_exact_argument_types_and_bounds(harness, field, value):
    _app, client, _db, _clock = harness
    error(post(client, "/operations", payload(**{field: value})), 422, "invalid_input")


@pytest.mark.parametrize(
    "change",
    [
        {"owner": "alice"},
        {"confirmed": True},
        {"role": "approver"},
        {"tool": "delete"},
        {"operation_id": "00000000-0000-1000-8000-000000000000"},
        {"operation_id": str(uuid4()).upper()},
        {"operation_id": str(uuid4()) + "\n"},
    ],
)
def test_exact_request_fields_and_uuid(harness, change):
    _app, client, _db, _clock = harness
    error(post(client, "/operations", {**payload(), **change}), 422, "invalid_input")


def test_size_media_query_and_routes_are_fixed_and_private(harness):
    _app, client, _db, _clock = harness
    error(
        client.post(
            "/operations", content=b" " * 4097, headers={**headers(), "Content-Type": "text/plain"}
        ),
        413,
        "request_too_large",
    )
    error(
        client.post("/operations", content=b"{}", headers=headers()), 415, "unsupported_media_type"
    )
    error(client.get("/me?clock=0", headers=headers()), 422, "invalid_input")
    error(client.get("/operations/not-a-uuid", headers=headers()), 422, "invalid_input")
    error(client.get("/missing"), 404, "route_not_found")
    error(client.delete("/documents"), 405, "method_not_allowed")
    assert client.get("/health").headers["cache-control"] == "no-store"
    body = json.dumps(payload(content=" \n\t"), ensure_ascii=False).encode()
    assert (
        client.post(
            "/operations",
            content=body,
            headers={**headers(), "Content-Type": "APPLICATION/JSON ; charset=utf-8"},
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "stage", ["after_document_update", "after_publication_insert", "before_commit"]
)
def test_mid_transaction_failure_rolls_back_all_state(harness, stage):
    app, client, db, _clock = harness
    item = approved(client)
    before = {name: rows(db, name) for name in ("documents", "operations", "publications")}

    def fail(current, _connection):
        if current == stage:
            raise sqlite3.OperationalError(PRIVATE)

    app.state.repository.fault_hook = fail
    error(action(client, item, "execute"), 503, "repository_unavailable")
    assert {name: rows(db, name) for name in before} == before


@pytest.mark.parametrize("mode", ["commit-before", "commit-after", "response"])
def test_unknown_commit_or_response_does_not_retry_or_invent_rollback(harness, mode):
    app, client, db, _clock = harness
    item = approved(client)
    database = app.state.repository
    original = database.commit
    calls = []
    if mode == "response":

        def fault(stage, _connection):
            if stage == "after_commit":
                raise RuntimeError(PRIVATE)

        database.fault_hook = fault
    else:

        def commit(connection):
            calls.append(True)
            if mode == "commit-after":
                original(connection)
            raise sqlite3.OperationalError(PRIVATE)

        database.commit = commit
    error(action(client, item, "execute"), 503, "result_unconfirmed")
    if mode != "response":
        assert calls == [True]
    database.commit = original
    database.fault_hook = lambda *_: None
    known = client.get(f"/operations/{item['operation_id']}", headers=headers()).json()
    assert known["status"] == ("approved" if mode == "commit-before" else "applied")
    reply = action(client, item, "execute").json()
    assert reply["replayed"] is (mode != "commit-before")
    assert len(rows(db, "publications")) == 1


def test_sql_constraints_prevent_accidental_intent_and_receipt_mutation(harness):
    _app, client, db, _clock = harness
    item = approved(client)
    assert action(client, item, "execute").status_code == 200
    with sqlite3.connect(db) as connection:
        for statement in (
            "UPDATE operations SET content='changed'",
            "UPDATE operations SET requester_id='other'",
            "UPDATE operations SET expires_at=expires_at+10",
            "UPDATE operations SET status='approved'",
            "UPDATE publications SET content='changed'",
            "DELETE FROM publications",
            "DELETE FROM operations",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)
            connection.rollback()
    assert len(rows(db, "publications")) == 1


def test_restart_keeps_database_and_rejects_future_schema(harness):
    _app, client, db, _clock = harness
    item = approved(client)
    first = action(client, item, "execute").json()
    source = """
import json, sys
from fastapi.testclient import TestClient
from app import create_app
with TestClient(create_app(sys.argv[1])) as client:
    response = client.post('/operations/' + sys.argv[2] + '/execute',
        json={'intent_hash': sys.argv[3]}, headers={'Authorization':'Bearer fixture-alice-agent'})
    print(json.dumps(response.json()))
"""
    completed = subprocess.run(  # noqa: S603 - fixed local child
        [sys.executable, "-B", "-c", source, str(db), item["operation_id"], item["intent_hash"]],
        cwd=ROOT,
        env={"PATH": os.defpath, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=True,
    )  # noqa: S603 - fixed local child
    assert json.loads(completed.stdout) == {"operation": first["operation"], "replayed": True}
    assert len(rows(db, "publications")) == 1
    future = db.parent / "future.db"
    with sqlite3.connect(future) as connection:
        connection.execute("PRAGMA user_version=2")
    with pytest.raises(ValueError, match="unsupported schema"):
        create_app(future)
    with sqlite3.connect(future) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


CHILD_EXECUTE = """
import json, sys
from fastapi.testclient import TestClient
from app import create_app
with TestClient(create_app(sys.argv[1])) as client:
    print('ready', flush=True)
    sys.stdin.readline()
    reply=client.post('/operations/'+sys.argv[2]+'/execute', json={'intent_hash':sys.argv[3]},
                     headers={'Authorization':'Bearer fixture-alice-agent'})
    print(json.dumps({'status':reply.status_code,'body':reply.json()}), flush=True)
"""


@pytest.mark.parametrize("same_key", [True, False])
def test_two_processes_share_the_same_atomic_version_and_receipt(harness, same_key):
    _app, client, db, _clock = harness
    # Use a real wall clock for subprocesses so both approvals remain valid.
    with TestClient(create_app(db)) as live:
        first = approved(live)
        second = first if same_key else approved(live)
    processes = []
    try:
        for item in (first, second):
            processes.append(
                subprocess.Popen(  # noqa: S603 - fixed local child
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        CHILD_EXECUTE,
                        str(db),
                        item["operation_id"],
                        item["intent_hash"],
                    ],
                    cwd=ROOT,
                    env={
                        "PATH": os.defpath,
                        "PYTHONPATH": str(ROOT),
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )  # noqa: S603 - fixed local child
        for process in processes:
            assert process.stdout.readline().strip() == "ready"
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        results = []
        for process in processes:
            stdout, _stderr = process.communicate(timeout=10)
            assert process.returncode == 0
            results.append(json.loads(stdout))
        if same_key:
            assert [result["status"] for result in results] == [200, 200]
            assert sorted(result["body"]["replayed"] for result in results) == [False, True]
            assert (
                results[0]["body"]["operation"]["receipt"]
                == results[1]["body"]["operation"]["receipt"]
            )
        else:
            assert sorted(result["status"] for result in results) == [200, 409]
        assert len(rows(db, "publications")) == 1
        assert rows(db, "documents")[0]["version"] == 2
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for pipe in (process.stdin, process.stdout, process.stderr):
                pipe.close()


@pytest.mark.parametrize(
    "change,expected", [("revoke", 401), ("expire", 401), ("permission", 403), ("subject", 401)]
)
def test_waiting_for_write_lock_rechecks_current_identity(harness, change, expected):
    app, client, db, clock = harness
    item = approved(client)
    database = app.state.repository
    original = database.connect
    connecting = threading.Event()

    def connect():
        connection = original()
        connection.set_trace_callback(
            lambda sql: connecting.set() if sql == "BEGIN IMMEDIATE" else None
        )
        return connection

    database.connect = connect
    session = app.state.sessions.sessions[AGENT]
    with sqlite3.connect(db, isolation_level=None) as blocker, ThreadPoolExecutor() as executor:
        blocker.execute("BEGIN IMMEDIATE")
        future = executor.submit(action, client, item, "execute")
        assert connecting.wait(timeout=1)
        with app.state.sessions.lock:
            if change == "revoke":
                session["revoked"] = True
            elif change == "expire":
                clock[0] = session["expires_at"]
            elif change == "permission":
                session["capabilities"] = ["read"]
            else:
                session["owner_id"] = "bob"
        blocker.rollback()
        error(
            future.result(timeout=2),
            expected,
            "forbidden" if expected == 403 else "authentication_required",
        )
    assert not rows(db, "publications")


@pytest.mark.parametrize("change,expected", [("revoke", 401), ("expire", 401), ("permission", 403)])
async def test_paused_body_does_not_hold_lock_and_identity_is_rechecked(harness, change, expected):
    app, _client, db, clock = harness
    started, resume = asyncio.Event(), asyncio.Event()
    raw = json.dumps(payload()).encode()

    async def body():
        yield raw[:1]
        started.set()
        await resume.wait()
        yield raw[1:]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://lab"
    ) as client:
        task = asyncio.create_task(
            client.post(
                "/operations",
                content=body(),
                headers={**headers(), "Content-Type": "application/json"},
            )
        )
        await asyncio.wait_for(started.wait(), 1)
        # A different DB connection can acquire the write lock while the body is paused.
        with sqlite3.connect(db, timeout=0, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        session = app.state.sessions.sessions[AGENT]
        with app.state.sessions.lock:
            if change == "revoke":
                session["revoked"] = True
            elif change == "expire":
                clock[0] = session["expires_at"]
            else:
                session["capabilities"] = ["read"]
        resume.set()
        error(
            await asyncio.wait_for(task, 2),
            expected,
            "forbidden" if expected == 403 else "authentication_required",
        )
    assert not rows(db, "operations")


async def test_chunked_actual_size_wins_over_content_length(harness):
    app, _client, db, _clock = harness

    async def body():
        yield b" " * 2048
        yield b" " * 2049

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://lab"
    ) as client:
        error(
            await client.post(
                "/operations",
                content=body(),
                headers={**headers(), "Content-Type": "application/json", "Content-Length": "1"},
            ),
            413,
            "request_too_large",
        )
    assert not rows(db, "operations")


def test_session_and_database_faults_are_sanitized(harness, caplog):
    app, client, _db, _clock = harness
    original = app.state.sessions.resolve

    def unavailable(*_):
        raise RuntimeError(PRIVATE)

    app.state.sessions.resolve = unavailable
    error(client.get("/me", headers=headers()), 503, "session_store_unavailable")
    app.state.sessions.resolve = original
    app.state.repository.connect = unavailable
    error(client.get("/documents", headers=headers()), 503, "repository_unavailable")
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize("first_action", ["revoke", "execute"])
def test_revoke_and_execute_serialize_in_observed_lock_order(harness, first_action):
    app, client, db, clock = harness
    item = approved(client)
    entered, release, second_waiting = threading.Event(), threading.Event(), threading.Event()

    def hold(stage, _connection):
        if stage == "before_commit":
            entered.set()
            assert release.wait(timeout=2)

    app.state.repository.fault_hook = hold
    second_app = create_app(db, clock=lambda: clock[0])
    original = second_app.state.repository.connect

    def connect():
        connection = original()
        connection.set_trace_callback(
            lambda sql: second_waiting.set() if sql == "BEGIN IMMEDIATE" else None
        )
        return connection

    second_app.state.repository.connect = connect
    with TestClient(second_app) as second_client, ThreadPoolExecutor() as executor:
        try:
            first = executor.submit(action, client, item, first_action)
            assert entered.wait(timeout=1)
            following = "execute" if first_action == "revoke" else "revoke"
            second = executor.submit(action, second_client, item, following)
            assert second_waiting.wait(timeout=1)
            release.set()
            assert first.result(timeout=2).status_code == 200
            error(
                second.result(timeout=2),
                409,
                "operation_revoked" if first_action == "revoke" else "already_applied",
            )
        finally:
            release.set()
    assert len(rows(db, "publications")) == (0 if first_action == "revoke" else 1)


def test_actual_sql_trigger_failure_rolls_back_document_and_approval(harness):
    _app, client, db, _clock = harness
    item = approved(client)
    before = {name: rows(db, name) for name in ("documents", "operations", "publications")}
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TRIGGER fail_publication BEFORE INSERT ON publications BEGIN SELECT RAISE(ABORT, 'private-diagnostic-must-not-escape'); END;"
        )
    error(action(client, item, "execute"), 503, "repository_unavailable")
    assert {name: rows(db, name) for name in before} == before


def test_publication_requires_matching_current_document_and_exact_approval_pair(harness):
    _app, client, db, _clock = harness
    item = approved(client)
    with sqlite3.connect(db) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO publications(owner_id,operation_id,document_id,version,content,intent_hash,applied_at) VALUES(?,?,?,?,?,?,?)",
                (
                    "alice",
                    item["operation_id"],
                    "agent-summary",
                    2,
                    item["content"],
                    item["intent_hash"],
                    1001,
                ),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE operations SET status='revoked',expires_at=NULL")
        connection.rollback()
    assert not rows(db, "publications")


def test_replay_still_requires_current_authentication(harness):
    app, client, db, _clock = harness
    item = approved(client)
    assert action(client, item, "execute").status_code == 200
    app.state.sessions.revoke(AGENT)
    error(action(client, item, "execute"), 401, "authentication_required")
    error(
        client.get(f"/operations/{item['operation_id']}", headers=headers()),
        401,
        "authentication_required",
    )
    assert len(rows(db, "publications")) == 1


def test_explicit_post_commit_fault_and_delay_options(tmp_path):
    db = tmp_path / "fault.db"
    with TestClient(create_app(db, fault_after_commit=True, response_delay_ms=0)) as client:
        item = approved(client)
        error(action(client, item, "execute"), 503, "result_unconfirmed")
        assert len(rows(db, "publications")) == 1
        assert (
            client.get(f"/operations/{item['operation_id']}", headers=headers()).json()["status"]
            == "applied"
        )
        replay = action(client, item, "execute")
        assert replay.status_code == 200 and replay.json()["replayed"] is True
    for invalid in (-1, 2001, True, 1.0):
        with pytest.raises(ValueError, match="invalid startup configuration"):
            create_app(db, response_delay_ms=invalid)


def test_input_views_are_detached_from_stored_values(harness):
    _app, client, _db, _clock = harness
    item = approved(client)
    item["content"] = "locally altered"
    current = client.get(f"/operations/{item['operation_id']}", headers=headers()).json()
    assert current["content"] != item["content"]
    assert (
        action(client, current, "execute").json()["operation"]["receipt"]["content"]
        == current["content"]
    )


@pytest.mark.parametrize("mode", ["success", "commit-before", "commit-after", "rejection"])
def test_cleanup_faults_do_not_replace_unknown_commit_or_business_rejection(harness, mode):
    app, client, db, _clock = harness
    item = approved(client)
    database = app.state.repository
    original_connect, original_commit = database.connect, database.commit

    class ClosingFault:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def rollback(self):
            self.connection.rollback()
            raise sqlite3.OperationalError(PRIVATE)

        def close(self):
            self.connection.close()
            raise sqlite3.OperationalError(PRIVATE)

    database.connect = lambda: ClosingFault(original_connect())
    calls = []

    def commit(connection):
        calls.append(True)
        if mode in {"success", "commit-after"}:
            original_commit(connection)
        if mode in {"commit-before", "commit-after"}:
            raise sqlite3.OperationalError(PRIVATE)

    database.commit = commit
    if mode == "rejection":
        error(action(client, item, "execute", intent_hash="0" * 64), 409, "intent_mismatch")
        assert calls == []
    else:
        error(action(client, item, "execute"), 503, "result_unconfirmed")
        assert calls == [True]
    database.connect, database.commit = original_connect, original_commit
    persisted = mode in {"success", "commit-after"}
    assert len(rows(db, "publications")) == int(persisted)
    reply = action(client, item, "execute").json()
    assert reply["replayed"] is persisted
    assert len(rows(db, "publications")) == 1
