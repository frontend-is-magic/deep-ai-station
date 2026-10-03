import asyncio
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
from urllib.parse import urlparse

import httpx
from defusedxml import ElementTree

SOURCES = [
    {
        "id": "openai",
        "name": "OpenAI",
        "track": "agent",
        "url": "https://openai.com/news/rss.xml",
        "home": "https://openai.com/news/",
    },
    {
        "id": "langchain",
        "name": "LangChain",
        "track": "agent",
        "url": "https://www.langchain.com/blog/rss.xml",
        "home": "https://www.langchain.com/blog",
    },
    {
        "id": "huggingface",
        "name": "Hugging Face Blog",
        "track": "agent",
        "url": "https://huggingface.co/blog/feed.xml",
        "home": "https://huggingface.co/blog/",
    },
    {
        "id": "typescript",
        "name": "TypeScript",
        "track": "fullstack",
        "url": "https://devblogs.microsoft.com/typescript/feed/",
        "home": "https://devblogs.microsoft.com/typescript/",
    },
    {
        "id": "go",
        "name": "Go Blog",
        "track": "fullstack",
        "url": "https://go.dev/blog/feed.atom",
        "home": "https://go.dev/blog/",
    },
    {
        "id": "python",
        "name": "Python",
        "track": "fullstack",
        "url": "https://blog.python.org/feeds/posts/default?alt=rss",
        "home": "https://blog.python.org/",
    },
]

CURATED = [
    {
        "id": "agents-sdk",
        "title": "用 Agents SDK 组织工具、交接与追踪",
        "summary": "从官方 SDK 理解 Agent 的执行循环，重点阅读工具输入、运行上下文和 trace。",
        "source": "OpenAI Docs",
        "url": "https://openai.github.io/openai-agents-python/",
        "track": "agent",
        "tags": ["Agent", "Tools"],
        "kind": "guide",
        "published": None,
    },
    {
        "id": "mcp-guide",
        "title": "MCP：把工具接入变成清晰的协议边界",
        "summary": "跟着规范建立资源、工具与客户端连接，确认协议能力和授权范围的区别。",
        "source": "MCP",
        "url": "https://modelcontextprotocol.io/docs/getting-started/intro",
        "track": "agent",
        "tags": ["MCP", "协议"],
        "kind": "guide",
        "published": None,
    },
    {
        "id": "langgraph",
        "title": "用状态图管理长期运行的 Agent",
        "summary": "理解节点、边、检查点与恢复，把复杂任务拆为可观测的步骤。",
        "source": "LangGraph Docs",
        "url": "https://docs.langchain.com/oss/python/langgraph/overview",
        "track": "agent",
        "tags": ["Workflow", "状态"],
        "kind": "guide",
        "published": None,
    },
    {
        "id": "evals",
        "title": "从一组可重复的问题开始评测",
        "summary": "构建评测集，比较正确率、工具使用和成本，为提示词与模型升级保留回归证据。",
        "source": "OpenAI Docs",
        "url": "https://platform.openai.com/docs/guides/evals",
        "track": "agent",
        "tags": ["Evals", "质量"],
        "kind": "guide",
        "published": None,
    },
    {
        "id": "fastapi-guide",
        "title": "FastAPI：从第一个路由到类型化接口",
        "summary": "使用 Pydantic、依赖注入和 OpenAPI，让 Python API 契约可读、可测。",
        "source": "FastAPI",
        "url": "https://fastapi.tiangolo.com/tutorial/",
        "track": "fullstack",
        "tags": ["Python", "FastAPI"],
        "kind": "guide",
        "published": None,
    },
    {
        "id": "hono-guide",
        "title": "Hono：一个小而完整的 TypeScript 服务",
        "summary": "学习路由、中间件与类型化请求，从 Web 标准构建 API。",
        "source": "Hono",
        "url": "https://hono.dev/docs/",
        "track": "fullstack",
        "tags": ["TypeScript", "Hono"],
        "kind": "guide",
        "published": None,
    },
    {
        "id": "go-guide",
        "title": "Go Web 服务：理解 context 与请求生命周期",
        "summary": "将取消和 deadline 传递到外部依赖，构建可靠的并发服务。",
        "source": "Go",
        "url": "https://go.dev/blog/context",
        "track": "fullstack",
        "tags": ["Go", "并发"],
        "kind": "guide",
        "published": None,
    },
    {
        "id": "jotai-guide",
        "title": "Jotai：从最小状态推导产品行为",
        "summary": "用 atom 与派生状态组织学习进度，理解浏览器持久化的范围。",
        "source": "Jotai",
        "url": "https://jotai.org/docs",
        "track": "fullstack",
        "tags": ["React", "状态"],
        "kind": "guide",
        "published": None,
    },
]

