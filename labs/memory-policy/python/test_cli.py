import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import memory_policy
from loader import CONTRACT_VERSION, load_assets


@pytest.fixture
def package(tmp_path):
    root = Path(__file__).resolve().parent
    for name in ("memory_policy.py", "loader.py", "policy.py"):
        shutil.copyfile(root / name, tmp_path / name)
    for name, value in zip(("memories.json", "cases.json"), load_assets(), strict=True):
        (tmp_path / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def cli(package, *args):
    return subprocess.run(
        [sys.executable, "-B", "-m", "memory_policy", *args],
        cwd=package,
        capture_output=True,
        timeout=5,
        check=False,
    )


def result(process, code):
    assert process.returncode == code
    assert process.stderr == b""
    assert process.stdout.endswith(b"\n") and process.stdout.count(b"\n") == 1
    return json.loads(process.stdout)


def test_actual_cli_lists_and_resolves_without_modifying_sources(package):
    before = {path.name: path.read_bytes() for path in package.iterdir()}
    listed = result(cli(package, "--list"), 0)
    assert [case["id"] for case in listed["cases"]] == [
        "conflict",
        "expiry",
        "normal",
        "request-override",
        "revoked",
        "scope",
    ]
    conflict = result(cli(package, "--case", "conflict"), 0)
    assert conflict["resolution"]["status"] == "conflict"
    override = result(cli(package, "--language", "go", "--case", "conflict"), 0)
    assert override["resolution"]["source"] == "request"
    assert override["resolution"]["language"] == "go"
    assert override["records"] == conflict["records"]
    assert {path.name: path.read_bytes() for path in package.iterdir()} == before


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--help"],
        ["--list", "--case", "normal"],
        ["--case"],
        ["--case=normal"],
        ["--case", "unknown"],
        ["--case", "normal", "--case", "normal"],
        ["--language", "go"],
        ["--case", "normal", "--language", "rust"],
        ["--case", "normal", "--language", "go", "--language", "go"],
        ["normal"],
        ["--case", "normal", "--path", "private-input-fixture"],
    ],
)
def test_actual_cli_invalid_arguments_are_fixed_errors(package, args):
    assert result(cli(package, *args), 1) == {
        "contract_version": CONTRACT_VERSION,
        "error": "invalid_input",
    }


def test_explicit_language_does_not_bypass_full_asset_validation(package):
    path = package / "memories.json"
    asset = json.loads(path.read_text())
    asset["memories"][-1]["language"] = "private-input-fixture"
    path.write_text(json.dumps(asset))
    report = result(cli(package, "--case", "normal", "--language", "go"), 1)
    assert report == {"contract_version": CONTRACT_VERSION, "error": "assets_invalid"}
    assert "private-input-fixture" not in json.dumps(report)


def test_present_partial_bundle_never_falls_back_to_shared(package):
    shared = package.parent / "shared"
    shared.mkdir()
    shutil.copyfile(package / "cases.json", shared / "cases.json")
    shutil.copyfile(package / "memories.json", shared / "memories.json")
    (package / "cases.json").unlink()
    assert result(cli(package, "--list"), 1)["error"] == "assets_invalid"


def test_runtime_does_not_open_network_connections(monkeypatch):
    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("network call")

    monkeypatch.setattr(socket, "socket", unexpected_network)
    assert memory_policy.run(["--case", "scope"])["model_calls"] == 0


def test_internal_exception_is_redacted_before_output(monkeypatch, capfd):
    def failed():
        raise RuntimeError("private-input-fixture /secret/path")

    monkeypatch.setattr(memory_policy, "load_assets", failed)
    assert memory_policy.main(["--list"]) == 1
    stdout, stderr = capfd.readouterr()
    assert json.loads(stdout) == {
        "contract_version": CONTRACT_VERSION,
        "error": "experiment_failed",
    }
    assert stderr == ""


def test_real_closed_stdout_is_quiet_and_process_is_reaped(package):
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    try:
        with subprocess.Popen(
            [sys.executable, "-B", "-m", "memory_policy", "--list"],
            cwd=package,
            stdout=write_fd,
            stderr=subprocess.PIPE,
        ) as child:
            os.close(write_fd)
            write_fd = -1
            try:
                _, stderr = child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate(timeout=5)
                raise
            assert child.returncode == 1
            assert stderr == b""
    finally:
        if write_fd != -1:
            os.close(write_fd)
