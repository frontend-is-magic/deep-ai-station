"""Exercise only the verifier's false-pass and bounded-cleanup boundaries."""

import importlib
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = r"""
import os
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
if MODE == "duplicate":
    print('{"ok":true,"ok":false}', flush=True)
    raise SystemExit(0)
if MODE == "oversized":
    os.write(1, b'x' * 300000)
else:
    print('{"ok":true}', flush=True)
if MODE == "nonzero":
    raise SystemExit(1)
if MODE == "closed_pipes":
    os.close(1)
    os.close(2)
while True:
    time.sleep(0.1)
"""


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("verify_chunking_lab")


@pytest.mark.parametrize("mode", ["stalled", "closed_pipes", "duplicate", "oversized", "nonzero"])
def test_unconfirmed_exit_or_invalid_output_cannot_pass_and_child_is_reaped(
    verifier, tmp_path, mode
):
    helper = tmp_path / "fixed_child.py"
    helper.write_text("MODE = " + repr(mode) + "\n" + HELPER)
    child = verifier.Child(
        [sys.executable, "-s", "-E", "-B", str(helper)],
        tmp_path,
        {"PATH": os.defpath, "HOME": str(tmp_path)},
        timeout=0.5,
        grace=0.05,
    )
    with pytest.raises((AssertionError, ValueError, TimeoutError, subprocess.TimeoutExpired)):
        with child:
            verifier.report(child)
    assert child.reaped and child.process.returncode is not None
    if mode in {"stalled", "closed_pipes"}:
        assert child.process.returncode == -signal.SIGKILL
    with pytest.raises(ProcessLookupError):
        os.kill(child.process.pid, 0)


def test_nonfinite_and_surrogate_json_cannot_become_oracle_evidence(verifier):
    for raw in (b'{"value":1e999}', b'{"value":NaN}', b'{"value":"\\ud800"}'):
        with pytest.raises((ValueError, UnicodeError)):
            verifier.decode(raw)


def test_metrics_require_whole_versioned_span_deduplicate_gold_and_keep_k_denominator(verifier):
    gold = [
        {
            "gold_id": "first",
            "source_id": "fixed",
            "source_revision": "rev1",
            "start": 10,
            "end": 20,
        }
    ]
    left = {"source_id": "fixed", "source_revision": "rev1", "start": 0, "end": 15}
    right = {**left, "start": 15, "end": 30}
    assert verifier.metrics([left, right], gold, 2)["evidence_recall_at_k"] == 0
    full = {**left, "end": 20}
    assert verifier.metrics([full, full], gold, 5) == {
        "evidence_recall_at_k": 1.0,
        "chunk_precision_at_k": 0.4,
        "first_evidence_reciprocal_rank": 1.0,
        "no_result_accuracy": None,
    }
    assert (
        verifier.metrics([{**full, "source_revision": "old"}], gold, 1)["evidence_recall_at_k"] == 0
    )
    assert verifier.metrics([], [], 5) == {
        "evidence_recall_at_k": None,
        "chunk_precision_at_k": None,
        "first_evidence_reciprocal_rank": None,
        "no_result_accuracy": 1.0,
    }
