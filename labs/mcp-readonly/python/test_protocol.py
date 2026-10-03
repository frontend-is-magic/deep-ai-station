import json

import jsonschema
import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolRequestParams
from pydantic import ValidationError

import server
from protocol import COURSES, SearchArguments, SearchOutput, search

INVALID = [
    {},
    {"query": None},
    {"query": True},
    {"query": 1},
    {"query": []},
    {"query": ""},
    {"query": " \t\n"},
    {"query": "\u0085\u001c"},
    {"query": "a" * 101},
    {"query": " MCP " + " " * 96},
    {"query": "x\0y"},
    {"query": "\ud800"},
    {"query": "\udfff"},
    {"query": "MCP", "extra": True},
    {"query": "MCP", "limit": True},
    {"query": "MCP", "limit": False},
    {"query": "MCP", "limit": "1"},
    {"query": "MCP", "limit": 0},
    {"query": "MCP", "limit": 4},
    {"query": "MCP", "limit": None},
]


@pytest.mark.parametrize("arguments", INVALID)
def test_strict_schema_and_validator_reject(arguments):
    with pytest.raises(ValidationError):
        SearchArguments.model_validate(arguments)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(arguments, SearchArguments.contract_schema())


@pytest.mark.parametrize("value", [1.0, 2.5])
def test_strict_integer_rejects_float_representation(value):
    with pytest.raises(ValidationError):
        SearchArguments(query="MCP", limit=value)
    # JSON Schema integer describes the number's value, not JSON lexical spelling.
    if value == 1.0:
        jsonschema.validate({"query": "MCP", "limit": value}, SearchArguments.contract_schema())


@pytest.mark.parametrize(
    "query", ["MCP", " MCP ", "工具", "a" * 100, "\ufeff", "🧪" * 100, "\nMCP\n"]
)
def test_valid_queries_match_schema_and_keep_raw_value(query):
    arguments = SearchArguments(query=query)
    assert arguments.query == query
    assert arguments.limit == 3
    jsonschema.validate({"query": query}, SearchArguments.contract_schema())


def test_search_normalizes_for_matching_limits_and_returns_copies():
    result = search(SearchArguments(query=" mCp "))
    assert [item.id for item in result.items] == ["agent-mcp"]
    result.items[0].title = "changed"
    assert search(SearchArguments(query="MCP")).items[0].title == COURSES[0]["title"]
    assert len(search(SearchArguments(query="工具", limit=2)).items) == 2
    assert search(SearchArguments(query="no-match-fixture")).items == []
    jsonschema.validate(
        search(SearchArguments(query="工具")).model_dump(), SearchOutput.model_json_schema()
    )


def test_resource_texts_are_public_utf8_with_final_newline():
    assert [c["uri"] for c in COURSES] == [
        "course://agent-mcp",
        "course://agent-tool-contract",
        "course://agent-tool-safety",
    ]
    for course in COURSES:
        assert course["text"].endswith("\n")
        assert course["text"].encode("utf-8").decode("utf-8") == course["text"]


async def test_business_error_never_echoes_rejected_input():
    handler = server.make_server().get_request_handler("tools/call").handler
    result = await handler(
        None,
        CallToolRequestParams(
            name="knowledge_search",
            arguments={"query": "PRIVATE_TEST_MARKER", "unexpected": "PRIVATE_TEST_MARKER"},
        ),
    )
    payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert payload["isError"] is True
    assert payload["content"] == [{"type": "text", "text": "invalid_arguments"}]
    assert "PRIVATE_TEST_MARKER" not in json.dumps(payload)


async def test_unexpected_handler_failure_is_sanitized(monkeypatch):
    def broken(_arguments):
        raise OSError("PRIVATE_TEST_MARKER /private/local-file")

    monkeypatch.setattr(server, "search", broken)
    handler = server.make_server().get_request_handler("tools/call").handler
    with pytest.raises(MCPError) as failure:
        await handler(
            None, CallToolRequestParams(name="knowledge_search", arguments={"query": "MCP"})
        )
    assert (failure.value.code, failure.value.message, failure.value.data) == (
        -32603,
        "request_failed",
        None,
    )
