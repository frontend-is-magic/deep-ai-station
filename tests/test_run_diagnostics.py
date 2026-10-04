"""Real course-tool dispatch, bounded local cancellation and private API errors."""

import asyncio
import json
from copy import deepcopy
from itertools import count
from unittest.mock import Mock
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

import backend.agent_loop as agent
import backend.app as application
import backend.providers as providers
import backend.quota as quota
import backend.run_diagnostics as experiment
import backend.sandbox as sandbox
from backend.curriculum import LESSONS

PATH = "/api/playground/diagnostics"


def payload(track="agent", scenario="success", **overrides):
    return {
        "track": track,
        "lesson_id": "agent-tracing" if track == "agent" else "fullstack-observability",
        "scenario": scenario,
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
        (application, "begin_model_attempt"),
    ]:
        mock = Mock(side_effect=AssertionError("Diagnostics must not call paid or external paths"))
        monkeypatch.setattr(target, name, mock)
        blocked.append(mock)
    with TestClient(application.app, raise_server_exceptions=False) as client:
        yield client
    for mock in blocked:
        mock.assert_not_called()


def report_of(response, request):
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert len(response.content) <= 32768
    report = response.json()
    assert set(report) == {
        "contract_version",
        "run_id",
        "track",
        "lesson_id",
        "scenario",
        "read_only",
        "model_calls",
        "outcome",
        "ended_at_stage",
        "tool_dispatch_count",
        "root",
        "stages",
        "evidence",
        "cleanup_completed",
        "timeout_wait_cancelled",
    }
    assert report["contract_version"] == "run-diagnostics-v1"
    identifier = UUID(report["run_id"])
    assert identifier.version == 4 and str(identifier) == report["run_id"]
    assert all(report[key] == value for key, value in request.items())
    assert (
        report["read_only"] is True
        and type(report["model_calls"]) is int
        and report["model_calls"] == 0
    )
    assert report["cleanup_completed"] is True
    root = report["root"]
    assert root["id"] == report["run_id"] + ":root" and root["parent_id"] is None
    assert root["start_ms"] == 0 and root["duration_ms"] == root["end_ms"]
    previous = 0
    assert [stage["name"] for stage in report["stages"]] == ["search", "read", "summary"]
    for stage in report["stages"]:
        assert stage["id"] == report["run_id"] + ":" + stage["name"]
        assert stage["parent_id"] == root["id"]
        if stage["status"] == "skipped":
            assert all(
                stage[key] is None
                for key in (
                    "start_ms",
                    "end_ms",
                    "duration_ms",
                    "operation_id",
                    "tool_name",
                    "error_code",
                )
            )
            assert stage["tool_dispatch_count"] == 0
        else:
            assert previous <= stage["start_ms"] <= stage["end_ms"] <= root["end_ms"]
            assert stage["duration_ms"] == stage["end_ms"] - stage["start_ms"]
            previous = stage["end_ms"]
    assert (
        sum(stage["tool_dispatch_count"] for stage in report["stages"])
        == report["tool_dispatch_count"]
    )
    return report


