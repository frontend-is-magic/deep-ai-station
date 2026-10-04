from copy import deepcopy

import pytest

from engine import LoopError, run_loop
from policies import decide_evidence_first, decide_repeat_search
from tools import LocalTools, assets_sha256, load_assets

QUERY = "循环与工具"
EMPTY = "zzqxjx-agent-loop-empty-2048"
DOCUMENT = {
    "id": "loop-guide",
    "title": "循环与停止",
    "body": "每轮先读取上一轮观察，再选择下一步动作。没有停止条件的循环可能反复调用同一工具。",
    "source": "https://developers.openai.com/api/docs/guides/agents",
    "kind": "teaching-summary",
}
FOUND = {"type": "search_result", "query": QUERY, "found_ids": ["loop-guide", "tool-guide"]}
READ = {"type": "read_result", "document": DOCUMENT}
EMPTY_RESULT = {"type": "search_result", "query": EMPTY, "found_ids": []}
A = {"query": QUERY, "found_ids": [], "read_documents": [], "last_observation": None}
B = {**A, "found_ids": ["loop-guide", "tool-guide"], "last_observation": FOUND}
C = {**B, "read_documents": [DOCUMENT], "last_observation": READ}
D = {**A, "query": EMPTY}
E = {**D, "last_observation": EMPTY_RESULT}
SEARCH = {"action": "search", "query": QUERY}
READ_DECISION = {"action": "read", "document_id": "loop-guide"}
FINISH = {"action": "finish", "outcome": "completed"}


@pytest.fixture
def local_tools():
    return LocalTools(load_assets())


@pytest.mark.parametrize(
    ("query", "policy", "budget", "decisions", "observations", "states", "reason", "result"),
    [
        (
            QUERY,
            decide_evidence_first,
            3,
            [SEARCH, READ_DECISION, FINISH],
            [FOUND, READ, None],
            [A, B, C, C],
            "completed",
            DOCUMENT,
        ),
        (QUERY, decide_evidence_first, 1, [SEARCH], [FOUND], [A, B], "step_limit", None),
        (
            EMPTY,
            decide_evidence_first,
            3,
            [{"action": "search", "query": EMPTY}, {"action": "finish", "outcome": "no_evidence"}],
            [EMPTY_RESULT, None],
            [D, E, E],
            "no_evidence",
            None,
        ),
        (
            QUERY,
            decide_repeat_search,
            5,
            [SEARCH] * 5,
            [FOUND] * 5,
            [A] + [B] * 5,
            "step_limit",
            None,
        ),
        (
            QUERY,
            decide_evidence_first,
            2,
            [SEARCH, READ_DECISION],
            [FOUND, READ],
            [A, B, C],
            "step_limit",
            None,
        ),
    ],
)
def test_manual_baselines(
    query, policy, budget, decisions, observations, states, reason, result, local_tools
):
    actual = run_loop(query, policy, budget, search=local_tools.search, read=local_tools.read)
    expected_steps = [
        {
            "index": index + 1,
            "before": states[index],
            "decision": decision,
            "observation": observations[index],
            "after": states[index + 1],
        }
        for index, decision in enumerate(decisions)
    ]
    assert actual == {
        "max_steps": budget,
        "decision_count": len(decisions),
        "tool_dispatch_count": sum(item["action"] != "finish" for item in decisions),
        "stop_reason": reason,
        "steps": expected_steps,
        "final_state": states[-1],
        "result": result,
    }


def test_actual_empty_observation_changes_found_case_and_never_reads():
    dispatched = []

    def empty_search(query):
        dispatched.append(query)
        return {"type": "search_result", "query": query, "found_ids": []}

    def forbidden_read(document_id):
        pytest.fail(f"unexpected read: {document_id}")

    result = run_loop(QUERY, decide_evidence_first, 3, search=empty_search, read=forbidden_read)
    assert dispatched == [QUERY]
    assert (result["stop_reason"], result["decision_count"], result["tool_dispatch_count"]) == (
        "no_evidence",
        2,
        1,
    )
    assert result["result"] is None
    assert result["final_state"]["read_documents"] == []


def test_bad_policy_cannot_complete_without_actual_read(local_tools):
    calls = []

    def bad_policy(state):
        calls.append(state)
        return SEARCH if state["last_observation"] is None else FINISH

    with pytest.raises(LoopError):
        run_loop(QUERY, bad_policy, 3, search=local_tools.search, read=local_tools.read)
    assert len(calls) == 2
    assert calls[-1]["read_documents"] == []


@pytest.mark.parametrize("budget", [1, 3, 5])
def test_budget_bounds_actual_decisions_and_tool_calls(budget, local_tools):
    decisions, searches = [], []

    def decide(state):
        decisions.append(deepcopy(state))
        return decide_repeat_search(state)

    def search(query):
        searches.append(query)
        return local_tools.search(query)

    result = run_loop(QUERY, decide, budget, search=search, read=local_tools.read)
    assert len(decisions) == len(searches) == budget
    assert result["stop_reason"] == "step_limit"


