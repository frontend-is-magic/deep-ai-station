"""Short local SQLite transactions; committed checkpoints are never rewritten."""

import os
import sqlite3
import sys
import time
from contextlib import contextmanager, suppress
from pathlib import Path

from workflow import (
    CASES,
    CORPUS_REVISION,
    CORPUS_SHA256,
    NODES,
    OUTCOMES,
    PHASES,
    VERSION,
    LabError,
    canonical,
    checkpoint_hash,
    execute_node,
    load_fixtures,
    parse_json,
    valid_run_id,
    validate_saved_outputs,
)

DATABASE = Path(".data/checkpoints.sqlite3")
FAULTS = ("before-commit", "after-commit", "pause-before-commit")


def normalized_sql(value: str) -> str:
    return " ".join(value.strip().rstrip(";").split())


def schema_statements() -> list[str]:
    return [
        statement.strip()
        for statement in Path(__file__).with_name("schema.sql").read_text().split(";")
        if statement.strip()
    ]


def sqlite_text(raw: bytes) -> str:
    # sqlite3's default TEXT decoder wraps invalid UTF-8 in OperationalError,
    # before JSON validation can see it. Classify this exact decoding boundary;
    # unrelated lock, I/O, and commit errors keep their own error semantics.
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise LabError("checkpoint_invalid") from None


def storage_error(error: sqlite3.Error) -> LabError:
    code = getattr(error, "sqlite_errorcode", 0) & 0xFF
    if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return LabError("database_busy")
    if code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
        return LabError("checkpoint_invalid")
    return LabError("storage_failure")


def fault_exit(fault: str, node: str) -> None:
    print(
        canonical({"event": "fault_reached", "fault": fault, "node": node, "pid": os.getpid()}),
        file=sys.stderr,
        flush=True,
    )
    if fault == "pause-before-commit":
        time.sleep(2)
    os._exit({"before-commit": 70, "after-commit": 71, "pause-before-commit": 72}[fault])


