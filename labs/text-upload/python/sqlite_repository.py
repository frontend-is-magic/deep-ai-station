"""固定教学库的短事务仓储；不接受用户路径，不修复未知 schema。"""

import hashlib
import os
import re
import sqlite3
import stat
from collections.abc import Callable
from pathlib import Path

from repository import MAX_DOCUMENTS, MAX_OWNER_BYTES, QuotaExceeded, StoredDocument
from resources import fixture_path
from upload_policy import FILENAME, ascii_lower, validate_text

APPLICATION_ID = 1146442545
CONTRACT = "text-upload-sqlite-v1"


class StorageError(Exception):
    def __init__(self, code: str = "repository_unavailable"):
        self.code = code
        super().__init__(code)


def fixed_path(directory: Path | None = None) -> Path:
    # directory 仅供可信原生测试；CLI 没有覆盖参数。
    return (Path.cwd() if directory is None else directory).absolute() / ".data" / "uploads.sqlite3"


def schema_statements() -> list[str]:
    text = fixture_path("schema.sql").read_text(encoding="utf-8")
    statements, pending = [], ""
    for line in text.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statements.append(pending.strip().rstrip(";").strip())
            pending = ""
    if pending.strip() or len(statements) != 6:
        raise StorageError()
    return statements


def check_path(path: Path) -> None:
    try:
        parent = path.parent.lstat()
    except FileNotFoundError:
        raise StorageError("database_missing") from None
    if not stat.S_ISDIR(parent.st_mode):
        raise StorageError()
    try:
        target = path.lstat()
    except FileNotFoundError:
        raise StorageError("database_missing") from None
    if not stat.S_ISREG(target.st_mode):
        raise StorageError()


def strict_text(value: bytes) -> str:
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeError:
        raise StorageError() from None


def connect_existing(path: Path, *, write: bool = False) -> sqlite3.Connection:
    check_path(path)
    connection = sqlite3.connect(
        path.as_uri() + "?mode=rw", uri=True, timeout=0, isolation_level=None
    )
    try:
        connection.text_factory = strict_text
        connection.execute("PRAGMA busy_timeout=0")
        if write:
            connection.execute("PRAGMA synchronous=FULL")
        return connection
    except BaseException:
        connection.close()
        raise


def discard(connection) -> bool:
    """尽力回滚、始终关闭；返回清理是否成功，不打印驱动诊断。"""
    clean = True
    try:
        if connection.in_transaction:
            connection.rollback()
    except Exception:
        clean = False
    try:
        connection.close()
    except Exception:
        clean = False
    return clean


def initialize(directory: Path | None = None) -> None:
    path = fixed_path(directory)
    connection = None
    commit_started = False
    try:
        try:
            path.parent.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if not stat.S_ISDIR(path.parent.lstat().st_mode):
            raise StorageError()
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise StorageError("database_exists") from None
        os.close(descriptor)
        connection = connect_existing(path, write=True)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("BEGIN IMMEDIATE")
        for statement in schema_statements():
            connection.execute(statement)
        commit_started = True
        connection.commit()
    except BaseException as error:
        if connection is not None:
            discard(connection)
        if not isinstance(error, Exception):
            raise
        if commit_started:
            raise StorageError("result_unconfirmed") from None
        if isinstance(error, StorageError):
            raise
        raise StorageError() from None
    if connection is not None and not discard(connection):
        raise StorageError("result_unconfirmed")


def check_document(owner, filename, media_type, content, digest) -> None:
    if (
        type(owner) is not str
        or owner not in {"alice", "bob"}
        or type(filename) is not str
        or FILENAME.fullmatch(filename) is None
        or type(media_type) is not str
        or media_type not in {"text/plain", "text/markdown"}
        or ascii_lower(filename.rsplit(".", 1)[1])
        != ("txt" if media_type == "text/plain" else "md")
        or type(content) is not bytes
        or not 1 <= len(content) <= 4096
        or type(digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or hashlib.sha256(content).hexdigest() != digest
    ):
        raise StorageError()
    validate_text(content)


def validate_snapshot(connection, statements: list[str]) -> None:
    if (
        connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
        or connection.execute("PRAGMA user_version").fetchone()[0] != 1
    ):
        raise StorageError("unsupported_schema")
    if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
        raise StorageError()
    # 先有界读取 schema 大小，拒绝巨型 DDL 后才读取原文。
    objects = connection.execute(
        "SELECT type, name, tbl_name, length(CAST(sql AS BLOB)) FROM sqlite_schema LIMIT 4"
    ).fetchall()
    expected = {
        ("table", "storage_meta", "storage_meta"): statements[0],
        ("table", "documents", "documents"): statements[1],
        ("index", "documents_owner_id_id", "documents"): statements[2],
    }
    if len(objects) != 3 or any(
        tuple(row[:3]) not in expected
        or type(row[3]) is not int
        or row[3] > len(expected[tuple(row[:3])].encode()) + 16
        for row in objects
    ):
        raise StorageError()
    for kind, name, table, ddl in connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema"
    ):
        if type(ddl) is not str or ddl.strip().rstrip(";").strip() != expected[(kind, name, table)]:
            raise StorageError()
    meta_shape = connection.execute(
        "SELECT typeof(singleton), typeof(storage_contract), length(CAST(storage_contract AS BLOB)), "
        "typeof(next_id) FROM storage_meta LIMIT 2"
    ).fetchall()
    if (
        len(meta_shape) != 1
        or meta_shape[0][:2] != ("integer", "text")
        or meta_shape[0][3] != "integer"
    ):
        raise StorageError()
    if not 1 <= meta_shape[0][2] <= 100:
        raise StorageError("unsupported_schema")
    singleton, marker, next_id = connection.execute(
        "SELECT singleton, storage_contract, next_id FROM storage_meta"
    ).fetchone()
    if marker != CONTRACT:
        raise StorageError("unsupported_schema")
    if (
        type(singleton) is not int
        or singleton != 1
        or type(next_id) is not int
        or not 1 <= next_id <= 1000000
    ):
        raise StorageError()
    shapes = connection.execute(
        "SELECT id, typeof(id), typeof(owner_id), length(CAST(owner_id AS BLOB)), "
        "typeof(filename), length(CAST(filename AS BLOB)), typeof(media_type), "
        "length(CAST(media_type AS BLOB)), typeof(size_bytes), "
        "CASE WHEN typeof(size_bytes)='integer' THEN size_bytes ELSE NULL END, typeof(sha256), "
        "length(CAST(sha256 AS BLOB)), typeof(content), length(content) FROM documents ORDER BY id LIMIT 7"
    ).fetchall()
    if len(shapes) > 6 or next_id != len(shapes) + 1:
        raise StorageError()
    for index, row in enumerate(shapes, start=1):
        if (
            type(row[0]) is not int
            or row[0] != index
            or row[1] != "integer"
            or row[2] != "text"
            or not 1 <= row[3] <= 5
            or row[4] != "text"
            or not 5 <= row[5] <= 64
            or row[6] != "text"
            or not 1 <= row[7] <= 13
            or row[8] != "integer"
            or type(row[9]) is not int
            or not 1 <= row[9] <= 4096
            or row[10] != "text"
            or row[11] != 64
            or row[12] != "blob"
            or row[13] != row[9]
        ):
            raise StorageError()
    quotas = {"alice": [0, 0], "bob": [0, 0]}
    for owner, name, media, content, digest in connection.execute(
        "SELECT owner_id, filename, media_type, content, sha256 FROM documents ORDER BY id"
    ):
        check_document(owner, name, media, content, digest)
        quotas[owner][0] += 1
        quotas[owner][1] += len(content)
    if any(count > MAX_DOCUMENTS or size > MAX_OWNER_BYTES for count, size in quotas.values()):
        raise StorageError()


