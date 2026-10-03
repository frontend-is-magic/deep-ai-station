"""固定教学仓储：显式迁移、参数化查询和原子的进度／审计写入。"""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 2


class StorageError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def migration_path(version: int) -> Path:
    filename = {1: "001.sql", 2: "002.sql"}[version]
    local = Path(__file__).parent / "migrations" / filename
    return (
        local
        if local.is_file()
        else Path(__file__).parent.parent / "shared" / "migrations" / filename
    )


def execute_statements(connection: sqlite3.Connection, script: str) -> None:
    """逐条执行可信迁移，避免 executescript 在 BEGIN 之后隐式提交。"""
    pending = ""
    for character in script:
        pending += character
        if character == ";" and sqlite3.complete_statement(pending):
            connection.execute(pending)
            pending = ""
    if pending.strip():
        connection.execute(pending)


def sqlite_failure(error: sqlite3.Error) -> StorageError:
    code = getattr(error, "sqlite_errorcode", 0) & 0xFF
    name = (
        "database_busy"
        if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
        else "storage_failure"
    )
    return StorageError(name)


def progress_item(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "owner_id": row["owner_id"],
        "lesson_id": row["lesson_id"],
        "completed": bool(row["completed"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class Repository:
    def __init__(
        self,
        path: str | Path,
        *,
        _before_audit: Callable[[sqlite3.Connection], None] | None = None,
    ):
        # The private hook is only for trusted tests; stdin and argv cannot select it.
        self._before_audit = _before_audit
        self.connection = sqlite3.connect(path, timeout=1, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA busy_timeout = 1000")
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
            self.connection.commit()
        except BaseException:
            if self.connection.in_transaction:
                self.connection.rollback()
            raise

    def schema_version(self) -> int:
        return self.connection.execute("PRAGMA user_version").fetchone()[0]

    def require_schema(self) -> None:
        if self.schema_version() != SCHEMA_VERSION:
            raise StorageError("unsupported_schema")

    def migrate(self) -> dict:
        with self.transaction():
            version = self.schema_version()
            if version < 0 or version > SCHEMA_VERSION:
                raise StorageError("unsupported_schema")
            for target in range(version + 1, SCHEMA_VERSION + 1):
                execute_statements(
                    self.connection, migration_path(target).read_text(encoding="utf-8")
                )
                self.connection.execute(
                    {1: "PRAGMA user_version = 1", 2: "PRAGMA user_version = 2"}[target]
                )
        return {"schema_version": SCHEMA_VERSION}

    def set_progress(self, command: dict) -> dict:
        with self.transaction():
            self.require_schema()
            previous = self.connection.execute(
                "SELECT * FROM progress WHERE owner_id = ? AND lesson_id = ?",
                (command["owner_id"], command["lesson_id"]),
            ).fetchone()
            if previous is not None and bool(previous["completed"]) == command["completed"]:
                return {"item": progress_item(previous), "changed": False}
            if previous is None:
                result = self.connection.execute(
                    "INSERT INTO progress (owner_id, lesson_id, completed, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        command["owner_id"],
                        command["lesson_id"],
                        command["completed"],
                        command["at"],
                        command["at"],
                    ),
                )
                progress_id = result.lastrowid
            else:
                progress_id = previous["id"]
                self.connection.execute(
                    "UPDATE progress SET completed = ?, updated_at = ? WHERE id = ?",
                    (command["completed"], command["at"], progress_id),
                )
            if self._before_audit is not None:
                self._before_audit(self.connection)
            self.connection.execute(
                "INSERT INTO audit (progress_id, completed, at) VALUES (?, ?, ?)",
                (progress_id, command["completed"], command["at"]),
            )
            row = self.connection.execute(
                "SELECT * FROM progress WHERE id = ?", (progress_id,)
            ).fetchone()
            return {"item": progress_item(row), "changed": True}

    def list_progress(self, command: dict) -> dict:
        self.require_schema()
        limit = command["limit"]
        rows = self.connection.execute(
            "SELECT * FROM progress WHERE owner_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (command["owner_id"], command["cursor"], limit + 1),
        ).fetchall()
        items = [progress_item(row) for row in rows[:limit]]
        return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}

    def list_audit(self, command: dict) -> dict:
        self.require_schema()
        limit = command["limit"]
        rows = self.connection.execute(
            "SELECT a.id, a.progress_id, a.completed, a.at "
            "FROM audit AS a JOIN progress AS p ON p.id = a.progress_id "
            "WHERE p.owner_id = ? AND a.id > ? ORDER BY a.id ASC LIMIT ?",
            (command["owner_id"], command["cursor"], limit + 1),
        ).fetchall()
        items = [
            {
                "id": row["id"],
                "progress_id": row["progress_id"],
                "completed": bool(row["completed"]),
                "at": row["at"],
            }
            for row in rows[:limit]
        ]
        return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}


def execute(path: str | Path, command: dict) -> dict:
    repository = None
    try:
        repository = Repository(path)
        if command["op"] == "migrate":
            return repository.migrate()
        operation = {
            "set_progress": repository.set_progress,
            "list_progress": repository.list_progress,
            "list_audit": repository.list_audit,
        }[command["op"]]
        return operation(command)
    except StorageError:
        raise
    except sqlite3.Error as error:
        raise sqlite_failure(error) from None
    except (OSError, UnicodeError, ValueError, KeyError):
        raise StorageError("storage_failure") from None
    finally:
        if repository is not None:
            repository.close()
