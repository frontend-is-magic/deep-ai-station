"""Protect independent loop evidence from plausible but false success reports."""

import copy
import importlib
import json
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("verify_agent_loop_lab")


def encoded(report):
    return json.dumps(report, ensure_ascii=False).encode() + b"\n"


@pytest.mark.parametrize(
    "damage", ["boolean-count", "premature-finish", "rewritten-history", "diagnostic"]
)
def test_complete_report_requires_real_evidence_and_exact_types(verifier, damage):
    expected = verifier.rows[4]["report"]  # Actual read, but no budget remains for finish.
    altered = copy.deepcopy(expected)
    stderr = b""
    if damage == "boolean-count":
        altered["model_calls"] = False
    elif damage == "premature-finish":
        altered["stop_reason"] = "completed"
        altered["result"] = altered["final_state"]["read_documents"][0]
    elif damage == "rewritten-history":
        altered["steps"][0]["before"] = altered["final_state"]
    else:
        stderr = b"private-diagnostic\n"
    with pytest.raises(AssertionError):
        verifier.check_report(0, encoded(altered), stderr, expected)
    assert verifier.check_report(0, encoded(expected), b"", expected) == expected


def test_negative_control_requires_both_rejection_and_public_execution_error(verifier):
    execution_error = verifier.errors["execution_failed"]["report"]
    verifier.assert_negative_control((1, encoded(execution_error), b""))
    # A successfully completed report means the core incorrectly trusted the bad
    # policy; a syntax traceback says nothing about the read-before-finish rule.
    for actual in (
        (0, encoded(verifier.rows[0]["report"]), b""),
        (1, b"", b"SyntaxError: unrelated failure\n"),
        (1, encoded(verifier.errors["assets_invalid"]["report"]), b""),
    ):
        with pytest.raises(AssertionError):
            verifier.assert_negative_control(actual)


@pytest.mark.parametrize("damage", [None, "extra-member", "symlink", "changed-original"])
def test_zip_identity_is_verified_before_any_code_runs(verifier, tmp_path, damage):
    entries = {name: b"fixed placeholder" for name in verifier.MEMBERS}
    entries["manifest.json"] = encoded(
        {
            "id": "agent-loop",
            "version": "agent-loop-v1",
            "lessons": ["agent-agent-loop"],
            "languages": ["python"],
        }
    )
    fixture = copy.deepcopy(verifier.assets)
    if damage == "changed-original":
        fixture["documents"][0]["body"] = "different teaching source"
    entries["fixtures.json"] = encoded(fixture)
    if damage == "extra-member":
        entries["../unexpected.py"] = b"not allowed"
    archive = tmp_path / "lab.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, value in entries.items():
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = (
                0o120777 if damage == "symlink" and name == "tools.py" else 0o100644
            ) << 16
            bundle.writestr(entry, value)
    package = tmp_path / "package"
    if damage is None:
        verifier.extract(package, archive)
        assert {path.name for path in package.iterdir()} == verifier.MEMBERS
    else:
        with pytest.raises(AssertionError):
            verifier.extract(package, archive)
    assert not (tmp_path / "unexpected.py").exists()
