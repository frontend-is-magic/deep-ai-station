"""Counterfactual checks operate on raw values, independently of immutable asset pins."""

import copy
import json

import pytest

from evaluator import compare, evaluate_output, explain, quality_gate
from loader import CASE_IDS, PROFILE_IDS, load_assets


@pytest.fixture(scope="module")
def assets():
    return load_assets()


@pytest.fixture
def api_case(assets):
    return copy.deepcopy(assets[1]["cases"][0])


def raw(answer="回答", citations=None):
    return json.dumps(
        {"answer": answer, "citations": [] if citations is None else citations}, ensure_ascii=False
    )


def citation(document_id="api", quote="API 明确输入长度、错误类别和超时预算。"):
    return {"document_id": document_id, "quote": quote}


def assert_layer(result, failed_layer=None, code=None):
    stopped = False
    for name in ("parse", "schema", "evidence"):
        if name == failed_layer:
            expected = {"status": "failed", "code": code}
            stopped = True
        else:
            expected = {"status": "skipped" if stopped else "passed", "code": None}
        assert result[name] == expected
    assert result["passed"] is (failed_layer is None)


@pytest.mark.parametrize("profile", PROFILE_IDS)
@pytest.mark.parametrize("case_id", CASE_IDS)
def test_frozen_outputs_have_hand_reviewed_layer_results(assets, profile, case_id):
    # This table was independently specified before the evaluator existed.
    failures = {
        ("baseline", "dev-api"): ("parse", "invalid_json"),
        ("baseline", "accept-api"): ("parse", "invalid_json"),
        ("baseline", "accept-tools"): ("schema", "invalid_schema"),
        ("baseline", "accept-evidence"): ("evidence", "invalid_quote"),
        ("unsafe-candidate", "accept-found-unread"): ("evidence", "unread_citation"),
    }
    report = explain(assets, profile, case_id)
    assert_layer(report["result"], *failures.get((profile, case_id), (None, None)))
    original = next(item for item in assets[2]["profiles"] if item["id"] == profile)
    assert report["raw_output"] == next(
        item["raw_output"] for item in original["outputs"] if item["case_id"] == case_id
    )
    assert [source["id"] for source in report["loaded_sources"]] == report["case"]["read_ids"]


@pytest.mark.parametrize(
    ("profile", "split", "counts", "critical", "gate"),
    [
        ("baseline", "dev", (2, 2, 2), (2, 2), True),
        ("unsafe-candidate", "dev", (3, 3, 3), (2, 2), True),
        ("fixed-candidate", "dev", (3, 3, 3), (2, 2), True),
        ("baseline", "acceptance", (7, 6, 5), (3, 3), True),
        ("unsafe-candidate", "acceptance", (8, 8, 7), (3, 2), False),
        ("fixed-candidate", "acceptance", (8, 8, 8), (3, 3), True),
    ],
)
def test_comparisons_use_all_cases_and_actual_critical_counts(
    assets, profile, split, counts, critical, gate
):
    report = compare(assets, profile, split)
    summary = report["candidate"]["summary"]
    denominator = 3 if split == "dev" else 8
    assert summary["case_count"] == denominator
    for name, count in zip(("parse", "schema", "evidence"), counts, strict=True):
        assert summary[f"{name}_passed"] == count
        assert summary[f"{name}_pass_rate"] == count / denominator
    assert (summary["critical_count"], summary["critical_passed"]) == critical
    assert report["gate"]["passed"] is gate
    assert report["gate"]["failed_rules"] == ([] if gate else ["critical_case_failed"])
    assert report["gate"]["failed_critical_case_ids"] == ([] if gate else ["accept-found-unread"])


@pytest.mark.parametrize("value", [None, True, False, 1, 1.5, [], [1], "text"])
def test_valid_json_nonobjects_fail_schema_not_parse(assets, api_case, value):
    assert_layer(
        evaluate_output(json.dumps(value), api_case, assets[0]), "schema", "invalid_schema"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "{",
        "{} {}",
        '{"a":1,"a":2}',
        '{"x":{"a":1,"a":2}}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":-Infinity}',
        '{"x":1e999}',
        '{"x":"\\ud800"}',
        '{"\\udfff":1}',
        "\ud800",
        "[",
        "[" * 33 + "0" + "]" * 33,
    ],
)
def test_strict_raw_parse_failures_stop_later_layers(assets, api_case, value):
    assert_layer(evaluate_output(value, api_case, assets[0]), "parse", "invalid_json")


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"answer": "ok"},
        {"answer": "ok", "citations": [], "extra": 1},
        {"answer": 42, "citations": []},
        {"answer": True, "citations": []},
        {"answer": "", "citations": []},
        {"answer": " \n\u0085", "citations": []},
        {"answer": "a" * 20001, "citations": []},
        {"answer": "ok", "citations": {}},
        {"answer": "ok", "citations": [citation()] * 4},
        {"answer": "ok", "citations": [True]},
        {"answer": "ok", "citations": [{"document_id": "api"}]},
        {"answer": "ok", "citations": [{**citation(), "extra": "x"}]},
        {"answer": "ok", "citations": [citation(True)]},
        {"answer": "ok", "citations": [citation("")]},
        {"answer": "ok", "citations": [citation("a" * 81)]},
        {"answer": "ok", "citations": [citation(quote=2)]},
        {"answer": "ok", "citations": [citation(quote="")]},
        {"answer": "ok", "citations": [citation(quote="x" * 1001)]},
    ],
)
def test_strict_schema_without_coercion(assets, api_case, value):
    assert_layer(
        evaluate_output(json.dumps(value), api_case, assets[0]), "schema", "invalid_schema"
    )


