"""One bounded loop whose facts come exclusively from actual tool observations."""

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from tools import DOCUMENT_IDS, valid_document, valid_text

Snapshot = dict[str, Any]
Decision = dict[str, Any]
LoopResult = dict[str, Any]


class LoopError(Exception):
    """The loop did not produce a complete, valid report."""


def _decision(value: object, state: Snapshot) -> Decision:
    if not isinstance(value, dict):
        raise LoopError
    action = value.get("action")
    if action == "search" and set(value) == {"action", "query"}:
        if value["query"] == state["query"]:
            return deepcopy(value)
    if action == "read" and set(value) == {"action", "document_id"}:
        if value["document_id"] in state["found_ids"]:
            return deepcopy(value)
    if action == "finish" and set(value) == {"action", "outcome"}:
        observation = state["last_observation"]
        if observation is not None:
            if (
                value["outcome"] == "completed"
                and observation["type"] == "read_result"
                and observation["document"] in state["read_documents"]
            ):
                return deepcopy(value)
            if (
                value["outcome"] == "no_evidence"
                and observation["type"] == "search_result"
                and not observation["found_ids"]
                and not state["read_documents"]
            ):
                return deepcopy(value)
    raise LoopError


def _search_observation(value: object, query: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"type", "query", "found_ids"}:
        raise LoopError
    found = value["found_ids"]
    if (
        value["type"] != "search_result"
        or value["query"] != query
        or not isinstance(found, list)
        or not all(isinstance(item, str) and item in DOCUMENT_IDS for item in found)
        or len(set(found)) != len(found)
    ):
        raise LoopError
    return deepcopy(value)


def _read_observation(value: object, document_id: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"type", "document"}
        or value["type"] != "read_result"
        or not valid_document(value["document"])
        or value["document"]["id"] != document_id
    ):
        raise LoopError
    return deepcopy(value)


def run_loop(
    query: str,
    decide: Callable[[Snapshot], Decision],
    max_steps: int,
    *,
    search: Callable[[str], dict[str, Any]],
    read: Callable[[str], dict[str, Any]],
) -> LoopResult:
    if type(max_steps) is not int or not 1 <= max_steps <= 5 or not valid_text(query):
        raise LoopError
    state: Snapshot = {
        "query": query,
        "found_ids": [],
        "read_documents": [],
        "last_observation": None,
    }
    steps = []
    dispatches = 0
    stop_reason = "step_limit"
    result = None
    try:
        for index in range(1, max_steps + 1):
            before = deepcopy(state)
            decision = _decision(decide(deepcopy(state)), state)
            action = decision["action"]
            observation = None
            if action == "search":
                dispatches += 1
                observation = _search_observation(search(decision["query"]), query)
                state["found_ids"] = deepcopy(observation["found_ids"])
            elif action == "read":
                dispatches += 1
                observation = _read_observation(
                    read(decision["document_id"]), decision["document_id"]
                )
                document = observation["document"]
                if not any(item["id"] == document["id"] for item in state["read_documents"]):
                    state["read_documents"].append(deepcopy(document))
            else:
                stop_reason = decision["outcome"]
                if stop_reason == "completed":
                    result = deepcopy(state["last_observation"]["document"])
            if observation is not None:
                state["last_observation"] = deepcopy(observation)
            steps.append(
                {
                    "index": index,
                    "before": before,
                    "decision": decision,
                    "observation": observation,
                    "after": deepcopy(state),
                }
            )
            if action == "finish":
                break
    except Exception:
        raise LoopError from None
    return {
        "max_steps": max_steps,
        "decision_count": len(steps),
        "tool_dispatch_count": dispatches,
        "stop_reason": stop_reason,
        "steps": steps,
        "final_state": deepcopy(state),
        "result": result,
    }
