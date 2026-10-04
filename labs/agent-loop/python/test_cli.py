import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ARGUMENTS = ["--case", "found", "--policy", "evidence_first", "--max-steps", "3"]


@pytest.fixture
def package(tmp_path):
    destination = tmp_path / "package"
    destination.mkdir()
    source = Path(__file__).parent
    for name in ("agent_loop.py", "engine.py", "policies.py", "tools.py"):
        shutil.copyfile(source / name, destination / name)
    adjacent = source / "fixtures.json"
    assets = adjacent if adjacent.is_file() else source.parent / "shared" / "fixtures.json"
    shutil.copyfile(assets, destination / "fixtures.json")
    return destination


def invoke(package, arguments, *, cwd=None, seed="1"):
    environment = {
        "PYTHONPATH": str(package),
        "PYTHONHASHSEED": seed,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    }
    result = subprocess.run(
        [sys.executable, "-s", "-B", "-m", "agent_loop", *arguments],
        cwd=cwd or package,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.stderr == b""
    assert result.stdout.endswith(b"\n")
    assert result.stdout.count(b"\n") == 1 or arguments == ["--help"]
    return result


def assert_error(result, code, message, exit_code):
    assert result.returncode == exit_code
    assert json.loads(result.stdout) == {
        "contract_version": "agent-loop-v1",
        "ok": False,
        "error": {"code": code, "message": message},
    }
    assert b"private-input-fixture" not in result.stdout


@pytest.mark.parametrize(
    ("case", "policy", "budget", "reason", "decisions", "dispatches"),
    [
        ("found", "evidence_first", "3", "completed", 3, 2),
        ("found", "evidence_first", "1", "step_limit", 1, 1),
        ("empty", "evidence_first", "3", "no_evidence", 2, 1),
        ("found", "repeat_search", "5", "step_limit", 5, 5),
        ("found", "evidence_first", "2", "step_limit", 2, 2),
    ],
)
def test_real_cli_baselines(package, case, policy, budget, reason, decisions, dispatches):
    result = invoke(package, ["--policy", policy, "--max-steps", budget, "--case", case])
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert set(report) == {
        "contract_version",
        "ok",
        "lesson_id",
        "asset_version",
        "assets_sha256",
        "case_id",
        "policy",
        "decision_source",
        "read_only",
        "model_calls",
        "max_steps",
        "decision_count",
        "tool_dispatch_count",
        "stop_reason",
        "steps",
        "final_state",
        "result",
    }
    assert report["contract_version"] == "agent-loop-v1"
    assert report["ok"] is True
    assert report["lesson_id"] == "agent-agent-loop"
    assert report["asset_version"] == "agent-loop-corpus-v1"
    assert (
        report["assets_sha256"]
        == "45a01bfcdff94b3d96826ba2f9eab7a750a51ea3e362939d8c2a0e1d1c06007d"
    )
    assert report["case_id"] == case and report["policy"] == policy
    assert report["decision_source"] == "deterministic_policy"
    assert report["read_only"] is True and report["model_calls"] == 0
    assert (
        report["max_steps"],
        report["stop_reason"],
        report["decision_count"],
        report["tool_dispatch_count"],
    ) == (int(budget), reason, decisions, dispatches)
    assert len(report["steps"]) == decisions
    assert report["final_state"] == report["steps"][-1]["after"]
    assert (report["result"] is not None) == (reason == "completed")


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--help", "private-input-fixture"],
        ["--case=found", *ARGUMENTS[2:]],
        [*ARGUMENTS, "--case", "empty"],
        [*ARGUMENTS[:-1], "01"],
        [*ARGUMENTS[:-1], "1.0"],
        [*ARGUMENTS[:-1], " 3"],
        ["--case", "private-input-fixture", *ARGUMENTS[2:]],
    ],
)
def test_invalid_cli_precedes_invalid_assets(package, arguments):
    (package / "fixtures.json").write_text("private-input-fixture")
    assert_error(invoke(package, arguments), "invalid_input", "循环实验参数无效", 2)


def test_exact_help_is_only_non_json_success(package):
    result = invoke(package, ["--help"])
    assert result.returncode == 0
    assert "用法：python -m agent_loop" in result.stdout.decode()


@pytest.mark.parametrize(
    "damage", ["json", "duplicate", "nonfinite", "utf8", "directory", "version"]
)
def test_invalid_adjacent_assets_never_fall_back(package, damage):
    assets_path = package / "fixtures.json"
    shared = package.parent / "shared"
    shared.mkdir()
    shutil.copyfile(assets_path, shared / "fixtures.json")
    if damage == "directory":
        assets_path.unlink()
        assets_path.mkdir()
    elif damage == "version":
        asset = json.loads(assets_path.read_text())
        asset["asset_version"] = "private-input-fixture"
        assets_path.write_text(json.dumps(asset))
    else:
        contents = {
            "json": b"private-input-fixture",
            "duplicate": b'{"asset_version":"private-input-fixture","asset_version":"agent-loop-corpus-v1"}',
            "nonfinite": b'{"private-input-fixture":1e999}',
            "utf8": b"\x80",
        }
        assets_path.write_bytes(contents[damage])
    assert_error(invoke(package, ARGUMENTS), "assets_invalid", "循环实验资料无效", 1)


def test_fixed_repository_fallback_and_cwd_hashseed_determinism(package, tmp_path):
    original = invoke(package, ARGUMENTS)
    shared = package.parent / "shared"
    shared.mkdir()
    (package / "fixtures.json").rename(shared / "fixtures.json")
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    (elsewhere / "fixtures.json").write_text("private-input-fixture")
    alternate = invoke(package, ARGUMENTS, cwd=elsewhere, seed="73")
    assert alternate.returncode == original.returncode == 0
    assert alternate.stdout == original.stdout


def test_legal_asset_change_uses_actual_query_and_fingerprint(package):
    path = package / "fixtures.json"
    asset = json.loads(path.read_text())
    asset["cases"][0]["query"] = "工具"
    path.write_text(json.dumps(asset, ensure_ascii=False))
    result = invoke(package, ARGUMENTS)
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert (
        report["assets_sha256"]
        == "5797dc5bd4255438682f1ea8778f2a2a31caa53dcb7e6ef2ccdb5b47a3cbc205"
    )
    assert report["steps"][0]["observation"]["found_ids"] == ["tool-guide"]
    assert report["result"]["id"] == "tool-guide"
    assert (
        report["result"]["body"]
        == "搜索结果只提供资料标识。读取工具返回原文后，才拥有本次已读证据。"
    )


def test_real_bad_policy_process_is_fixed_execution_error_not_partial_success(package):
    path = package / "policies.py"
    source = path.read_text()
    original = 'return {"action": "read", "document_id": observation["found_ids"][0]}'
    assert source.count(original) == 1
    path.write_text(source.replace(original, 'return {"action": "finish", "outcome": "completed"}'))
    result = invoke(package, ARGUMENTS)
    assert_error(result, "execution_failed", "循环实验未产生完整报告", 1)
