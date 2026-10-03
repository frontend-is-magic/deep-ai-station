import os
from typing import Literal

import httpx
from fastapi import HTTPException

Provider = Literal["demo", "openai", "anthropic", "deepseek"]
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
        "model": "claude-sonnet-4-20250514",
        "url": "https://api.anthropic.com/v1/messages",
    },
    "deepseek": {
        "key": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "model": "deepseek-chat",
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
        "live_requires_access_token": True,
    }


async def generate(
    provider: str,
    prompt: str,
    system: str,
    temperature: float,
    client: httpx.AsyncClient | None = None,
) -> dict:
    config = PROVIDERS[provider]
    key = os.getenv(config["key"])
    if not key:
        raise HTTPException(503, "该模型尚未配置")
    model = os.getenv(config["model_env"], config["model"])
    headers = {"Authorization": f"Bearer {key}"}
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "max_tokens": 1200,
        "temperature": temperature,
    }
    if provider == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        body = {
            "model": model,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1200,
            "temperature": temperature,
        }
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=40)
    try:
        response = await client.post(config["url"], headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
        if provider == "anthropic":
            answer = "".join(
                x.get("text", "") for x in data.get("content", []) if x.get("type") == "text"
            )
        else:
            answer = data["choices"][0]["message"]["content"]
        if not isinstance(answer, str) or not answer:
            raise ValueError("empty_answer")
        return {"answer": answer, "usage": data.get("usage"), "model": model}
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "模型请求超时，请稍后重试") from exc
    except httpx.HTTPStatusError as exc:
        code = 429 if exc.response.status_code == 429 else 502
        raise HTTPException(
            code, "模型服务限流" if code == 429 else "模型服务请求失败，请检查服务端配置"
        ) from exc
    except (httpx.RequestError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "模型响应不可用，请稍后重试") from exc
    finally:
        if own_client:
            await client.aclose()
