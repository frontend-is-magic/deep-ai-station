import copy
import hashlib
import json
import socket
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from backend import providers, retrieval
from backend.curriculum import LESSONS
from backend.retrieval import retrieve, retrieve_scored
from backend.retrieval_evaluation import (
    DATASET_VERSION,
    DATASETS,
    RetrievalEvaluationRequest,
    _summarize,
    case_metrics,
    corpus_revision,
    evaluate_retrieval,
)


def request_data(track="agent"):
    return {
        "track": track,
        "baseline": {"strategy": "title", "top_k": 3},
        "candidate": {"strategy": "weighted", "top_k": 3},
    }


def test_case_metrics_follow_hand_calculated_rankings_and_fixed_k_denominator():
    assert case_metrics(["other", "a", "missing", "b"], ("a", "b", "c"), 3) == {
        "precision_at_k": 1 / 3,
        "recall_at_k": 1 / 3,
        "reciprocal_rank": 1 / 2,
        "no_result_accuracy": None,
    }
    assert case_metrics(["a"], ("a", "b"), 5) == {
        "precision_at_k": 1 / 5,
        "recall_at_k": 1 / 2,
        "reciprocal_rank": 1.0,
        "no_result_accuracy": None,
    }
    assert case_metrics([], ("a",), 1) == {
        "precision_at_k": 0.0,
        "recall_at_k": 0.0,
        "reciprocal_rank": 0.0,
        "no_result_accuracy": None,
    }


def test_negative_cases_do_not_enter_positive_macro_metrics():
    cases = [
        {"is_negative": False, "baseline": {"metrics": case_metrics(["x", "a"], ("a",), 4)}},
        {"is_negative": False, "baseline": {"metrics": case_metrics(["b"], ("b", "c"), 4)}},
        {"is_negative": True, "baseline": {"metrics": case_metrics([], (), 4)}},
        {"is_negative": True, "baseline": {"metrics": case_metrics(["x"], (), 4)}},
    ]
    assert _summarize(cases, "baseline") == {
        "positive_cases": 2,
        "negative_cases": 2,
        "precision_at_k": 0.25,
        "recall_at_k": 0.75,
        "mrr": 0.75,
        "no_result_accuracy": 0.5,
    }
    for case in cases[2:]:
        metric = case["baseline"]["metrics"]
        assert metric["precision_at_k"] is None
        assert metric["recall_at_k"] is None
        assert metric["reciprocal_rank"] is None


@pytest.mark.parametrize("top_k", [0, 6, -1, True, False, 1.0, 1.5, "3", None])
@pytest.mark.parametrize("configuration", ["baseline", "candidate"])
def test_request_rejects_invalid_or_coerced_top_k(configuration, top_k):
    data = request_data()
    data[configuration]["top_k"] = top_k
    with pytest.raises(ValidationError):
        RetrievalEvaluationRequest.model_validate(data)


@pytest.mark.parametrize(
    "override",
    [
        {"track": "outside"},
        {"url": "https://example.com"},
        {"corpus": []},
        {"baseline": {"strategy": "vector", "top_k": 3}},
        {"candidate": {"strategy": "weighted", "top_k": 3, "documents": []}},
    ],
)
def test_request_rejects_unknown_sources_fields_and_strategies(override):
    with pytest.raises(ValidationError):
        RetrievalEvaluationRequest.model_validate({**request_data(), **override})


@pytest.mark.parametrize("track", ["agent", "fullstack"])
def test_fixed_dataset_has_unique_queries_and_real_labels_in_its_own_track(track):
    cases = DATASETS[track]
    assert len({case.id for case in cases}) == len(cases) == 12
    assert len({case.query for case in cases}) == len(cases)
    assert sum(not case.relevant_ids for case in cases) == 2
    assert any(len(case.relevant_ids) > 1 for case in cases)
    for case in cases:
        assert len(set(case.relevant_ids)) == len(case.relevant_ids)
        assert all(LESSONS[id]["track"] == track for id in case.relevant_ids)


@pytest.mark.parametrize("track", ["agent", "fullstack"])
@pytest.mark.parametrize("top_k", [1, 5])
def test_report_exposes_ranked_evidence_and_independent_configs_at_top_k_boundaries(track, top_k):
    data = request_data(track)
    data["candidate"]["top_k"] = top_k
    result = evaluate_retrieval(RetrievalEvaluationRequest.model_validate(data))
    assert UUID(result["run_id"])
    assert result["track"] == track
    assert result["model_calls"] == 0
    assert result["dataset_version"] == DATASET_VERSION == "course-retrieval-v1"
    assert result["configurations"] == {name: data[name] for name in ("baseline", "candidate")}
    assert "不衡量生成回答质量" in result["notice"]
    for name in ("baseline", "candidate"):
        summary = result["metrics"][name]
        assert summary["positive_cases"] == 10 and summary["negative_cases"] == 2
        assert all(
            0 <= summary[key] <= 1
            for key in ("precision_at_k", "recall_at_k", "mrr", "no_result_accuracy")
        )
        for case in result["cases"]:
            relevant_ids = [item["id"] for item in case["relevant"]]
            assert case["is_negative"] == (not relevant_ids)
            assert all(item["title"] == LESSONS[item["id"]]["title"] for item in case["relevant"])
            ranked = case[name]["results"]
            assert len(ranked) <= data[name]["top_k"]
            assert [item["score"] for item in ranked] == sorted(
                (item["score"] for item in ranked), reverse=True
            )
            for item in ranked:
                assert LESSONS[item["id"]]["track"] == track
                assert item["is_relevant"] == (item["id"] in relevant_ids)
                assert item["score"] > 0 and item["matched_terms"]
                assert item["matched_terms"] == sorted(set(item["matched_terms"]))


