"""Real CLI subprocesses, real transactions, and cleanup of only our own children."""

import json
import os
import select
import signal
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

APP = Path(__file__).with_name("app.py").resolve()
RUN_ID = "11111111-1111-4111-8111-111111111111"
NODES = ["retrieve", "draft", "validate"]


def environment(cwd):
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(cwd),
        "LANG": "en_US.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def invoke(cwd, *arguments, expected=0):
    result = subprocess.run(
        [sys.executable, "-B", str(APP), *arguments],
        cwd=cwd,
        env=environment(cwd),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == expected, result.stdout
    assert result.stderr == ""
    value = json.loads(result.stdout)
    assert value["ok"] == (expected == 0)
    return value


def start(cwd, case="normal"):
    return invoke(cwd, "start", "--run-id", RUN_ID, "--case", case)


def inspect(cwd):
    return invoke(cwd, "inspect", "--run-id", RUN_ID)


def node_args(command, revision):
    return [command, "--run-id", RUN_ID, "--expected-revision", str(revision)]


def database(cwd):
    return cwd / ".data/checkpoints.sqlite3"


def sql_state(cwd):
    with sqlite3.connect(f"{database(cwd).as_uri()}?mode=ro", uri=True) as connection:
        return {
            "runs": connection.execute("SELECT * FROM runs ORDER BY run_id").fetchall(),
            "checkpoints": connection.execute(
                "SELECT * FROM checkpoints ORDER BY run_id,revision"
            ).fetchall(),
        }


@contextmanager
def process(cwd, arguments, *, barrier=False):
    if barrier:
        # Fixed test-only launcher. It executes only the packaged app after both
        # children have reached a parent-controlled pipe barrier.
        launcher = "import runpy,sys;from pathlib import Path;sys.path.insert(0,str(Path(sys.argv[1]).parent));sys.stderr.write('ready\\n');sys.stderr.flush();sys.stdin.buffer.read(1);sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')"
        argv = [sys.executable, "-B", "-c", launcher, str(APP), *arguments]
    else:
        argv = [sys.executable, "-B", str(APP), *arguments]
    child = subprocess.Popen(
        argv,
        cwd=cwd,
        env=environment(cwd),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        yield child
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()


def line(stream):
    ready, _, _ = select.select([stream], [], [], 3)
    assert ready, "owned subprocess did not reach fixed barrier"
    return stream.readline()


@pytest.mark.parametrize(
    "case,outcome,sources",
    [
        ("normal", "complete", ["api"]),
        ("empty", "insufficient_evidence", []),
        ("conflict", "conflicting_evidence", ["billing-a", "billing-b"]),
    ],
)
def test_cli_complete_and_repeat_result_matches_durable_rows(tmp_path, case, outcome, sources):
    initial = start(tmp_path, case)
    assert initial["run"]["revision"] == 0
    completed = invoke(tmp_path, *node_args("resume", 0))
    assert completed["performed"] == NODES
    assert completed["result"]["outcome"] == outcome
    assert [item["source_id"] for item in completed["result"]["citations"]] == sources
    before = sql_state(tmp_path)
    again = invoke(tmp_path, *node_args("resume", 3))
    assert again["performed"] == []
    assert again["reused"] == NODES
    assert again["result"] == completed["result"]
    assert sql_state(tmp_path) == before
    assert len(before["checkpoints"]) == 3
    assert json.loads(before["checkpoints"][-1][3]) == completed["result"]


@pytest.mark.parametrize("revision", [0, 1, 2])
@pytest.mark.parametrize(
    "fault,exit_code,committed", [("before-commit", 70, False), ("after-commit", 71, True)]
)
def test_real_process_fault_each_node_and_new_process_recovers(
    tmp_path, revision, fault, exit_code, committed
):
    start(tmp_path)
    for current in range(revision):
        invoke(tmp_path, *node_args("step", current))
    args = [*node_args("step", revision), "--fault", fault, "--fault-node", NODES[revision]]
    with process(tmp_path, args) as child:
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == exit_code
        assert stdout == b""
        event = json.loads(stderr)
        assert event == {
            "event": "fault_reached",
            "fault": fault,
            "node": NODES[revision],
            "pid": child.pid,
        }
    recovered = inspect(tmp_path)
    expected = revision + int(committed)
    assert recovered["run"]["revision"] == expected
    assert len(sql_state(tmp_path)["checkpoints"]) == expected
    completed = invoke(tmp_path, *node_args("resume", expected))
    assert completed["performed"] == NODES[expected:]
    assert completed["reused"] == NODES[:expected]
    assert len(sql_state(tmp_path)["checkpoints"]) == 3


@pytest.mark.skipif(os.name != "posix", reason="real signal/pipe verification requires POSIX")
def test_sigkill_while_real_transaction_open_rolls_back_then_restarts(tmp_path):
    start(tmp_path)
    args = [*node_args("step", 0), "--fault", "pause-before-commit", "--fault-node", "retrieve"]
    with process(tmp_path, args) as child:
        event = json.loads(line(child.stderr))
        assert event["pid"] == child.pid
        assert event["fault"] == "pause-before-commit"
        assert Path(str(database(tmp_path)) + "-journal").stat().st_size > 0
        with sqlite3.connect(database(tmp_path), timeout=0) as connection:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                connection.execute("BEGIN IMMEDIATE")
        child.kill()
        assert child.wait(timeout=3) == -signal.SIGKILL
        assert child.stdout.read() == b""
    recovered = inspect(tmp_path)
    assert recovered["run"]["revision"] == 0
    assert recovered["checkpoints"] == []
    assert invoke(tmp_path, *node_args("resume", 0))["performed"] == NODES


def test_lock_wait_is_bounded_and_does_not_advance(tmp_path):
    start(tmp_path)
    args = [*node_args("step", 0), "--fault", "pause-before-commit", "--fault-node", "retrieve"]
    with process(tmp_path, args) as child:
        assert json.loads(line(child.stderr))["pid"] == child.pid
        rejected = invoke(tmp_path, *node_args("step", 0), expected=1)
        assert rejected["error"] == {"code": "database_busy"}
        assert rejected["performed"] == []
        child.kill()
        child.wait(timeout=3)
    assert inspect(tmp_path)["run"]["revision"] == 0


@pytest.mark.skipif(os.name != "posix", reason="pipe barrier requires POSIX")
def test_two_real_processes_same_revision_only_one_commits(tmp_path):
    start(tmp_path)
    arguments = node_args("step", 0)
    with (
        process(tmp_path, arguments, barrier=True) as first,
        process(tmp_path, arguments, barrier=True) as second,
    ):
        assert line(first.stderr) == b"ready\n"
        assert line(second.stderr) == b"ready\n"
        for child in (first, second):
            child.stdin.write(b"x")
            child.stdin.flush()
        reports = []
        for child in (first, second):
            stdout, stderr = child.communicate(timeout=5)
            assert stderr == b""
            reports.append((child.returncode, json.loads(stdout)))
    assert sorted(code for code, _ in reports) == [0, 1]
    success = next(report for code, report in reports if code == 0)
    failure = next(report for code, report in reports if code == 1)
    assert success["performed"] == ["retrieve"]
    assert failure["error"] == {"code": "revision_conflict"}
    assert failure["performed"] == []
    assert inspect(tmp_path)["run"]["revision"] == 1
    assert len(sql_state(tmp_path)["checkpoints"]) == 1


def test_fault_cannot_reexecute_confirmed_node(tmp_path):
    start(tmp_path)
    invoke(tmp_path, *node_args("step", 0))
    before = sql_state(tmp_path)
    failed = invoke(
        tmp_path,
        *node_args("resume", 1),
        "--fault",
        "after-commit",
        "--fault-node",
        "retrieve",
        expected=1,
    )
    assert failed["error"] == {"code": "invalid_input"}
    assert sql_state(tmp_path) == before


def test_invalid_cli_and_corrupt_database_do_not_echo_inputs_or_paths(tmp_path):
    failed = invoke(tmp_path, "inspect", "--run-id", "private-input-fixture", expected=1)
    assert failed == {
        "contract_version": "workflow-checkpoint-v1",
        "ok": False,
        "error": {"code": "invalid_input"},
        "performed": [],
    }
    assert not database(tmp_path).exists()
    database(tmp_path).parent.mkdir()
    database(tmp_path).write_bytes(b"private-input-fixture")
    failed = invoke(tmp_path, "start", "--run-id", RUN_ID, "--case", "normal", expected=1)
    assert failed["error"] == {"code": "checkpoint_invalid"}
    assert "private-input-fixture" not in json.dumps(failed)
    assert str(tmp_path) not in json.dumps(failed)


def test_closed_stdout_has_no_traceback_and_restart_finds_committed_node(tmp_path):
    start(tmp_path)
    with process(tmp_path, node_args("step", 0), barrier=True) as child:
        assert line(child.stderr) == b"ready\n"
        child.stdout.close()
        child.stdin.write(b"x")
        child.stdin.flush()
        assert child.wait(timeout=5) == 1
        assert child.stderr.read() == b""
    recovered = inspect(tmp_path)
    assert recovered["run"]["revision"] == 1
    assert len(recovered["checkpoints"]) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE checkpoints SET output_json=CAST(X'80' AS TEXT)",
        "UPDATE metadata SET workflow_version=CAST(X'80' AS TEXT)",
        "UPDATE runs SET corpus_revision=CAST(X'80' AS TEXT)",
    ],
)
def test_raw_invalid_utf8_sqlite_text_rejected_before_json_decode(tmp_path, mutation):
    start(tmp_path)
    invoke(tmp_path, *node_args("step", 0))
    with sqlite3.connect(database(tmp_path)) as connection:
        connection.execute(mutation)
    before = database(tmp_path).read_bytes()
    commands = [
        ["inspect", "--run-id", RUN_ID],
        node_args("resume", 1),
        ["start", "--run-id", "22222222-2222-4222-8222-222222222222", "--case", "normal"],
    ]
    for arguments in commands:
        failed = invoke(tmp_path, *arguments, expected=1)
        assert failed["error"] == {"code": "checkpoint_invalid"}
        assert failed["performed"] == []
        assert database(tmp_path).read_bytes() == before
