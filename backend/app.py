import ast
import asyncio
import hmac
import json
import os
import time
from collections import defaultdict, deque
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.curriculum import LESSONS, TRACKS
from backend.feed import get_feed
from backend.providers import PROVIDERS, Provider, capabilities, generate

app = FastAPI(
    title="Deep AI Station", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Playground-Token"],
)


@app.middleware("http")
async def request_limits(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > 50_000):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "请求内容过大"}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Request-ID"] = str(uuid4())
    return response


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0", "lessons": len(LESSONS)}


@app.get("/api/curriculum")
def curriculum():
    return {"tracks": TRACKS, "version": 1}


@app.get("/api/lessons/{lesson_id}")
def lesson(lesson_id: str):
    if lesson_id not in LESSONS:
        raise HTTPException(404, "课时不存在")
    return LESSONS[lesson_id]


@app.get("/api/search")
def search(
    q: str = Query(min_length=1, max_length=100), track: Literal["agent", "fullstack"] | None = None
):
    words = q.strip().lower().split()
    if not words:
        return {"items": []}
    ranked = []
    for item in LESSONS.values():
        if track and item["track"] != track:
            continue
        title = item["title"].lower()
        corpus = (item["objective"] + " ".join(item["body"]) + title).lower()
        score = sum(3 if word in title else 1 if word in corpus else 0 for word in words)
        if score:
            ranked.append(
                (
                    score,
                    {
                        "id": item["id"],
                        "title": item["title"],
                        "objective": item["objective"],
                        "track": item["track"],
                    },
                )
            )
    ranked.sort(key=lambda x: x[0], reverse=True)
    return {"items": [x[1] for x in ranked[:12]]}


@app.get("/api/feed")
async def feed(refresh: bool = False):
    return await get_feed(refresh)


@app.get("/api/capabilities")
def get_capabilities():
    return capabilities()


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=4000)
    system: str = Field(
        default="你是一位严谨的 AI 工程导师。给出可验证的步骤，明确不确定性。", max_length=2000
    )
    provider: Provider = "demo"
    track: Literal["agent", "fullstack"] = "agent"
    temperature: float = Field(default=0.3, ge=0, le=1)


_live_requests: dict[str, deque] = defaultdict(deque)


def authorize_live(token: str | None):
    expected = os.getenv("PLAYGROUND_ACCESS_TOKEN")
    if not expected or not token or not hmac.compare_digest(expected, token):
        raise HTTPException(401, "真实模型调用需要有效的实验访问码")
    # One shared access code has a bounded in-process quota. Production multi-instance
    # budgets additionally require provider-side quotas or a shared rate-limit store.
    queue = _live_requests["live"]
    now = time.monotonic()
    while queue and now - queue[0] > 60:
        queue.popleft()
    if len(queue) >= 10:
        raise HTTPException(429, "运行过于频繁，请一分钟后重试")
    queue.append(now)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/playground/run")
async def run(
    body: RunInput, request: Request, x_playground_token: str | None = Header(default=None)
):
    if not body.prompt.strip():
        raise HTTPException(422, "请输入任务")
    if body.provider != "demo":
        if not os.getenv(PROVIDERS[body.provider]["key"]):
            raise HTTPException(503, "该模型尚未配置")
        authorize_live(x_playground_token)
    run_id = str(uuid4())

    async def events():
        start = time.monotonic()
        yield sse("start", {"run_id": run_id, "mode": body.provider})
        yield sse(
            "trace",
            {
                "title": "输入校验完成",
                "detail": f"任务已接收 · {len(body.prompt)} 字符",
                "status": "success",
            },
        )
        if body.provider == "demo":
            await asyncio.sleep(0.12)
            words = [word for word in body.prompt.lower().split() if len(word) > 1]
            pool = [x for x in LESSONS.values() if x["track"] == body.track]
            scored = sorted(
                pool,
                key=lambda x: sum(
                    word in (x["title"] + " ".join(x["body"])).lower() for word in words
                ),
                reverse=True,
            )
            selected = scored[:3]
            yield sse(
                "trace",
                {
                    "title": "knowledge_search",
                    "detail": f"从课程索引读取 {len(selected)} 条资料 · 只读工具",
                    "status": "success",
                },
            )
            await asyncio.sleep(0.12)
            answer = (
                "### 教学演示 · 课程检索工作流\n\n这是确定性的课程检索演示，没有调用语言模型。你的任务是：\n\n> "
                + body.prompt.replace("\n", " ")
                + "\n\n可从以下课程开始：\n\n"
                + "\n\n".join(
                    f"**{i + 1}. {x['title']}**\n\n{x['body'][0]}\n\n实践：{x['steps'][0]}。\n\n[官方资料]({x['resources'][0]['url']})"
                    for i, x in enumerate(selected)
                )
            )
            yield sse(
                "trace",
                {
                    "title": "证据整理完成",
                    "detail": "仅使用课程索引中的资料，保留官方来源",
                    "status": "success",
                },
            )
            result = {"answer": answer, "usage": None, "model": None}
        else:
            yield sse(
                "trace",
                {
                    "title": "请求模型服务",
                    "detail": f"{body.provider} · 输出上限 1200 tokens",
                    "status": "running",
                },
            )
            try:
                result = await generate(body.provider, body.prompt, body.system, body.temperature)
            except HTTPException as exc:
                yield sse(
                    "error", {"message": exc.detail, "code": exc.status_code, "run_id": run_id}
                )
                return
        if await request.is_disconnected():
            return
        # Response chunks are UI delivery chunks. Provider generation is non-streaming.
        for index in range(0, len(result["answer"]), 60):
            if await request.is_disconnected():
                return
            yield sse("delta", {"text": result["answer"][index : index + 60]})
            await asyncio.sleep(0.015)
        yield sse(
            "done",
            {
                "run_id": run_id,
                "mode": body.provider,
                "model": result["model"],
                "usage": result["usage"],
                "duration_ms": round((time.monotonic() - start) * 1000),
            },
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


class CodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["python", "typescript", "go"]
    code: str = Field(min_length=1, max_length=20000)


@app.post("/api/playground/check")
def check_code(body: CodeInput):
    checks = []
    if body.language == "python":
        try:
            tree = ast.parse(body.code)
            checks.append({"title": "Python 语法解析", "passed": True})
            functions = [
                x for x in ast.walk(tree) if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            checks.append({"title": "包含可复用函数", "passed": bool(functions)})
        except SyntaxError as exc:
            checks.append(
                {"title": f"Python 语法 · 第 {exc.lineno} 行：{exc.msg}", "passed": False}
            )
    elif body.language == "typescript":
        checks.append(
            {
                "title": "包含函数或路由声明（文本检查）",
                "passed": any(x in body.code for x in ["function ", "=>", "app.get("]),
            }
        )
        checks.append({"title": "包含导出边界（文本检查）", "passed": "export " in body.code})
    else:
        checks.append({"title": "包含 package 声明（文本检查）", "passed": "package " in body.code})
        checks.append({"title": "包含函数声明（文本检查）", "passed": "func " in body.code})
    checks.append({"title": "未遗留 TODO 占位标记", "passed": "TODO" not in body.code})
    return {
        "mode": "static-check",
        "executed": False,
        "checks": checks,
        "passed": all(x["passed"] for x in checks),
        "notice": "仅静态检查；不编译、不运行代码，不证明业务行为正确。Python 使用 AST 语法解析，TS/Go 使用文本检查。",
    }