def test_scoring_uses_each_tokens_highest_weight_and_curriculum_order_for_ties(monkeypatch):
    rows = [
        {
            "id": "first",
            "track": "agent",
            "title": "alpha",
            "objective": "alpha beta",
            "body": ["alpha beta gamma"],
        },
        {
            "id": "second",
            "track": "agent",
            "title": "alpha",
            "objective": "alpha beta",
            "body": ["alpha beta gamma"],
        },
        {
            "id": "outside",
            "track": "fullstack",
            "title": "alpha beta gamma",
            "objective": "",
            "body": [],
        },
    ]
    monkeypatch.setattr(retrieval, "LESSONS", {row["id"]: row for row in rows})
    weighted = retrieve_scored("alpha beta gamma", "agent", 3)
    title = retrieve_scored("alpha beta gamma", "agent", 3, "title")
    assert [item["lesson"]["id"] for item in weighted] == ["first", "second"]
    assert [item["score"] for item in weighted] == [7, 7]
    assert weighted[0]["matched_terms"] == ["alpha", "beta", "gamma"]
    assert [item["score"] for item in title] == [1, 1]
    assert title[0]["matched_terms"] == ["alpha"]


# Captured from the previous retrieve implementation before the scoring refactor.
@pytest.mark.parametrize(
    ("query", "track", "limit", "expected"),
    [
        (
            "怎样处理工具授权和幂等",
            "agent",
            3,
            ["agent-tool-contract", "agent-tool-safety", "agent-mcp"],
        ),
        (
            "FastAPI",
            "fullstack",
            5,
            ["fullstack-routing", "fullstack-validation", "fullstack-vercel"],
        ),
        ("SSE", "agent", 5, ["agent-streaming"]),
        (
            "版本",
            "agent",
            4,
            ["agent-chunking", "agent-state-machine", "agent-regression", "agent-deploy"],
        ),
        ("文档证据", "fullstack", 3, ["fullstack-ai-rag", "fullstack-launch"]),
        ("", None, 3, []),
        ("xyzzy-no-course-match", None, 3, []),
        ("429", None, 3, []),
        (
            "API",
            None,
            5,
            [
                "fullstack-http",
                "fullstack-routing",
                "fullstack-ai-stream",
                "agent-sandbox",
                "agent-deploy",
            ],
        ),
    ],
)
def test_existing_retrieve_preserves_ids_and_order(query, track, limit, expected):
    result = retrieve(query, track, limit)
    assert [lesson["id"] for lesson in result] == expected
    assert all(lesson is LESSONS[lesson["id"]] for lesson in result)


def test_identical_requests_have_reproducible_results_and_distinct_run_ids():
    request = RetrievalEvaluationRequest.model_validate(request_data())
    first = evaluate_retrieval(request)
    second = evaluate_retrieval(request)
    assert first.pop("run_id") != second.pop("run_id")
    assert first == second
    encoded = json.dumps(
        [
            {key: row[key] for key in ("id", "track", "title", "objective", "body")}
            for row in LESSONS.values()
            if row["track"] == "agent"
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert first["corpus_revision"] == f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"
    assert first["corpus_revision"] != corpus_revision("fullstack")


@pytest.mark.parametrize("field", ["id", "track", "title", "objective", "body"])
def test_corpus_revision_changes_when_searchable_course_fields_change(monkeypatch, field):
    original = corpus_revision("agent")
    lesson = copy.deepcopy(LESSONS["agent-mcp"])
    lesson[field] = ["changed"] if field == "body" else "changed"
    monkeypatch.setitem(LESSONS, "agent-mcp", lesson)
    assert corpus_revision("agent") != original


def test_corpus_revision_tracks_tie_order_but_ignores_unsearched_fields(monkeypatch):
    from backend import retrieval_evaluation

    original = corpus_revision("agent")
    lesson = copy.deepcopy(LESSONS["agent-mcp"])
    lesson["minutes"] += 10
    monkeypatch.setitem(LESSONS, "agent-mcp", lesson)
    assert corpus_revision("agent") == original
    monkeypatch.setattr(retrieval_evaluation, "LESSONS", dict(reversed(list(LESSONS.items()))))
    assert corpus_revision("agent") != original


def test_evaluation_uses_no_network_or_model_client(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("Fixed retrieval evaluation must not contact a network or model")

    monkeypatch.setattr(socket, "create_connection", unexpected)
    monkeypatch.setattr(socket.socket, "connect", unexpected)
    monkeypatch.setattr(httpx.Client, "request", unexpected)
    monkeypatch.setattr(httpx.AsyncClient, "request", unexpected)
    monkeypatch.setattr(providers, "stream_generate", unexpected)
    for track in DATASETS:
        result = evaluate_retrieval(RetrievalEvaluationRequest.model_validate(request_data(track)))
        assert result["model_calls"] == 0 and len(result["cases"]) == 12
