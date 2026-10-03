import sqlite3

import pytest

import repository
from repository import Store, execute
from workflow import LabError, canonical, checkpoint_hash

RUN_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "checkpoints.sqlite3")


def start(store, case="normal"):
    return execute("start", RUN_ID, case_id=case, store=store)


def inspect(store):
    return execute("inspect", RUN_ID, store=store)


def mutate(store, sql, parameters=()):
    with sqlite3.connect(store.path) as connection:
        connection.execute(sql, parameters)


@pytest.mark.parametrize("case", ["normal", "empty", "conflict"])
def test_resume_reuses_confirmed_nodes_and_terminal_has_no_writes(store, case, monkeypatch):
    start(store, case)
    first = execute("step", RUN_ID, expected_revision=0, store=store)
    assert first["performed"] == ["retrieve"]
    assert first["reused"] == []
    calls = []
    original = repository.execute_node

    def observed(node, *args):
        calls.append(node)
        return original(node, *args)

    monkeypatch.setattr(repository, "execute_node", observed)
    final = execute("resume", RUN_ID, expected_revision=1, store=store)
    assert calls == ["draft", "validate"]
    assert final["performed"] == ["draft", "validate"]
    assert final["reused"] == ["retrieve"]
    before = store.path.read_bytes()
    again = execute("resume", RUN_ID, expected_revision=3, store=store)
    assert again["performed"] == []
    assert again["reused"] == ["retrieve", "draft", "validate"]
    assert again["result"] == final["result"]
    assert store.path.read_bytes() == before
    with pytest.raises(LabError, match="revision_conflict"):
        execute("resume", RUN_ID, expected_revision=2, store=store)
    assert store.path.read_bytes() == before


def test_duplicate_start_never_overwrites_intent(store):
    start(store)
    before = store.path.read_bytes()
    with pytest.raises(LabError, match="run_exists"):
        start(store, "conflict")
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("command", ["inspect", "step", "resume"])
def test_missing_database_is_not_created(store, command):
    options = {"expected_revision": 0} if command != "inspect" else {}
    with pytest.raises(LabError, match="not_found"):
        execute(command, RUN_ID, store=store, **options)
    assert not store.path.exists()


def test_unknown_run_does_not_add_rows(store):
    start(store)
    before = store.path.read_bytes()
    with pytest.raises(LabError, match="not_found"):
        execute("resume", OTHER_ID, expected_revision=0, store=store)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize(
    "sql,parameters,error",
    [
        ("PRAGMA user_version=2", (), "unsupported_schema"),
        ("UPDATE metadata SET workflow_version='future'", (), "incompatible_version"),
        ("UPDATE runs SET corpus_revision='future'", (), "incompatible_version"),
        ("UPDATE metadata SET corpus_sha256=?", ("0" * 64,), "incompatible_version"),
        (
            "UPDATE checkpoints SET output_json=?",
            ('{"query":"private-input-fixture"}',),
            "checkpoint_invalid",
        ),
        ("UPDATE checkpoints SET output_json=?", ('{"query":1,"query":2}',), "checkpoint_invalid"),
        ("UPDATE checkpoints SET output_json=?", ('{"value":1e999}',), "checkpoint_invalid"),
        ("UPDATE checkpoints SET state_hash=?", ("0" * 64,), "checkpoint_invalid"),
        ("DELETE FROM checkpoints", (), "checkpoint_invalid"),
        ("CREATE TABLE unexpected (data TEXT)", (), "checkpoint_invalid"),
        (
            "CREATE TRIGGER unexpected AFTER UPDATE ON runs BEGIN SELECT 1; END",
            (),
            "checkpoint_invalid",
        ),
    ],
)
def test_corruption_or_incompatible_version_rejected_without_application_write(
    store, sql, parameters, error
):
    start(store)
    execute("step", RUN_ID, expected_revision=0, store=store)
    mutate(store, sql, parameters)
    before = store.path.read_bytes()
    for command, args in [
        ("inspect", {}),
        ("resume", {"expected_revision": 1}),
        ("start", {"case_id": "normal"}),
    ]:
        with pytest.raises(LabError, match=error):
            execute(command, OTHER_ID if command == "start" else RUN_ID, store=store, **args)
        assert store.path.read_bytes() == before


