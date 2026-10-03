"""Actual rankings and independent interval metrics, without strategy-winner claims."""

from copy import deepcopy

import pytest

from chunking import chunk_sources
from loader import LabError, load_assets
from retrieval import case_metrics, evaluate, retrieve, tokens


def block(start=0, end=10, source="one", text="API 超时", revision="r1"):
    return {
        "source_id": source,
        "source_revision": revision,
        "start": start,
        "end": end,
        "text": text,
        "header_path": [],
    }


def gold(gold_id="fact", start=2, end=6, source="one", revision="r1"):
    return {
        "gold_id": gold_id,
        "source_id": source,
        "source_revision": revision,
        "start": start,
        "end": end,
    }


def test_existing_course_token_semantics_and_term_limit():
    assert tokens("如何 API operation_id 超时预算 a 中") == {
        "api",
        "operation_id",
        "超时预算",
        "超时",
        "时预",
        "预算",
        "中",
    }
    words = " ".join(f"word{i:03}" for i in range(70))
    assert tokens(words) == {f"word{i:03}" for i in range(64)}
    assert tokens("a 😀") == set()


def test_same_score_ties_use_source_order_then_start_end_and_no_zero_result():
    chunks = [
        block(20, 40, source="first"),
        block(10, 40, source="first"),
        block(10, 30, source="first"),
        block(source="second"),
        block(source="third", text="nothing"),
    ]
    result = retrieve("API API", chunks, 5)
    assert [(r["source_id"], r["start"], r["end"]) for r in result] == [
        ("first", 10, 30),
        ("first", 10, 40),
        ("first", 20, 40),
        ("second", 0, 10),
    ]
    assert all(r["score"] == 1 and r["matched_terms"] == ["api"] for r in result)
    result[0]["header_path"].append("mutation")
    assert all(c["header_path"] == [] for c in chunks)
    assert retrieve("zzzz unmatched", chunks, 2) == []


def test_heading_path_does_not_artificially_increase_score():
    chunk = block(text="body only")
    chunk["header_path"] = ["API"]
    assert retrieve("API", [chunk], 2) == []


@pytest.mark.parametrize("top_k", [0, 6, True, 2.0])
def test_bad_top_k_rejected(top_k):
    with pytest.raises(LabError, match="^invalid_input$"):
        retrieve("API", [], top_k)


def test_duplicate_overlap_is_one_evidence_but_two_relevant_blocks():
    metrics = case_metrics([block(0, 8), block(1, 9)], [gold()], 2)
    assert metrics == {
        "evidence_recall_at_k": 1.0,
        "chunk_precision_at_k": 1.0,
        "first_evidence_reciprocal_rank": 1.0,
        "no_result_accuracy": None,
    }


def test_partial_spans_cannot_be_stitched_into_one_complete_hit():
    metrics = case_metrics([block(0, 4), block(4, 10)], [gold()], 2)
    assert metrics["evidence_recall_at_k"] == 0
    assert metrics["chunk_precision_at_k"] == 0


def test_conflict_requires_both_sides_and_fixed_k_denominator():
    metrics = case_metrics([block(0, 8)], [gold("a"), gold("b", 9, 12)], 3)
    assert metrics["evidence_recall_at_k"] == 0.5
    assert metrics["chunk_precision_at_k"] == pytest.approx(1 / 3)
    assert metrics["first_evidence_reciprocal_rank"] == 1


@pytest.mark.parametrize("chunk", [block(source="wrong"), block(revision="old")])
def test_same_coordinates_do_not_cover_wrong_source_or_version(chunk):
    assert case_metrics([chunk], [gold()], 1)["evidence_recall_at_k"] == 0


def test_first_complete_hit_rank_and_negative_metrics_are_separate():
    metrics = case_metrics([block(0, 1), block(0, 10)], [gold()], 2)
    assert metrics["first_evidence_reciprocal_rank"] == 0.5
    assert case_metrics([], [], 2) == {
        "evidence_recall_at_k": None,
        "chunk_precision_at_k": None,
        "first_evidence_reciprocal_rank": None,
        "no_result_accuracy": 1.0,
    }
    assert case_metrics([block()], [], 2)["no_result_accuracy"] == 0.0


def test_actual_six_cases_keep_both_strategies_failure_examples_and_costs():
    corpus, dataset = load_assets()
    before = deepcopy((corpus, dataset))
    report = evaluate(corpus, dataset)
    assert (corpus, dataset) == before
    assert len(report["cases"]) == 6
    assert [case["baseline"]["metrics"]["evidence_recall_at_k"] for case in report["cases"]] == [
        1,
        1,
        1,
        1,
        0,
        None,
    ]
    assert [case["candidate"]["metrics"]["evidence_recall_at_k"] for case in report["cases"]] == [
        1,
        0,
        1,
        1,
        1,
        None,
    ]
    assert report["summary"]["baseline"]["indexed_chunk_count"] == 12
    assert report["summary"]["baseline"]["indexed_codepoints"] == 409
    assert report["summary"]["candidate"]["indexed_chunk_count"] == 7
    assert report["summary"]["candidate"]["indexed_codepoints"] == 473
    for side in ("baseline", "candidate"):
        summary = report["summary"][side]
        assert summary["positive_case_count"] == 5 and summary["negative_case_count"] == 1
        assert summary["evidence_recall_at_k"] == 0.8
        assert summary["no_result_accuracy"] == 1
        assert summary["retrieved_codepoints_total"] == sum(
            len(r["text"]) for c in report["cases"] for r in c[side]["results"]
        )


def test_evaluate_computes_metrics_from_supplied_gold_instead_of_canned_scores():
    corpus, dataset = load_assets()
    dataset["cases"][0]["gold"][0]["start"] = 1000
    dataset["cases"][0]["gold"][0]["end"] = 1001
    report = evaluate(corpus, dataset)
    assert report["cases"][0]["baseline"]["metrics"]["evidence_recall_at_k"] == 0
    assert report["summary"]["baseline"]["evidence_recall_at_k"] == 0.6
    # CLI loads/pins the original assets; this isolated pure-function input is not a CLI override.


def test_changed_configuration_runs_real_windows_and_can_return_duplicate_evidence():
    corpus, dataset = load_assets()
    report = evaluate(corpus, dataset, size=34, overlap=16, top_k=5)
    indexed = chunk_sources(corpus, "window", size=34, overlap=16)
    assert report["summary"]["candidate"]["indexed_chunk_count"] == len(indexed)
    case = report["cases"][3]
    span = case["gold"][0]
    containing = [
        r
        for r in case["candidate"]["results"]
        if r["source_id"] == span["source_id"]
        and r["start"] <= span["start"]
        and r["end"] >= span["end"]
    ]
    assert len(containing) >= 2
    assert case["candidate"]["metrics"]["evidence_recall_at_k"] == 1
