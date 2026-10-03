import json
from copy import deepcopy
from unittest.mock import Mock
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.agent_loop as agent
import backend.app as application
import backend.providers as providers
import backend.quota as quota
import backend.sandbox as sandbox
import backend.tool_contract as experiment
from backend.curriculum import LESSONS

PATH = "/api/playground/tool-contract"


def payload(**overrides):
    return {
        "track": "agent",
        "lesson_id": "agent-tool-contract",
        "tool_name": "knowledge_search",
        "arguments_json": '{"query":"MCP"}',
        **overrides,
    }


@pytest.fixture
def client(monkeypatch):
    blocked = []
    for target, name in [
        (httpx.HTTPTransport, "handle_request"),
        (httpx.AsyncHTTPTransport, "handle_async_request"),
        (providers, "stream_generate"),
        (agent, "stream_generate"),
        (application, "stream_agent"),
        (application, "execute_code"),
        (sandbox, "execute_code"),
        (quota, "admit"),
        (application, "admit"),
    ]:
        mock = Mock(
            side_effect=AssertionError("Free tool experiment cannot call external execution")
        )
        monkeypatch.setattr(target, name, mock)
        blocked.append(mock)
    with TestClient(application.app, raise_server_exceptions=False) as client:
        yield client
    for mock in blocked:
        mock.assert_not_called()


def check_report(response, request, *, outcome):
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    report = response.json()
    assert set(report) == {
        "contract_version",
        "run_id",
        "track",
        "lesson_id",
        "tool_name",
        "arguments_json",
        "model_calls",
        "outcome",
        "observation",
    }
    assert report["contract_version"] == "tool-contract-v1"
    identifier = UUID(report["run_id"])
    assert identifier.version == 4 and str(identifier) == report["run_id"]
    for field, expected in request.items():
        assert report[field] == expected
    assert report["model_calls"] == 0 and report["outcome"] == outcome
    observation = report["observation"]
    assert observation["read_only"] is True
    assert observation["operation_id"] == report["run_id"] + ":1"
    return report


def assert_outer_rejection(response, status, execute):
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"detail"}
    assert isinstance(response.json()["detail"], str)
    execute.assert_not_called()


def test_directory_exposes_real_schemas_examples_and_supported_lessons(client):
    original = deepcopy(agent.TOOLS)
    response = client.get(PATH)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    directory = response.json()
    assert directory["contract_version"] == "tool-contract-v1"
    assert directory["tools"] == agent.TOOLS
    assert directory["supported_lesson_ids"] == ["agent-structured-output", "agent-tool-contract"]
    assert directory["limits"] == {"arguments_bytes": 4096, "read_only": True, "model_calls": 0}
    for tool in directory["tools"]:
        assert tool["parameters"]["additionalProperties"] is False
        assert "limit" not in tool["parameters"]["properties"]
    for example in directory["examples"]:
        assert set(example) == {"label", "tool_name", "arguments_json"}
        assert example["label"] and isinstance(example["arguments_json"], str)
        request = payload(tool_name=example["tool_name"], arguments_json=example["arguments_json"])
        report = client.post(PATH, json=request)
        assert report.status_code == 200 and report.json()["model_calls"] == 0
    directory["tools"][0]["parameters"]["properties"].clear()
    assert client.get(PATH).json()["tools"] == original == agent.TOOLS


@pytest.mark.parametrize("lesson_id", experiment.SUPPORTED_LESSON_IDS)
def test_actual_search_in_both_supported_lessons_preserves_raw_input(client, lesson_id):
    request = payload(lesson_id=lesson_id, arguments_json=' \n{ "query" : "  MCP  " }\t')
    report = check_report(client.post(PATH, json=request), request, outcome="success")
    assert report["observation"]["items"]
    assert len(report["observation"]["items"]) <= 3
    for item in report["observation"]["items"]:
        lesson = LESSONS[item["id"]]
        assert lesson["track"] == "agent"
        assert item["source"] == lesson["resources"][0]["url"]


def test_same_route_lesson_read_may_target_another_agent_lesson(client):
    request = payload(tool_name="lesson_read", arguments_json='{"lesson_id":"agent-mcp"}')
    report = check_report(client.post(PATH, json=request), request, outcome="success")
    expected = agent.execute_tool(
        "lesson_read", {"lesson_id": "agent-mcp"}, "agent", report["run_id"] + ":1"
    )
    assert report["observation"] == expected
    assert report["lesson_id"] == "agent-tool-contract"
    assert report["observation"]["lesson"]["id"] == "agent-mcp"