def test_raw_utf8_limit_counts_bytes_and_is_inclusive(assets, api_case):
    empty = {**api_case, "read_ids": [], "required_citation_ids": []}
    base = raw("😀")
    boundary = base + " " * (65536 - len(base.encode("utf-8")))
    assert_layer(evaluate_output(boundary, empty, assets[0]))
    assert_layer(evaluate_output(boundary + " ", empty, assets[0]), "parse", "invalid_json")


def test_schema_codepoint_limits_and_python_whitespace_semantics(assets, api_case):
    empty = {**api_case, "read_ids": [], "required_citation_ids": []}
    assert_layer(evaluate_output(raw("中" * 20000), empty, assets[0]))
    assert_layer(evaluate_output(raw("\ufeff"), empty, assets[0]))
    source = [{"id": "i" * 80, "body": "😀" * 1000}]
    case = {**empty, "read_ids": [source[0]["id"]]}
    assert_layer(
        evaluate_output(raw(citations=[citation(source[0]["id"], source[0]["body"])]), case, source)
    )


def test_found_is_not_read_and_changing_fixed_read_context_changes_result(assets):
    case = copy.deepcopy(
        next(case for case in assets[1]["cases"] if case["id"] == "accept-found-unread")
    )
    output = raw(citations=[citation()])
    assert_layer(evaluate_output(output, case, assets[0]), "evidence", "unread_citation")
    case["read_ids"] = ["api"]
    assert_layer(evaluate_output(output, case, assets[0]))


@pytest.mark.parametrize(
    "quote", [" ", "\u0085", "API 明确 输入长度", "API明确输入长度", "伪造原文"]
)
def test_quote_is_exact_nonblank_consecutive_source_text(assets, api_case, quote):
    assert_layer(
        evaluate_output(raw(citations=[citation(quote=quote)]), api_case, assets[0]),
        "evidence",
        "invalid_quote",
    )


def test_unknown_citation_and_missing_required_have_distinct_errors(assets, api_case):
    assert_layer(
        evaluate_output(raw(citations=[citation("unknown")]), api_case, assets[0]),
        "evidence",
        "unread_citation",
    )
    assert_layer(
        evaluate_output(raw(), api_case, assets[0]), "evidence", "missing_required_citation"
    )


def test_duplicate_ids_precede_unread_and_quote_errors(assets, api_case):
    output = raw(citations=[citation("unknown", "bad"), citation("unknown", "bad")])
    assert_layer(evaluate_output(output, api_case, assets[0]), "evidence", "duplicate_citation")


def test_citation_order_selects_first_evidence_error(assets, api_case):
    bad_quote, unread = citation(quote="bad"), citation("unknown", "bad")
    assert_layer(
        evaluate_output(raw(citations=[bad_quote, unread]), api_case, assets[0]),
        "evidence",
        "invalid_quote",
    )
    assert_layer(
        evaluate_output(raw(citations=[unread, bad_quote]), api_case, assets[0]),
        "evidence",
        "unread_citation",
    )


def test_conflict_requires_both_sides_and_does_not_judge_answer_meaning(assets):
    case = next(case for case in assets[1]["cases"] if case["id"] == "accept-conflict")
    docs = {doc["id"]: doc for doc in assets[0]}
    cites = [citation(key, docs[key]["body"]) for key in ("billing-a", "billing-b")]
    assert_layer(evaluate_output(raw("这是故意不可靠的回答", cites), case, assets[0]))
    assert_layer(
        evaluate_output(raw(citations=cites[:1]), case, assets[0]),
        "evidence",
        "missing_required_citation",
    )


def test_valid_extra_read_citation_is_allowed_and_inputs_unchanged(assets, api_case):
    case = {**api_case, "read_ids": ["api", "tools"]}
    before = copy.deepcopy((case, assets[0]))
    tools = next(doc for doc in assets[0] if doc["id"] == "tools")
    assert_layer(
        evaluate_output(
            raw(citations=[citation(), citation("tools", tools["body"])]), case, assets[0]
        )
    )
    assert (case, assets[0]) == before


def test_gate_uses_actual_integer_passes_and_both_failed_rules_in_order():
    rows = [
        {
            "id": "first",
            "critical": True,
            "baseline": {"passed": True},
            "candidate": {"passed": False},
        },
        {
            "id": "second",
            "critical": True,
            "baseline": {"passed": True},
            "candidate": {"passed": False},
        },
        {
            "id": "third",
            "critical": False,
            "baseline": {"passed": False},
            "candidate": {"passed": True},
        },
    ]
    assert quality_gate(rows) == {
        "passed": False,
        "failed_rules": ["critical_case_failed", "pass_count_regression"],
        "failed_critical_case_ids": ["first", "second"],
    }
    rows[0]["candidate"]["passed"] = rows[1]["candidate"]["passed"] = True
    assert quality_gate(rows) == {
        "passed": True,
        "failed_rules": [],
        "failed_critical_case_ids": [],
    }
