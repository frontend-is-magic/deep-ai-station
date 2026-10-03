"""Real subprocess checks: fixed commands, deterministic wire output and safe failures."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import app

BASE = Path(__file__).parent
SOURCE_REVISION = "sha256:bf6a09d8e98c52294f205f7f8063fc7c6b11596cce3e70c9926ec9d9cc3cd5c7"
CLI_FILES = ("app.py", "loader.py", "chunking.py", "retrieval.py", "corpus.json", "cases.json")


def run_cli(argv, cwd, base=BASE, seed="1"):
    env = {
        "PATH": os.defpath,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": seed,
    }
    result = subprocess.run(
        [sys.executable, "-B", str(base / "app.py"), *argv],
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.stderr == b""
    assert result.stdout.endswith(b"\n")
    assert len(result.stdout.splitlines()) == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is (result.returncode == 0)
    assert (
        result.stdout
        == json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        + b"\n"
    )
    return result, payload


@pytest.mark.parametrize(
    "argv",
    [
        ["chunks", "--strategy", "heading"],
        ["chunks", "--strategy=window"],
        ["compare"],
        ["compare", "--size=34", "--overlap=16", "--top-k=5"],
    ],
)
def test_real_commands_from_unrelated_cwd_are_deterministic_across_processes(tmp_path, argv):
    first, payload = run_cli(argv, tmp_path, seed="1")
    other = tmp_path / "other"
    other.mkdir()
    second, _ = run_cli(argv, other, seed="999")
    assert first.returncode == 0
    assert first.stdout == second.stdout
    assert payload["model_calls"] == 0
    assert payload["coordinate_unit"] == "unicode_codepoint"
    assert not list(other.iterdir())


def test_real_unicode_quote_matches_raw_utf8(tmp_path):
    result, payload = run_cli(
        [
            "quote",
            "--source-id",
            "tool-evidence",
            "--revision",
            SOURCE_REVISION,
            "--start",
            "172",
            "--end",
            "178",
        ],
        tmp_path,
    )
    assert result.returncode == 0 and payload["quote"] == "A😀e\u0301Z。"
    assert (payload["byte_start"], payload["byte_end"]) == (442, 454)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["private-input-fixture"],
        ["compare", "--unknown", "private-input-fixture"],
        ["compare", "--top-k", "2", "--top-k=2"],
        ["compare", "--size"],
        ["compare", "--si", "80"],
        ["compare", "80"],
        ["compare", "--help", "--unknown"],
        ["--help", "--unknown"],
        ["chunks", "--strategy", "heading", "--size", "80"],
        ["chunks"],
        ["chunks", "--strategy", "other"],
        ["quote", "--source-id", "api-guide"],
        ["compare", "--size", "31"],
        ["compare", "--size", "257"],
        ["compare", "--overlap", "17"],
        ["compare", "--top-k", "0"],
        ["compare", "--top-k", "6"],
    ],
)
def test_invalid_arguments_are_fixed_and_never_echo_input(tmp_path, argv):
    result, payload = run_cli(argv, tmp_path)
    assert result.returncode == 1
    assert payload == {
        "contract_version": "document-chunking-v1",
        "ok": False,
        "error": {"code": "invalid_input"},
    }
    assert b"private-input-fixture" not in result.stdout


@pytest.mark.parametrize(
    "value", ["-1", "+80", "080", "80.0", "8e1", "80\n", " 80", "2147483648", "9999999999999999"]
)
def test_strict_integer_lexical_boundary_in_real_process(tmp_path, value):
    _, payload = run_cli(["compare", "--size", value], tmp_path)
    assert payload["error"]["code"] == "invalid_input"


@pytest.mark.parametrize(
    "source,rev,start,end,code",
    [
        ("missing", SOURCE_REVISION, "0", "1", "source_not_found"),
        ("tool-evidence", "sha256:" + "0" * 64, "0", "1", "revision_mismatch"),
        ("tool-evidence", SOURCE_REVISION, "1", "1", "invalid_range"),
        ("tool-evidence", SOURCE_REVISION, "0", "234", "invalid_range"),
        ("tool-evidence\n", SOURCE_REVISION, "0", "1", "invalid_input"),
        ("tool-evidence", SOURCE_REVISION + "\n", "0", "1", "invalid_input"),
    ],
)
def test_real_quote_error_categories(tmp_path, source, rev, start, end, code):
    _, payload = run_cli(
        ["quote", "--source-id", source, "--revision", rev, "--start", start, "--end", end],
        tmp_path,
    )
    assert payload["error"]["code"] == code


@pytest.fixture
def standalone(tmp_path):
    folder = tmp_path / "bundle"
    folder.mkdir()
    for name in CLI_FILES:
        shutil.copyfile(BASE / name, folder / name)
    return folder


@pytest.mark.parametrize(
    "argv", [["--help"], ["compare", "--help"], ["chunks", "--help"], ["quote", "--help"]]
)
def test_exact_help_needs_no_assets_and_has_no_variable_paths(standalone, tmp_path, argv):
    (standalone / "corpus.json").unlink()
    result = subprocess.run(
        [sys.executable, "-B", str(standalone / "app.py"), *argv],
        cwd=tmp_path,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0 and result.stderr == b""
    assert result.stdout.startswith(b"Usage: python app.py")
    assert str(tmp_path).encode() not in result.stdout


@pytest.mark.parametrize(
    "raw", [b"\x80", b'{"x":1e999}', b'{"x":"\\ud800"}', b'{"x":1,"x":2}', b" " * 65537]
)
def test_real_asset_corruption_is_not_a_successful_empty_result(standalone, tmp_path, raw):
    asset = standalone / "corpus.json"
    asset.write_bytes(raw)
    before = hashlib.sha256(asset.read_bytes()).hexdigest()
    result, payload = run_cli(["compare"], tmp_path, base=standalone)
    assert result.returncode == 1 and payload["error"]["code"] == "corpus_invalid"
    assert hashlib.sha256(asset.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob("*.json"))


def test_argument_validation_precedes_missing_assets(standalone, tmp_path):
    (standalone / "corpus.json").unlink()
    _, payload = run_cli(["compare", "--size", "-1"], tmp_path, base=standalone)
    assert payload["error"]["code"] == "invalid_input"
    _, payload = run_cli(["compare"], tmp_path, base=standalone)
    assert payload["error"]["code"] == "corpus_invalid"


def test_unexpected_failure_is_fixed_without_raw_exception(monkeypatch, capfd):
    def fail(_):
        raise RuntimeError("private-input-fixture")

    monkeypatch.setattr(app, "execute", fail)
    assert app.main(["compare"]) == 1
    out, err = capfd.readouterr()
    assert not err and "private-input-fixture" not in out
    assert json.loads(out)["error"]["code"] == "experiment_failed"


def test_closed_stdout_does_not_emit_traceback_or_retry(tmp_path):
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        result = subprocess.run(
            [sys.executable, "-B", str(BASE / "app.py"), "compare"],
            cwd=tmp_path,
            stdout=write_fd,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    finally:
        os.close(write_fd)
    assert result.returncode == 1 and result.stderr == b""