def test_no_match_is_success_and_each_request_has_a_new_operation_id(client):
    request = payload(arguments_json='{"query":"zzqxjx-no-match-976238"}')
    reports = [
        check_report(client.post(PATH, json=request), request, outcome="success") for _ in range(2)
    ]
    assert all(report["observation"]["items"] == [] for report in reports)
    assert reports[0]["run_id"] != reports[1]["run_id"]
    assert reports[0]["observation"]["operation_id"] != reports[1]["observation"]["operation_id"]


@pytest.mark.parametrize(
    "tool_name,arguments,error",
    [
        ("knowledge_search", {}, "invalid_arguments"),
        ("knowledge_search", {"query": "MCP", "limit": 1}, "invalid_arguments"),
        ("knowledge_search", {"query": ""}, "invalid_arguments"),
        ("knowledge_search", {"query": " \n\t"}, "invalid_arguments"),
        ("knowledge_search", {"query": 1}, "invalid_arguments"),
        ("knowledge_search", {"query": True}, "invalid_arguments"),
        ("knowledge_search", {"query": None}, "invalid_arguments"),
        ("knowledge_search", {"query": ["MCP"]}, "invalid_arguments"),
        ("knowledge_search", {"query": {"query": "MCP"}}, "invalid_arguments"),
        ("knowledge_search", {"query": "MCP" + " " * 98}, "invalid_arguments"),
        ("lesson_read", {"lesson_id": ""}, "invalid_arguments"),
        ("lesson_read", {"lesson_id": 1}, "invalid_arguments"),
        ("lesson_read", {"lesson_id": True}, "invalid_arguments"),
        ("lesson_read", {"lesson_id": None}, "invalid_arguments"),
        ("lesson_read", {"lesson_id": "agent-mcp", "extra": 1}, "invalid_arguments"),
        ("lesson_read", {"lesson_id": "fullstack-routing"}, "lesson_not_in_track"),
        ("lesson_read", {"lesson_id": "agent-missing"}, "lesson_not_in_track"),
        ("lesson_read", {"lesson_id": " "}, "lesson_not_in_track"),
        ("unknown_tool", {}, "unknown_tool"),
        (" ", {}, "unknown_tool"),
        ("https://example.com/tool", {}, "unknown_tool"),
    ],
)
def test_real_tool_rejections_keep_existing_agent_semantics(
    client, monkeypatch, tool_name, arguments, error
):
    execute = Mock(wraps=agent.execute_tool)
    retrieve = Mock(side_effect=AssertionError("Invalid calls must not perform retrieval"))
    monkeypatch.setattr(experiment, "execute_tool", execute)
    monkeypatch.setattr(agent, "retrieve", retrieve)
    request = payload(tool_name=tool_name, arguments_json=json.dumps(arguments))
    report = check_report(client.post(PATH, json=request), request, outcome="rejected")
    assert report["observation"] == {
        "read_only": True,
        "operation_id": report["run_id"] + ":1",
        "error": error,
    }
    execute.assert_called_once_with(tool_name, arguments, "agent", report["run_id"] + ":1")
    retrieve.assert_not_called()


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "{",
        '{"query":"MCP"',
        '{"query":"MCP"} trailing',
        '{"query":"MCP",}',
        "null",
        "false",
        "1",
        '"MCP"',
        "[]",
        '[{"query":"MCP"}]',
        '{"query":"MCP","query":"tools"}',
        '{"query":"MCP","qu\\u0065ry":"tools"}',
        '{"extra":{"x":1,"x":2},"query":"MCP"}',
        '{"query":NaN}',
        '{"query":Infinity}',
        '{"query":-Infinity}',
        '{"query":1e9999}',
        '{"query":"MCP","extra":[1e9999]}',
        '{"query":"\\ud800"}',
        '{"\\udfff":"MCP"}',
        '\ufeff{"query":"MCP"}',
        '{"query":"MCP\x00"}',
        '{"query":' + "[" * 32 + '"MCP"' + "]" * 32 + "}",
        '{"query":' + "[" * 1900 + "0" + "]" * 1900 + "}",
    ],
)
def test_invalid_inner_json_is_a_teaching_rejection_without_tool_execution(
    client, monkeypatch, raw
):
    execute = Mock(side_effect=AssertionError("Parser rejection must never execute a tool"))
    monkeypatch.setattr(experiment, "execute_tool", execute)
    request = payload(arguments_json=raw)
    report = check_report(client.post(PATH, json=request), request, outcome="rejected")
    assert report["observation"]["error"] == "invalid_arguments_json"
    execute.assert_not_called()


