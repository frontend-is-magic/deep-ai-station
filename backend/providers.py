import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import anyio
import httpx
from fastapi import HTTPException

from backend.quota import quota_configured
from backend.sandbox import sandbox_capabilities
from backend.tool_protocol import ToolAccumulator
from backend.usage import USAGE_FIELDS, usage_counts

Provider = Literal["demo", "deepseek"]
STREAM_TIMEOUT = 45
CLOSE_TIMEOUT = 1.0
PROVIDERS = {
    "deepseek": {
        "key": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "model": "deepseek-flash",
        "url": "https://api.deepseek.com/chat/completions",
    },
}


def provider_model(provider: str) -> str:
    if provider not in PROVIDERS:
        raise HTTPException(422, "仅支持 DeepSeek 真实模型")
    config = PROVIDERS[provider]
    model = os.getenv(config["model_env"], config["model"])
    if (
        not 1 <= len(model) <= 200
        or not model.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in model)
    ):
        raise HTTPException(503, "模型配置不可用，请检查服务端配置")
    try:
        model.encode("utf-8")
    except UnicodeEncodeError:
        raise HTTPException(503, "模型配置不可用，请检查服务端配置") from None
    return model


def capabilities() -> dict:
    protected = bool(os.getenv("PLAYGROUND_ACCESS_TOKEN")) and quota_configured()
    return {
        "providers": [{"id": "demo", "name": "教学演示", "enabled": True, "model": None}]
        + [
            {
                "id": name,
                "name": "DeepSeek",
                "enabled": bool(os.getenv(config["key"])) and protected,
                "model": os.getenv(config["model_env"], config["model"])
                if os.getenv(config["key"])
                else None,
            }
            for name, config in PROVIDERS.items()
        ],
        "code_execution": "static-check",
        "sandbox": sandbox_capabilities(),
        "live_requires_access_token": True,
    }


async def _sse_payloads(response: httpx.Response) -> AsyncIterator[str]:
    """Decode bounded SSE frames, including CRLF and split UTF-8 network chunks."""
    buffer = b""
    lines = []
    frame_size = 0
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > 1_000_000:
            raise ValueError("stream_limit")
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            frame_size += len(line) + 1
            if frame_size > 65_536:
                raise ValueError("frame_limit")
            line = line.rstrip(b"\r")
            if not line:
                if lines:
                    yield b"\n".join(lines).decode("utf-8")
                lines = []
                frame_size = 0
            elif line.startswith(b"data:"):
                lines.append(line[5:].removeprefix(b" "))
        if frame_size + len(buffer) > 65_536:
            raise ValueError("frame_limit")
    # An incomplete final frame is not accepted as a completed model response.


async def _close_resource(close):
    # StreamingResponse uses a level-triggered AnyIO cancel scope. Shield just
    # cleanup, under a short deadline, so each resource can finish its awaits.
    with anyio.CancelScope(shield=True):
        async with asyncio.timeout(CLOSE_TIMEOUT):
            await close()


@asynccontextmanager
async def _response_stream(client: httpx.AsyncClient, url: str, headers: dict, body: dict):
    manager = client.stream("POST", url, headers=headers, json=body)
    response = await manager.__aenter__()
    try:
        yield response
    finally:
        interrupted = sys.exception()
        try:
            # httpx.stream owns response.aclose in its finally. Enter/exit it
            # explicitly so the shield covers the await of that exit exactly once.
            await _close_resource(lambda: manager.__aexit__(None, None, None))
        except BaseException:
            if isinstance(interrupted, (asyncio.CancelledError, GeneratorExit)):
                raise interrupted from None
            raise


async def close_client(client: httpx.AsyncClient):
    interrupted = sys.exception()
    try:
        await _close_resource(client.aclose)
    except Exception:
        if interrupted is not None:
            raise interrupted from None
        raise HTTPException(502, "模型响应不可用，请稍后重试") from None


