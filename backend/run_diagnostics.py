"""Free, fixed local trace exercises using the existing read-only course tools."""

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.agent_loop import course_item, execute_tool
from backend.curriculum import LESSONS, TRACKS

CONTRACT_VERSION = "run-diagnostics-v1"
SUPPORTED_LESSONS = {"agent-tracing": "agent", "fullstack-observability": "fullstack"}
SCENARIOS = ("success", "no_evidence", "invalid_arguments", "timeout")
PHASES = ("search", "read", "summary")
MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 32768
LOCAL_TIMEOUT_SECONDS = 0.025
LOCAL_WAIT_SECONDS = 0.2
NO_MATCH_QUERY = "zzqxjx-diagnostics-no-match-976238"
QUERIES = {"agent": "Trace", "fullstack": "可观测性"}
router = APIRouter(prefix="/api/playground/diagnostics")


class DiagnosticsRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    track: Literal["agent", "fullstack"]
    lesson_id: str = Field(min_length=1, max_length=100)
    scenario: Literal["success", "no_evidence", "invalid_arguments", "timeout"]

    @field_validator("lesson_id")
    @classmethod
    def valid_utf8(cls, value):
        try:
            value.encode("utf-8", "strict")
        except UnicodeError:
            raise ValueError("invalid_utf8") from None
        return value


def require(condition):
    if not condition:
        raise ValueError("invalid_diagnostics_evidence")


def shape(value, keys):
    return type(value) is dict and set(value) == set(keys)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("nonfinite")


def parse_request(raw: bytes) -> DiagnosticsRequest:
    require(len(raw) <= MAX_REQUEST_BYTES)
    text = raw.decode("utf-8", "strict")
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            require(depth <= 8)
        elif char in "]}":
            depth -= 1
    value = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    return DiagnosticsRequest.model_validate(value)


def valid_lesson(body: DiagnosticsRequest) -> bool:
    # LESSONS is a dict, so inspect the actual catalog too: duplicate IDs must not
    # become a silently overwritten, apparently unique target.
    found = [
        (track, lesson)
        for track in TRACKS
        for lesson in track["lessons"]
        if lesson["id"] == body.lesson_id
    ]
    return (
        len(found) == 1
        and found[0][0]["id"] == body.track
        and found[0][1]["track"] == body.track
        and LESSONS.get(body.lesson_id) == found[0][1]
        and SUPPORTED_LESSONS.get(body.lesson_id) == body.track
    )


def check_observation(observation, tool_name, track, operation_id, target=None):
    require(type(observation) is dict)
    require(observation.get("read_only") is True)
    require(observation.get("operation_id") == operation_id)
    base = {"read_only", "operation_id"}
    if "error" in observation:
        require(shape(observation, base | {"error"}))
        require(observation["error"] == "invalid_arguments")
        return
    if tool_name == "knowledge_search":
        require(shape(observation, base | {"items"}))
        items = observation["items"]
        require(type(items) is list and len(items) <= 3)
    else:
        require(shape(observation, base | {"lesson"}))
        lesson = observation["lesson"]
        require(type(lesson) is dict and lesson.get("id") == target)
        original = LESSONS.get(target)
        require(original is not None)
        require(
            lesson
            == {
                **course_item(original),
                "explanation": original["body"][:2],
                "steps": original["steps"],
                "criteria": original["criteria"],
            }
        )
        items = [course_item(original)]
    seen = set()
    for item in items:
        require(type(item) is dict)
        identifier = item.get("id")
        require(type(identifier) is str and identifier not in seen)
        original = LESSONS.get(identifier)
        require(original is not None and original["track"] == track)
        require(item == course_item(original))
        require(type(item["title"]) is str and 1 <= len(item["title"]) <= 200)
        require(type(item["summary"]) is str and 1 <= len(item["summary"]) <= 800)
        require(
            type(item["source"]) is str
            and item["source"].startswith("https://")
            and len(item["source"]) <= 2048
        )
        seen.add(identifier)