@pytest.mark.parametrize("track", ["agent", "fullstack"])
@pytest.mark.parametrize(
    "scenario,outcome,ended,count,states",
    [
        ("success", "success", "summary", 2, ["passed", "passed", "passed"]),
        ("no_evidence", "no_evidence", "search", 1, ["passed", "skipped", "skipped"]),
        ("invalid_arguments", "rejected", "search", 1, ["rejected", "skipped", "skipped"]),
        ("timeout", "timed_out", "read", 1, ["passed", "timed_out", "skipped"]),
    ],
)
def test_real_course_tools_and_actual_phase_outcomes(
    client, monkeypatch, track, scenario, outcome, ended, count, states
):
    dispatch = Mock(wraps=agent.execute_tool)
    monkeypatch.setattr(experiment, "execute_tool", dispatch)
    request = payload(track, scenario)
    report = report_of(client.post(PATH, json=request), request)
    assert (report["outcome"], report["ended_at_stage"], report["tool_dispatch_count"]) == (
        outcome,
        ended,
        count,
    )
    assert [stage["status"] for stage in report["stages"]] == states
    assert dispatch.call_count == count
    assert dispatch.call_args_list[0].args == (
        "knowledge_search",
        {
            "query": 42
            if scenario == "invalid_arguments"
            else experiment.NO_MATCH_QUERY
            if scenario == "no_evidence"
            else experiment.QUERIES[track]
        },
        track,
        report["run_id"] + ":1",
    )
    if outcome == "success":
        evidence = report["evidence"]
        selected = LESSONS[evidence["found_ids"][0]]
        assert selected["track"] == track
        assert evidence["read_lesson"] == {
            "id": selected["id"],
            "title": selected["title"],
            "summary": selected["body"][0][:800],
            "source": selected["resources"][0]["url"],
        }
        assert evidence["summary"] == selected["title"] + "：" + selected["body"][0][:800]
        assert dispatch.call_args_list[1].args == (
            "lesson_read",
            {"lesson_id": selected["id"]},
            track,
            report["run_id"] + ":2",
        )
    else:
        assert report["evidence"]["read_lesson"] is None and report["evidence"]["summary"] is None
    if outcome == "timed_out":
        read = report["stages"][1]
        assert read["duration_ms"] >= 25
        assert read["operation_id"] is None and read["tool_name"] is None
        assert read["error_code"] == "deadline_exceeded"
    if outcome == "no_evidence":
        assert report["root"]["status"] == "passed"
        assert report["evidence"]["found_ids"] == []
    assert report["timeout_wait_cancelled"] is (outcome == "timed_out")


def test_invalid_arguments_really_dispatch_then_existing_schema_blocks_retrieval(
    client, monkeypatch
):
    retrieve = Mock(side_effect=AssertionError("Invalid parameters cannot reach retrieval"))
    monkeypatch.setattr(agent, "retrieve", retrieve)
    request = payload(scenario="invalid_arguments")
    report = report_of(client.post(PATH, json=request), request)
    assert report["stages"][0]["error_code"] == "invalid_arguments"
    assert report["tool_dispatch_count"] == 1
    retrieve.assert_not_called()


@pytest.mark.parametrize("scenario", ["success", "timeout", "invalid_arguments"])
def test_actual_empty_observation_overrides_scenario_and_skips_read(client, monkeypatch, scenario):
    def empty(name, arguments, track, operation_id):
        assert name == "knowledge_search"
        return {"read_only": True, "operation_id": operation_id, "items": []}

    monkeypatch.setattr(experiment, "execute_tool", empty)
    request = payload(scenario=scenario)
    report = report_of(client.post(PATH, json=request), request)
    assert report["outcome"] == "no_evidence" and report["tool_dispatch_count"] == 1
    assert report["timeout_wait_cancelled"] is False


def test_no_evidence_scenario_can_follow_actual_nonempty_observation(client, monkeypatch):
    def with_evidence(name, arguments, track, operation_id):
        if name == "knowledge_search":
            arguments = {"query": "Trace"}
        return agent.execute_tool(name, arguments, track, operation_id)

    monkeypatch.setattr(experiment, "execute_tool", with_evidence)
    request = payload(scenario="no_evidence")
    report = report_of(client.post(PATH, json=request), request)
    assert report["outcome"] == "success" and report["tool_dispatch_count"] == 2


def test_actual_read_rejection_stops_before_summary(client, monkeypatch):
    def reject_read(name, arguments, track, operation_id):
        if name == "lesson_read":
            return agent.execute_tool(name, {"lesson_id": 42}, track, operation_id)
        return agent.execute_tool(name, arguments, track, operation_id)

    monkeypatch.setattr(experiment, "execute_tool", reject_read)
    request = payload()
    report = report_of(client.post(PATH, json=request), request)
    assert (report["outcome"], report["ended_at_stage"], report["tool_dispatch_count"]) == (
        "rejected",
        "read",
        2,
    )
    assert report["stages"][1]["error_code"] == "invalid_arguments"
    assert report["stages"][2]["status"] == "skipped"