async def stream_generate(
    provider: str,
    prompt: str,
    system: str,
    temperature: float,
    client: httpx.AsyncClient | None = None,
    *,
    messages: list[dict] | None = None,
    tools: list[dict] | None = None,
    tool_choice: Literal["auto", "none"] = "auto",
    model: str | None = None,
) -> AsyncIterator[dict]:
    if provider != "deepseek":
        raise HTTPException(422, "仅支持 DeepSeek 真实模型")
    config = PROVIDERS[provider]
    key = os.getenv(config["key"])
    if not key:
        raise HTTPException(503, "该模型尚未配置")
    model = provider_model(provider) if model is None else model
    headers = {"Authorization": f"Bearer {key}"}
    messages = messages if messages is not None else [{"role": "user", "content": prompt}]
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}]
        + [{key: value for key, value in m.items() if key != "is_error"} for m in messages],
        "max_tokens": 1200,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
        "thinking": {"type": "disabled"},
    }
    if tools:
        body["tools"] = [{"type": "function", "function": tool} for tool in tools]
        body["tool_choice"] = tool_choice
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=40)
    try:
        async with (
            asyncio.timeout(STREAM_TIMEOUT),
            _response_stream(client, config["url"], headers, body) as response,
        ):
            response.raise_for_status()
            usage = {}
            output_size = 0
            ended = False
            finish_reason = None
            tool_parts = ToolAccumulator()
            async for payload in _sse_payloads(response):
                if payload == "[DONE]":
                    ended = True
                    break
                data = json.loads(payload)
                if not isinstance(data, dict) or "error" in data:
                    raise ValueError("invalid_stream")
                text = None
                counts = usage_counts(data.get("usage"))
                if counts:
                    usage.update(counts)
                    # Publish before parsing later fields or waiting for [DONE]. Failure
                    # or cancellation may prevent a terminal response from ever arriving.
                    yield {"event": "usage", "usage": dict(usage), "usage_complete": False}
                choices = data.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    text = delta.get("content")
                    finish_reason = choices[0].get("finish_reason") or finish_reason
                    for call in delta.get("tool_calls") or []:
                        if (
                            not tools
                            or tool_choice == "none"
                            or call.get("type", "function") != "function"
                        ):
                            raise ValueError("tools_not_allowed")
                        function = call.get("function") or {}
                        tool_parts.add(
                            call.get("index"),
                            id=call.get("id"),
                            name=function.get("name"),
                            fragment=function.get("arguments"),
                        )
                if text is not None:
                    if not isinstance(text, str):
                        raise ValueError("invalid_text")
                    output_size += len(text)
                    if output_size > 20_000:
                        raise ValueError("output_limit")
                    if text:
                        yield {"event": "delta", "text": text}
            if (
                not ended
                or not output_size
                and not tool_parts.calls
                or finish_reason in {"aborted", "insufficient_system_resource", "pause_turn"}
            ):
                raise ValueError("incomplete_stream")
            truncated = finish_reason in {"length", "max_tokens"}
            calls = [] if truncated else tool_parts.finish()
            if not truncated and bool(calls) != (finish_reason == "tool_calls"):
                raise ValueError("invalid_tool_finish")
            yield {
                "event": "done",
                "usage": usage or None,
                "usage_complete": USAGE_FIELDS <= usage.keys(),
                "model": model,
                "truncated": truncated,
                **({"tool_calls": calls} if tools else {}),
            }
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise HTTPException(504, "模型请求超时，请稍后重试") from exc
    except httpx.HTTPStatusError as exc:
        code = 429 if exc.response.status_code == 429 else 502
        raise HTTPException(
            code, "模型服务限流" if code == 429 else "模型服务请求失败，请检查服务端配置"
        ) from exc
    except (
        httpx.RequestError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        RecursionError,
    ) as exc:
        raise HTTPException(502, "模型响应不可用，请稍后重试") from exc
    finally:
        if own_client:
            await close_client(client)
