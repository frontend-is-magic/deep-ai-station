"""真实文件数据库与独立 CLI 进程验证，不连接模型或网络。"""

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import repository as storage
from app import MAX_CURSOR, parse_command
from repository import Repository, StorageError, execute, execute_statements, migration_path

FIRST = "2026-10-03T12:00:00.000Z"
LATER = "2026-10-03T13:00:00.001Z"


def update(**overrides):
    return {
        "op": "set_progress",
        "owner_id": "alice",
        "lesson_id": "fullstack-database",
        "completed": True,
        "at": FIRST,
        **overrides,
    }


def listing(operation="list_progress", **overrides):
    return {"op": operation, "owner_id": "alice", "cursor": 0, "limit": 2, **overrides}


def run_cli(path, command=None, *, raw=None, entry=None):
    entry = Path(entry) if entry is not None else Path(__file__).with_name("app.py")
    body = raw if raw is not None else json.dumps(command, ensure_ascii=False).encode()
    process = subprocess.run(  # noqa: S603 - fixed test-owned CLI command and fixture arguments.
        [sys.executable, str(entry), str(path)],
        input=body,
        capture_output=True,
        check=False,
        timeout=5,
        cwd=path.parent if path.parent.is_dir() else entry.parent,
    )
    assert process.stderr == b""
    assert len(process.stdout.splitlines()) == 1
    return process.returncode, json.loads(process.stdout)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "progress.sqlite3"
    assert run_cli(path, {"op": "migrate"}) == (0, {"schema_version": 2})
    return path


def test_cli_restart_persistence_idempotence_and_atomic_audit(database):
    status, first = run_cli(database, update())
    assert status == 0
    assert first == {
        "item": {
            "id": 1,
            "owner_id": "alice",
            "lesson_id": "fullstack-database",
            "completed": True,
            "created_at": FIRST,
            "updated_at": FIRST,
        },
        "changed": True,
    }
    assert run_cli(database, update(at=LATER)) == (0, {**first, "changed": False})
    assert run_cli(database, listing("list_audit")) == (
        0,
        {
            "items": [{"id": 1, "progress_id": 1, "completed": True, "at": FIRST}],
            "next_cursor": None,
        },
    )
    assert run_cli(database, update(completed=False, at=LATER)) == (
        0,
        {
            "item": {**first["item"], "completed": False, "updated_at": LATER},
            "changed": True,
        },
    )
    status, page = run_cli(database, listing())
    assert status == 0
    assert page["items"][0]["created_at"] == FIRST
    assert page["items"][0]["updated_at"] == LATER
    assert run_cli(database, listing("list_audit")) == (
        0,
        {
            "items": [
                {"id": 1, "progress_id": 1, "completed": True, "at": FIRST},
                {"id": 2, "progress_id": 1, "completed": False, "at": LATER},
            ],
            "next_cursor": None,
        },
    )


def test_v1_migration_preserves_ids_and_backfills_updated_at(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        execute_statements(connection, migration_path(1).read_text())
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            "INSERT INTO progress VALUES (?, ?, ?, ?, ?)", (42, "alice", "first-lesson", 1, FIRST)
        )
        connection.execute("INSERT INTO audit VALUES (?, ?, ?, ?)", (17, 42, 1, FIRST))
    assert run_cli(path, listing()) == (1, {"error": "unsupported_schema"})
    assert run_cli(path, {"op": "migrate"}) == (0, {"schema_version": 2})
    before = path.read_bytes()
    assert run_cli(path, {"op": "migrate"}) == (0, {"schema_version": 2})
    assert path.read_bytes() == before
    assert run_cli(path, listing()) == (
        0,
        {
            "items": [
                {
                    "id": 42,
                    "owner_id": "alice",
                    "lesson_id": "first-lesson",
                    "completed": True,
                    "created_at": FIRST,
                    "updated_at": FIRST,
                }
            ],
            "next_cursor": None,
        },
    )
    assert run_cli(path, listing("list_audit")) == (
        0,
        {
            "items": [{"id": 17, "progress_id": 42, "completed": True, "at": FIRST}],
            "next_cursor": None,
        },
    )


def test_future_schema_is_rejected_without_any_file_changes(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 3")
        connection.execute("CREATE TABLE sentinel (value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('keep this value')")
    before = path.read_bytes()
    for command in [{"op": "migrate"}, update(), listing(), listing("list_audit")]:
        assert run_cli(path, command) == (1, {"error": "unsupported_schema"})
        assert path.read_bytes() == before


def test_migration_failure_rolls_back_ddl_and_version(tmp_path, monkeypatch):
    path = tmp_path / "broken-migration.sqlite3"
    second = tmp_path / "second.sql"
    second.write_text(
        "ALTER TABLE progress ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''; INVALID SQL;"
    )
    original = storage.migration_path
    monkeypatch.setattr(
        storage, "migration_path", lambda version: second if version == 2 else original(version)
    )
    with pytest.raises(StorageError, match="storage_failure"):
        execute(path, {"op": "migrate"})
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
        )