def test_each_request_uses_a_new_id(client):
    ids = [client.post(PATH, json=payload()).json()["run_id"] for _ in range(2)]
    assert ids[0] != ids[1]


@pytest.mark.parametrize(
    "change",
    [
        {"track": "bad"},
        {"track": "fullstack"},
        {"lesson_id": "agent-missing"},
        {"lesson_id": "agent-mcp"},
        {"lesson_id": ""},
        {"lesson_id": "a" * 101},
        {"lesson_id": 42},
        {"scenario": "unknown"},
        {"scenario": None},
        {"scenario": True},
        {"query": "private-sentinel"},
    ],
)
def test_invalid_outer_request_has_no_dispatch(client, monkeypatch, change):
    dispatch = Mock()
    monkeypatch.setattr(experiment, "execute_tool", dispatch)
    response = client.post(PATH, json={**payload(), **change})
    assert response.status_code == 422
    assert response.json() == {"detail": "运行诊断请求无效"}
    assert response.headers["cache-control"] == "no-store"
    dispatch.assert_not_called()


@pytest.mark.parametrize("field", ["track", "lesson_id", "scenario"])
def test_every_outer_field_is_required(client, monkeypatch, field):
    request = payload()
    del request[field]
    dispatch = Mock()
    monkeypatch.setattr(experiment, "execute_tool", dispatch)
    assert client.post(PATH, json=request).status_code == 422
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "raw",
    [
        b"null",
        b"[]",
        b"false",
        b"not-json",
        b"\xff",
        b"\xef\xbb\xbf{}",
        b'{"track":"agent","track":"fullstack"}',
        b'{"scenario":NaN}',
        b'{"scenario":1e999}',
        b"[" * 9 + b"]" * 9,
        b'{"lesson_id":"\\ud800","track":"agent","scenario":"success"}',
    ],
)
def test_raw_invalid_request_never_echoes_or_dispatches(client, monkeypatch, raw):
    dispatch = Mock()
    monkeypatch.setattr(experiment, "execute_tool", dispatch)
    response = client.post(PATH, content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and response.json() == {"detail": "运行诊断请求无效"}
    assert response.headers["cache-control"] == "no-store"
    dispatch.assert_not_called()


def test_actual_request_byte_limit_includes_json_whitespace(client, monkeypatch):
    raw = json.dumps(payload()).encode()
    raw += b" " * (4096 - len(raw))
    assert client.post(PATH, content=raw).status_code == 200
    dispatch = Mock()
    monkeypatch.setattr(experiment, "execute_tool", dispatch)
    assert client.post(PATH, content=raw + b" ").status_code == 422
    dispatch.assert_not_called()


@pytest.mark.parametrize("damage", ["duplicate", "missing", "wrong-track"])
def test_target_must_be_unique_in_real_catalog(client, monkeypatch, damage):
    tracks = deepcopy(experiment.TRACKS)
    agent_track = next(track for track in tracks if track["id"] == "agent")
    target = next(lesson for lesson in agent_track["lessons"] if lesson["id"] == "agent-tracing")
    if damage == "duplicate":
        next(track for track in tracks if track["id"] == "fullstack")["lessons"].append(
            deepcopy(target)
        )
    elif damage == "missing":
        agent_track["lessons"].remove(target)
    else:
        target["track"] = "fullstack"
    monkeypatch.setattr(experiment, "TRACKS", tracks)
    dispatch = Mock()
    monkeypatch.setattr(experiment, "execute_tool", dispatch)
    assert client.post(PATH, json=payload()).status_code == 422
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "damage", ["exception", "unknown-error", "operation", "foreign", "wrong-shape", "oversized"]
)
def test_internal_observation_failure_is_fixed_500(client, monkeypatch, caplog, damage):
    def invalid(name, arguments, track, operation_id):
        if damage == "exception":
            raise RuntimeError("private-diagnostic-sentinel")
        if damage == "unknown-error":
            return {
                "read_only": True,
                "operation_id": operation_id,
                "error": "private-diagnostic-sentinel",
            }
        value = agent.execute_tool(name, arguments, track, operation_id)
        if damage == "operation":
            value["operation_id"] = "other-run"
        elif damage == "foreign":
            value["items"] = [agent.course_item(LESSONS["fullstack-observability"])]
        elif damage == "wrong-shape":
            value["extra"] = "private-diagnostic-sentinel"
        elif damage == "oversized":
            value["items"][0]["summary"] = "x" * 10000
        return value

    monkeypatch.setattr(experiment, "execute_tool", invalid)
    response = client.post(PATH, json=payload())
    assert response.status_code == 500 and response.json() == {"detail": "运行诊断演练暂不可用"}
    assert response.headers["cache-control"] == "no-store"
    assert "private-diagnostic-sentinel" not in response.text + caplog.text


