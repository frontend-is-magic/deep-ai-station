"""Two deterministic policies; neither knows a case ID or expected answer."""

from typing import Any


def decide_evidence_first(snapshot: dict[str, Any]) -> dict[str, str]:
    observation = snapshot["last_observation"]
    if observation is None:
        return {"action": "search", "query": snapshot["query"]}
    if observation["type"] == "search_result":
        if observation["found_ids"]:
            return {"action": "read", "document_id": observation["found_ids"][0]}
        return {"action": "finish", "outcome": "no_evidence"}
    if observation["type"] == "read_result":
        return {"action": "finish", "outcome": "completed"}
    raise ValueError("unexpected observation")


def decide_repeat_search(snapshot: dict[str, Any]) -> dict[str, str]:
    """Exercise: this policy observes results but deliberately never advances."""
    observation = snapshot["last_observation"]
    if observation is None:
        return {"action": "search", "query": snapshot["query"]}
    if observation["type"] == "search_result":
        return {"action": "search", "query": observation["query"]}
    raise ValueError("unexpected observation")
