"""Real SQLite transactions; no open transaction spans HTTP body IO or review."""

import sqlite3
import sys
import time
from pathlib import Path

from auth import clock_value
from errors import LabError
from resources import resource_path


def no_fault(_stage, _connection):
    pass


class Repository:
    def __init__(self, path, *, clock=time.time, fault_hook=no_fault, timeout=0.5):
        self.path = str(Path(path))
        self.clock = clock
        self.fault_hook = fault_hook
        self.timeout = timeout

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self, documents):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchone():
                    raise ValueError("unversioned database")
                pending = ""
                for line in (
                    resource_path("migrations/001.sql").read_text(encoding="utf-8").splitlines(True)
                ):
                    pending += line
                    if sqlite3.complete_statement(pending):
                        connection.execute(pending)
                        pending = ""
                if pending.strip():
                    raise ValueError("incomplete migration")
                for document in documents:
                    connection.execute(
                        "INSERT INTO documents(owner_id,id,title,content,version) VALUES(?,?,?,?,?)",
                        tuple(
                            document[key]
                            for key in ("owner_id", "id", "title", "content", "version")
                        ),
                    )
                connection.execute("PRAGMA user_version = 1")
            elif version != 1:
                raise ValueError("unsupported schema")
            else:
                for name in ("documents", "operations", "publications"):
                    # Names are a fixed maintainer whitelist, never request data.
                    connection.execute("SELECT 1 FROM " + name + " LIMIT 1")  # noqa: S608 - fixed table names
            connection.commit()
        finally:
            connection.close()

    def commit(self, connection):
        connection.commit()

    def transaction(self, work, final_identity):
        connection = None
        committing = False
        committed = False
        try:
            connection = self.connect()
            connection.execute("BEGIN IMMEDIATE")
            principal = final_identity()
            now = clock_value(self.clock)
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise LabError(503, "repository_unavailable")
            rejection = None
            try:
                result = work(connection, principal, now)
            except LabError as error:
                if not error.persist:
                    raise
                # Expiry is terminal even when this action returns an expiry rejection.
                rejection = error
                result = None
            self.fault_hook("before_commit", connection)
            committing = True
            try:
                self.commit(connection)
            except Exception:
                raise LabError(503, "result_unconfirmed") from None
            committed = True
            if rejection is not None:
                raise rejection
            return result
        except LabError:
            raise
        except Exception:
            raise LabError(
                503, "result_unconfirmed" if committing else "repository_unavailable"
            ) from None
        finally:
            if connection is not None:
                cleanup_failed = False
                try:
                    if not committed and not committing:
                        connection.rollback()
                except Exception:
                    cleanup_failed = True
                try:
                    connection.close()
                except Exception:
                    cleanup_failed = True
                # Cleanup must not replace an unknown commit or a fixed rejection.
                # A new cleanup failure after success still fails conservatively.
                if cleanup_failed and sys.exception() is None:
                    raise LabError(
                        503, "result_unconfirmed" if committing else "repository_unavailable"
                    ) from None
