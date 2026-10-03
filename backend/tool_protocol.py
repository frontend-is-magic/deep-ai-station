"""Bounded client-tool protocol. It never runs a tool or accepts external capabilities."""

import json


class ToolAccumulator:
    def __init__(self):
        self.calls: dict[int, dict] = {}

    def add(self, index, *, id=None, name=None, fragment=None, initial=None):
        if type(index) is not int or not 0 <= index <= 64:
            raise ValueError("tool_index")
        if index not in self.calls:
            if self.calls:
                raise ValueError("parallel_tools")
            self.calls[index] = {"id": "", "name": "", "json": "", "initial": None, "closed": False}
        call = self.calls[index]
        if call["closed"]:
            raise ValueError("closed_tool")
        for key, value, maximum in (("id", id, 256), ("name", name, 100)):
            if value is None:
                continue
            if not isinstance(value, str) or not 0 < len(value) <= maximum:
                raise ValueError("tool_identity")
            if call[key] and call[key] != value:
                raise ValueError("changed_tool_identity")
            call[key] = value
        if initial is not None:
            if not isinstance(initial, dict):
                raise ValueError("tool_input")
            call["initial"] = initial
        if fragment is not None:
            if not isinstance(fragment, str):
                raise ValueError("tool_fragment")
            call["json"] += fragment
        if len(call["json"].encode()) > 4096:
            raise ValueError("tool_arguments_limit")

    def close(self, index):
        if type(index) is not int or not 0 <= index <= 64:
            raise ValueError("tool_index")
        if index in self.calls:
            self.calls[index]["closed"] = True

    def finish(self, *, require_closed=False):
        result = []
        for call in self.calls.values():
            if not call["id"] or not call["name"] or require_closed and not call["closed"]:
                raise ValueError("incomplete_tool")
            arguments = json.loads(call["json"]) if call["json"] else call["initial"] or {}
            if not isinstance(arguments, dict) or len(json.dumps(arguments).encode()) > 4096:
                raise ValueError("tool_input")
            result.append({"id": call["id"], "name": call["name"], "arguments": arguments})
        return result


def anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert our internal Chat-style history to Claude's immediate tool-result blocks."""
    result = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            result.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message["tool_call_id"],
                            "content": message["content"],
                            "is_error": message.get("is_error", False),
                        }
                    ],
                }
            )
        elif message.get("tool_calls"):
            blocks = []
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            for call in message["tool_calls"]:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": json.loads(call["function"]["arguments"]),
                    }
                )
            result.append({"role": role, "content": blocks})
        else:
            result.append({"role": role, "content": message["content"]})
    return result