async def local_wait():
    await asyncio.sleep(LOCAL_WAIT_SECONDS)


async def run_diagnostics(
    body: DiagnosticsRequest,
    *,
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[], Awaitable[None]] = local_wait,
    timeout_seconds: float = LOCAL_TIMEOUT_SECONDS,
) -> dict:
    """Trusted test seams only; none are accepted from the HTTP request."""
    run_id = str(uuid4())
    origin = clock()
    require(math.isfinite(origin))
    last = 0.0

    def stamp():
        nonlocal last
        value = (clock() - origin) * 1000
        require(math.isfinite(value) and value >= last)
        last = value
        return value

    stages = [
        {
            "id": f"{run_id}:{name}",
            "parent_id": f"{run_id}:root",
            "name": name,
            "status": "skipped",
            "start_ms": None,
            "end_ms": None,
            "duration_ms": None,
            "tool_name": None,
            "operation_id": None,
            "tool_dispatch_count": 0,
            "error_code": None,
        }
        for name in PHASES
    ]
    report = {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        **body.model_dump(),
        "read_only": True,
        "model_calls": 0,
        "outcome": "no_evidence",
        "ended_at_stage": "search",
        "tool_dispatch_count": 0,
        "root": {
            "id": f"{run_id}:root",
            "parent_id": None,
            "status": "passed",
            "start_ms": 0,
            "end_ms": 0,
            "duration_ms": 0,
        },
        "stages": stages,
        "evidence": {"found_ids": [], "read_lesson": None, "summary": None},
        "cleanup_completed": False,
        "timeout_wait_cancelled": False,
    }

    def start(stage):
        stage["start_ms"] = stamp()

    def finish(stage, status="passed", error=None):
        stage["status"] = status
        stage["end_ms"] = stamp()
        stage["duration_ms"] = stage["end_ms"] - stage["start_ms"]
        stage["error_code"] = error
        report["ended_at_stage"] = stage["name"]

    def dispatch(stage, name, arguments):
        report["tool_dispatch_count"] += 1
        require(report["tool_dispatch_count"] <= 2)
        operation = f"{run_id}:{report['tool_dispatch_count']}"
        stage.update(tool_name=name, operation_id=operation, tool_dispatch_count=1)
        observation = execute_tool(name, arguments, body.track, operation)
        check_observation(observation, name, body.track, operation, arguments.get("lesson_id"))
        return observation

    async def timed_wait():
        try:
            await wait()
        except asyncio.CancelledError:
            report["timeout_wait_cancelled"] = True
            raise
        finally:
            # Actual coroutine cleanup, not a simulated remote resource receipt.
            wait_cleanup.append(True)

    wait_cleanup = []
    try:
        search, read, summary = stages
        start(search)
        query = (
            42
            if body.scenario == "invalid_arguments"
            else NO_MATCH_QUERY
            if body.scenario == "no_evidence"
            else QUERIES[body.track]
        )
        observation = dispatch(search, "knowledge_search", {"query": query})
        if "error" in observation:
            finish(search, "rejected", "invalid_arguments")
            report["outcome"] = "rejected"
        else:
            report["evidence"]["found_ids"] = [item["id"] for item in observation["items"]]
            finish(search)
            if observation["items"]:
                start(read)
                timed_out = False
                if body.scenario == "timeout":
                    try:
                        async with asyncio.timeout(timeout_seconds):
                            await timed_wait()
                    except TimeoutError:
                        require(report["timeout_wait_cancelled"] and wait_cleanup == [True])
                        finish(read, "timed_out", "deadline_exceeded")
                        report["outcome"] = "timed_out"
                        timed_out = True
                if not timed_out:
                    found = observation["items"][0]["id"]
                    observation = dispatch(read, "lesson_read", {"lesson_id": found})
                    if "error" in observation:
                        finish(read, "rejected", "invalid_arguments")
                        report["outcome"] = "rejected"
                    else:
                        lesson = observation["lesson"]
                        report["evidence"]["read_lesson"] = {
                            key: lesson[key] for key in ("id", "title", "summary", "source")
                        }
                        finish(read)
                        start(summary)
                        report["evidence"]["summary"] = lesson["title"] + "：" + lesson["summary"]
                        finish(summary)
                        report["outcome"] = "success"
    finally:
        report["root"]["end_ms"] = stamp()
        report["root"]["duration_ms"] = report["root"]["end_ms"]
        report["root"]["status"] = {"rejected": "rejected", "timed_out": "timed_out"}.get(
            report["outcome"], "passed"
        )
        report["cleanup_completed"] = True
    validate_report(report)
    return report