_cache: dict[str, tuple[float, list[dict]]] = {}
_failed_at: dict[str, float] = {}


def parse_feed(xml: bytes, source: dict) -> list[dict]:
    root = ElementTree.fromstring(xml)
    atom = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall(".//item") or root.findall("a:entry", atom)
    result = []
    allowed_host = urlparse(source["home"]).hostname
    for item in entries[:8]:
        title = item.findtext("title") or item.findtext("a:title", namespaces=atom)
        link = item.findtext("link")
        if not link:
            links = item.findall("a:link", atom)
            link = next(
                (x.get("href") for x in links if x.get("rel", "alternate") == "alternate"), None
            )
        if not title or not link:
            continue
        try:
            parsed = urlparse(link)
            permitted = (
                parsed.scheme == "https"
                and parsed.hostname == allowed_host
                and not parsed.username
                and not parsed.password
                and parsed.port in {None, 443}
            )
        except ValueError:
            permitted = False
        if not permitted:
            continue
        raw_date = (
            item.findtext("pubDate")
            or item.findtext("a:published", namespaces=atom)
            or item.findtext("a:updated", namespaces=atom)
        )
        published = None
        if raw_date:
            try:
                published = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).isoformat()
            except ValueError:
                try:
                    published = parsedate_to_datetime(raw_date).isoformat()
                except (ValueError, TypeError):
                    pass
        result.append(
            {
                "id": f"{source['id']}-{sha256(link.encode()).hexdigest()[:24]}",
                "title": title[:250],
                "summary": "来自官方信息源，打开原文查看完整内容。",
                "source": source["name"],
                "url": link,
                "track": source["track"],
                "tags": [source["name"]],
                "kind": "news",
                "published": published,
            }
        )
    return result


async def fetch_source(source: dict) -> tuple[list[dict], dict]:
    cached = _cache.get(source["id"])
    now = time.monotonic()
    if cached and now - cached[0] < 300:
        return cached[1], {"id": source["id"], "name": source["name"], "status": "cached"}
    failed_at = _failed_at.get(source["id"])
    if failed_at is not None and now - failed_at < 60:
        return (cached[1] if cached else []), {
            "id": source["id"],
            "name": source["name"],
            "status": "unavailable",
            "cached": bool(cached),
        }
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            async with client.stream(
                "GET", source["url"], headers={"User-Agent": "DeepAIStation/1.0"}
            ) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 1_000_000:
                        raise ValueError("feed_too_large")
        items = parse_feed(bytes(data), source)
        _cache[source["id"]] = (time.monotonic(), items)
        _failed_at.pop(source["id"], None)
        return items, {"id": source["id"], "name": source["name"], "status": "live"}
    except Exception:
        _failed_at[source["id"]] = time.monotonic()
        return (cached[1] if cached else []), {
            "id": source["id"],
            "name": source["name"],
            "status": "unavailable",
            "cached": bool(cached),
        }


async def get_feed(refresh: bool) -> dict:
    items = list(CURATED)
    status = []
    if refresh:
        results = await asyncio.gather(*(fetch_source(source) for source in SOURCES))
        for entries, state in results:
            items.extend(entries)
            status.append(state)
    else:
        status = [{"id": x["id"], "name": x["name"], "status": "not_requested"} for x in SOURCES]
    items.sort(key=lambda x: x.get("published") or "", reverse=True)
    return {
        "items": items,
        "sources": status,
        "fetched_at": datetime.now(UTC).isoformat(),
        "mode": "live+curated" if refresh else "curated",
    }
