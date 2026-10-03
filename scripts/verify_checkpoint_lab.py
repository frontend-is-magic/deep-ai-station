"""Verify the frozen checkpoint ZIP with real CLI processes and independent SQLite reads."""

# ruff: noqa: S101 - assertions specify this fixed, maintainer-owned experiment

import hashlib
import json
import os
import selectors
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from verify_course_labs import stop_owned_process_group

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "public/labs/workflow-checkpoint-python.zip"
VERSION = "workflow-checkpoint-v1"
CORPUS = "agent-corpus-v1"
CORPUS_HASH = "3d22e8986d9c33832b2d4604dc27d431d8caaa21b5344f79f40065a0301f48b1"
NODES = ("retrieve", "draft", "validate")
PHASES = ("ready", "retrieved", "drafted", "completed")
QUERIES = {"normal": "API 超时", "empty": "zzzz unmatched", "conflict": "取消计费冲突"}
ANSWERS = {
    "normal": "API 调用需要明确输入、错误类别和超时预算。",
    "empty": "固定资料中没有匹配证据。",
    "conflict": "合成资料对取消计费存在冲突，不能据此给出一致结论。",
}
OUTCOMES = {
    "normal": "complete",
    "empty": "insufficient_evidence",
    "conflict": "conflicting_evidence",
}
# Independent literals from the frozen contract, never imported from the product.
DOCUMENTS = [
    {
        "id": "api",
        "title": "API 契约与失败状态",
        "body": "API 明确输入长度、错误类别和超时预算。成功与失败都返回可识别的状态，前端保留输入并允许重试。取消请求不能证明供应商没有计费。",
        "url": "https://fastapi.tiangolo.com/tutorial/handling-errors/",
        "kind": "course-excerpt",
        "conflict_group": None,
    },
    {
        "id": "billing-a",
        "title": "取消费用 · 冲突练习 A（合成资料）",
        "body": "练习声明 A：取消后一定不计费。",
        "url": None,
        "kind": "conflict-fixture",
        "conflict_group": "cancel-billing",
    },
    {
        "id": "billing-b",
        "title": "取消费用 · 冲突练习 B（合成资料）",
        "body": "练习声明 B：取消只会终止传输，已经发生的供应商用量仍可能计费。",
        "url": None,
        "kind": "conflict-fixture",
        "conflict_group": "cancel-billing",
    },
]
MAX_OUTPUT = 262144
SENTINEL = "private-checkpoint-sentinel"


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON evidence field")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("Nonfinite JSON evidence")


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


def same(actual, expected):
    # Python == would otherwise accept True as the integer 1.
    assert canonical(actual) == canonical(expected), "Fixed checkpoint evidence differs"


def outputs(case):
    documents = DOCUMENTS[:1] if case == "normal" else DOCUMENTS[1:] if case == "conflict" else []
    citations = [
        {
            "source_id": document["id"],
            "quote": "API 明确输入长度、错误类别和超时预算。"
            if case == "normal"
            else document["body"],
            "url": document["url"],
        }
        for document in documents
    ]
    return [
        {
            "query": QUERIES[case],
            "found_ids": [d["id"] for d in documents],
            "read_documents": documents,
        },
        {"answer": ANSWERS[case], "citations": citations},
        {
            "outcome": OUTCOMES[case],
            "answer": ANSWERS[case],
            "citations": citations,
            "read_only": True,
            "model_calls": 0,
        },
    ]


def state_hash(run_id, revision, node, output, previous):
    return digest(
        {
            "run_id": run_id,
            "revision": revision,
            "node": node,
            "workflow_version": VERSION,
            "corpus_revision": CORPUS,
            "corpus_sha256": CORPUS_HASH,
            "previous_hash": previous,
            "output": output,
        }
    )