def test_legal_nested_json_goes_to_schema_validation_and_brackets_inside_strings_do_not_count(
    client,
):
    request = payload(arguments_json='{"query":' + "[" * 31 + '"MCP"' + "]" * 31 + "}")
    report = check_report(client.post(PATH, json=request), request, outcome="rejected")
    assert report["observation"]["error"] == "invalid_arguments"
    query = '{["\\]}' * 12
    request = payload(arguments_json=json.dumps({"query": query}))
    check_report(client.post(PATH, json=request), request, outcome="success")


@pytest.mark.parametrize("query", ["MCP", "汉" * 100, "🌱" * 100])
def test_exact_4096_utf8_bytes_are_accepted_but_one_extra_byte_is_outer_422(
    client, monkeypatch, query
):
    raw = json.dumps({"query": query}, ensure_ascii=False)
    raw = " " * (4096 - len(raw.encode())) + raw
    assert len(raw.encode()) == 4096
    request = payload(arguments_json=raw)
    check_report(client.post(PATH, json=request), request, outcome="success")
    execute = Mock()
    monkeypatch.setattr(experiment, "execute_tool", execute)
    assert_outer_rejection(client.post(PATH, json=payload(arguments_json=raw + " ")), 422, execute)


@pytest.mark.parametrize("field", ["track", "lesson_id", "tool_name", "arguments_json"])
@pytest.mark.parametrize("value", [None, 1, True, [], {}])
def test_outer_fields_do_not_coerce_types(client, monkeypatch, field, value):
    execute = Mock()
    monkeypatch.setattr(experiment, "execute_tool", execute)
    assert_outer_rejection(client.post(PATH, json=payload(**{field: value})), 422, execute)


@pytest.mark.parametrize("field", ["track", "lesson_id", "tool_name", "arguments_json"])
def test_outer_fields_are_required(client, monkeypatch, field):
    execute = Mock()
    monkeypatch.setattr(experiment, "execute_tool", execute)
    request = payload()
    del request[field]
    assert_outer_rejection(client.post(PATH, json=request), 422, execute)


@pytest.mark.parametrize(
    "overrides,status",
    [
        ({"lesson_id": "agent-no-such-course"}, 404),
        ({"lesson_id": "agent-mcp"}, 422),
        ({"lesson_id": "fullstack-routing"}, 422),
        ({"lesson_id": "fullstack-routing", "track": "fullstack"}, 422),
        ({"track": "fullstack"}, 422),
        ({"track": "unknown"}, 422),
        ({"lesson_id": ""}, 422),
        ({"lesson_id": "x" * 101}, 422),
        ({"tool_name": ""}, 422),
        ({"tool_name": "x" * 101}, 422),
        ({"extra": "private-extra-marker"}, 422),
    ],
)
def test_outer_ownership_and_field_constraints_never_execute(
    client, monkeypatch, overrides, status
):
    execute = Mock()
    monkeypatch.setattr(experiment, "execute_tool", execute)
    # Even broken argument JSON cannot hide an invalid target course.
    request = payload(arguments_json="{", **overrides)
    response = client.post(PATH, json=request)
    assert_outer_rejection(response, status, execute)
    assert "private-extra-marker" not in response.text


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b'{"arguments_json":',
        b"\xff",
        b"[" * 1100 + b"]" * 1100,
        json.dumps(payload(arguments_json="\ud800")).encode(),
        json.dumps(payload(tool_name="\ud800")).encode(),
        json.dumps(payload(lesson_id="\ud800")).encode(),
    ],
)
def test_malformed_outer_json_is_fixed_422_without_echo_or_dispatch(client, monkeypatch, raw):
    execute = Mock()
    monkeypatch.setattr(experiment, "execute_tool", execute)
    response = client.post(PATH, content=raw, headers={"Content-Type": "application/json"})
    assert_outer_rejection(response, 422, execute)
    assert response.json() == {"detail": "工具契约请求无效"}


@pytest.mark.parametrize(
    "error",
    [RuntimeError("private-server-diagnostic"), HTTPException(503, "private-server-diagnostic")],
)
def test_internal_tool_failure_is_fixed_500_without_raw_input_or_diagnostics(
    client, monkeypatch, caplog, error
):
    execute = Mock(side_effect=error)
    monkeypatch.setattr(experiment, "execute_tool", execute)
    request = payload(arguments_json='{"query":"private-user-marker"}')
    response = client.post(PATH, json=request)
    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "工具契约实验暂不可用"}
    assert "private-" not in response.text + caplog.text
    execute.assert_called_once()


def test_unknown_internal_observation_is_not_mislabeled_as_a_teaching_rejection(
    client, monkeypatch
):
    monkeypatch.setattr(
        experiment, "execute_tool", Mock(return_value={"error": "private-unexpected-error"})
    )
    response = client.post(PATH, json=payload())
    assert response.status_code == 500 and response.json() == {"detail": "工具契约实验暂不可用"}
