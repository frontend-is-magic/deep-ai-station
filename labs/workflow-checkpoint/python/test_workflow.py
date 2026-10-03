import copy
import json

import pytest

from app import parse_args
from workflow import (
    NODES,
    LabError,
    execute_node,
    load_fixtures,
    parse_json,
    validate_citations,
    validate_saved_outputs,
)

RUN_ID = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize(
    "case,ids,outcome",
    [
        ("normal", ["api"], "complete"),
        ("empty", [], "insufficient_evidence"),
        ("conflict", ["billing-a", "billing-b"], "conflicting_evidence"),
    ],
)
def test_fixed_workflow_preserves_actual_reads_and_outcomes(case, ids, outcome):
    documents = load_fixtures()
    outputs = []
    for node in NODES:
        outputs.append(execute_node(node, case, outputs, documents))
    validate_saved_outputs(case, outputs, documents)
    assert outputs[0]["found_ids"] == ids
    assert [document["id"] for document in outputs[0]["read_documents"]] == ids
    assert [citation["source_id"] for citation in outputs[2]["citations"]] == ids
    assert outputs[2]["outcome"] == outcome
    assert outputs[2]["read_only"] is True
    assert outputs[2]["model_calls"] == 0
    if case == "conflict":
        assert all(citation["url"] is None for citation in outputs[2]["citations"])


@pytest.mark.parametrize(
    "change",
    ["not_read", "empty_quote", "foreign_quote", "duplicate", "wrong_url", "one_conflict_side"],
)
def test_citation_validation_rejects_unread_unbound_and_incomplete_sources(change):
    case = "conflict" if change == "one_conflict_side" else "normal"
    retrieval = execute_node("retrieve", case, [], load_fixtures())
    draft = execute_node("draft", case, [retrieval], load_fixtures())
    if change == "not_read":
        retrieval["read_documents"] = []
    elif change == "empty_quote":
        draft["citations"][0]["quote"] = ""
    elif change == "foreign_quote":
        draft["citations"][0]["quote"] = "invented-evidence"
    elif change == "duplicate":
        draft["citations"].append(copy.deepcopy(draft["citations"][0]))
    elif change == "wrong_url":
        draft["citations"][0]["url"] = "https://example.invalid/"
    else:
        draft["citations"].pop()
    with pytest.raises(LabError, match="checkpoint_invalid"):
        validate_citations(case, retrieval, draft)


@pytest.mark.parametrize(
    "raw",
    [
        '{"a":1,"a":2}',
        '{"a":NaN}',
        '{"a":1e999}',
        '{"a":"\\ud800"}',
        '"' + "x" * 16384 + '"',
        "{broken",
    ],
)
def test_corrupt_json_is_rejected(raw):
    with pytest.raises(LabError, match="checkpoint_invalid"):
        parse_json(raw)


def test_fixture_modification_is_not_accepted_as_new_version(tmp_path):
    path = tmp_path / "fixtures.json"
    path.write_text(json.dumps({"documents": [], "cases": {}}))
    with pytest.raises(LabError, match="incompatible_version"):
        load_fixtures(path)


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["reset"],
        ["start", "--run-id", RUN_ID],
        ["inspect", "--run-id", RUN_ID, "--run-id", RUN_ID],
        ["inspect", "--run-id", RUN_ID, "--db", "private-input-fixture"],
        ["step", "--run-id", RUN_ID, "--expected-revision", "+1"],
        ["step", "--run-id", RUN_ID, "--expected-revision", "1.0"],
        ["step", "--run-id", RUN_ID, "--expected-revision", "4"],
    ],
)
def test_cli_rejects_invalid_options_without_repeating_values(args):
    with pytest.raises(LabError, match="invalid_input"):
        parse_args(args)
