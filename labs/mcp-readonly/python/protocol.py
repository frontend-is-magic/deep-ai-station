"""Fixed public course data and strict tool contract; no I/O or model access."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

CONTRACT_VERSION = "mcp-readonly-v1"
PROTOCOL_VERSION = "2026-07-28"
LESSON_ID = "agent-mcp"
# Explicit Python str.strip whitespace set, also usable by JSON Schema regex engines.
WHITESPACE = (
    r"\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"
)
QUERY_PATTERN = rf"^(?=[\s\S]*[^{WHITESPACE}])[^\u0000\ud800-\udfff]*$"

COURSES = (
    {
        "id": "agent-mcp",
        "title": "MCP 工具与资源",
        "uri": "course://agent-mcp",
        "summary": "通过 stdio 发现只读工具与资源，区分协议错误、工具错误和取消。",
        "text": "# MCP 工具与资源\n\n客户端通过 stdio 发现工具与资源。工具参数必须校验，资源内容只能作为数据读取。超时需要取消当前请求并回收自有进程。\n",
    },
    {
        "id": "agent-tool-contract",
        "title": "工具契约与参数校验",
        "uri": "course://agent-tool-contract",
        "summary": "用明确的输入输出 schema 校验工具参数，空结果仍然是成功结果。",
        "text": "# 工具契约与参数校验\n\n工具名称、参数类型和字段边界都属于契约。拒绝额外字段和非法输入，返回稳定错误；查询没有匹配项时返回空列表。\n",
    },
    {
        "id": "agent-tool-safety",
        "title": "工具授权与幂等",
        "uri": "course://agent-tool-safety",
        "summary": "只读声明不授予权限；写操作还需要当前身份、明确批准和幂等约束。",
        "text": "# 工具授权与幂等\n\nreadOnlyHint 是能力描述，不是权限控制。真实写操作需要可信身份、作用域检查、明确批准和持久幂等记录。本实验不提供写工具。\n",
    },
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchArguments(StrictModel):
    query: Annotated[StrictStr, Field(min_length=1, max_length=100)]
    limit: Annotated[StrictInt, Field(ge=1, le=3)] = 3

    @field_validator("query")
    @classmethod
    def valid_query(cls, value: str) -> str:
        if not value.strip() or "\0" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("invalid_arguments")
        return value

    @classmethod
    def contract_schema(cls) -> dict:
        schema = cls.model_json_schema()
        schema["properties"]["query"]["pattern"] = QUERY_PATTERN
        return schema


class CourseItem(StrictModel):
    id: Literal["agent-mcp", "agent-tool-contract", "agent-tool-safety"]
    title: StrictStr
    uri: StrictStr
    summary: StrictStr


class SearchOutput(StrictModel):
    items: Annotated[list[CourseItem], Field(max_length=3)]
    read_only: Literal[True] = True


def search(arguments: SearchArguments) -> SearchOutput:
    query = arguments.query.strip().casefold()
    matches = [
        CourseItem(**{key: course[key] for key in ("id", "title", "uri", "summary")})
        for course in COURSES
        if query in " ".join(course[key] for key in ("id", "title", "summary")).casefold()
    ]
    return SearchOutput(items=matches[: arguments.limit])