def test_policy_and_tool_aliases_cannot_rewrite_facts_or_old_snapshots(local_tools):
    exposed = []

    def decide(state):
        action = decide_evidence_first(state)
        exposed.append(state)
        state["query"] = "mutated"
        state["found_ids"].clear()
        state["read_documents"].clear()
        return action

    observations = []

    def search(query):
        observation = local_tools.search(query)
        observations.append(observation)
        return observation

    result = run_loop(QUERY, decide, 3, search=search, read=local_tools.read)
    saved = deepcopy(result)
    observations[0]["found_ids"].clear()
    exposed[0]["last_observation"] = {"unexpected": True}
    assert result == saved
    assert result["steps"][0]["before"] == A
    assert result["steps"][0]["after"] == B
    assert result["final_state"] == C
    result["final_state"]["read_documents"].clear()
    assert result["steps"][-1]["after"] == C
    assert result["result"] == DOCUMENT


def test_latest_search_replaces_ids_and_actual_reads_accumulate(local_tools):
    decisions = iter(
        [SEARCH, READ_DECISION, SEARCH, {"action": "read", "document_id": "tool-guide"}, FINISH]
    )
    found = iter([["loop-guide"], ["tool-guide"]])

    def search(query):
        return {"type": "search_result", "query": query, "found_ids": next(found)}

    result = run_loop(QUERY, lambda state: next(decisions), 5, search=search, read=local_tools.read)
    assert result["final_state"]["found_ids"] == ["tool-guide"]
    assert [item["id"] for item in result["final_state"]["read_documents"]] == [
        "loop-guide",
        "tool-guide",
    ]
    assert result["result"] == local_tools.read("tool-guide")["document"]


def test_repeated_read_keeps_first_read_order_and_one_document(local_tools):
    decisions = iter([SEARCH, READ_DECISION, READ_DECISION, FINISH])
    result = run_loop(
        QUERY, lambda state: next(decisions), 5, search=local_tools.search, read=local_tools.read
    )
    assert result["final_state"]["read_documents"] == [DOCUMENT]
    assert result["decision_count"] == 4
    assert result["tool_dispatch_count"] == 3


@pytest.mark.parametrize("budget", [True, 0, 6, 1.0, "3"])
def test_invalid_budget_does_not_call_policy_or_tools(budget):
    def forbidden(*args):
        pytest.fail("invalid budget dispatched")

    with pytest.raises(LoopError):
        run_loop(QUERY, forbidden, budget, search=forbidden, read=forbidden)


@pytest.mark.parametrize(
    "decision",
    [FINISH, READ_DECISION, {"action": "search", "query": "other"}, {**SEARCH, "extra": True}],
)
def test_invalid_first_decision_does_not_dispatch(decision):
    def forbidden(*args):
        pytest.fail("invalid decision dispatched")

    with pytest.raises(LoopError):
        run_loop(QUERY, lambda state: decision, 3, search=forbidden, read=forbidden)


@pytest.mark.parametrize(
    "observation",
    [
        {**FOUND, "query": "other"},
        {**FOUND, "found_ids": ["loop-guide", "loop-guide"]},
        {**FOUND, "found_ids": ["unknown"]},
    ],
)
def test_invalid_search_observation_is_execution_error(observation, local_tools):
    with pytest.raises(LoopError):
        run_loop(
            QUERY, decide_evidence_first, 3, search=lambda query: observation, read=local_tools.read
        )


def test_wrong_read_identity_and_tool_exception_are_execution_errors(local_tools):
    with pytest.raises(LoopError):
        run_loop(
            QUERY,
            decide_evidence_first,
            3,
            search=local_tools.search,
            read=lambda document_id: local_tools.read("tool-guide"),
        )

    def broken(query):
        raise RuntimeError("private-input-fixture")

    with pytest.raises(LoopError) as error:
        run_loop(QUERY, decide_evidence_first, 3, search=broken, read=local_tools.read)
    assert str(error.value) == ""


def test_actual_assets_tools_and_legal_query_change():
    asset = load_assets()
    assert (
        assets_sha256(asset) == "45a01bfcdff94b3d96826ba2f9eab7a750a51ea3e362939d8c2a0e1d1c06007d"
    )
    tools = LocalTools(asset)
    assert tools.read("loop-guide") == READ
    assert tools.search(QUERY) == FOUND
    assert tools.search(EMPTY) == EMPTY_RESULT
    assert tools.search("  工具  ")["found_ids"] == ["tool-guide"]
    asset["documents"][0]["keywords"] = ["LOOP"]
    asset["documents"][0]["body"] = "本地修改的真实原文"
    changed = LocalTools(asset)
    assert changed.search(" loop ")["found_ids"] == ["loop-guide"]
    assert changed.read("loop-guide")["document"]["body"] == "本地修改的真实原文"
    assert tools.read("loop-guide") == READ