def test_response_byte_budget_is_checked_before_publication(client, monkeypatch):
    monkeypatch.setattr(experiment, "MAX_RESPONSE_BYTES", 100)
    response = client.post(PATH, json=payload())
    assert response.status_code == 500 and response.json() == {"detail": "运行诊断演练暂不可用"}


async def test_monotonic_timings_are_measured_from_injected_clock():
    ticks = count(100)
    report = await experiment.run_diagnostics(
        experiment.DiagnosticsRequest(**payload()), clock=lambda: next(ticks) / 1000
    )
    assert report["root"]["duration_ms"] == pytest.approx(7)
    assert [stage["duration_ms"] for stage in report["stages"]] == pytest.approx([1, 1, 1])
    assert [stage["start_ms"] for stage in report["stages"]] == pytest.approx([1, 3, 5])


async def test_real_timeout_cancels_wait_and_cleans_before_report(monkeypatch):
    started, closed = asyncio.Event(), asyncio.Event()
    cancellations = []

    async def wait():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellations.append(True)
            raise
        finally:
            closed.set()

    dispatch = Mock(wraps=agent.execute_tool)
    monkeypatch.setattr(experiment, "execute_tool", dispatch)
    task = asyncio.create_task(
        experiment.run_diagnostics(
            experiment.DiagnosticsRequest(**payload(scenario="timeout")),
            wait=wait,
            timeout_seconds=0.01,
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    report = await asyncio.wait_for(task, 1)
    assert closed.is_set() and cancellations == [True]
    assert report["cleanup_completed"] and report["timeout_wait_cancelled"]
    assert report["outcome"] == "timed_out" and dispatch.call_count == 1


async def test_external_cancel_remains_cancelled_and_leaves_no_background_wait(monkeypatch):
    started, closed = asyncio.Event(), asyncio.Event()
    root_finally_observations = []

    async def wait():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    ticks = count()

    def clock():
        root_finally_observations.append(closed.is_set())
        return next(ticks)

    dispatch = Mock(wraps=agent.execute_tool)
    monkeypatch.setattr(experiment, "execute_tool", dispatch)
    existing = asyncio.all_tasks()
    task = asyncio.create_task(
        experiment.run_diagnostics(
            experiment.DiagnosticsRequest(**payload(scenario="timeout")),
            wait=wait,
            timeout_seconds=5,
            clock=clock,
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and root_finally_observations[-1]
    assert dispatch.call_count == 1
    assert not (asyncio.all_tasks() - existing)


async def test_wait_error_cannot_be_reported_as_a_real_timeout():
    async def broken():
        raise TimeoutError("private-diagnostic-sentinel")

    with pytest.raises(ValueError, match="invalid_diagnostics_evidence"):
        await experiment.run_diagnostics(
            experiment.DiagnosticsRequest(**payload(scenario="timeout")), wait=broken
        )
