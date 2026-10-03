"""Maintainer-provided capstone API; never executes learner code."""

import asyncio
import hmac
import os
import time
from collections import deque
from typing import Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from engine import DOCUMENTS, demo, generate


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    prompt: str = Field(min_length=1, max_length=1000)
    mode: Literal["demo", "openai"] = "demo"


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > 16384:
                return await JSONResponse({"error": "request_too_large"}, 413)(scope, receive, send)
            chunks.append(body)
            if not message.get("more_body", False):
                break
        delivered = False

        async def buffered():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, buffered, send)


def create_app(client_factory=httpx.AsyncClient):
    api = FastAPI(
        title="AI Research Agent Capstone", docs_url="/api/docs", openapi_url="/api/openapi.json"
    )
    api.add_middleware(BodyLimit)
    quota = deque()

    @api.exception_handler(HTTPException)
    async def error(_request, exception):
        return JSONResponse({"error": exception.detail}, exception.status_code)

    from fastapi.exceptions import RequestValidationError

    @api.exception_handler(RequestValidationError)
    async def invalid(_request, _exception):
        return JSONResponse({"error": "invalid_input"}, 422)

    @api.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "framework": "fastapi",
            "documents": len(DOCUMENTS),
            "workflow": "research-agent",
        }

    @api.post("/api/ask")
    async def ask(body: Question, request: Request, x_playground_token: str | None = Header(None)):
        if not body.prompt.strip():
            raise HTTPException(422, "invalid_input")
        if body.mode == "openai":
            expected = os.getenv("PLAYGROUND_ACCESS_TOKEN", "")
            if (
                not expected
                or not x_playground_token
                or not hmac.compare_digest(expected.encode(), x_playground_token.encode())
            ):
                raise HTTPException(401, "access_required")
            if not os.getenv("OPENAI_API_KEY"):
                raise HTTPException(503, "provider_not_configured")
        if body.mode == "demo":
            return demo(body.prompt)

        def charge():
            now = time.monotonic()
            while quota and now - quota[0] >= 60:
                quota.popleft()
            if len(quota) >= 10:
                raise HTTPException(429, "rate_limited")
            quota.append(now)

        task = asyncio.create_task(generate(body.prompt, charge, client_factory))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.1)
                if await request.is_disconnected():
                    raise HTTPException(499, "client_disconnected")
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return api


app = create_app()