class Store:
    def __init__(self, path: Path = DATABASE, *, connect=sqlite3.connect, fault_hook=fault_exit):
        self.path = path
        self.connect = connect
        self.fault_hook = fault_hook
        self.documents = load_fixtures()

    @contextmanager
    def transaction(self, *, create=False, write=False, after_commit=None):
        connection = None
        try:
            if not create and not self.path.is_file():
                raise LabError("not_found")
            if create:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            uri = self.path.resolve().as_uri() + ("?mode=rwc" if create else "?mode=rw")
            connection = self.connect(uri, uri=True, timeout=0.5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.text_factory = sqlite_text
            connection.execute("PRAGMA busy_timeout=500")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            # Do not switch an existing unknown/WAL database into another journal mode.
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise LabError("checkpoint_invalid")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            objects = connection.execute("SELECT name FROM sqlite_master").fetchall()
            if create and version == 0 and not objects:
                for statement in schema_statements():
                    connection.execute(statement)
                connection.execute("PRAGMA user_version=1")
                connection.execute(
                    "INSERT INTO metadata VALUES (1, ?, ?, ?)",
                    (VERSION, CORPUS_REVISION, CORPUS_SHA256),
                )
            self.validate_database(connection)
            yield connection
            try:
                connection.commit()
            except Exception:
                # Commit acknowledgement may be lost after durable commit. Never retry it.
                raise LabError("result_unconfirmed" if write else "storage_failure") from None
            if after_commit is not None:
                try:
                    after_commit()
                except Exception:
                    raise LabError("result_unconfirmed" if write else "storage_failure") from None
        except sqlite3.Error as error:
            raise storage_error(error) from None
        finally:
            if connection is not None:
                # Rollback is also harmless after a confirmed commit. An exception during
                # commit remains unconfirmed, even if this best-effort rollback succeeds.
                with suppress(sqlite3.Error):
                    connection.rollback()
                with suppress(sqlite3.Error):
                    connection.close()

    def validate_database(self, connection):
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise LabError("unsupported_schema")
        expected = {
            statement.split()[2]: normalized_sql(statement) for statement in schema_statements()
        }
        actual = connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
        ).fetchall()
        if {row["name"]: normalized_sql(row["sql"]) for row in actual} != expected or any(
            row["type"] != "table" for row in actual
        ):
            raise LabError("checkpoint_invalid")
        if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
            raise LabError("checkpoint_invalid")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise LabError("checkpoint_invalid")
        metadata = connection.execute("SELECT * FROM metadata").fetchall()
        if len(metadata) != 1 or tuple(metadata[0]) != (1, VERSION, CORPUS_REVISION, CORPUS_SHA256):
            raise LabError("incompatible_version")
        # This teaching database is small and private. Reject corruption in any run
        # before allowing an application write, including creation of another run.
        for row in connection.execute("SELECT run_id FROM runs"):
            self.snapshot(connection, row[0])

    def snapshot(self, connection, run_id):
        row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise LabError("not_found")
        run = dict(row)
        if (run["workflow_version"], run["corpus_revision"], run["corpus_sha256"]) != (
            VERSION,
            CORPUS_REVISION,
            CORPUS_SHA256,
        ):
            raise LabError("incompatible_version")
        revision = run["revision"]
        if (
            not valid_run_id(run["run_id"])
            or run["case_id"] not in CASES
            or type(revision) is not int
            or not 0 <= revision <= 3
            or run["phase"] != PHASES[revision]
            or run["outcome"] != (OUTCOMES[run["case_id"]] if revision == 3 else None)
        ):
            raise LabError("checkpoint_invalid")
        rows = connection.execute(
            "SELECT * FROM checkpoints WHERE run_id=? ORDER BY revision", (run_id,)
        ).fetchall()
        if len(rows) != revision:
            raise LabError("checkpoint_invalid")
        checkpoints = []
        previous_hash = None
        for index, row in enumerate(rows, 1):
            output = parse_json(row["output_json"])
            if (
                row["revision"] != index
                or row["node"] != NODES[index - 1]
                or row["previous_hash"] != previous_hash
                or canonical(output) != row["output_json"]
                or row["state_hash"]
                != checkpoint_hash(run, index, row["node"], output, previous_hash)
            ):
                raise LabError("checkpoint_invalid")
            previous_hash = row["state_hash"]
            checkpoints.append(
                {
                    "revision": index,
                    "node": row["node"],
                    "output": output,
                    "previous_hash": row["previous_hash"],
                    "state_hash": previous_hash,
                }
            )
        if run.pop("last_hash") != previous_hash:
            raise LabError("checkpoint_invalid")
        validate_saved_outputs(
            run["case_id"], [item["output"] for item in checkpoints], self.documents
        )
        return {
            "run": run,
            "checkpoints": checkpoints,
            "result": checkpoints[-1]["output"] if revision == 3 else None,
        }

    def start(self, run_id, case_id):
        with self.transaction(create=True, write=True) as connection:
            if connection.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
                raise LabError("run_exists")
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, 0, 'ready', NULL, NULL)",
                (run_id, case_id, VERSION, CORPUS_REVISION, CORPUS_SHA256),
            )
            snapshot = self.snapshot(connection, run_id)
        return snapshot

    def inspect(self, run_id):
        with self.transaction() as connection:
            snapshot = self.snapshot(connection, run_id)
        return snapshot

    def step(self, run_id, expected_revision, fault=None, fault_node=None):
        node = NODES[expected_revision] if expected_revision < 3 else None

        def after_commit():
            if fault == "after-commit" and node == fault_node:
                self.fault_hook(fault, node)

        with self.transaction(write=True, after_commit=after_commit) as connection:
            snapshot = self.snapshot(connection, run_id)
            run = snapshot["run"]
            if run["revision"] != expected_revision:
                raise LabError("revision_conflict")
            if node is None:
                return snapshot, None
            output = execute_node(
                node,
                run["case_id"],
                [item["output"] for item in snapshot["checkpoints"]],
                self.documents,
            )
            revision = expected_revision + 1
            previous_hash = snapshot["checkpoints"][-1]["state_hash"] if expected_revision else None
            state_hash = checkpoint_hash(run, revision, node, output, previous_hash)
            connection.execute(
                "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, revision, node, canonical(output), previous_hash, state_hash),
            )
            changed = connection.execute(
                "UPDATE runs SET revision=?, phase=?, outcome=?, last_hash=? WHERE run_id=? AND revision=?",
                (
                    revision,
                    PHASES[revision],
                    output["outcome"] if revision == 3 else None,
                    state_hash,
                    run_id,
                    expected_revision,
                ),
            ).rowcount
            if changed != 1:
                raise LabError("revision_conflict")
            snapshot = self.snapshot(connection, run_id)
            if fault in {"before-commit", "pause-before-commit"} and node == fault_node:
                self.fault_hook(fault, node)
        return snapshot, node


def execute(
    command: str,
    run_id: str,
    *,
    case_id=None,
    expected_revision=None,
    fault=None,
    fault_node=None,
    store=None,
):
    if (
        command not in {"start", "inspect", "step", "resume"}
        or not valid_run_id(run_id)
        or (command == "start" and case_id not in CASES)
        or (command != "start" and case_id is not None)
        or (
            command in {"step", "resume"}
            and (type(expected_revision) is not int or not 0 <= expected_revision <= 3)
        )
        or (command not in {"step", "resume"} and expected_revision is not None)
        or (fault is None) != (fault_node is None)
        or (
            fault is not None
            and (
                command not in {"step", "resume"} or fault not in FAULTS or fault_node not in NODES
            )
        )
    ):
        raise LabError("invalid_input")
    if fault is not None and (
        NODES.index(fault_node) < expected_revision
        or (command == "step" and NODES.index(fault_node) != expected_revision)
    ):
        raise LabError("invalid_input")
    store = store or Store()
    performed = []
    try:
        if command == "start":
            snapshot = store.start(run_id, case_id)
        elif command == "inspect":
            snapshot = store.inspect(run_id)
        else:
            for _ in range(3):
                snapshot, node = store.step(run_id, expected_revision, fault, fault_node)
                if node is not None:
                    performed.append(node)
                expected_revision = snapshot["run"]["revision"]
                if command == "step" or expected_revision == 3:
                    break
    except LabError as error:
        error.performed = performed.copy()
        raise
    except Exception:
        error = LabError("storage_failure")
        error.performed = performed.copy()
        raise error from None
    return {
        "contract_version": VERSION,
        "ok": True,
        "command": command,
        **snapshot,
        "performed": performed,
        "reused": [
            item["node"] for item in snapshot["checkpoints"] if item["node"] not in performed
        ],
    }
