"""Reject false success from a fixed CLI and release its actual child process."""

import importlib
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("verify_upload_storage")
    monkeypatch.setattr(module, "CLI_TIMEOUT", 0.3)
    return module


@pytest.mark.parametrize(
    "mode", ["wrong_exit", "duplicate", "boolean", "private_diagnostic", "stalled", "closed_pipes"]
)
def test_cli_output_does_not_replace_correct_exit_or_process_cleanup(verifier, tmp_path, mode):
    child = tmp_path / "fixed_child.py"
    child.write_text("""import os, signal, sys, time
from pathlib import Path
Path('child.pid').write_text(str(os.getpid()))
mode = sys.argv[1]
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode == 'duplicate':
    print('{"schema_version":2,"schema_version":1,"storage_contract":"text-upload-sqlite-v1"}', flush=True)
elif mode == 'boolean':
    print('{"schema_version":true,"storage_contract":"text-upload-sqlite-v1"}', flush=True)
else:
    print('{"schema_version":1,"storage_contract":"text-upload-sqlite-v1"}', flush=True)
if mode == 'private_diagnostic':
    print('private database diagnostic', file=sys.stderr, flush=True)
if mode == 'wrong_exit':
    raise SystemExit(1)
if mode == 'closed_pipes':
    os.close(1)
    os.close(2)
if mode in ('stalled', 'closed_pipes'):
    while True:
        time.sleep(0.1)
""")
    with pytest.raises((ValueError, RuntimeError, subprocess.TimeoutExpired)):
        verifier.cli(
            [sys.executable, "-s", "-E", "-B", str(child), mode],
            tmp_path,
            {"PATH": os.defpath},
            verifier.INIT,
        )
    pid = int((tmp_path / "child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.fixture
def race_database(verifier, tmp_path):
    path = tmp_path / verifier.DB
    path.parent.mkdir()
    shared = Path(__file__).resolve().parents[1] / "labs/text-upload/shared"
    with closing(sqlite3.connect(path)) as database:
        database.executescript((shared / "schema.sql").read_text())
    return path


def insert_document(verifier, path, content=b"seed"):
    with closing(sqlite3.connect(path)) as database:
        number = database.execute("SELECT next_id FROM storage_meta").fetchone()[0]
        metadata = verifier.upload_metadata(number, content)
        database.execute(
            "INSERT INTO documents VALUES (?, 'alice', 'notes.txt', 'text/plain', ?, ?, ?)",
            (number, len(content), metadata["sha256"], content),
        )
        database.execute("UPDATE storage_meta SET next_id=?", (number + 1,))
        database.commit()
    return metadata


@pytest.mark.parametrize(
    ("commit", "statuses", "valid"),
    [
        (False, ["unknown", "unavailable"], True),
        (False, ["unavailable", "unavailable"], True),
        (True, ["confirmed", "quota"], True),
        (True, ["confirmed", "unknown"], True),
        (True, ["unknown", "unavailable"], True),
        (True, ["confirmed", "confirmed"], False),
        (True, ["unavailable", "unavailable"], False),
        (False, ["quota", "unknown"], False),
        (False, ["confirmed", "unavailable"], False),
    ],
)
def test_race_responses_are_judged_by_real_committed_rows(
    verifier, race_database, commit, statuses, valid
):
    for _ in range(2):
        insert_document(verifier, race_database)
    before = verifier.sql_snapshot(race_database)
    content = b"last"
    if commit:
        insert_document(verifier, race_database, content)
    responses = {
        "confirmed": (201, verifier.upload_metadata(3, content)),
        "quota": (409, {"error": "quota_exceeded"}),
        "unavailable": (503, {"error": "repository_unavailable"}),
        "unknown": (503, {"error": "result_unconfirmed"}),
    }
    results = [responses[status] for status in statuses]
    if valid:
        assert verifier.check_quota_race(race_database, before, results, content) == int(commit)
    else:
        with pytest.raises(RuntimeError):
            verifier.check_quota_race(race_database, before, results, content)


@pytest.mark.parametrize("damage", ["old_row", "next_id", "owner", "metadata", "extra", "bytes"])
def test_race_rejects_damage_and_false_confirmation(verifier, race_database, damage):
    for _ in range(2):
        insert_document(verifier, race_database, b"x" * 4096 if damage == "bytes" else b"seed")
    before = verifier.sql_snapshot(race_database)
    content = b"last"
    metadata = insert_document(verifier, race_database, content)
    if damage == "extra":
        insert_document(verifier, race_database, content)
    elif damage == "metadata":
        metadata["filename"] = "wrong.txt"
    elif damage != "bytes":
        statement = {
            "old_row": "UPDATE documents SET filename='wrong.txt' WHERE id=1",
            "next_id": "UPDATE storage_meta SET next_id=99",
            "owner": "UPDATE documents SET owner_id='bob' WHERE id=3",
        }[damage]
        with closing(sqlite3.connect(race_database)) as database:
            database.execute(statement)
            database.commit()
    with pytest.raises(RuntimeError):
        verifier.check_quota_race(
            race_database,
            before,
            [(201, metadata), (503, {"error": "repository_unavailable"})],
            content,
        )


def test_real_shared_reader_blocks_both_commits_and_all_servers_exit(
    verifier, race_database, monkeypatch, tmp_path
):
    # Fixed repository entrypoint, real HTTP and SQLite locks; no mocked status/body.
    app = Path(__file__).resolve().parents[1] / "labs/text-upload/python/app.py"
    processes = []
    original_popen = subprocess.Popen

    def owned_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(verifier.subprocess, "Popen", owned_popen)
    counters = {"server_processes": 0, "reaped_servers": 0, "http_cases": 0}
    try:
        verifier.verify_quota_race(
            [sys.executable, "-s", "-B", str(app)],
            tmp_path,
            {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
            counters,
            "count",
            held_reader=True,
        )
        assert counters == {"server_processes": 2, "reaped_servers": 2, "http_cases": 6}
        assert len(verifier.sql_snapshot(race_database)) == 3
    finally:
        for process in processes:
            assert process.returncode is not None
            with pytest.raises(ProcessLookupError):
                os.kill(process.pid, 0)
