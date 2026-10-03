"""Free, read-only exercises against the existing Agent tool contracts."""

import json
import math
from functools import wraps
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.agent_loop import TOOLS, execute_tool
from backend.curriculum import LESSONS

CONTRACT_VERSION = "tool-contract-v1"
SUPPORTED_LESSON_IDS = ("agent-structured-output", "agent-tool-contract")
MAX_ARGUMENTS_BYTES = 4096
MAX_JSON_DEPTH = 32
REJECTION_ERRORS = {"invalid_arguments", "unknown_tool", "lesson_not_in_track"}
EXAMPLES = (
    {
        "label": "检索 MCP 课程",
        "tool_name": "knowledge_search",
        "arguments_json": '{"query":"MCP"}',
    },
    {
        "label": "读取 Agent 课程",
        "tool_name": "lesson_read",
        "arguments_json": '{"lesson_id":"agent-mcp"}',
    },
    {
        "label": "拒绝额外字段",
        "tool_name": "knowledge_search",
        "arguments_json": '{"query":"MCP","limit":3}',
    },
    {"label": "拒绝数字类型", "tool_name": "knowledge_search", "arguments_json": '{"query":42}'},
    {
        "label": "拒绝跨路线读取",
        "tool_name": "lesson_read",
        "arguments_json": '{"lesson_id":"fullstack-routing"}',
    },
    {
        "label": "拒绝重复字段",
        "tool_name": "knowledge_search",
        "arguments_json": '{"query":"MCP","query":"工具"}',
    },
)


class ToolContractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    track: Literal["agent", "fullstack"]
    lesson_id: str = Field(min_length=1, max_length=100)
    tool_name: str = Field(min_length=1, max_length=100)
    arguments_json: str = Field(max_length=MAX_ARGUMENTS_BYTES)

    @field_validator("lesson_id", "tool_name", "arguments_json")
    @classmethod
    def valid_utf8(cls, value, info):
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("invalid_utf8") from None
        if info.field_name == "arguments_json" and len(encoded) > MAX_ARGUMENTS_BYTES:
            raise ValueError("arguments_too_large")
        return value


def failure(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"detail": message}, status_code=status, headers={"Cache-Control": "no-store"}
    )


class PrivateRoute(APIRoute):
    """Keep validation inputs and internal diagnostics out of this teaching endpoint."""

    def get_route_handler(self):
        original = super().get_route_handler()

        @wraps(original)
        async def handler(request: Request):
            if request.method == "POST":
                try:
                    # Cache the decoded body for FastAPI, including malformed UTF-8/deep JSON handling.
                    await request.json()
                except (ValueError, RecursionError):
                    return failure(422, "工具契约请求无效")
            try:
                response = await original(request)
            except RequestValidationError:
                return failure(422, "工具契约请求无效")
            except Exception:
                return failure(500, "工具契约实验暂不可用")
            response.headers["Cache-Control"] = "no-store"
            return response

        return handler


router = APIRouter(prefix="/api/playground/tool-contract", route_class=PrivateRoute)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("nonfinite_number")


def parse_arguments(raw: str) -> dict:
    # Bound nesting before invoking the recursive JSON decoder. Brackets inside strings don't count.
    depth, quoted, escaped = 0, False, False
    for character in raw:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("arguments_too_deep")
        elif character in "]}":
            depth -= 1
    result = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
    if not isinstance(result, dict):
        raise ValueError("object_required")
    pending = [result]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("nonfinite_number")
        elif isinstance(value, str):
            value.encode("utf-8", errors="strict")
    return result


@router.get("")
def tool_contract():
    return {
        "contract_version": CONTRACT_VERSION,
        "tools": TOOLS,
        "supported_lesson_ids": SUPPORTED_LESSON_IDS,
        "examples": EXAMPLES,
        "limits": {"arguments_bytes": MAX_ARGUMENTS_BYTES, "read_only": True, "model_calls": 0},
    }


@router.post("")
def run_tool_contract(body: ToolContractRequest):
    lesson = LESSONS.get(body.lesson_id)
    if lesson is None:
        return failure(404, "课时不存在")
    if lesson["track"] != body.track:
        return failure(422, "课时与学习方向不匹配")
    if body.lesson_id not in SUPPORTED_LESSON_IDS:
        return failure(422, "该课时不支持工具契约实验")
    run_id = str(uuid4())
    operation_id = f"{run_id}:1"
    try:
        arguments = parse_arguments(body.arguments_json)
    except (ValueError, RecursionError):
        observation = {
            "read_only": True,
            "operation_id": operation_id,
            "error": "invalid_arguments_json",
        }
    else:
        observation = execute_tool(body.tool_name, arguments, body.track, operation_id)
        if "error" in observation and observation["error"] not in REJECTION_ERRORS:
            raise ValueError("unexpected_tool_observation")
    return {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        **body.model_dump(),
        "model_calls": 0,
        "outcome": "rejected" if "error" in observation else "success",
        "observation": observation,
    }
