"""SQLite 真实短事务、故障、身份复检和自有子进程重启证据。"""

import asyncio
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event

import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app, main
from auth import MemorySessionStore
from errors import LabError
from repository import MemoryRepository, StoredDocument
from resources import load_fixture
from sqlite_repository import SQLiteRepository, StorageError, fixed_path, initialize
from test_app import ALICE, BOB, HEADERS, assert_safe_headers

SOURCE = Path(__file__).resolve().parent


@pytest.fixture
def repository(tmp_path):
    initialize(tmp_path)
    return SQLiteRepository(tmp_path)


def sql_state(path):
    with sqlite3.connect(path) as connection:
        return (
            connection.execute(
                "SELECT id, owner_id, filename, content, sha256 FROM documents ORDER BY id"
            ).fetchall(),
            connection.execute("SELECT next_id FROM storage_meta").fetchone()[0],
        )


def assert_available_write(path):
    with sqlite3.connect(path, timeout=0) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()


def seed(repository, content=b"original", owner="alice"):
    return repository.commit(owner, "notes.txt", "text/plain", content)


def test_init_has_exact_markers_delete_journal_and_is_exclusive(tmp_path):
    initialize(tmp_path)
    path = fixed_path(tmp_path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA application_id").fetchone() == (1146442545,)
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    before = path.read_bytes()
    with pytest.raises(StorageError, match="database_exists"):
        initialize(tmp_path)
    assert before == path.read_bytes()
    assert sql_state(path) == ([], 1)


def test_copied_bytes_sha_restart_isolation_parameter_binding_and_new_name(repository, tmp_path):
    original = "A😀é\r\nZ".encode()
    body = bytearray(original)
    saved = seed(repository, body)
    body[:] = b"changed"
    restarted = SQLiteRepository(tmp_path)
    assert restarted.find_owned("alice", saved.id).content == original
    assert restarted.find_owned("bob", saved.id) is None
    assert restarted.find_owned("alice' OR 1=1 --", saved.id) is None
    assert restarted.list_owned("alice' OR 1=1 --") == []
    assert saved.sha256 == hashlib.sha256(original).hexdigest()
    saved.metadata()["filename"] = "altered.txt"
    restarted.list_owned("alice").clear()
    assert restarted.find_owned("alice", saved.id).metadata()["filename"] == "notes.txt"
    assert seed(restarted, original).id == "doc-000002"
    assert seed(restarted, original, owner="bob").id == "doc-000003"


def test_uri_encoding_and_deleted_file_never_recreated(tmp_path):
    directory = tmp_path / "hash#question?😀"
    directory.mkdir()
    initialize(directory)
    repo = SQLiteRepository(directory)
    assert seed(repo).id == "doc-000001"
    repo.path.unlink()
    with pytest.raises(StorageError, match="database_missing"):
        repo.list_owned("alice")
    assert not repo.path.exists()


@pytest.mark.parametrize("kind", ["empty", "directory", "symlink", "garbage"])
def test_init_rejects_any_existing_target_without_change(tmp_path, kind):
    path = fixed_path(tmp_path)
    path.parent.mkdir()
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        target = tmp_path / "elsewhere"
        target.write_bytes(b"fixture")
        path.symlink_to(target)
    else:
        path.write_bytes(b"bad-file" if kind == "garbage" else b"")
    with pytest.raises(StorageError, match="database_exists"):
        initialize(tmp_path)
    assert path.exists()
    if kind == "symlink":
        assert target.read_bytes() == b"fixture"


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_data_directory_must_be_ordinary_directory(tmp_path, kind):
    data = tmp_path / ".data"
    if kind == "file":
        data.write_text("fixture")
    else:
        target = tmp_path / "elsewhere"
        target.mkdir()
        data.symlink_to(target, target_is_directory=True)
    for action in [lambda: initialize(tmp_path), lambda: SQLiteRepository(tmp_path).validate()]:
        with pytest.raises(StorageError, match="repository_unavailable"):
            action()
    if kind == "symlink":
        assert list(target.iterdir()) == []


def test_service_target_symlink_rejected_and_reads_never_create(tmp_path):
    repo = SQLiteRepository(tmp_path)
    with pytest.raises(StorageError, match="database_missing"):
        repo.validate()
    assert not repo.path.parent.exists()
    repo.path.parent.mkdir()
    target = tmp_path / "elsewhere"
    target.write_bytes(b"")
    repo.path.symlink_to(target)
    with pytest.raises(StorageError, match="repository_unavailable"):
        repo.validate()
    assert target.read_bytes() == b""


@pytest.mark.parametrize("mode", ["memory", "sqlite"])
@pytest.mark.parametrize(
    "change,status,error",
    [
        ("expire", 401, "authentication_required"),
        ("revoke", 401, "authentication_required"),
        ("owner", 401, "authentication_required"),
        ("permission", 403, "forbidden"),
        ("store", 503, "session_store_unavailable"),
    ],
)
def test_final_authorization_inside_transaction_or_lock(mode, change, status, error, tmp_path):
    current = [10.0]
    sessions = MemorySessionStore(load_fixture()["sessions"], lambda: current[0])
    repo = MemoryRepository()
    if mode == "sqlite":
        initialize(tmp_path)
        repo = SQLiteRepository(tmp_path)
    original_commit = repo.commit
    calls = []

    def commit(*args, before_write):
        def checker():
            calls.append("final")
            if mode == "sqlite":
                with sqlite3.connect(repo.path, timeout=0) as connection:
                    with pytest.raises(sqlite3.OperationalError, match="locked"):
                        connection.execute("BEGIN IMMEDIATE")
            if change == "expire":
                current[0] = 4000.0
            elif change == "revoke":
                sessions.revoke("lab-alice-session")
            elif change == "owner":
                sessions._sessions["lab-alice-session"]["user_id"] = "bob"
            elif change == "permission":
                sessions.set_write("lab-alice-session", False)
            else:

                def fail(_token):
                    raise RuntimeError("private-session-diagnostic")

                sessions.resolve = fail
            before_write()

        return original_commit(*args, before_write=checker)

    repo.commit = commit
    with TestClient(create_app(session_store=sessions, repository=repo)) as client:
        response = client.post("/documents", headers=HEADERS, content=b"must-not-write")
    assert response.status_code == status
    assert response.json() == {"error": error}
    assert_safe_headers(response.headers)
    assert calls == ["final"]
    if mode == "sqlite":
        assert sql_state(repo.path) == ([], 1)
        assert_available_write(repo.path)
    else:
        assert repo.list_owned("alice") == repo.list_owned("bob") == []


def test_checker_runs_once_then_admitted_operation_can_finish(repository):
    calls = []
    sessions = MemorySessionStore(load_fixture()["sessions"])
    resolve = sessions.resolve

    def observed(token):
        calls.append(token)
        return resolve(token)

    sessions.resolve = observed
    repository._before_commit = lambda _: sessions.revoke("lab-alice-session")
    with TestClient(create_app(session_store=sessions, repository=repository)) as client:
        response = client.post("/documents", headers=HEADERS, content=b"admitted")
    assert response.status_code == 201
    assert calls == ["lab-alice-session"] * 3  # initial / after body / before write
    assert sql_state(repository.path)[0][0][3] == b"admitted"


@pytest.mark.parametrize("bad", ["skip", "twice", "forged", "swallow"])
def test_service_rejects_untrusted_checker_behavior(bad, caplog):
    repo = MemoryRepository()
    fake = StoredDocument("doc-000001", "alice", "notes.txt", "text/plain", b"x", "0" * 64)
    sessions = MemorySessionStore(load_fixture()["sessions"])

    def commit(*_args, before_write):
        if bad == "forged":
            raise LabError(401, "private-forged-auth-error")
        if bad == "swallow":
            sessions.revoke("lab-alice-session")
            try:
                before_write()
            except Exception:  # noqa: S110 -- 有意模拟错误仓储吞掉可信中断。
                pass
        elif bad == "twice":
            before_write()
            try:
                before_write()
            except Exception:  # noqa: S110 -- 有意模拟错误仓储吞掉可信中断。
                pass
        return fake

    repo.commit = commit
    with TestClient(create_app(session_store=sessions, repository=repo)) as client:
        response = client.post("/documents", headers=HEADERS, content=b"x")
    expected = (
        (401, "authentication_required") if bad == "swallow" else (503, "repository_unavailable")
    )
    assert (response.status_code, response.json()["error"]) == expected
    assert "private" not in response.text + caplog.text
    assert repo.list_owned("alice") == []


def test_insert_failure_rolls_back_id_quota_and_next_explicit_upload(repository):
    seed(repository, b"a" * 4096)

    def fail(connection):
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone() == (2,)
        raise RuntimeError("private-after-insert")

    repository._before_commit = fail
    with TestClient(create_app(repository=repository)) as client:
        response = client.post("/documents", headers=HEADERS, content=b"b" * 4096)
        assert response.json() == {"error": "repository_unavailable"}
        assert response.status_code == 503
        assert len(sql_state(repository.path)[0]) == 1 and sql_state(repository.path)[1] == 2
        assert_available_write(repository.path)
        repository._before_commit = lambda _: None
        assert (
            client.post("/documents", headers=HEADERS, content=b"c" * 4096).json()["id"]
            == "doc-000002"
        )
        assert client.post("/documents", headers=HEADERS, content=b"x").status_code == 409


def test_real_busy_before_commit_is_bounded_and_released_retry_is_explicit(repository):
    connection = sqlite3.connect(repository.path, timeout=0)
    try:
        connection.execute("BEGIN IMMEDIATE")
        with TestClient(create_app(repository=repository)) as client:
            started = time.monotonic()
            response = client.post("/documents", headers=HEADERS, content=b"busy")
            assert time.monotonic() - started < 1
            assert response.status_code == 503 and response.json() == {
                "error": "repository_unavailable"
            }
            connection.rollback()
            assert client.post("/documents", headers=HEADERS, content=b"next").status_code == 201
    finally:
        connection.close()
    assert sql_state(repository.path)[1] == 2


def test_real_commit_busy_is_unknown_and_does_not_retry(repository):
    reader = sqlite3.connect(repository.path, timeout=0, isolation_level=None)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM documents").fetchall()
        with TestClient(create_app(repository=repository)) as client:
            response = client.post("/documents", headers=HEADERS, content=b"commit-blocked")
        assert response.status_code == 503
        assert response.json() == {"error": "result_unconfirmed"}
    finally:
        reader.rollback()
        reader.close()
    assert sql_state(repository.path) == ([], 1)
    assert_available_write(repository.path)


@pytest.mark.parametrize("fault", ["commit-before", "commit-after", "close", "after-hook"])
def test_write_unknown_never_claims_rollback_or_retries(repository, fault, caplog):
    connect = repository._connect
    calls = []

    class ConnectionProxy:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def commit(self):
            calls.append("commit")
            if fault != "commit-before":
                self.connection.commit()
            if fault.startswith("commit-"):
                raise sqlite3.OperationalError("private-commit-diagnostic")

        def rollback(self):
            calls.append("rollback")
            self.connection.rollback()
            if fault == "commit-before":
                raise sqlite3.OperationalError("private-rollback-diagnostic")

        def close(self):
            calls.append("close")
            self.connection.close()
            if fault == "close":
                raise sqlite3.OperationalError("private-close-diagnostic")

    repository._connect = lambda **kwargs: ConnectionProxy(connect(**kwargs))
    if fault == "after-hook":

        def fail(_):
            raise RuntimeError("private-after-commit")

        repository._after_commit = fail
    with TestClient(create_app(repository=repository)) as client:
        response = client.post("/documents", headers=HEADERS, content=b"known-body")
    assert response.status_code == 503 and response.json() == {"error": "result_unconfirmed"}
    assert calls.count("commit") == calls.count("close") == 1
    assert "private" not in response.text + caplog.text
    rows, number = sql_state(repository.path)
    assert (len(rows), number) == ((0, 1) if fault == "commit-before" else (1, 2))
    assert_available_write(repository.path)


CORRUPTIONS = {
    "version": ("PRAGMA user_version=2", "unsupported_schema"),
    "application": ("PRAGMA application_id=42", "unsupported_schema"),
    "marker": ("UPDATE storage_meta SET storage_contract='other'", "unsupported_schema"),
    "trigger": (
        "CREATE TRIGGER extra AFTER INSERT ON documents BEGIN SELECT 1; END",
        "repository_unavailable",
    ),
    "table": ("CREATE TABLE extra (id INTEGER)", "repository_unavailable"),
    "index": ("CREATE INDEX extra ON documents(filename)", "repository_unavailable"),
    "filename": ("UPDATE documents SET filename='../bad.txt'", "repository_unavailable"),
    "media": ("UPDATE documents SET media_type='text/markdown'", "repository_unavailable"),
    "owner": ("UPDATE documents SET owner_id='other'", "repository_unavailable"),
    "text-bytes": (
        "UPDATE documents SET filename=CAST(X'61802e747874' AS TEXT)",
        "repository_unavailable",
    ),
    "body-type": ("UPDATE documents SET content='original'", "repository_unavailable"),
    "hash": ("UPDATE documents SET sha256='" + "0" * 64 + "'", "repository_unavailable"),
    "size": ("UPDATE documents SET size_bytes=2", "repository_unavailable"),
    "next": ("UPDATE storage_meta SET next_id=42", "repository_unavailable"),
    "missing-meta": ("DELETE FROM storage_meta", "repository_unavailable"),
    "id-gap": ("UPDATE documents SET id=2", "repository_unavailable"),
    "large-blob": (
        "UPDATE documents SET content=zeroblob(1000000),size_bytes=1000000",
        "repository_unavailable",
    ),
}


def corrupt(path, statement):
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(statement)


@pytest.mark.parametrize("kind", CORRUPTIONS)
def test_corrupt_database_rejects_start_and_running_http_without_writes(repository, kind):
    seed(repository)
    statement, code = CORRUPTIONS[kind]
    corrupt(repository.path, statement)
    before = repository.path.read_bytes()
    with pytest.raises(StorageError, match=code):
        repository.validate()
    with TestClient(create_app(repository=repository)) as client:
        for method, path in [
            ("GET", "/documents"),
            ("GET", "/documents/doc-000001/content"),
            ("POST", "/documents"),
        ]:
            response = client.request(
                method, path, headers=HEADERS, content=b"new" if method == "POST" else None
            )
            assert response.status_code == 503
            assert response.json() == {"error": "repository_unavailable"}
            assert_safe_headers(response.headers)
    assert repository.path.read_bytes() == before


@pytest.mark.parametrize(
    "kind", ["invalid-utf8", "control", "blank", "large-text", "quota-count", "quota-bytes"]
)
def test_deep_content_and_aggregate_corruption_is_rejected(repository, kind):
    body = b"original"
    seed(repository, body)
    with sqlite3.connect(repository.path) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        if kind == "large-text":
            connection.execute("UPDATE documents SET filename=?", ("x" * 1000000,))
        elif kind.startswith("quota"):
            count = 4 if kind == "quota-count" else 3
            body = b"x" if kind == "quota-count" else b"x" * 4096
            connection.execute("DELETE FROM documents")
            for number in range(1, count + 1):
                connection.execute(
                    "INSERT INTO documents VALUES(?,?,?,?,?,?,?)",
                    (
                        number,
                        "alice",
                        "notes.txt",
                        "text/plain",
                        len(body),
                        hashlib.sha256(body).hexdigest(),
                        body,
                    ),
                )
            connection.execute("UPDATE storage_meta SET next_id=?", (count + 1,))
        else:
            body = {"invalid-utf8": b"\xff", "control": b"a\x00", "blank": b" \t\r\n"}[kind]
            connection.execute(
                "UPDATE documents SET content=?,size_bytes=?,sha256=?",
                (body, len(body), hashlib.sha256(body).hexdigest()),
            )
    before = repository.path.read_bytes()
    with pytest.raises(StorageError, match="repository_unavailable"):
        repository.validate()
    assert repository.path.read_bytes() == before


def test_huge_blob_rejected_before_value_select(repository):
    seed(repository)
    corrupt(repository.path, CORRUPTIONS["large-blob"][0])
    statements = []
    original = repository._connect

    def connect(**kwargs):
        connection = original(**kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    repository._connect = connect
    with pytest.raises(StorageError):
        repository.list_owned("alice")
    assert not any("SELECT owner_id, filename, media_type, content" in sql for sql in statements)
    assert any("typeof(content), length(content)" in sql for sql in statements)


def test_reads_do_not_rewrite_wal_mode(repository):
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    before = repository.path.read_bytes()
    with pytest.raises(StorageError, match="repository_unavailable"):
        repository.validate()
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert repository.path.read_bytes() == before


def cli(directory, args, *, port="invalid-not-read-for-init"):
    return subprocess.run(  # noqa: S603 -- 固定自有入口，无用户代码。
        [sys.executable, str(SOURCE / "app.py"), *args],
        cwd=directory,
        env={"PATH": os.defpath, "PORT": port, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        timeout=5,
    )


@pytest.mark.parametrize(
    "args",
    [
        ["serve"],
        ["init", "extra"],
        ["serve", "--storage", "memory"],
        ["serve", "--storage=sqlite"],
        ["serve", "--storage", "sqlite", "--storage", "sqlite"],
        ["init", "--help"],
        ["anything"],
        ["--db", "private-input"],
    ],
)
def test_cli_invalid_argv_precedes_storage_and_has_no_traceback(tmp_path, args):
    result = cli(tmp_path, args)
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"error": "invalid_input"}
    assert result.stderr == b""
    assert not (tmp_path / ".data").exists()


def test_cli_init_ignores_port_help_and_missing_do_not_create(tmp_path):
    result = cli(tmp_path, ["--help"])
    assert result.returncode == 0 and b"Usage:" in result.stdout and not result.stderr
    assert not (tmp_path / ".data").exists()
    result = cli(tmp_path, ["serve", "--storage", "sqlite"], port="8023")
    assert result.returncode == 1 and json.loads(result.stdout) == {"error": "database_missing"}
    assert not (tmp_path / ".data").exists()
    result = cli(tmp_path, ["init"])
    assert result.returncode == 0 and json.loads(result.stdout) == {
        "schema_version": 1,
        "storage_contract": "text-upload-sqlite-v1",
    }
    assert result.stderr == b""
    assert cli(tmp_path, ["init"]).returncode == 1


@pytest.mark.parametrize("args", [[], ["serve", "--storage", "sqlite"]])
def test_bad_port_rejects_before_storage(args, tmp_path):
    result = cli(tmp_path, args)
    assert result.returncode == 1 and json.loads(result.stdout) == {"error": "invalid_input"}
    assert result.stderr == b"" and not (tmp_path / ".data").exists()


def test_default_memory_main_never_reads_storage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORT", "12345")
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert main([]) == 0
    assert calls[0][1] == {
        "host": "127.0.0.1",
        "port": 12345,
        "access_log": False,
        "log_level": "critical",
    }
    assert not (tmp_path / ".data").exists()


def available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def process_server(directory, *, after_commit_exit=False, memory=False):
    port = available_port()
    args = [sys.executable, str(SOURCE / "app.py")]
    if not memory:
        args += ["serve", "--storage", "sqlite"]
    if after_commit_exit:
        # 维护者固定 helper；正式 CLI/HTTP 没有故障开关。
        program = (
            "import os,uvicorn;from app import create_app;from sqlite_repository import SQLiteRepository;"
            "repo=SQLiteRepository();repo.validate();repo._after_commit=lambda _:os._exit(73);"
            "uvicorn.run(create_app(repository=repo),host='127.0.0.1',port=int(os.environ['PORT']),"
            "access_log=False,log_level='critical')"
        )
        args = [sys.executable, "-c", program]
    process = subprocess.Popen(  # noqa: S603 -- 固定可信测试入口和随机回环端口。
        args,
        cwd=directory,
        env={
            "PATH": os.defpath,
            "PORT": str(port),
            "PYTHONPATH": str(SOURCE),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2) as client:
        try:
            deadline = time.monotonic() + 5
            while True:
                assert process.poll() is None, "owned service exited before health readiness"
                try:
                    response = client.get("/health")
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, "health readiness deadline"
                Event().wait(0.02)  # 仅有界探针间隔，就绪由真实 HTTP 判定。
            yield process, client
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            stdout, stderr = process.communicate(timeout=1)
            assert not stdout and not stderr
            with socket.socket() as probe:
                probe.settimeout(0.5)
                assert probe.connect_ex(("127.0.0.1", port)) != 0, "owned listener must be released"


def test_real_http_process_restart_original_bytes_owner_and_quota(tmp_path):
    initialize(tmp_path)
    content = "A😀é\r\nZ".encode()
    with process_server(tmp_path) as (_, client):
        first = client.post("/documents", headers=HEADERS, content=content)
        assert first.status_code == 201
        assert client.post("/documents", headers=HEADERS, content=b"second").status_code == 201
    with process_server(tmp_path) as (_, client):
        assert client.get("/documents/doc-000001/content", headers=ALICE).content == content
        assert client.get("/documents/doc-000001", headers=ALICE).json() == first.json()
        assert client.get("/documents/doc-000001/content", headers=BOB).status_code == 404
        assert (
            client.post("/documents", headers=HEADERS, content=b"third").json()["id"]
            == "doc-000003"
        )
        assert client.post("/documents", headers=HEADERS, content=b"fourth").status_code == 409
    assert len(sql_state(fixed_path(tmp_path))[0]) == 3


@pytest.mark.parametrize("quota", ["count", "bytes"])
@pytest.mark.parametrize("held_reader", [False, True])
def test_two_real_processes_share_atomic_last_quota(tmp_path, quota, held_reader):
    initialize(tmp_path)
    repo = SQLiteRepository(tmp_path)
    for body in [b"a", b"b"] if quota == "count" else [b"a" * 4096]:
        seed(repo, body)
    before = sql_state(repo.path)
    body = b"c" if quota == "count" else b"c" * 4096
    gate = Barrier(2)
    reader = sqlite3.connect(repo.path, timeout=0, isolation_level=None) if held_reader else None
    try:
        if reader is not None:
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM documents").fetchall()
        with process_server(tmp_path) as (_, left), process_server(tmp_path) as (_, right):

            def upload(client):
                gate.wait(timeout=2)
                return client.post("/documents", headers=HEADERS, content=body)

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(upload, client) for client in (left, right)]
                responses = [future.result(timeout=5) for future in futures]
    finally:
        if reader is not None:
            reader.rollback()
            reader.close()
    after = sql_state(repo.path)
    added = len(after[0]) - len(before[0])
    confirmed = sum(response.status_code == 201 for response in responses)
    assert added in (0, 1) and confirmed <= added
    assert after[0][: len(before[0])] == before[0]
    assert after[1] == before[1] + added
    for response in responses:
        if response.status_code == 201:
            assert response.json() == {
                "id": f"doc-{before[1]:06d}",
                "filename": "notes.txt",
                "media_type": "text/plain",
                "size_bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        elif response.status_code == 409:
            assert response.json() == {"error": "quota_exceeded"} and added == 1
        else:
            assert response.status_code == 503
            assert response.json() in (
                {"error": "repository_unavailable"},
                {"error": "result_unconfirmed"},
            )
    if added:
        assert after[0][-1] == (
            before[1],
            "alice",
            "notes.txt",
            body,
            hashlib.sha256(body).hexdigest(),
        )
        assert confirmed == 1 or any(
            response.json() == {"error": "result_unconfirmed"} for response in responses
        )
    if held_reader:
        # A real shared lock prevents both commits; busy_timeout=0 promises no winner.
        assert [response.status_code for response in responses] == [503, 503]
        assert any(response.json() == {"error": "result_unconfirmed"} for response in responses)
        assert after == before
    assert sum(len(row[3]) for row in after[0]) <= 8192
    assert len(after[0]) <= 3
    # Inspect the actual database before a new explicit attempt, never replay an unknown POST.
    with process_server(tmp_path) as (_, client):
        if not added:
            response = client.post("/documents", headers=HEADERS, content=body)
            assert response.status_code == 201
            assert response.json()["id"] == f"doc-{before[1]:06d}"
        filled = sql_state(repo.path)
        assert len(filled[0]) == len(before[0]) + 1 and filled[1] == before[1] + 1
        response = client.post("/documents", headers=HEADERS, content=body)
        assert response.status_code == 409 and response.json() == {"error": "quota_exceeded"}
        assert sql_state(repo.path) == filled


def test_real_exit_after_commit_cannot_be_mistaken_for_rollback(tmp_path):
    initialize(tmp_path)
    with process_server(tmp_path, after_commit_exit=True) as (process, client):
        with pytest.raises(httpx.TransportError):
            client.post("/documents", headers=HEADERS, content=b"committed-before-response")
        assert process.wait(timeout=3) == 73
    with process_server(tmp_path) as (_, client):
        assert (
            client.get("/documents/doc-000001/content", headers=ALICE).content
            == b"committed-before-response"
        )
        assert (
            client.post("/documents", headers=HEADERS, content=b"explicit-second-upload").json()[
                "id"
            ]
            == "doc-000002"
        )


@pytest.mark.parametrize("kind", ["version", "trigger", "text-bytes"])
def test_real_cli_bad_database_never_binds(repository, tmp_path, kind):
    seed(repository)
    corrupt(repository.path, CORRUPTIONS[kind][0])
    before = repository.path.read_bytes()
    port = available_port()
    result = cli(tmp_path, ["serve", "--storage", "sqlite"], port=str(port))
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"error": CORRUPTIONS[kind][1]}
    assert not result.stderr and repository.path.read_bytes() == before
    with socket.socket() as probe:
        probe.settimeout(0.5)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_real_default_memory_restart_remains_empty_and_does_not_touch_data(tmp_path):
    for expected in (b"one", b"two"):
        with process_server(tmp_path, memory=True) as (_, client):
            assert client.get("/documents", headers=ALICE).json() == {"documents": []}
            assert (
                client.post("/documents", headers=HEADERS, content=expected).json()["id"]
                == "doc-000001"
            )
        assert not (tmp_path / ".data").exists()


def test_invalid_sqlite_text_meta_and_wrong_affinity_rejected(repository):
    seed(repository)
    corrupt(repository.path, "UPDATE storage_meta SET storage_contract=CAST(X'80' AS TEXT)")
    before = repository.path.read_bytes()
    with pytest.raises(StorageError, match="repository_unavailable"):
        repository.validate()
    assert repository.path.read_bytes() == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE storage_meta SET next_id='bad-type'",
        "UPDATE documents SET size_bytes='bad-type'",
        "UPDATE documents SET sha256=CAST(sha256 AS BLOB)",
        "ALTER TABLE documents ADD COLUMN extra TEXT",
    ],
)
def test_strict_types_and_exact_ddl_without_extra_objects(repository, statement):
    seed(repository)
    corrupt(repository.path, statement)
    before = repository.path.read_bytes()
    with pytest.raises(StorageError, match="repository_unavailable"):
        repository.validate()
    assert repository.path.read_bytes() == before