def validate_report(report):
    """Fail closed if internal timing/count/evidence relations ever drift."""
    require(report["cleanup_completed"] is True and report["model_calls"] == 0)
    stages = report["stages"]
    require(len(stages) == 3 and [stage["name"] for stage in stages] == list(PHASES))
    last_end, count = 0, 0
    for stage in stages:
        if stage["status"] == "skipped":
            require(
                all(
                    stage[key] is None
                    for key in (
                        "start_ms",
                        "end_ms",
                        "duration_ms",
                        "tool_name",
                        "operation_id",
                        "error_code",
                    )
                )
            )
            require(stage["tool_dispatch_count"] == 0)
            continue
        start, end, duration = (stage[key] for key in ("start_ms", "end_ms", "duration_ms"))
        require(
            all(
                type(value) in (int, float) and math.isfinite(value)
                for value in (start, end, duration)
            )
        )
        require(last_end <= start <= end <= report["root"]["end_ms"] and duration == end - start)
        last_end = end
        count += stage["tool_dispatch_count"]
        if stage["tool_dispatch_count"]:
            require(stage["operation_id"] == f"{report['run_id']}:{count}")
        else:
            require(stage["operation_id"] is None and stage["tool_name"] is None)
    require(count == report["tool_dispatch_count"] and count in (1, 2))
    states = [stage["status"] for stage in stages]
    outcome = report["outcome"]
    evidence = report["evidence"]
    if outcome == "success":
        require(states == ["passed"] * 3 and count == 2 and report["ended_at_stage"] == "summary")
        require(evidence["found_ids"] and evidence["read_lesson"] is not None)
        lesson = evidence["read_lesson"]
        require(lesson["id"] == evidence["found_ids"][0])
        require(evidence["summary"] == lesson["title"] + "：" + lesson["summary"])
    else:
        require(evidence["read_lesson"] is None and evidence["summary"] is None)
        if outcome == "no_evidence":
            require(
                states == ["passed", "skipped", "skipped"]
                and count == 1
                and not evidence["found_ids"]
            )
            require(report["ended_at_stage"] == "search")
        elif outcome == "rejected":
            require(
                states in (["rejected", "skipped", "skipped"], ["passed", "rejected", "skipped"])
            )
            require(report["ended_at_stage"] == ("search" if count == 1 else "read"))
        else:
            require(
                outcome == "timed_out"
                and states == ["passed", "timed_out", "skipped"]
                and count == 1
            )
            require(evidence["found_ids"] and report["ended_at_stage"] == "read")
    require(report["timeout_wait_cancelled"] is (outcome == "timed_out"))


def failure(status, message):
    return JSONResponse(
        {"detail": message}, status_code=status, headers={"Cache-Control": "no-store"}
    )


@router.post("")
async def diagnose(request: Request):
    try:
        raw = await request.body()
        body = parse_request(raw)
    except (ValueError, RecursionError, ValidationError):
        return failure(422, "运行诊断请求无效")
    try:
        if not valid_lesson(body):
            return failure(422, "运行诊断请求无效")
        report = await run_diagnostics(body)
        response = JSONResponse(report, headers={"Cache-Control": "no-store"})
        if len(response.body) > MAX_RESPONSE_BYTES:
            return failure(500, "运行诊断演练暂不可用")
        return response
    except Exception:
        return failure(500, "运行诊断演练暂不可用")
