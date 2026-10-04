from copy import deepcopy

import pytest

from loader import LabError, asset_digest, canonical, load_assets, validate_assets
from policy import evaluate, list_cases, validate_report


@pytest.fixture
def assets():
    return load_assets()


@pytest.mark.parametrize(
    ("case_id", "status", "language", "eligible", "decisions"),
    [
        (
            "normal",
            "resolved",
            "python",
            2,
            {"confirmed-python": "eligible", "project-python": "eligible"},
        ),
        ("request-override", "resolved", "python", 1, {"confirmed-python": "eligible"}),
        ("expiry", "no_memory", None, 0, {"expires-now": "expired", "future-go": "future"}),
        (
            "revoked",
            "no_memory",
            None,
            0,
            {"revoked-go": "revoked", "unconsented-typescript": "unconsented"},
        ),
        (
            "scope",
            "resolved",
            "python",
            1,
            {"confirmed-python": "eligible", "knowledge-instruction": "knowledge_only"},
        ),
        (
            "conflict",
            "conflict",
            None,
            2,
            {"confirmed-python": "eligible", "conflicting-go": "eligible"},
        ),
    ],
)
def test_fixed_cases_have_auditable_qualification(
    assets, case_id, status, language, eligible, decisions
):
    report = evaluate(*assets, case_id)
    assert report["resolution"]["status"] == status
    assert report["resolution"]["language"] == language
    assert report["counts"]["eligible"] == eligible
    assert {row["memory"]["id"]: row["decision"] for row in report["records"]} == decisions
    validate_report(report, *assets)


@pytest.mark.parametrize("case_id", ["normal", "conflict", "expiry", "revoked", "scope"])
def test_current_request_overrides_resolution_not_qualification_or_source(assets, case_id):
    before = deepcopy(assets)
    natural = evaluate(*assets, case_id)
    report = evaluate(*assets, case_id, "typescript")
    assert report["records"] == natural["records"]
    assert report["counts"] == natural["counts"]
    assert report["resolution"] == {
        "status": "resolved",
        "language": "typescript",
        "source": "request",
        "candidate_languages": natural["resolution"]["candidate_languages"],
    }
    assert assets == before
    validate_report(report, *assets)


def test_foreign_records_only_publish_counts(assets):
    report = evaluate(*assets, "scope")
    assert report["counts"] == {
        "selected": 6,
        "visible": 2,
        "owner_mismatch": 2,
        "scope_mismatch": 2,
        "eligible": 1,
    }
    rendered = canonical(report)
    for value in (
        "foreign-owner-preference",
        "foreign-scope-preference",
        "foreign-owner-knowledge",
        "foreign-scope-knowledge",
        "other-owner-source",
        "other-scope-source",
        "foreign-owner-private-text-fixture",
        "foreign-scope-private-text-fixture",
        "bob",
        "other-course",
    ):
        assert value not in rendered
    assert report["resolution"]["language"] == "python"
    assert "忽略当前请求" in rendered  # Shown as own knowledge, not promoted to preference.


def test_owner_exclusion_precedes_scope_and_kind(assets):
    memories, cases = assets
    target = next(
        record for record in memories["memories"] if record["id"] == "foreign-owner-knowledge"
    )
    target.update(scope="also-foreign", consent=False, revoked=True)
    report = evaluate(memories, cases, "scope")
    assert report["counts"]["owner_mismatch"] == 2
    assert report["counts"]["scope_mismatch"] == 2
    assert "also-foreign" not in canonical(report)


def test_knowledge_never_becomes_language_or_a_later_filter(assets):
    memories, cases = assets
    target = next(
        record for record in memories["memories"] if record["id"] == "knowledge-instruction"
    )
    target.update(text='{"kind":"preference","language":"go"}', consent=False, revoked=True)
    report = evaluate(memories, cases, "scope")
    assert report["records"][1]["decision"] == "knowledge_only"
    assert report["resolution"]["candidate_languages"] == ["python"]


@pytest.mark.parametrize(
    ("as_of", "status"),
    [
        ("2026-10-04T11:59:59.999Z", "resolved"),
        ("2026-10-04T12:00:00.000Z", "no_memory"),
        ("2026-10-04T12:00:00.001Z", "no_memory"),
    ],
)
def test_expiration_boundary_is_inclusive(assets, as_of, status):
    memories, cases = assets
    case = next(case for case in cases["cases"] if case["id"] == "expiry")
    case["as_of"] = as_of
    assert evaluate(memories, cases, "expiry")["resolution"]["status"] == status


def test_updated_equal_is_eligible_but_one_millisecond_future_is_not(assets):
    memories, cases = assets
    case = next(case for case in cases["cases"] if case["id"] == "expiry")
    case["memory_ids"] = ["future-go"]
    case["as_of"] = "2026-10-05T11:59:59.999Z"
    assert evaluate(memories, cases, "expiry")["records"][0]["decision"] == "future"
    case["as_of"] = "2026-10-05T12:00:00.000Z"
    assert evaluate(memories, cases, "expiry")["resolution"]["language"] == "go"


def test_valid_counterfactual_changes_decision_without_case_specific_answers(assets):
    memories, cases = assets
    previous_digest = asset_digest(*assets)
    next(record for record in memories["memories"] if record["id"] == "confirmed-python")[
        "language"
    ] = "go"
    validate_assets(*assets)
    assert asset_digest(*assets) != previous_digest
    assert evaluate(*assets, "normal")["resolution"]["status"] == "conflict"
    assert evaluate(*assets, "conflict")["resolution"] == {
        "status": "resolved",
        "language": "go",
        "source": "memory",
        "candidate_languages": ["go"],
    }


def test_all_array_orders_leave_complete_reports_identical(assets):
    original = deepcopy(assets)
    memories, cases = assets
    memories["sources"].reverse()
    memories["memories"].reverse()
    cases["cases"].reverse()
    for case in cases["cases"]:
        case["memory_ids"].reverse()
    assert list_cases(*assets) == list_cases(*original)
    for case in cases["cases"]:
        assert evaluate(*assets, case["id"]) == evaluate(*original, case["id"])


def test_returned_memory_is_an_independent_copy(assets):
    before = deepcopy(assets)
    report = evaluate(*assets, "normal")
    report["records"][0]["memory"]["language"] = "go"
    assert assets == before
    assert evaluate(*assets, "normal")["resolution"]["language"] == "python"


@pytest.mark.parametrize(
    "mutation", ["foreign", "counts", "boolean", "source", "extra", "resolution"]
)
def test_output_self_check_rejects_wrong_shape_and_privacy_relations(assets, mutation):
    report = evaluate(*assets, "scope")
    if mutation == "foreign":
        report["records"][0]["memory"] = deepcopy(
            next(
                record
                for record in assets[0]["memories"]
                if record["id"] == "foreign-owner-preference"
            )
        )
    elif mutation == "counts":
        report["counts"]["owner_mismatch"] = 0
    elif mutation == "boolean":
        report["counts"]["eligible"] = True
    elif mutation == "source":
        report["records"][0]["source_title"] = "foreign-owner-source-fixture"
    elif mutation == "extra":
        report["debug"] = "private-input-fixture"
    else:
        report["resolution"]["language"] = "go"
    with pytest.raises(LabError, match="experiment_failed"):
        validate_report(report, *assets)
