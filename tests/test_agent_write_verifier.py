"""Real sockets/SQLite ensure the verifier cannot mislabel a disconnect as post-commit."""

import importlib
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Event

import pytest

ROOT = Path(__file__).resolve().parents[1]
OPERATION = {"operation_id": "aaaabbbb-cccc-4ddd-8eee-ffff00001111", "intent_hash": "0" * 64}


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("verify_agent_write_lab")


@pytest.fixture
def database(tmp_path):
    db = tmp_path / "observation.sqlite3"
    with closing(sqlite3.connect(db)) as connection:
        connection.executescript(
            """
            CREATE TABLE operations(owner_id TEXT, operation_id TEXT, status TEXT);
            CREATE TABLE publications(owner_id TEXT, operation_id TEXT);
            CREATE TABLE documents(owner_id TEXT, id TEXT, content TEXT, version INTEGER);
            INSERT INTO documents VALUES('alice','agent-summary','initial',1);
            """
        )
        connection.execute(
            "INSERT INTO operations VALUES('alice',?,'approved')", (OPERATION["operation_id"],)
        )
        connection.commit()
    return db


@contextmanager
def fixed_server(database, *, commit, respond):
    response_sent, disconnected = Event(), Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        port = listener.getsockname()[1]
        assert port not in {8000, 5173}

        def receive():
            connection, _address = listener.accept()
            with connection:
                connection.settimeout(5)
                data = b""
                while b"\r\n\r\n" not in data:
                    data += connection.recv(4096)
                headers, body = data.split(b"\r\n\r\n", 1)
                content_length = next(
                    int(line.split(b":", 1)[1])
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                while len(body) < content_length:
                    body += connection.recv(4096)
                if commit:
                    with closing(sqlite3.connect(database)) as db:
                        db.execute("UPDATE operations SET status='applied'")
                        db.execute(
                            "INSERT INTO publications VALUES('alice',?)",
                            (OPERATION["operation_id"],),
                        )
                        db.execute("UPDATE documents SET content='committed', version=2")
                        db.commit()
                if respond:
                    connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                    response_sent.set()
                try:
                    while connection.recv(4096):
                        pass
                except ConnectionResetError:
                    pass
                disconnected.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(receive)
            try:
                yield port, response_sent
            finally:
                future.result(timeout=6)
                assert disconnected.is_set(), "Verifier did not close its own HTTP socket"


def test_post_commit_disconnect_requires_committed_sqlite_state(verifier, database):
    with fixed_server(database, commit=True, respond=False) as (port, _sent):
        verifier.disconnect_after_commit(verifier.API(port), database, OPERATION)
    assert verifier.read_database(database, OPERATION["operation_id"]) == (
        1,
        "applied",
        ("committed", 2),
    )


def test_response_already_arrived_cannot_count_as_disconnect_recovery(
    verifier, database, monkeypatch
):
    with fixed_server(database, commit=True, respond=True) as (port, sent):
        read_database = verifier.read_database

        def after_response(*args):
            assert sent.wait(timeout=3)
            return read_database(*args)

        monkeypatch.setattr(verifier, "read_database", after_response)
        with pytest.raises(AssertionError, match="response arrived before"):
            verifier.disconnect_after_commit(verifier.API(port), database, OPERATION)


def test_never_committed_request_cannot_count_as_post_commit_disconnect(verifier, database):
    with fixed_server(database, commit=False, respond=False) as (port, _sent):
        with pytest.raises(AssertionError, match="Did not observe the committed publication"):
            verifier.disconnect_after_commit(verifier.API(port), database, OPERATION)
    assert verifier.read_database(database, OPERATION["operation_id"]) == (
        0,
        "approved",
        ("initial", 1),
    )