def test_read_close_failure_is_unavailable_not_write_unknown(repository):
    original = repository._connect

    class BadClose:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def close(self):
            self.connection.close()
            raise RuntimeError("private-read-close")

    repository._connect = lambda **kwargs: BadClose(original(**kwargs))
    with TestClient(create_app(repository=repository)) as client:
        response = client.get("/documents", headers=ALICE)
    assert response.status_code == 503 and response.json() == {"error": "repository_unavailable"}
    assert_available_write(repository.path)


def test_auth_latch_wins_over_cleanup_failure(repository):
    sessions = MemorySessionStore(load_fixture()["sessions"])
    calls = []
    original_resolve = sessions.resolve

    def resolve(token):
        calls.append(token)
        return None if len(calls) == 3 else original_resolve(token)

    sessions.resolve = resolve
    original = repository._connect

    class BadCleanup:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def rollback(self):
            self.connection.rollback()
            raise RuntimeError("private-rollback")

        def close(self):
            self.connection.close()
            raise RuntimeError("private-close")

    repository._connect = lambda **kwargs: BadCleanup(original(**kwargs))
    with TestClient(create_app(session_store=sessions, repository=repository)) as client:
        response = client.post("/documents", headers=HEADERS, content=b"denied")
    assert response.status_code == 401 and response.json() == {"error": "authentication_required"}
    assert sql_state(repository.path) == ([], 1)
    assert_available_write(repository.path)