@pytest.mark.parametrize("operation", ["list_progress", "list_audit"])
def test_owner_keyset_pagination_has_no_leaks_duplicates_or_false_next_cursor(database, operation):
    for index, owner in enumerate(["alice", "bob", "alice", "bob", "alice"]):
        assert run_cli(database, update(owner_id=owner, lesson_id=f"lesson-{index}"))[0] == 0
    status, first = run_cli(database, listing(operation, limit=2))
    assert status == 0
    assert [item["id"] for item in first["items"]] == [1, 3]
    assert first["next_cursor"] == 3
    assert run_cli(database, listing(operation, cursor=3, limit=2))[1] == {
        "items": [run_cli(database, listing(operation, cursor=4, limit=2))[1]["items"][0]],
        "next_cursor": None,
    }
    status, last = run_cli(database, listing(operation, cursor=first["next_cursor"], limit=2))
    assert status == 0
    assert [item["id"] for item in last["items"]] == [5]
    assert last["next_cursor"] is None
    assert run_cli(database, listing(operation, cursor=5))[1] == {"items": [], "next_cursor": None}
    status, other = run_cli(database, listing(operation, owner_id="bob", limit=2))
    assert status == 0
    assert [item["id"] for item in other["items"]] == [2, 4]
    assert other["next_cursor"] is None


def test_parameterized_repository_values_cannot_change_sql_or_owner_scope(database):
    repository = Repository(database)
    try:
        odd_owner = "alice' OR 1=1 --"
        odd_lesson = "x'); DROP TABLE progress; --"
        repository.set_progress(update())
        injected = repository.set_progress(update(owner_id=odd_owner, lesson_id=odd_lesson))
        assert injected["item"]["owner_id"] == odd_owner
        assert repository.list_progress(listing(owner_id=odd_owner))["items"] == [injected["item"]]
        assert len(repository.list_progress(listing())["items"]) == 1
        assert len(repository.list_audit(listing("list_audit", owner_id=odd_owner))["items"]) == 1
        assert repository.connection.execute("SELECT COUNT(*) FROM progress").fetchone()[0] == 2
    finally:
        repository.close()


@pytest.mark.parametrize("existing", [False, True])
def test_audit_failure_rolls_back_progress_insert_or_update(database, existing):
    if existing:
        assert run_cli(database, update())[0] == 0
    before_progress = run_cli(database, listing())[1]
    before_audit = run_cli(database, listing("list_audit"))[1]

    def fail_audit(connection):
        # A temporary trigger creates a genuine SQLite audit failure after the upsert.
        execute_statements(
            connection,
            "CREATE TEMP TRIGGER fail_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT, 'private audit fixture failure'); END;",
        )

    repository = Repository(database, _before_audit=fail_audit)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            repository.set_progress(update(completed=False, at=LATER))
        assert repository.connection.in_transaction is False
    finally:
        repository.close()
    assert run_cli(database, listing())[1] == before_progress
    assert run_cli(database, listing("list_audit"))[1] == before_audit


@pytest.mark.parametrize("command", [{"op": "migrate"}, update()])
def test_busy_write_returns_stable_error_without_database_path(database, command):
    with sqlite3.connect(database, isolation_level=None) as holder:
        holder.execute("BEGIN IMMEDIATE")
        try:
            assert run_cli(database, command) == (1, {"error": "database_busy"})
        finally:
            holder.rollback()
    assert run_cli(database, listing()) == (0, {"items": [], "next_cursor": None})


def test_connections_enable_foreign_keys_and_exact_busy_timeout(database):
    repository = Repository(database)
    try:
        assert repository.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert repository.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 1000
        with pytest.raises(sqlite3.IntegrityError):
            repository.connection.execute(
                "INSERT INTO audit (progress_id, completed, at) VALUES (?, ?, ?)", (999, 1, FIRST)
            )
    finally:
        repository.close()


