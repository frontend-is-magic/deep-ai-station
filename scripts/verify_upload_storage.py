"""Verify fixed upload ZIPs with real SQLite, independent processes and original bytes."""

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path

from build_course_labs import DESTINATION, ROOT
from verify_course_labs import command, ensure_listener_closed

DB = Path(".data/uploads.sqlite3")
INIT = {"schema_version": 1, "storage_contract": "text-upload-sqlite-v1"}
CLI_TIMEOUT = 15
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def entry(language, folder):
    if language == "python":
        return [str(folder / ".venv/bin/python"), str(folder / "app.py")]
    if language == "typescript":
        return [shutil.which("node"), str(folder / "dist/server.js")]
    return [str(folder / "lab-server")]


def check_diagnostics(errors):
    # This fixed runtime warning is distinct from application/SQL diagnostics.
    allowed_warning = (
        rb"\(node:[0-9]+\) ExperimentalWarning: SQLite is an experimental feature and might change at any time\n"
        rb"(?:\(Use `node --trace-warnings \.\.\.` to show where the warning was created\)\n)?"
    )
    if len(errors) > 4096 or (errors.strip() and re.fullmatch(allowed_warning, errors) is None):
        raise RuntimeError("Storage process emitted unexpected diagnostics")


def stop_direct_process(process):
    # entry() launches the fixed runtime itself: no uv/pnpm, worker, reload or helper tree.
    # Hold the owned Popen until wait; do not probe a recycled process-group ID after reap.
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    else:
        process.wait(timeout=5)


def strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def cli(args, cwd, env, expected, *, code=0):
    """Fixed direct entrypoints only; bounded output and actual process exit are required."""
    process = subprocess.Popen(  # noqa: S603 - fixed repository CLI, no external commands
        args,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        output, errors = process.communicate(timeout=CLI_TIMEOUT)
        if process.returncode != code or len(output) > 4096 or len(errors) > 4096:
            raise RuntimeError("Storage CLI exit or output budget mismatch")
        check_diagnostics(errors)
        if json.dumps(
            json.loads(output, object_pairs_hook=strict_pairs), sort_keys=True
        ) != json.dumps(expected, sort_keys=True):
            raise RuntimeError("Storage CLI did not return the fixed result")
    finally:
        if process.poll() is None:
            stop_direct_process(process)
        process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
    return process.pid


def exchange(port, method, path, *, owner="alice", body=None, filename="notes.txt"):
    headers = {"Authorization": f"Bearer lab-{owner}-session", "Connection": "close"}
    if body is not None:
        headers.update({"Content-Type": "text/plain", "X-Filename": filename})
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read(65537)
        if len(payload) > 65536:
            raise RuntimeError("Storage HTTP response exceeded its budget")
        for name, value in HEADERS.items():
            if response.getheader(name) != value:
                raise RuntimeError("Private storage response headers changed")
        if response.getheader("Set-Cookie") or response.getheader("Access-Control-Allow-Origin"):
            raise RuntimeError("Private storage response added credentials or CORS")
        if path.endswith("/content") and response.status == 200:
            if response.getheader("Content-Type") != "application/octet-stream":
                raise RuntimeError("Stored content is not an attachment")
            value = payload
        else:
            if (response.getheader("Content-Type") or "").split(";")[0] != "application/json":
                raise RuntimeError("Storage response must be JSON")
            value = json.loads(payload)
        return response.status, value
    finally:
        connection.close()


def require_result(actual, status, value):
    if actual[0] != status or json.dumps(actual[1], sort_keys=True) != json.dumps(
        value, sort_keys=True
    ):
        raise RuntimeError("Storage HTTP result differs from independent expectation")


@contextmanager
def server(args, cwd, env, counters):
    port = free_port()
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(  # noqa: S603 - trusted repository server, fixed loopback
            [*args, "serve", "--storage", "sqlite"],
            cwd=cwd,
            env={**env, "PORT": str(port)},
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        counters["server_processes"] += 1
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("SQLite server exited before becoming healthy")
                try:
                    require_result(
                        exchange(port, "GET", "/health"),
                        200,
                        {"status": "ok", "lab": "text-upload-v1"},
                    )
                    break
                except (ConnectionError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("SQLite server did not become healthy")
            yield port
        finally:
            stop_direct_process(process)
            ensure_listener_closed(port)
            counters["reaped_servers"] += 1
            log.seek(0)
            check_diagnostics(log.read(4097))


def workspace(parent, name):
    folder = parent / name
    folder.mkdir()
    # Go's existing trusted resource loader supports the ZIP root working directory.
    for asset in ("fixtures.json", "schema.sql"):
        shutil.copyfile(parent / asset, folder / asset)
    return folder


def expected_uploads(cases):
    result = []
    for case in cases:
        if case["status"] != 201:
            continue
        headers = {key.lower(): value for key, value in case.get("headers", {}).items()}
        owner = headers["authorization"].split("lab-", 1)[1].split("-session")[0]
        if "raw" in case:
            content = case["raw"].encode("utf-8")
        elif "body_base64" in case:
            content = base64.b64decode(case["body_base64"], validate=True)
        else:
            repeat = case["repeat_body"]
            content = (repeat["character"] * repeat["count"]).encode("utf-8")
        metadata = case["expected"]
        if metadata["sha256"] != hashlib.sha256(content).hexdigest():
            raise RuntimeError("Independent shared fixture digest mismatch")
        result.append((owner, metadata, content))
    if len(result) != 6:
        raise RuntimeError("Shared upload fixture baseline changed")
    return result


def sql_snapshot(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as database:
        if database.execute("PRAGMA application_id").fetchone() != (1146442545,):
            raise RuntimeError("Wrong storage application identity")
        if database.execute("PRAGMA user_version").fetchone() != (1,):
            raise RuntimeError("Wrong storage version")
        rows = database.execute(
            "SELECT id,owner_id,filename,media_type,size_bytes,sha256,content FROM documents ORDER BY id"
        ).fetchall()
        next_id = database.execute("SELECT next_id FROM storage_meta WHERE singleton=1").fetchone()
    if next_id != (len(rows) + 1,) or [row[0] for row in rows] != list(range(1, len(rows) + 1)):
        raise RuntimeError("Persistent IDs were consumed or changed")
    for row in rows:
        if (
            type(row[6]) is not bytes
            or len(row[6]) != row[4]
            or hashlib.sha256(row[6]).hexdigest() != row[5]
        ):
            raise RuntimeError("Independent SQL did not find intact original BLOB bytes")
    return rows


def sql_expect(path, expected):
    rows = sql_snapshot(path)
    wanted = [
        (
            int(m["id"][4:]),
            owner,
            m["filename"],
            m["media_type"],
            m["size_bytes"],
            m["sha256"],
            content,
        )
        for owner, m, content in expected
    ]
    if rows != wanted:
        raise RuntimeError("Committed SQL rows differ from shared HTTP fixture")


def assert_preserved(port, expected):
    count = 0
    for owner in ("alice", "bob"):
        require_result(
            exchange(port, "GET", "/documents", owner=owner),
            200,
            {"documents": [m for o, m, _ in expected if o == owner]},
        )
        require_result(
            exchange(port, "POST", "/documents", owner=owner, body=b"full"),
            409,
            {"error": "quota_exceeded"},
        )
        count += 2
    for owner, metadata, content in expected:
        path = "/documents/" + metadata["id"]
        require_result(exchange(port, "GET", path, owner=owner), 200, metadata)
        status, downloaded = exchange(port, "GET", path + "/content", owner=owner)
        if status != 200 or downloaded != content:
            raise RuntimeError("Restart changed original attachment bytes")
        other = "bob" if owner == "alice" else "alice"
        for suffix in ("", "/content"):
            require_result(
                exchange(port, "GET", path + suffix, owner=other),
                404,
                {"error": "document_not_found"},
            )
        count += 4
    return count


def upload_metadata(number, content):
    return {
        "id": f"doc-{number:06d}",
        "filename": "notes.txt",
        "media_type": "text/plain",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def check_quota_race(path, before, results, content, *, held_reader=False):
    """Judge responses against independent committed rows, never an assumed winner."""
    after = sql_snapshot(path)
    added = len(after) - len(before)
    confirmed = sum(status == 201 for status, _ in results)
    if (
        len(results) != 2
        or added not in (0, 1)
        or confirmed > added
        or after[: len(before)] != before
        or len(after) > 3
        or sum(row[4] for row in after) > 8192
    ):
        raise RuntimeError("Race overspent persistent quota or changed existing rows")
    metadata = upload_metadata(len(before) + 1, content)
    if added:
        wanted = (
            len(before) + 1,
            "alice",
            "notes.txt",
            "text/plain",
            len(content),
            metadata["sha256"],
            content,
        )
        if after[-1] != wanted:
            raise RuntimeError("Race committed an unexpected document")
    unconfirmed = False
    for status, value in results:
        if status == 201:
            require_result((status, value), 201, metadata)
        elif status == 409:
            require_result((status, value), 409, {"error": "quota_exceeded"})
            if added != 1:
                raise RuntimeError("Quota refusal without a committed last document")
        elif status == 503:
            if value not in ({"error": "repository_unavailable"}, {"error": "result_unconfirmed"}):
                raise RuntimeError("Unexpected storage failure response")
            unconfirmed |= value == {"error": "result_unconfirmed"}
        else:
            raise RuntimeError("Unexpected quota race response")
    if added and not confirmed and not unconfirmed:
        raise RuntimeError("Committed row has neither confirmed nor unknown response")
    if held_reader and (added or confirmed or not unconfirmed or any(s != 503 for s, _ in results)):
        raise RuntimeError("Held shared reader did not block both commits")
    return added


def verify_quota_race(args, work, env, counters, quota, *, held_reader=False):
    with (
        server(args, work, env, counters) as first,
        server(args, work, env, counters) as second,
    ):
        seeds = [b"seed", b"seed"] if quota == "count" else [b"a" * 4096]
        for number, seed in enumerate(seeds, start=1):
            require_result(
                exchange(first, "POST", "/documents", body=seed),
                201,
                upload_metadata(number, seed),
            )
            counters["http_cases"] += 1
        before = sql_snapshot(work / DB)
        gate = threading.Barrier(2)

        def compete(port):
            gate.wait(timeout=5)
            return exchange(port, "POST", "/documents", body=content)

        content = b"last" if quota == "count" else b"b" * 4096
        reader = (
            sqlite3.connect(work / DB, timeout=0, isolation_level=None) if held_reader else None
        )
        try:
            if reader is not None:
                reader.execute("BEGIN")
                reader.execute("SELECT * FROM documents").fetchall()
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(compete, port) for port in (first, second)]
                results = [future.result(timeout=10) for future in futures]
        finally:
            if reader is not None:
                try:
                    reader.rollback()
                finally:
                    reader.close()
        counters["http_cases"] += 2
        added = check_quota_race(work / DB, before, results, content, held_reader=held_reader)
        if not added:
            # The SQL read above proved zero commits. This is a new explicit request,
            # not an automatic replay based on a 503 response's guessed meaning.
            fresh = exchange(second, "POST", "/documents", body=content)
            require_result(fresh, 201, upload_metadata(len(before) + 1, content))
            sql_expect(
                work / DB,
                [
                    ("alice", upload_metadata(number, body), body)
                    for number, body in enumerate([*seeds, content], start=1)
                ],
            )
            counters["http_cases"] += 1
        filled = sql_snapshot(work / DB)
        require_result(
            exchange(second, "POST", "/documents", body=content),
            409,
            {"error": "quota_exceeded"},
        )
        counters["http_cases"] += 1
        if sql_snapshot(work / DB) != filled:
            raise RuntimeError("Full-quota refusal changed committed storage")


def verify_storage(language, folder, env):
    # Imported lazily because the existing memory verifier calls this function.
    from verify_upload_labs import request

    args = entry(language, folder)
    counters = {"server_processes": 0, "reaped_servers": 0, "http_cases": 0, "cli_processes": 0}

    def invoke(extra, work, expected, code=0):
        cli([*args, *extra], work, env, expected, code=code)
        counters["cli_processes"] += 1

    cases = json.loads((folder / "contract-cases.json").read_text())
    expected = expected_uploads(cases)
    invoke(["serve", "--storage", "sqlite"], folder, {"error": "database_missing"}, 1)
    for argv in (["serve"], ["init", "extra"], ["serve", "--storage=sqlite"], ["init", "--help"]):
        invoke(argv, folder, {"error": "invalid_input"}, 1)
    if (folder / ".data").exists():
        raise RuntimeError("Refused CLI unexpectedly created persistent state")
    invoke(["init"], folder, INIT)
    if sql_snapshot(folder / DB):
        raise RuntimeError("Initialization must create an empty database")
    with server(args, folder, env, counters) as port:
        for case in cases:
            request(f"http://127.0.0.1:{port}", case)
            counters["http_cases"] += 1
    sql_expect(folder / DB, expected)
    before = (folder / DB).read_bytes()
    invoke(["init"], folder, {"error": "database_exists"}, 1)
    if (folder / DB).read_bytes() != before:
        raise RuntimeError("Refused initialization changed the existing database")
    with server(args, folder, env, counters) as port:
        counters["http_cases"] += assert_preserved(port, expected)
    sql_expect(folder / DB, expected)

    for quota, held_reader in (("count", False), ("bytes", False), ("count", True)):
        work = workspace(folder, "race-" + quota + ("-held-reader" if held_reader else ""))
        invoke(["init"], work, INIT)
        verify_quota_race(args, work, env, counters, quota, held_reader=held_reader)

    work = workspace(folder, "busy")
    invoke(["init"], work, INIT)
    with server(args, work, env, counters) as port:
        with closing(sqlite3.connect(work / DB, timeout=0, isolation_level=None)) as locked:
            locked.execute("BEGIN IMMEDIATE")
            try:
                require_result(
                    exchange(port, "POST", "/documents", body=b"blocked"),
                    503,
                    {"error": "repository_unavailable"},
                )
            finally:
                locked.execute("ROLLBACK")
        if sql_snapshot(work / DB):
            raise RuntimeError("Busy refusal left persistent rows")
        status, metadata = exchange(port, "POST", "/documents", body=b"after-lock")
        if status != 201 or metadata["id"] != "doc-000001":
            raise RuntimeError("Released write lock did not allow a fresh explicit request")
        counters["http_cases"] += 2
    if len(sql_snapshot(work / DB)) != 1:
        raise RuntimeError("Fresh request after lock was not committed once")

    corruptions = [
        ("version", "PRAGMA user_version=99", "unsupported_schema"),
        ("identity", "PRAGMA application_id=0", "unsupported_schema"),
        (
            "trigger",
            "CREATE TRIGGER unwanted AFTER INSERT ON documents BEGIN SELECT 1; END",
            "repository_unavailable",
        ),
        (
            "digest",
            "UPDATE documents SET sha256='" + "0" * 64 + "' WHERE id=1",
            "repository_unavailable",
        ),
        ("length", "UPDATE documents SET size_bytes=18 WHERE id=1", "repository_unavailable"),
        (
            "text",
            "UPDATE documents SET filename=CAST(X'FF' AS TEXT) WHERE id=1",
            "repository_unavailable",
        ),
        ("next-id", "UPDATE storage_meta SET next_id=9", "repository_unavailable"),
    ]
    for label, statement, code in corruptions:
        work = workspace(folder, "corrupt-" + label)
        (work / DB).parent.mkdir()
        shutil.copyfile(folder / DB, work / DB)
        with closing(sqlite3.connect(work / DB)) as database:
            database.execute("PRAGMA ignore_check_constraints=ON")
            database.execute(statement)
            database.commit()
        before = (work / DB).read_bytes()
        invoke(["serve", "--storage", "sqlite"], work, {"error": code}, 1)
        if (work / DB).read_bytes() != before:
            raise RuntimeError("Refused malformed database was rewritten")
    work = workspace(folder, "runtime-corrupt")
    (work / DB).parent.mkdir()
    shutil.copyfile(folder / DB, work / DB)
    with server(args, work, env, counters) as port:
        with closing(sqlite3.connect(work / DB)) as database:
            database.execute("UPDATE documents SET sha256=? WHERE id=1", ("0" * 64,))
            database.commit()
        before = (work / DB).read_bytes()
        require_result(
            exchange(port, "GET", "/documents"), 503, {"error": "repository_unavailable"}
        )
        require_result(
            exchange(port, "GET", "/documents/doc-000002/content", owner="bob"),
            503,
            {"error": "repository_unavailable"},
        )
        counters["http_cases"] += 2
    if (work / DB).read_bytes() != before:
        raise RuntimeError("Runtime malformed database was repaired or changed")
    if counters["server_processes"] != counters["reaped_servers"]:
        raise RuntimeError("Storage verification did not release every server")
    print(
        json.dumps(
            {
                "language": language,
                "storage_contract": "text-upload-sqlite-v1",
                "sqlite": "verified",
                **counters,
                "corruption_cases": len(corruptions) + 1,
                "model_calls": 0,
            }
        )
    )


def prepare(language, folder, env):
    with zipfile.ZipFile(DESTINATION / f"text-upload-{language}.zip") as archive:
        archive.extractall(folder)
    if language == "python":
        command([shutil.which("uv"), "sync", "--locked"], folder, env)
    elif language == "typescript":
        command(["pnpm", "install", "--frozen-lockfile"], folder, env)
        command(["pnpm", "build"], folder, env)
    else:
        command(["go", "build", "-mod=readonly", "-o", "lab-server", "."], folder, env)


def cross_language():
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "USER",
        "LOGNAME",
        "CI",
        "UV_CACHE_DIR",
        "GOCACHE",
        "GOMODCACHE",
        "GOPATH",
        "GOTOOLCHAIN",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "PNPM_HOME",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    counters = {"server_processes": 0, "reaped_servers": 0}
    with tempfile.TemporaryDirectory(prefix="deep-ai-upload-cross-") as temporary:
        root = Path(temporary).resolve()
        if root.is_relative_to(ROOT):
            raise RuntimeError("Cross-language verification must be outside the repository")
        folders = {}
        for language in ("typescript", "go", "python"):
            folder = root / language
            folder.mkdir()
            prepare(language, folder, env)
            folders[language] = folder
        work = folders["typescript"]
        cli([*entry("typescript", work), "init"], work, env, INIT)
        records = []
        for index, language in enumerate(("typescript", "go", "python", "typescript")):
            with server(entry(language, folders[language]), work, env, counters) as port:
                for metadata, content in records:
                    status, value = exchange(
                        port, "GET", "/documents/" + metadata["id"] + "/content"
                    )
                    if status != 200 or value != content:
                        raise RuntimeError(
                            "Another language could not read the same original database"
                        )
                if index < 3:
                    content = f"{language}\r\n中文 🌱 e\u0301".encode()
                    status, metadata = exchange(port, "POST", "/documents", body=content)
                    wanted = {
                        "id": f"doc-{index + 1:06d}",
                        "filename": "notes.txt",
                        "media_type": "text/plain",
                        "size_bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                    require_result((status, metadata), 201, wanted)
                    records.append((wanted, content))
                else:
                    require_result(
                        exchange(port, "POST", "/documents", body=b"fourth"),
                        409,
                        {"error": "quota_exceeded"},
                    )
            sql_expect(work / DB, [("alice", metadata, content) for metadata, content in records])
        if counters["server_processes"] != counters["reaped_servers"]:
            raise RuntimeError("Cross-language verification did not release every server")
    print(
        json.dumps(
            {
                "storage_contract": "text-upload-sqlite-v1",
                "same_database_language_order": ["typescript", "go", "python", "typescript"],
                "cross_language": "verified",
                **counters,
                "model_calls": 0,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    cross_language()


if __name__ == "__main__":
    main()