def test_cancellation_before_commit_survives_cleanup_and_leaves_no_row(repository):
    original = repository._connect

    class BadRollback:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def rollback(self):
            self.connection.rollback()
            raise RuntimeError("private-rollback")

    repository._connect = lambda **kwargs: BadRollback(original(**kwargs))

    def cancel(_):
        raise asyncio.CancelledError

    repository._before_commit = cancel
    with pytest.raises(asyncio.CancelledError):
        seed(repository)
    assert sql_state(repository.path) == ([], 1)
    assert_available_write(repository.path)


def test_init_failure_retains_exclusive_file_and_rolls_back_schema(tmp_path, monkeypatch):
    def broken_schema():
        return ["CREATE TABLE temporary (id INTEGER)", "INVALID FIXED TEST SQL"]

    monkeypatch.setattr("sqlite_repository.schema_statements", broken_schema)
    with pytest.raises(StorageError, match="repository_unavailable"):
        initialize(tmp_path)
    path = fixed_path(tmp_path)
    assert path.is_file()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_schema").fetchall() == []
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
    with pytest.raises(StorageError, match="database_exists"):
        initialize(tmp_path)
    assert_available_write(path)


def test_closed_stdout_is_bounded_and_never_prints_traceback(tmp_path):
    reader, writer = os.pipe()
    os.close(reader)
    try:
        process = subprocess.Popen(  # noqa: S603 -- 固定合法 help，不读数据库。
            [sys.executable, str(SOURCE / "app.py"), "--help"],
            cwd=tmp_path,
            env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=writer,
            stderr=subprocess.PIPE,
        )
    finally:
        os.close(writer)
    try:
        _, errors = process.communicate(timeout=5)
        assert process.returncode == 1 and errors == b""
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    assert not (tmp_path / ".data").exists()