def stored(row) -> StoredDocument:
    number, owner, name, media, content, digest = row
    return StoredDocument(f"doc-{number:06d}", owner, name, media, bytes(content), digest)


class SQLiteRepository:
    def __init__(self, directory: Path | None = None):
        self.path = fixed_path(directory)
        self._statements = schema_statements()

    def _connect(self, *, write: bool):
        return connect_existing(self.path, write=write)

    def _before_commit(self, _connection) -> None:
        """仅受信原生测试注入；不从 CLI、环境或请求读取故障设置。"""

    def _after_commit(self, _connection) -> None:
        """提交完成、响应前的受信原生故障点。"""

    def _transaction(self, operation, *, write=False):
        connection = None
        commit_started = False
        try:
            connection = self._connect(write=write)
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            result = operation(connection)
            if write:
                self._before_commit(connection)
                commit_started = True
                connection.commit()
                self._after_commit(connection)
        except BaseException as error:
            if connection is not None:
                discard(connection)
            if not isinstance(error, Exception):
                raise
            if commit_started:
                raise StorageError("result_unconfirmed") from None
            if isinstance(error, (StorageError, QuotaExceeded)):
                raise
            raise StorageError() from None
        if not discard(connection):
            raise StorageError("result_unconfirmed" if commit_started else "repository_unavailable")
        return result

    def validate(self) -> None:
        self._transaction(lambda connection: validate_snapshot(connection, self._statements))

    def list_owned(self, owner: str) -> list[StoredDocument]:
        def read(connection):
            validate_snapshot(connection, self._statements)
            return [
                stored(row)
                for row in connection.execute(
                    "SELECT id, owner_id, filename, media_type, content, sha256 FROM documents WHERE owner_id=? ORDER BY id",
                    (owner,),
                )
            ]

        return self._transaction(read)

    def find_owned(self, owner: str, document_id: str) -> StoredDocument | None:
        def read(connection):
            validate_snapshot(connection, self._statements)
            number = int(document_id[4:]) if re.fullmatch(r"doc-[0-9]{6}", document_id) else 0
            row = connection.execute(
                "SELECT id, owner_id, filename, media_type, content, sha256 FROM documents WHERE owner_id=? AND id=?",
                (owner, number),
            ).fetchone()
            return stored(row) if row is not None else None

        return self._transaction(read)

    def commit(
        self,
        owner: str,
        filename: str,
        media_type: str,
        content: bytes,
        *,
        before_write: Callable[[], None] | None = None,
    ) -> StoredDocument:
        copied = bytes(content)
        digest = hashlib.sha256(copied).hexdigest()

        def write(connection):
            if before_write is not None:
                before_write()
            validate_snapshot(connection, self._statements)
            check_document(owner, filename, media_type, copied, digest)
            count, size = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM documents WHERE owner_id=?",
                (owner,),
            ).fetchone()
            if count >= MAX_DOCUMENTS or size + len(copied) > MAX_OWNER_BYTES:
                raise QuotaExceeded
            number = connection.execute(
                "SELECT next_id FROM storage_meta WHERE singleton=1"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO documents(id,owner_id,filename,media_type,size_bytes,sha256,content) VALUES(?,?,?,?,?,?,?)",
                (number, owner, filename, media_type, len(copied), digest, copied),
            )
            connection.execute("UPDATE storage_meta SET next_id=? WHERE singleton=1", (number + 1,))
            return StoredDocument(f"doc-{number:06d}", owner, filename, media_type, copied, digest)

        return self._transaction(write, write=True)