def test_rehashed_valid_json_wrong_semantics_is_still_rejected(store):
    start(store)
    snapshot = execute("step", RUN_ID, expected_revision=0, store=store)
    output = snapshot["checkpoints"][0]["output"]
    output["read_documents"] = []
    state_hash = checkpoint_hash(snapshot["run"], 1, "retrieve", output, None)
    mutate(
        store, "UPDATE checkpoints SET output_json=?, state_hash=?", (canonical(output), state_hash)
    )
    mutate(store, "UPDATE runs SET last_hash=?", (state_hash,))
    before = store.path.read_bytes()
    with pytest.raises(LabError, match="checkpoint_invalid"):
        inspect(store)
    assert store.path.read_bytes() == before


def test_physical_corruption_is_not_initialized(store):
    store.path.write_bytes(b"private-input-fixture, not a database")
    before = store.path.read_bytes()
    with pytest.raises(LabError, match="checkpoint_invalid"):
        start(store)
    assert store.path.read_bytes() == before


def test_existing_wal_mode_is_not_switched(store):
    start(store)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    before = store.path.read_bytes()
    with pytest.raises(LabError, match="checkpoint_invalid"):
        inspect(store)
    assert store.path.read_bytes() == before
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_precommit_failure_rolls_back_all_node_writes(store):
    start(store)

    def failure(_fault, _node):
        raise RuntimeError("private-input-fixture")

    store.fault_hook = failure
    with pytest.raises(LabError, match="storage_failure") as error:
        execute(
            "step",
            RUN_ID,
            expected_revision=0,
            fault="before-commit",
            fault_node="retrieve",
            store=store,
        )
    assert error.value.performed == []
    snapshot = inspect(store)
    assert snapshot["run"]["revision"] == 0
    assert snapshot["checkpoints"] == []


@pytest.mark.parametrize("did_commit", [False, True])
def test_commit_acknowledgement_failure_is_unconfirmed_and_never_retried(store, did_commit):
    start(store)
    commits = []

    class UncertainConnection(sqlite3.Connection):
        def commit(self):
            commits.append(1)
            if did_commit:
                super().commit()
            raise sqlite3.OperationalError("private-input-fixture")

    def connect(*args, **kwargs):
        return sqlite3.connect(*args, factory=UncertainConnection, **kwargs)

    uncertain = Store(store.path, connect=connect)
    with pytest.raises(LabError, match="result_unconfirmed") as error:
        execute("resume", RUN_ID, expected_revision=0, store=uncertain)
    assert error.value.performed == []
    assert commits == [1]
    recovered = inspect(store)
    assert recovered["run"]["revision"] == int(did_commit)
    assert len(recovered["checkpoints"]) == int(did_commit)


def test_resume_error_preserves_only_previously_acknowledged_commits(store):
    start(store)

    def fail(_fault, _node):
        raise sqlite3.OperationalError("private-input-fixture")

    store.fault_hook = fail
    with pytest.raises(LabError, match="storage_failure") as error:
        execute(
            "resume",
            RUN_ID,
            expected_revision=0,
            fault="before-commit",
            fault_node="draft",
            store=store,
        )
    assert error.value.performed == ["retrieve"]
    assert inspect(store)["run"]["revision"] == 1


def test_node_failure_does_not_create_running_marker(store, monkeypatch):
    start(store)
    before = store.path.read_bytes()

    def fail(*_args):
        raise RuntimeError("private-input-fixture")

    monkeypatch.setattr(repository, "execute_node", fail)
    with pytest.raises(LabError, match="storage_failure"):
        execute("step", RUN_ID, expected_revision=0, store=store)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize(
    "parameters",
    [
        {"run_id": "not-a-run"},
        {"run_id": RUN_ID.upper().replace("1", "A", 1)},
        {"expected_revision": True},
        {"expected_revision": 1.0},
        {"fault": "after-commit"},
        {"fault": "before-commit", "fault_node": "draft"},
    ],
)
def test_invalid_execution_parameters_do_not_create_database(store, parameters):
    options = {"run_id": RUN_ID, "expected_revision": 0, **parameters}
    with pytest.raises(LabError, match="invalid_input"):
        execute("step", store=store, **options)
    assert not store.path.exists()


def test_failure_after_commit_is_unconfirmed_and_durable(store):
    start(store)

    def fail(_fault, _node):
        raise RuntimeError("private-input-fixture")

    store.fault_hook = fail
    with pytest.raises(LabError, match="result_unconfirmed") as error:
        execute(
            "step",
            RUN_ID,
            expected_revision=0,
            fault="after-commit",
            fault_node="retrieve",
            store=store,
        )
    assert error.value.performed == []
    assert inspect(store)["run"]["revision"] == 1