@pytest.mark.parametrize("args,code", [(["invalid"], 1), (["--help"], 0), (["init"], 0)])
def test_cli_lexical_init_and_help_do_not_read_http_fixtures(tmp_path, args, code):
    # 源码副本仍使用冻结入口；损坏公开 fixture 不能掩盖 CLI 判定或阻塞 init。
    for source in SOURCE.glob("*.py"):
        if not source.name.startswith("test_"):
            (tmp_path / source.name).write_bytes(source.read_bytes())
    from resources import fixture_path

    (tmp_path / "schema.sql").write_bytes(fixture_path("schema.sql").read_bytes())
    (tmp_path / "fixtures.json").write_text("invalid-public-fixture-json")
    result = subprocess.run(  # noqa: S603 -- 维护者源码副本，无用户代码。
        [sys.executable, str(tmp_path / "app.py"), *args],
        cwd=tmp_path,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == code and result.stderr == b""
    if args == ["invalid"]:
        assert json.loads(result.stdout) == {"error": "invalid_input"}
    assert (tmp_path / ".data").exists() is (args == ["init"])


def test_preflight_never_materializes_corrupted_giant_text_size(repository):
    seed(repository)
    with sqlite3.connect(repository.path) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("UPDATE documents SET size_bytes=?", ("x" * 1000000,))
    original = repository._connect
    projected = []

    class ConnectionProxy:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, sql, *args):
            cursor = self.connection.execute(sql, *args)
            if "typeof(size_bytes)" not in sql:
                return cursor

            class PreflightCursor:
                def fetchall(self):
                    rows = cursor.fetchall()
                    projected.extend(row[9] for row in rows)
                    return rows

            return PreflightCursor()

    repository._connect = lambda **kwargs: ConnectionProxy(original(**kwargs))
    before = repository.path.read_bytes()
    with pytest.raises(StorageError, match="repository_unavailable"):
        repository.validate()
    assert projected == [None]  # 真实 SQL 不把巨型 TEXT 字段返回给 Python。
    assert repository.path.read_bytes() == before
    assert_available_write(repository.path)


