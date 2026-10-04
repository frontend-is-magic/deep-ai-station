"""Real fixed-code subprocesses; all exits are bounded and no services are launched."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import app
from loader import PINS, canonical

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "app.py"
COMPARE = ["compare", "--candidate", "unsafe-candidate", "--split", "acceptance"]


def run(*arguments, script=SCRIPT, cwd=None, seed="1"):
    # No inherited credentials; these child processes only execute the fixed bundled CLI.
    environment = {"PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
    return subprocess.run(
        [sys.executable, str(script), *arguments],
        capture_output=True,
        cwd=cwd,
        env=environment,
        timeout=10,
        check=False,
    )


def decoded(result, code):
    assert result.returncode == code
    assert result.stderr == b""
    value = json.loads(result.stdout)
    assert result.stdout == (canonical(value) + "\n").encode("utf-8")
    return value


@pytest.mark.parametrize(
    ("candidate", "split", "code"),
    [
        ("baseline", "dev", 0),
        ("unsafe-candidate", "dev", 0),
        ("fixed-candidate", "dev", 0),
        ("baseline", "acceptance", 0),
        ("unsafe-candidate", "acceptance", 2),
        ("fixed-candidate", "acceptance", 0),
    ],
)
def test_real_cli_gate_exit_and_report(candidate, split, code):
    report = decoded(run("compare", "--candidate", candidate, "--split", split), code)
    assert report["command"] == "compare"
    assert report["split"] == split
    assert report["candidate"]["profile"] == candidate
    assert report["gate"]["passed"] is (code == 0)
    assert report["model_calls"] == 0
    assert report["read_only"] is True
    assert report["measurement_mode"] == "synthetic-output-replay"
    assert "error" not in report


@pytest.mark.parametrize(
    ("profile", "case_id", "failed_layer"),
    [
        ("baseline", "accept-api", "parse"),
        ("baseline", "accept-tools", "schema"),
        ("baseline", "accept-evidence", "evidence"),
        ("unsafe-candidate", "accept-found-unread", "evidence"),
    ],
)
def test_failed_case_explanation_is_successful_command(profile, case_id, failed_layer):
    report = decoded(run("explain", "--profile", profile, "--case", case_id), 0)
    assert report["case"]["id"] == case_id
    assert report["result"][failed_layer]["status"] == "failed"
    assert report["result"]["passed"] is False
    assert report["profile"] == profile
    if case_id == "accept-found-unread":
        assert report["loaded_sources"] == []
        assert report["case"]["found_ids"] == ["api"]


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["compare"],
        ["--help", "extra"],
        ["compare", "--help", "--unknown"],
        ["unknown", "--help"],
        ["explain", "--help", "--case", "dev-api"],
        ["compare", "--candidate=baseline", "--split", "dev"],
        ["compare", "--candidate", "baseline", "--candidate", "baseline"],
        ["compare", "--candidate", "baseline", "--split", "dev", "--split", "dev"],
        ["compare", "--candidate", "baseline", "--split", "private"],
        ["compare", "--candidate", "url://private", "--split", "dev"],
        ["compare", "--candidate", "baseline", "split", "dev"],
        ["compare", "--profile", "baseline", "--split", "dev"],
        ["explain", "--profile", "baseline", "--case", "unknown"],
        ["explain", "--profile", "baseline", "--case", "../corpus.json"],
        ["explain", "--profile", "baseline", "--case", "dev-api", "positional"],
    ],
)
def test_invalid_cli_has_fixed_error_no_echo(arguments):
    assert decoded(run(*arguments), 1) == {
        "experiment_version": "output-regression-v1",
        "error": {"code": "invalid_input"},
    }


@pytest.mark.parametrize("arguments", [["--help"], ["compare", "--help"], ["explain", "--help"]])
def test_only_exact_help_shapes_succeed(arguments):
    result = run(*arguments)
    assert result.returncode == 0
    assert result.stderr == b""
    assert b"synthetic" in result.stdout
    assert b"compare" in result.stdout


def test_output_is_stable_across_cwd_hash_seed_and_flag_order(tmp_path):
    before = {name: (ROOT / name).read_bytes() for name in PINS}
    first = run(*COMPARE)
    second = run(
        "compare",
        "--split",
        "acceptance",
        "--candidate",
        "unsafe-candidate",
        cwd=tmp_path,
        seed="4242",
    )
    decoded(first, 2)
    decoded(second, 2)
    assert first.stdout == second.stdout
    assert list(tmp_path.iterdir()) == []
    assert before == {name: (ROOT / name).read_bytes() for name in PINS}


@pytest.fixture
def package(tmp_path):
    for name in ("app.py", "loader.py", "evaluator.py", *PINS):
        shutil.copyfile(ROOT / name, tmp_path / name)
    return tmp_path


def test_cli_validation_precedes_missing_assets_and_help_does_not_load(package):
    (package / "corpus.json").unlink()
    script = package / "app.py"
    assert decoded(run("wrong-private-input", script=script), 1)["error"]["code"] == "invalid_input"
    assert run("--help", script=script).returncode == 0
    assert decoded(run(*COMPARE, script=script), 1)["error"]["code"] == "assets_unavailable"


def test_acceptance_does_not_skip_corrupt_dev_or_unselected_profile(package):
    path = package / "profiles.json"
    profiles = json.loads(path.read_bytes())
    profiles["profiles"][2]["outputs"].pop(0)  # fixed-candidate dev case, neither selected.
    path.write_text(json.dumps(profiles), encoding="utf-8")
    result = decoded(run(*COMPARE, script=package / "app.py"), 1)
    assert result == {
        "experiment_version": "output-regression-v1",
        "error": {"code": "assets_invalid"},
    }
    assert "cases" not in result


def test_explain_also_checks_all_assets_before_selecting_one_case(package):
    path = package / "cases.json"
    cases = json.loads(path.read_bytes())
    cases["cases"][-1]["read_ids"] = ["unknown"]
    path.write_text(json.dumps(cases), encoding="utf-8")
    report = decoded(
        run("explain", "--profile", "baseline", "--case", "dev-api", script=package / "app.py"), 1
    )
    assert report["error"]["code"] == "assets_invalid"


def test_report_receipts_are_original_body_bytes():
    report = decoded(run(*COMPARE), 2)
    sources = {doc["id"]: doc for doc in json.loads((ROOT / "corpus.json").read_bytes())}
    for case in report["cases"]:
        assert case["loaded_source_receipts"] == [
            {
                "document_id": key,
                "body_sha256": hashlib.sha256(sources[key]["body"].encode("utf-8")).hexdigest(),
            }
            for key in case["fixed_context"]["read_ids"]
        ]


def test_unexpected_internal_failure_is_sanitized(monkeypatch):
    def failure(_arguments):
        raise RuntimeError("private-secret-path-value")

    written = []
    monkeypatch.setattr(app, "execute", failure)
    monkeypatch.setattr(app, "write_stdout", lambda text: written.append(text) or True)
    assert app.main(COMPARE) == 1
    assert json.loads(written[0]) == {
        "experiment_version": "output-regression-v1",
        "error": {"code": "experiment_failed"},
    }
    assert "private-secret" not in written[0]


def test_closed_stdout_pipe_exits_without_traceback_or_second_diagnostic():
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *COMPARE],
            stdout=write_fd,
            stderr=subprocess.PIPE,
            env={"PYTHONDONTWRITEBYTECODE": "1"},
            timeout=10,
            check=False,
        )
    finally:
        os.close(write_fd)
    assert result.returncode == 1
    assert result.stderr == b""


def test_write_stdout_handles_partial_writes_and_stops_on_error(monkeypatch):
    chunks = []

    def partial(_fd, pending):
        chunks.append(pending[:2])
        return min(2, len(pending))

    monkeypatch.setattr(app.os, "write", partial)
    assert app.write_stdout("中文")
    assert b"".join(chunks) == "中文".encode()
    calls = []

    def failed(_fd, _pending):
        calls.append(True)
        raise BrokenPipeError("private-diagnostic")

    monkeypatch.setattr(app.os, "write", failed)
    assert not app.write_stdout("error")
    assert len(calls) == 1
