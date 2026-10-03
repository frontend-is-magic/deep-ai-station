"""可独立运行的 FastAPI 契约实验，入口为 uvicorn app:app。"""

import json
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from repository import FixedRepository, Repository, ascii_lower
from service import LessonNotFound, LessonService, RepositoryUnavailable

MAX_BODY_BYTES = 4096
# Unicode White_Space；Python 默认 strip 还会移除 U+001C–U+001F。
TRIM_WHITESPACE = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u0085\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    question: str = Field(min_length=1, max_length=500)

    @field_validator("question", mode="before")
    @classmethod
    def trim_question(cls, value: object) -> object:
        if isinstance(value, str):
            # JSON 转义也可能带入无法以 UTF-8 输出的孤立代理码点。
            value.encode("utf-8", errors="strict")
            return value.strip(TRIM_WHITESPACE)
        return value


class BodyLimit:
    """计数实际接收的分块字节，不依赖可伪造或缺失的 Content-Length。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                await JSONResponse({"error": "request_too_large"}, 413)(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)


_default_repository = FixedRepository.from_fixture()


def get_repository() -> Repository:
    return _default_repository


def get_service(repository: Annotated[Repository, Depends(get_repository)]) -> LessonService:
    return LessonService(repository)


def reject_non_json_constant(value: str):
    # Python 的 json.loads 默认容忍 NaN / Infinity，但它们不属于 JSON。
    raise ValueError("invalid JSON constant")


app = FastAPI(title="API 契约实验", version="1.0.0")
app.add_middleware(BodyLimit)


@app.exception_handler(LessonNotFound)
async def lesson_not_found(request: Request, error: LessonNotFound):
    return JSONResponse({"error": "lesson_not_found"}, 404)


@app.exception_handler(RepositoryUnavailable)
async def repository_unavailable(request: Request, error: RepositoryUnavailable):
    return JSONResponse({"error": "repository_unavailable"}, 503)


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException):
    names = {404: "not_found", 405: "method_not_allowed"}
    return JSONResponse(
        {"error": names.get(error.status_code, "invalid_request")}, error.status_code
    )


@app.get("/health")
async def health():
    return {"status": "ok", "lab": "api-contract-v1"}


@app.get("/lessons/{lesson_id}")
async def get_lesson(lesson_id: str, service: Annotated[LessonService, Depends(get_service)]):
    return service.find(lesson_id)


@app.post("/search")
async def search(request: Request, service: Annotated[LessonService, Depends(get_service)]):
    media_type = ascii_lower(request.headers.get("content-type", "").split(";", 1)[0].strip(" \t"))
    if media_type != "application/json":
        return JSONResponse({"error": "unsupported_media_type"}, 415)
    try:
        body = (await request.body()).decode("utf-8", errors="strict")
        payload = json.loads(body, parse_constant=reject_non_json_constant)
        question = SearchInput.model_validate(payload).question
    except (ValueError, UnicodeError, ValidationError, RecursionError):
        return JSONResponse({"error": "invalid_input"}, 422)
    return service.search(question)