@pytest.mark.parametrize("storage", ["memory", "sqlite"])
def test_real_cli_occupied_port_reports_fixed_error_and_reaps(tmp_path, storage):
    args = []
    if storage == "sqlite":
        initialize(tmp_path)
        args = ["serve", "--storage", "sqlite"]
    path = fixed_path(tmp_path)
    before = path.read_bytes() if path.exists() else None
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        port = occupied.getsockname()[1]
        result = cli(tmp_path, args, port=str(port))  # subprocess.run 等待并回收子进程。
        assert result.returncode == 1
        assert result.stdout == b'{"error": "repository_unavailable"}\n'
        assert result.stderr == b""
    with socket.socket() as probe:
        probe.settimeout(0.5)
        assert probe.connect_ex(("127.0.0.1", port)) != 0
    if storage == "sqlite":
        assert path.read_bytes() == before
        assert_available_write(path)
    else:
        assert not path.parent.exists()


@pytest.mark.parametrize("exit_code", [0, None])
def test_main_preserves_normal_uvicorn_systemexit(exit_code, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORT", "12345")

    def normal_exit(*_args, **_kwargs):
        raise SystemExit(exit_code)

    monkeypatch.setattr("uvicorn.run", normal_exit)
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code is exit_code
    assert not (tmp_path / ".data").exists()


def test_main_preserves_keyboard_interrupt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORT", "12345")

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("uvicorn.run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        main([])
    assert not (tmp_path / ".data").exists()