@pytest.mark.parametrize(
    "command",
    [
        {},
        [],
        None,
        {"op": "missing"},
        {"op": "migrate", "extra": True},
        update(owner_id="Alice"),
        update(owner_id="alice' OR 1=1 --"),
        update(owner_id="a" * 65),
        update(lesson_id=""),
        update(lesson_id="../other"),
        update(lesson_id="工具"),
        update(completed=1),
        update(completed="true"),
        update(completed=None),
        update(at="2026-10-03T12:00:00Z"),
        update(at="2026-10-03T12:00:00.00Z"),
        update(at="2026-10-03T20:00:00.000+08:00"),
        update(at="2026-02-30T12:00:00.000Z"),
        update(at="0999-01-01T00:00:00.000Z"),
        update(at="+010000-01-01T00:00:00.000Z"),
        update(at="2026-10-03T24:00:00.000Z"),
        update(at=FIRST, extra="blocked"),
        listing(cursor=True),
        listing(limit=False),
        listing(cursor=-1),
        listing(cursor=MAX_CURSOR + 1),
        listing(cursor=1.5),
        listing(limit=0),
        listing(limit=51),
        listing(limit="1"),
        {"op": "list_progress", "owner_id": "alice", "cursor": 0},
    ],
)
def test_invalid_command_never_creates_a_database(tmp_path, command):
    path = tmp_path / "must-not-exist.sqlite3"
    assert run_cli(path, command) == (1, {"error": "invalid_input"})
    assert not path.exists()


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b'{"op":"migrate"} {}',
        b'{"op":"migrate"}\xc2\xa0',
        b'\xef\xbb\xbf{"op":"migrate"}',
        b'{"op":"\xff"}',
        b'{"op":"\\ud800"}',
        b'{"op":NaN}',
        b'{"op":Infinity}',
        b" " * 4097,
    ],
)
def test_invalid_encoding_json_and_byte_limit_do_not_write(tmp_path, raw):
    path = tmp_path / "must-not-exist.sqlite3"
    assert run_cli(path, raw=raw) == (1, {"error": "invalid_input"})
    assert not path.exists()


def test_exact_byte_boundary_and_integral_float_json_numbers(database):
    body = b'{"op":"migrate"}'
    assert run_cli(database, raw=body + b" " * (4096 - len(body))) == (0, {"schema_version": 2})
    assert run_cli(database, raw=body + b" " * (4097 - len(body))) == (
        1,
        {"error": "invalid_input"},
    )
    assert run_cli(database, listing(cursor=0.0, limit=1.0)) == (
        0,
        {"items": [], "next_cursor": None},
    )
    assert parse_command(json.dumps(listing(cursor=MAX_CURSOR)).encode())["cursor"] == MAX_CURSOR
    for date in [
        "1000-01-01T00:00:00.000Z",
        "9999-12-31T23:59:59.999Z",
        "2024-02-29T23:59:59.001Z",
    ]:
        assert parse_command(json.dumps(update(at=date)).encode())["at"] == date


def test_invalid_existing_database_input_leaves_bytes_unchanged(database):
    assert run_cli(database, update())[0] == 0
    before = database.read_bytes()
    assert run_cli(database, update(completed="false")) == (1, {"error": "invalid_input"})
    assert database.read_bytes() == before


def test_unavailable_or_corrupt_storage_returns_only_public_error(tmp_path):
    path = tmp_path / "missing-parent" / "private-database-location.sqlite3"
    assert run_cli(path, {"op": "migrate"}) == (1, {"error": "storage_failure"})
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_text("test-only invalid database")
    assert run_cli(corrupt, {"op": "migrate"}) == (1, {"error": "storage_failure"})


def test_download_layout_resolves_migrations_without_source_checkout(tmp_path):
    package = tmp_path / "standalone"
    package.mkdir()
    (package / "migrations").mkdir()
    for name in ("app.py", "repository.py"):
        shutil.copy2(Path(__file__).with_name(name), package / name)
    for version in (1, 2):
        source = migration_path(version)
        shutil.copy2(source, package / "migrations" / source.name)
    path = tmp_path / "standalone.sqlite3"
    assert run_cli(path, {"op": "migrate"}, entry=package / "app.py") == (0, {"schema_version": 2})
    assert run_cli(path, update(), entry=package / "app.py")[1]["changed"] is True
    assert len(run_cli(path, listing(), entry=package / "app.py")[1]["items"]) == 1


def test_audit_owner_filter_uses_progress_id_after_multiple_state_changes(database):
    assert run_cli(database, update())[0] == 0
    assert run_cli(database, update(completed=False, at=LATER))[0] == 0
    assert run_cli(database, update(owner_id="bob"))[0] == 0
    assert run_cli(database, update(owner_id="bob", completed=False, at=LATER))[0] == 0
    alice = run_cli(database, listing("list_audit", limit=50))[1]
    bob = run_cli(database, listing("list_audit", owner_id="bob", limit=50))[1]
    assert [(item["id"], item["progress_id"]) for item in alice["items"]] == [(1, 1), (2, 1)]
    assert [(item["id"], item["progress_id"]) for item in bob["items"]] == [(3, 2), (4, 2)]
    assert alice["next_cursor"] is None
    assert bob["next_cursor"] is None


@pytest.mark.parametrize("extra_argument", [False, True])
def test_cli_requires_exactly_one_database_argument(tmp_path, extra_argument):
    path = tmp_path / "must-not-exist.sqlite3"
    arguments = [str(path), "unexpected-argument"] if extra_argument else []
    process = subprocess.run(  # noqa: S603 - fixed test-owned CLI and fixture arguments.
        [sys.executable, str(Path(__file__).with_name("app.py")), *arguments],
        input=b'{"op":"migrate"}',
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert process.returncode == 1
    assert json.loads(process.stdout) == {"error": "invalid_input"}
    assert process.stderr == b""
    assert not path.exists()
