"""Bounded client-tool protocol. It never runs a tool or accepts external capabilities."""

import json


class ToolAccumulator:
    def __init__(self):
        self.calls: dict[int, dict] = {}

    def add(self, index, *, id=None, name=None, fragment=None):
        if type(index) is not int or not 0 <= index <= 64:
            raise ValueError("tool_index")
        if index not in self.calls:
            if self.calls:
                raise ValueError("parallel_tools")
            self.calls[index] = {"id": "", "name": "", "json": ""}
        call = self.calls[index]
        for key, value, maximum in (("id", id, 256), ("name", name, 100)):
            if value is None:
                continue
            if not isinstance(value, str) or not 0 < len(value) <= maximum:
                raise ValueError("tool_identity")
            if call[key] and call[key] != value:
                raise ValueError("changed_tool_identity")
            call[key] = value
        if fragment is not None:
            if not isinstance(fragment, str):
                raise ValueError("tool_fragment")
            call["json"] += fragment
        if len(call["json"].encode()) > 4096:
            raise ValueError("tool_arguments_limit")

    def finish(self):
        result = []
        for call in self.calls.values():
            if not call["id"] or not call["name"]:
                raise ValueError("incomplete_tool")
            arguments = json.loads(call["json"]) if call["json"] else {}
            if not isinstance(arguments, dict) or len(json.dumps(arguments).encode()) > 4096:
                raise ValueError("tool_input")
            result.append({"id": call["id"], "name": call["name"], "arguments": arguments})
        return result