def expected_state(run_id, case, revision):
    previous = None
    checkpoints = []
    for index, output in enumerate(outputs(case)[:revision], 1):
        hashed = state_hash(run_id, index, NODES[index - 1], output, previous)
        checkpoints.append(
            {
                "revision": index,
                "node": NODES[index - 1],
                "output": output,
                "previous_hash": previous,
                "state_hash": hashed,
            }
        )
        previous = hashed
    run = {
        "run_id": run_id,
        "case_id": case,
        "workflow_version": VERSION,
        "corpus_revision": CORPUS,
        "corpus_sha256": CORPUS_HASH,
        "revision": revision,
        "phase": PHASES[revision],
        "outcome": OUTCOMES[case] if revision == 3 else None,
    }
    return run, checkpoints


class Child:
    """Bound output and waits; cleanup only the process handle this verifier created."""

    def __init__(self, args, cwd, env, *, timeout=8, grace=0.3):
        self.process = subprocess.Popen(  # noqa: S603 - fixed package entry or fixed self-test helper
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.deadline = time.monotonic() + timeout
        self.grace = grace
        self.selector = selectors.DefaultSelector()
        for name in ("stdout", "stderr"):
            self.selector.register(getattr(self.process, name), selectors.EVENT_READ, name)
        self.output = {"stdout": bytearray(), "stderr": bytearray()}
        self.reaped = False

    def pump(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Checkpoint CLI observation exceeded its bound")
        for key, _mask in self.selector.select(min(remaining, 0.05)):
            data = os.read(key.fileobj.fileno(), 8192)
            if not data:
                self.selector.unregister(key.fileobj)
            else:
                self.output[key.data].extend(data)
                assert sum(map(len, self.output.values())) <= MAX_OUTPUT, (
                    "CLI output exceeded its bound"
                )

    def fault_reached(self, fault, node):
        while b"\n" not in self.output["stderr"]:
            if not self.selector.get_map():
                raise AssertionError("Process exited without reaching the transaction fault")
            self.pump()
        same(
            decode(bytes(self.output["stderr"])),
            {"event": "fault_reached", "fault": fault, "node": node, "pid": self.process.pid},
        )
        assert not self.output["stdout"]

    def finish(self):
        while self.selector.get_map():
            self.pump()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Checkpoint CLI observation exceeded its bound")
        code = self.process.wait(timeout=remaining)
        self.reaped = True
        return code, bytes(self.output["stdout"]), bytes(self.output["stderr"])

    def close(self):
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=self.grace)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            else:
                self.process.wait(timeout=1)
            self.reaped = True
        finally:
            self.selector.close()
            self.process.stdout.close()
            self.process.stderr.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def report(child, *, error=None):
    code, stdout, stderr = child.finish()
    assert code == (1 if error else 0), "CLI exit status does not match its contract"
    assert not stderr, "CLI diagnostics must be fixed JSON stdout only"
    assert stdout.endswith(b"\n") and stdout.count(b"\n") == 1
    result = decode(stdout)
    if error:
        same(
            result,
            {"contract_version": VERSION, "ok": False, "error": {"code": error}, "performed": []},
        )
        assert SENTINEL.encode() not in stdout
    return result


class Lab:
    def __init__(self, python, package, runtime, root):
        self.python, self.package, self.runtime, self.root = python, package, runtime, root
        self.children = []
        self.scenarios = []

    def folder(self, name):
        folder = self.root / name
        folder.mkdir()
        return folder

    def spawn(self, folder, *args, timeout=8):
        child = Child(
            [str(self.python), "-s", "-E", "-B", str(self.package / "app.py"), *args],
            folder,
            self.runtime,
            timeout=timeout,
        )
        self.children.append(child)
        return child

    def call(self, folder, command, run_id, *options, error=None):
        with self.spawn(folder, command, "--run-id", run_id, *options) as child:
            return report(child, error=error)

    def success(self, folder, command, run_id, case, revision, performed=(), *options):
        actual = self.call(folder, command, run_id, *options)
        run, checkpoints = expected_state(run_id, case, revision)
        same(
            actual,
            {
                "contract_version": VERSION,
                "ok": True,
                "command": command,
                "run": run,
                "checkpoints": checkpoints,
                "result": outputs(case)[2] if revision == 3 else None,
                "performed": list(performed),
                "reused": [node for node in NODES[:revision] if node not in performed],
            },
        )
        assert_sql(folder, run_id, case, revision)
        return actual

    def start(self, folder, case="normal"):
        run_id = str(uuid.uuid4())
        self.success(folder, "start", run_id, case, 0, (), "--case", case)
        return run_id

    def advance(self, folder, run_id, case, revision):
        for previous in range(revision):
            self.success(
                folder,
                "step",
                run_id,
                case,
                previous + 1,
                (NODES[previous],),
                "--expected-revision",
                str(previous),
            )

    def assert_clean(self):
        for child in self.children:
            assert child.reaped and child.process.returncode is not None
            try:
                os.kill(child.process.pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError("An owned CLI process still exists")


@contextmanager
def sql_connection(*args, **kwargs):
    connection = sqlite3.connect(*args, **kwargs)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def database(folder):
    return folder / ".data/checkpoints.sqlite3"


def read_sql(folder):
    with sql_connection(database(folder).as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return {
            "version": connection.execute("PRAGMA user_version").fetchone()[0],
            "integrity": connection.execute("PRAGMA integrity_check").fetchall()[0][0],
            "foreign_keys": [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")],
            "metadata": [dict(row) for row in connection.execute("SELECT * FROM metadata")],
            "runs": [dict(row) for row in connection.execute("SELECT * FROM runs ORDER BY run_id")],
            "checkpoints": [
                dict(row)
                for row in connection.execute("SELECT * FROM checkpoints ORDER BY run_id,revision")
            ],
        }


def assert_sql(folder, run_id, case, revision):
    state = read_sql(folder)
    assert state["version"] == 1 and state["integrity"] == "ok" and state["foreign_keys"] == []
    same(
        state["metadata"],
        [
            {
                "singleton": 1,
                "workflow_version": VERSION,
                "corpus_revision": CORPUS,
                "corpus_sha256": CORPUS_HASH,
            }
        ],
    )
    run, checkpoints = expected_state(run_id, case, revision)
    same(
        state["runs"],
        [{**run, "last_hash": checkpoints[-1]["state_hash"] if checkpoints else None}],
    )
    same(
        state["checkpoints"],
        [
            {
                "run_id": run_id,
                "revision": checkpoint["revision"],
                "node": checkpoint["node"],
                "output_json": canonical(checkpoint["output"]),
                "previous_hash": checkpoint["previous_hash"],
                "state_hash": checkpoint["state_hash"],
            }
            for checkpoint in checkpoints
        ],
    )


def file_digest(folder):
    return hashlib.sha256(database(folder).read_bytes()).hexdigest()


def normal_cases(lab):
    baseline = None
    for case in QUERIES:
        folder = lab.folder(case)
        run_id = lab.start(folder, case)
        lab.success(folder, "resume", run_id, case, 3, NODES, "--expected-revision", "0")
        before = file_digest(folder)
        lab.success(folder, "inspect", run_id, case, 3)
        lab.success(folder, "resume", run_id, case, 3, (), "--expected-revision", "3")
        lab.success(folder, "step", run_id, case, 3, (), "--expected-revision", "3")
        lab.call(folder, "resume", run_id, "--expected-revision", "2", error="revision_conflict")
        lab.call(folder, "start", run_id, "--case", case, error="run_exists")
        assert file_digest(folder) == before, "Terminal replay wrote to the database"
        lab.scenarios.append(f"complete-and-replay-{case}")
        if case == "normal":
            baseline = folder, run_id
    return baseline


def crash_cases(lab):
    for fault, code in (("before-commit", 70), ("after-commit", 71)):
        for previous, node in enumerate(NODES):
            folder = lab.folder(f"{fault}-{node}")
            run_id = lab.start(folder)
            lab.advance(folder, run_id, "normal", previous)
            with lab.spawn(
                folder,
                "step",
                "--run-id",
                run_id,
                "--expected-revision",
                str(previous),
                "--fault",
                fault,
                "--fault-node",
                node,
            ) as child:
                child.fault_reached(fault, node)
                actual_code, stdout, stderr = child.finish()
                assert actual_code == code and not stdout
                assert stderr.count(b"\n") == 1
            # A fresh CLI permits SQLite hot-journal recovery, not application repair.
            committed = previous + (fault == "after-commit")
            lab.success(folder, "inspect", run_id, "normal", committed)
            lab.success(
                folder,
                "resume",
                run_id,
                "normal",
                3,
                NODES[committed:],
                "--expected-revision",
                str(committed),
            )
            lab.scenarios.append(f"{fault}-{node}")


def killed_transaction(lab):
    folder = lab.folder("killed-open-transaction")
    run_id = lab.start(folder)
    lab.advance(folder, run_id, "normal", 1)
    original = read_sql(folder)
    with lab.spawn(
        folder,
        "step",
        "--run-id",
        run_id,
        "--expected-revision",
        "1",
        "--fault",
        "pause-before-commit",
        "--fault-node",
        "draft",
    ) as child:
        child.fault_reached("pause-before-commit", "draft")
        assert child.process.poll() is None, (
            "Kill fixture expired before the verifier could observe it"
        )
        with sql_connection(database(folder), timeout=0) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                assert "locked" in str(error)
            else:
                connection.rollback()
                raise AssertionError("Fault marker did not correspond to a held SQLite transaction")
        assert Path(str(database(folder)) + "-journal").stat().st_size > 0
        child.process.kill()
        code, stdout, _stderr = child.finish()
        assert code == -signal.SIGKILL and not stdout
    lab.success(folder, "inspect", run_id, "normal", 1)
    same(read_sql(folder), original)
    lab.success(folder, "resume", run_id, "normal", 3, NODES[1:], "--expected-revision", "1")
    lab.scenarios.append("sigkill-open-transaction-recovered")


def competing_processes(lab):
    folder = lab.folder("concurrent-cas")
    run_id = lab.start(folder)
    with sql_connection(database(folder), isolation_level=None) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        with lab.spawn(folder, "step", "--run-id", run_id, "--expected-revision", "0") as first:
            with lab.spawn(
                folder, "step", "--run-id", run_id, "--expected-revision", "0"
            ) as second:
                assert first.process.pid != second.process.pid
                assert first.process.poll() is None and second.process.poll() is None
                blocker.rollback()
                completed = [child.finish() for child in (first, second)]
    assert sorted(item[0] for item in completed) == [0, 1]
    for code, stdout, stderr in completed:
        assert not stderr and stdout.endswith(b"\n") and stdout.count(b"\n") == 1
        actual = decode(stdout)
        if code:
            same(
                actual,
                {
                    "contract_version": VERSION,
                    "ok": False,
                    "error": {"code": "revision_conflict"},
                    "performed": [],
                },
            )
        else:
            run, checkpoints = expected_state(run_id, "normal", 1)
            same(
                actual,
                {
                    "contract_version": VERSION,
                    "ok": True,
                    "command": "step",
                    "run": run,
                    "checkpoints": checkpoints,
                    "result": None,
                    "performed": ["retrieve"],
                    "reused": [],
                },
            )
    lab.success(folder, "inspect", run_id, "normal", 1)
    before = file_digest(folder)
    with sql_connection(database(folder), isolation_level=None) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        lab.call(folder, "step", run_id, "--expected-revision", "1", error="database_busy")
        blocker.rollback()
    assert file_digest(folder) == before
    lab.success(folder, "resume", run_id, "normal", 3, NODES[1:], "--expected-revision", "1")
    lab.scenarios.extend(["two-process-cas", "bounded-database-busy"])


def rehash(connection, run_id):
    """Deliberately repair hashes after semantic tampering: hashes alone must not pass."""
    previous = None
    for revision, node, raw in connection.execute(
        "SELECT revision,node,output_json FROM checkpoints ORDER BY revision"
    ).fetchall():
        hashed = state_hash(run_id, revision, node, decode(raw), previous)
        connection.execute(
            "UPDATE checkpoints SET previous_hash=?,state_hash=? WHERE revision=?",
            (previous, hashed, revision),
        )
        previous = hashed
    connection.execute("UPDATE runs SET last_hash=?", (previous,))


def rejected_storage(lab, baseline):
    source, run_id = baseline
    missing = lab.folder("missing-database")
    for command in ("inspect", "step", "resume"):
        options = () if command == "inspect" else ("--expected-revision", "0")
        lab.call(missing, command, run_id, *options, error="not_found")
        assert list(missing.iterdir()) == [], "Reading a missing database created state"
    lab.scenarios.append("missing-database-does-not-create")

    def copied(name):
        folder = lab.folder(name)
        database(folder).parent.mkdir()
        shutil.copyfile(database(source), database(folder))
        return folder

    valid = copied("invalid-requests")
    before = file_digest(valid)
    lab.call(valid, "inspect", str(uuid.uuid4()), error="not_found")
    lab.call(valid, "inspect", SENTINEL, error="invalid_input")
    lab.call(valid, "inspect", run_id, "--run-id", run_id, error="invalid_input")
    lab.call(
        valid,
        "resume",
        run_id,
        "--expected-revision",
        "3",
        "--fault",
        "before-commit",
        "--fault-node",
        "retrieve",
        error="invalid_input",
    )
    assert file_digest(valid) == before
    lab.scenarios.append("unknown-and-invalid-requests-do-not-write")

    mutations = [
        ("unsupported-schema", "PRAGMA user_version=2", (), "unsupported_schema"),
        (
            "metadata-version",
            "UPDATE metadata SET workflow_version=?",
            (SENTINEL,),
            "incompatible_version",
        ),
        ("run-version", "UPDATE runs SET corpus_revision=?", (SENTINEL,), "incompatible_version"),
        ("corpus-hash", "UPDATE metadata SET corpus_sha256=?", ("0" * 64,), "incompatible_version"),
        (
            "invalid-json",
            "UPDATE checkpoints SET output_json=? WHERE revision=2",
            ('{"x":',),
            "checkpoint_invalid",
        ),
        (
            "invalid-utf8-text",
            "UPDATE checkpoints SET output_json=CAST(X'80' AS TEXT) WHERE revision=2",
            (),
            "checkpoint_invalid",
        ),
        (
            "overflow-json-number",
            "UPDATE checkpoints SET output_json=? WHERE revision=2",
            ('{"value":1e999}',),
            "checkpoint_invalid",
        ),
        (
            "duplicate-json",
            "UPDATE checkpoints SET output_json=? WHERE revision=2",
            ('{"answer":"a","answer":"b"}',),
            "checkpoint_invalid",
        ),
        (
            "wrong-chain",
            "UPDATE checkpoints SET previous_hash=? WHERE revision=2",
            ("0" * 64,),
            "checkpoint_invalid",
        ),
        (
            "missing-checkpoint",
            "DELETE FROM checkpoints WHERE revision=2",
            (),
            "checkpoint_invalid",
        ),
        (
            "unexpected-trigger",
            "CREATE TRIGGER extra_after_update AFTER UPDATE ON runs BEGIN SELECT 1; END",
            (),
            "checkpoint_invalid",
        ),
    ]
    for name, sql, parameters, error in mutations:
        folder = copied(name)
        with sql_connection(database(folder)) as connection:
            connection.execute(sql, parameters)
        before = file_digest(folder)
        for command in ("inspect", "resume"):
            options = () if command == "inspect" else ("--expected-revision", "3")
            lab.call(folder, command, run_id, *options, error=error)
            assert file_digest(folder) == before, "Rejected storage was changed"
        lab.scenarios.append(name)

    for name in ("rehashed-wrong-answer", "rehashed-missing-read"):
        folder = copied(name)
        revision = 2 if name == "rehashed-wrong-answer" else 1
        payload = outputs("normal")[revision - 1]
        if revision == 2:
            payload["answer"] = SENTINEL
        else:
            payload["read_documents"] = []
        with sql_connection(database(folder)) as connection:
            connection.execute(
                "UPDATE checkpoints SET output_json=? WHERE revision=?",
                (canonical(payload), revision),
            )
            rehash(connection, run_id)
        before = file_digest(folder)
        lab.call(folder, "resume", run_id, "--expected-revision", "3", error="checkpoint_invalid")
        assert file_digest(folder) == before
        lab.scenarios.append(name)

    for name in ("physical-corruption", "unrecognized-database"):
        folder = lab.folder(name)
        database(folder).parent.mkdir()
        if name == "physical-corruption":
            database(folder).write_bytes(SENTINEL.encode())
            error = "checkpoint_invalid"
        else:
            with sql_connection(database(folder)) as connection:
                connection.execute("CREATE TABLE unrelated(value TEXT)")
                connection.execute("INSERT INTO unrelated VALUES(?)", (SENTINEL,))
            error = "unsupported_schema"
        before = file_digest(folder)
        lab.call(folder, "start", run_id, "--case", "normal", error=error)
        assert file_digest(folder) == before
        lab.scenarios.append(name)


def clean_environment(folder):
    home = folder / "home"
    home.mkdir()
    runtime = {"PATH": os.defpath, "HOME": str(home), "LANG": "C.UTF-8"}
    install = {
        key: os.environ[key]
        for key in ("PATH", "UV_CACHE_DIR", "TMPDIR", "CI")
        if key in os.environ
    }
    return {**runtime, **install}, runtime


def owned_command(args, folder, env, *, timeout=600):
    process = subprocess.Popen(args, cwd=folder, env=env, start_new_session=True)  # noqa: S603 - fixed install/test commands
    try:
        assert process.wait(timeout=timeout) == 0, "Frozen checkpoint lab command failed"
    finally:
        if process.poll() is None:
            stop_owned_process_group(process)
        else:
            process.wait(timeout=1)


def verify():
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required on PATH")
    temp_root = "/private/tmp" if Path("/private/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(
        prefix="deep-ai-checkpoint-verify-", dir=temp_root
    ) as temporary:
        root = Path(temporary).resolve()
        assert not root.is_relative_to(ROOT)
        package = root / "package"
        package.mkdir()
        with zipfile.ZipFile(ARCHIVE) as archive:
            archive.extractall(package)
        fixture = decode((package / "fixtures.json").read_bytes())
        assert digest(fixture["documents"]) == CORPUS_HASH
        same(fixture["cases"], {case: {"query": query} for case, query in QUERIES.items()})
        install, runtime = clean_environment(root)
        owned_command([uv, "sync", "--locked", "--python", "3.12"], package, install)
        for command in (
            ("ruff", "check", "."),
            ("ruff", "format", "--check", "."),
            ("pytest", "-q"),
        ):
            owned_command([uv, "run", "--frozen", *command], package, install)
        cases = root / "cases"
        cases.mkdir()
        lab = Lab(package / ".venv/bin/python", package, runtime, cases)
        try:
            baseline = normal_cases(lab)
            crash_cases(lab)
            killed_transaction(lab)
            competing_processes(lab)
            rejected_storage(lab, baseline)
            lab.assert_clean()
        finally:
            for child in lab.children:
                if not child.reaped:
                    child.close()
        result = {
            "lab": VERSION,
            "archive": "verified",
            "native_checks": "passed",
            "corpus_sha256": CORPUS_HASH,
            "black_box_scenarios": len(lab.scenarios),
            "scenarios": lab.scenarios,
            "cli_processes_reaped": len(lab.children),
            "commit_boundary_exits": 6,
            "open_transaction_sigkill_recovered": 1,
            "concurrent_cas_processes": 2,
            "model_calls": 0,
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    verify()
