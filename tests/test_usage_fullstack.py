"""Real browser -> HTTP API -> provider parser, with controlled provider and admission."""

import asyncio
import json
import socket
import threading
import time
from contextlib import aclosing, contextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import uvicorn
from fastapi import HTTPException
from playwright.sync_api import expect, sync_playwright
from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from backend import agent_loop, providers
from backend import app as api_module

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[1]


def frame(value):
    data = value if isinstance(value, str) else json.dumps(value)
    return f"data: {data}\n\n".encode()


@contextmanager
def owned_server(application):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(application, log_level="critical", access_log=False, lifespan="off")
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("Owned usage test API did not start")
            time.sleep(0.02)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=2)
        listener.close()
        assert not thread.is_alive(), "Owned usage API thread did not stop"
        with socket.socket() as check:
            assert check.connect_ex(("127.0.0.1", port)) != 0, "Owned API listener was not released"


@pytest.mark.parametrize("ending", ["quota", "provider", "cancel"])
def test_usage_survives_actual_browser_api_failure_and_disconnect(monkeypatch, ending):
    assert (ROOT / "dist/index.html").is_file(), "Run pnpm build before the e2e suite"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixed-local-test-key")
    monkeypatch.setenv("PLAYGROUND_ACCESS_TOKEN", "fixed-local-test-access")
    monkeypatch.setenv("AI_QUOTA_MODE", "memory")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    calls = []
    admissions = []
    waiting = threading.Event()
    closed = threading.Event()

    class PausedProvider(httpx.AsyncByteStream):
        async def __aiter__(self):
            waiting.set()
            await asyncio.Event().wait()
            yield b""

        async def aclose(self):
            closed.set()

    first = b"".join(
        [
            frame(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "first-read",
                                        "type": "function",
                                        "function": {
                                            "name": "knowledge_search",
                                            "arguments": '{"query":"MCP"}',
                                        },
                                    }
                                ]
                            }
                        }
                    ],
                    "usage": None,
                }
            ),
            # The documented DeepSeek final content chunk carries usage before [DONE].
            frame(
                {
                    "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                }
            ),
            frame("[DONE]"),
        ]
    )

    def response(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(200, content=first)
        if ending == "provider":
            return httpx.Response(502, text="private-provider-diagnostic")
        return httpx.Response(200, stream=PausedProvider())

    original_generate = providers.stream_generate

    async def generate(provider, prompt, system, temperature, client=None, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as remote:
            async with aclosing(
                original_generate(provider, prompt, system, temperature, remote, **kwargs)
            ) as stream:
                async for event in stream:
                    yield event

    async def begin_attempt(run_id, request_index, provider, model):
        assert provider == "deepseek" and request_index == len(admissions) + 1
        admissions.append((run_id, request_index, provider, model))
        if ending == "quota" and len(admissions) == 2:
            raise HTTPException(429, "共享请求额度已用完", headers={"Retry-After": "30"})

        return AsyncMock()

    monkeypatch.setattr(api_module, "begin_model_attempt", begin_attempt)
    monkeypatch.setattr(api_module, "stream_generate", generate)
    monkeypatch.setattr(agent_loop, "stream_generate", generate)

    async def index(_request):
        return FileResponse(ROOT / "dist/index.html")

    application = Starlette(
        routes=[
            Route("/playground", index),
            Mount("/assets", StaticFiles(directory=ROOT / "dist/assets")),
            Mount("/", api_module.app),
        ]
    )
    with owned_server(application) as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 1000})
        errors = []
        try:
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/playground?workflow=agent&lesson=agent-agent-loop")
            page.get_by_label("模型服务").select_option("deepseek")
            page.get_by_text("高级配置", exact=True).click()
            page.get_by_label("实验访问码").fill("fixed-local-test-access")
            page.get_by_role("button", name="运行实验", exact=True).click()
            expect(
                page.get_by_text("tokens · 输入 3 / 输出 2 / 总量 5", exact=True)
            ).to_be_visible()
            if ending == "cancel":
                assert waiting.wait(timeout=2), "Second provider did not enter its paused stream"
                page.get_by_role("button", name="停止运行", exact=True).click()
                expect(page.get_by_role("alert")).to_contain_text("停止")
                assert closed.wait(timeout=3), "HTTP disconnect did not close the provider"
            else:
                expect(page.get_by_role("alert")).to_contain_text(
                    "共享请求额度" if ending == "quota" else "模型服务请求失败"
                )
            expect(
                page.get_by_text("tokens · 输入 3 / 输出 2 / 总量 5", exact=True)
            ).to_be_visible()
            expect(page.get_by_role("button", name="运行实验", exact=True)).to_be_enabled()
            expect(page.get_by_role("button", name="写入本课笔记", exact=True)).to_have_count(0)
            assert len(calls) == (1 if ending == "quota" else 2)
            stored = json.loads(page.evaluate("localStorage.getItem('deep-ai-station:v1')") or "{}")
            assert not stored.get("runs")
            assert "fixed-local-test-access" not in json.dumps(stored)
            assert "private-provider-diagnostic" not in page.locator("body").inner_text()
            assert not errors
        finally:
            context.close()
            browser.close()
