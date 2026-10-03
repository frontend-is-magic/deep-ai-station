import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Literal

import httpx
from fastapi import HTTPException

from backend.sandbox import sandbox_capabilities
from backend.tool_protocol import ToolAccumulator, anthropic_messages

Provider = Literal["demo", "openai", "anthropic", "deepseek"]
STREAM_TIMEOUT = 45
PROVIDERS = {
    "openai": {
        "key": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "model": "gpt-4.1-mini",
        "url": "https://api.openai.com/v1/chat/completions",
    },
    "anthropic": {
        "key": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "model": "claude-sonnet-4-6",
        "url": "https://api.anthropic.com/v1/messages",
    },
    "deepseek": {
        "key": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "model": "deepseek-flash",
        "url": "https://api.deepseek.com/chat/completions",
    },
}


def capabilities() -> dict:
    protected = bool(os.getenv("PLAYGROUND_ACCESS_TOKEN"))
    return {
        "providers": [{"id": "demo", "name": "教学演示", "enabled": True, "model": None}]
        + [
            {
                "id": name,
                "name": name.capitalize(),
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


def _usage_counts(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    }
    return {
        key: count
        for key, count in value.items()
        if key in allowed and type(count) is int and 0 <= count <= 100_000_000
    }


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
) -> AsyncIterator[dict]:
    config = PROVIDERS[provider]
    key = os.getenv(config["key"])
    if not key:
        raise HTTPException(503, "该模型尚未配置")
    model = os.getenv(config["model_env"], config["model"])
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
    }
    if provider == "deepseek":
        # Current DeepSeek models default to thinking. This bounded tutorial
        # requests non-thinking output so the 1200-token budget serves the answer.
        body["thinking"] = {"type": "disabled"}
    if provider == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        body = {
            "model": model,
            "system": system,
            "messages": anthropic_messages(messages),
            "max_tokens": 1200,
            "temperature": temperature,
            "stream": True,
        }
    if tools:
        if provider == "anthropic":
            body["tools"] = [
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "input_schema": tool["parameters"],
                }
                for tool in tools
            ]
            body["tool_choice"] = {"type": tool_choice}
            if tool_choice == "auto":
                body["tool_choice"]["disable_parallel_tool_use"] = True
        else:
            body["tools"] = [{"type": "function", "function": tool} for tool in tools]
            body["tool_choice"] = tool_choice
            if provider == "openai":
                body["parallel_tool_calls"] = False
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=40)
    try:
        async with (
            asyncio.timeout(STREAM_TIMEOUT),
            client.stream("POST", config["url"], headers=headers, json=body) as response,
        ):
            response.raise_for_status()
            usage = {}
            output_size = 0
            ended = False
            finish_reason = None
            tool_parts = ToolAccumulator()
            async for payload in _sse_payloads(response):
                if provider != "anthropic" and payload == "[DONE]":
                    ended = True
                    break
                data = json.loads(payload)
                if not isinstance(data, dict) or "error" in data:
                    raise ValueError("invalid_stream")
                text = None
                if provider == "anthropic":
                    kind = data.get("type")
                    if kind == "message_stop":
                        ended = True
                        break
                    if kind == "message_start":
                        usage.update(_usage_counts(data["message"].get("usage")))
                    elif kind == "message_delta":
                        usage.update(_usage_counts(data.get("usage")))
                        finish_reason = data.get("delta", {}).get("stop_reason")
                    elif kind == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text")
                        elif delta.get("type") == "input_json_delta":
                            if not tools or tool_choice == "none":
                                raise ValueError("tools_not_allowed")
                            tool_parts.add(data.get("index"), fragment=delta.get("partial_json"))
                    elif kind == "content_block_start":
                        block = data.get("content_block", {})
                        if block.get("type") == "text":
                            text = block.get("text")
                        elif block.get("type") == "tool_use":
                            if not tools or tool_choice == "none":
                                raise ValueError("tools_not_allowed")
                            tool_parts.add(
                                data.get("index"),
                                id=block.get("id"),
                                name=block.get("name"),
                                initial=block.get("input"),
                            )
                    elif kind == "content_block_stop":
                        tool_parts.close(data.get("index"))
                    # Pings, thinking deltas and unknown future events are not answer text.
                else:
                    usage.update(_usage_counts(data.get("usage")))
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
            calls = [] if truncated else tool_parts.finish(require_closed=provider == "anthropic")
            if not truncated and bool(calls) != (finish_reason in {"tool_calls", "tool_use"}):
                raise ValueError("invalid_tool_finish")
            yield {
                "event": "done",
                "usage": usage or None,
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
            await client.aclose()
