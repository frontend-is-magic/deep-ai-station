"""Actual HTTP API -> PostgreSQL ledger -> provider SSE parser, with no paid network."""

import asyncio
import json
import os
import socket
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
import pytest
import uvicorn
from psycopg import sql
from psycopg.rows import dict_row

from backend import agent_loop, providers, quota
from backend import app as api

ROOT = Path(__file__).resolve().parents[1]
COUNTS = {"prompt_tokens": 3, "completion_tokens": 0, "total_tokens": 3}


def frame(value):
    return ("data: " + (value if isinstance(value, str) else json.dumps(value)) + "\n\n").encode()


def completion(index, *, tool=False, done=True):
    delta = {"content": "fixed-private-answer"}
    if tool:
        delta = {
            "tool_calls": [
                {
                    "index": 0,
                    "id": f"tool-{index}",
                    "type": "function",
                    "function": {"name": "knowledge_search", "arguments": '{"query":"MCP"}'},
                }
            ]
        }
    return b"".join(
        [
            frame(
                {"choices": [{"delta": delta, "finish_reason": "tool_calls" if tool else "stop"}]}
            ),
            frame({"choices": [], "usage": {"prompt_tokens": 1}}),
            frame({"choices": [], "usage": COUNTS}),
            frame({"choices": [], "usage": COUNTS}),
            frame("[DONE]") if done else b"",
        ]
    )


@asynccontextmanager
async def owned_server():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(api.app, log_level="critical", access_log=False, lifespan="off")
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Owned ledger HTTP server did not start")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        try:
            async with asyncio.timeout(5):
                await asyncio.shield(task)
        finally:
            if not task.done():
                server.force_exit = True
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            listener.close()
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0


@pytest.fixture
async def harness(monkeypatch):
    dsn = os.getenv("TEST_QUOTA_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_QUOTA_DATABASE_URL is required for the actual HTTP/PG contract")
    admin = None
    setup_failed = False
    try:
        admin = await psycopg.AsyncConnection.connect(
            dsn, autocommit=True, connect_timeout=3, row_factory=dict_row
        )
        for name in ("001_request_quota.sql", "002_model_usage.sql"):
            await admin.execute((ROOT / "backend/migrations" / name).read_text())
    except (psycopg.Error, OSError, ValueError):
        setup_failed = True
    if setup_failed:
        if admin is not None:
            await admin.close()
        pytest.fail("Configured HTTP/PG test database is unavailable", pytrace=False)
    scope = f"ledger-api-{uuid4().hex}"
    for key, value in {
        "AI_QUOTA_MODE": "postgres",
        "AI_QUOTA_DATABASE_URL": dsn,
        "AI_QUOTA_SCOPE": scope,
        "DEEPSEEK_API_KEY": "fixed-test-key",
        "DEEPSEEK_MODEL": "fixed-test-model",
        "PLAYGROUND_ACCESS_TOKEN": "fixed-test-access",
    }.items():
        monkeypatch.setenv(key, value)
    quota._store.cache_clear()
    state = SimpleNamespace(
        admin=admin,
        scope=scope,
        calls=[],
        rounds=1,
        failure=None,
        pause=False,
        closed=asyncio.Event(),
        waiting=asyncio.Event(),
    )

    async def rows():
        cursor = await admin.execute(
            "SELECT * FROM public.ai_model_usage_v1 WHERE scope = %s ORDER BY request_index",
            (scope,),
        )
        return await cursor.fetchall()

    state.rows = rows

    class Paused(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield completion(1, done=False)
            state.waiting.set()
            await asyncio.Event().wait()

        async def aclose(self):
            await asyncio.sleep(0)
            state.closed.set()

    async def remote_response(request):
        records = await rows()
        index = len(state.calls) + 1
        # A separate connection observes the committed quota and row before provider IO.
        assert len(records) == index and records[-1]["status"] == "admitted"
        cursor = await admin.execute(
            "SELECT day_count FROM public.ai_request_quota_v1 WHERE scope = %s AND resource = 'model'",
            (scope,),
        )
        assert (await cursor.fetchone())["day_count"] == index
        body = json.loads(request.content)
        assert body["model"] == records[-1]["model"] == "fixed-test-model"
        state.calls.append(body)
        if state.failure == index:
            return httpx.Response(502, text="private-provider-diagnostic")
        if state.pause:
            return httpx.Response(200, stream=Paused())
        return httpx.Response(200, content=completion(index, tool=index < state.rounds))

    original = providers.stream_generate

    async def generate(provider, prompt, system, temperature, client=None, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(remote_response)) as remote:
            async with aclosing(
                original(provider, prompt, system, temperature, remote, **kwargs)
            ) as stream:
                async for event in stream:
                    yield event

    monkeypatch.setattr(api, "stream_generate", generate)
    monkeypatch.setattr(agent_loop, "stream_generate", generate)
    try:
        async with owned_server() as base, httpx.AsyncClient(base_url=base, timeout=10) as browser:
            state.browser = browser
            yield state
    finally:
        quota._store.cache_clear()
        try:
            for table in ("ai_model_usage_v1", "ai_request_quota_v1"):
                await admin.execute(
                    sql.SQL("DELETE FROM public.{} WHERE scope = %s").format(sql.Identifier(table)),
                    (scope,),
                )
        finally:
            await admin.close()


def request_values(workflow="agent"):
    return {
        "json": {"provider": "deepseek", "prompt": "fixed-private-input MCP", "workflow": workflow},
        "headers": {"X-Playground-Token": "fixed-test-access"},
    }


def events(response):
    assert response.status_code == 200
    return [
        {"event": lines[0][7:], "data": json.loads(lines[1][6:])}
        for part in response.text.strip().split("\n\n")
        if part and (lines := part.splitlines())
    ]


@pytest.mark.parametrize(
    "workflow,rounds", [("retrieval", 1), ("agent", 1), ("agent", 2), ("agent", 3)]
)
async def test_http_pg_commits_one_row_per_round_and_latest_usage(harness, workflow, rounds):
    harness.rounds = rounds
    received = events(await harness.browser.post("/api/playground/run", **request_values(workflow)))
    assert received[-1]["event"] == "done"
    assert received[-1]["data"]["usage"] == {key: count * rounds for key, count in COUNTS.items()}
    records = await harness.rows()
    assert len(records) == len(harness.calls) == rounds
    assert {str(row["run_id"]) for row in records} == {received[-1]["data"]["run_id"]}
    for row in records:
        assert {key: row[key] for key in COUNTS} == COUNTS
        assert row["status"] == "completed" and row["usage_complete"]
        assert row["snapshot_seq"] == 3  # partial, full, terminal; duplicates do not add tokens.
        assert row["finished_at"] >= row["admitted_at"]
    serialized = json.dumps(records, default=str)
    for private in (
        "fixed-private-input",
        "fixed-private-answer",
        "fixed-test-key",
        "fixed-test-access",
    ):
        assert private not in serialized


async def test_http_pg_upstream_failure_retains_prior_round_and_unknown_second_usage(harness):
    harness.rounds, harness.failure = 2, 2
    received = events(await harness.browser.post("/api/playground/run", **request_values()))
    assert received[-1]["event"] == "error" and received[-1]["data"]["usage"] == COUNTS
    records = await harness.rows()
    assert [row["status"] for row in records] == ["completed", "failed"]
    assert all(records[1][key] is None for key in COUNTS)
    assert records[1]["reason"] == "upstream_error"
    assert len(harness.calls) == 2


@pytest.mark.parametrize("phase", ["snapshot", "terminal"])
async def test_http_pg_failed_write_stops_next_round_and_keeps_known_sse_usage(harness, phase):
    name = "ledger_api_fault_" + uuid4().hex
    condition = "NEW.status = 'admitted'" if phase == "snapshot" else "NEW.status = 'completed'"
    await harness.admin.execute(
        sql.SQL("CREATE FUNCTION public.{}() RETURNS trigger LANGUAGE plpgsql AS {} ").format(
            sql.Identifier(name),
            sql.Literal(
                f"BEGIN IF NEW.scope = '{harness.scope}' AND {condition} THEN RAISE EXCEPTION 'private-ledger-diagnostic'; END IF; RETURN NEW; END"
            ),
        )
    )
    try:
        await harness.admin.execute(
            sql.SQL(
                "CREATE TRIGGER {} BEFORE UPDATE ON public.ai_model_usage_v1 FOR EACH ROW EXECUTE FUNCTION public.{}()"
            ).format(sql.Identifier(name), sql.Identifier(name))
        )
        harness.rounds = 2
        response = await harness.browser.post("/api/playground/run", **request_values())
        received = events(response)
        assert received[-1]["event"] == "error"
        assert received[-1]["data"]["message"] == "模型请求账本暂不可用，请稍后重试"
        expected = {"prompt_tokens": 1} if phase == "snapshot" else COUNTS
        assert received[-1]["data"]["usage"] == expected
        assert not received[-1]["data"]["usage_complete"]
        records = await harness.rows()
        assert len(harness.calls) == len(records) == 1
        assert records[0]["status"] == ("failed" if phase == "snapshot" else "admitted")
        assert "private-ledger-diagnostic" not in response.text
    finally:
        await harness.admin.execute(
            sql.SQL("DROP TRIGGER IF EXISTS {} ON public.ai_model_usage_v1").format(
                sql.Identifier(name)
            )
        )
        await harness.admin.execute(
            sql.SQL("DROP FUNCTION public.{}()").format(sql.Identifier(name))
        )


@pytest.mark.parametrize("workflow", ["retrieval", "agent"])
async def test_http_disconnect_persists_partial_usage_and_releases_upstream(harness, workflow):
    harness.pause = True
    async with harness.browser.stream(
        "POST", "/api/playground/run", **request_values(workflow)
    ) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                value = json.loads(line[6:])
                if value.get("usage") == COUNTS:
                    break
    async with asyncio.timeout(4):
        await harness.closed.wait()
        while True:
            records = await harness.rows()
            if records and records[0]["status"] == "cancelled":
                break
            await asyncio.sleep(0.01)
    assert len(harness.calls) == len(records) == 1
    assert {key: records[0][key] for key in COUNTS} == COUNTS
    assert not records[0]["usage_complete"] and records[0]["reason"] == "cancelled"


@pytest.mark.parametrize("workflow", ["retrieval", "agent"])
async def test_http_terminal_commit_ack_loss_is_not_retried_or_rewritten(
    harness, monkeypatch, workflow
):
    original = quota.QuotaConnection.commit
    commits = []

    async def commit(connection):
        cursor = await connection.execute(
            "SELECT status FROM public.ai_model_usage_v1 WHERE scope = %s",
            (harness.scope,),
        )
        row = await cursor.fetchone()
        await original(connection)
        commits.append(row[0])
        if row[0] == "completed":
            # Actual commit succeeds; the response is deliberately made uncertain.
            raise psycopg.OperationalError("private-terminal-ack-diagnostic")

    monkeypatch.setattr(quota.QuotaConnection, "commit", commit)
    harness.rounds = 2 if workflow == "agent" else 1
    response = await harness.browser.post("/api/playground/run", **request_values(workflow))
    received = events(response)
    assert received[-1]["event"] == "error"
    assert received[-1]["data"]["code"] == 503
    assert received[-1]["data"]["usage"] == COUNTS
    assert not received[-1]["data"]["usage_complete"]
    records = await harness.rows()
    assert len(records) == len(harness.calls) == 1
    assert records[0]["status"] == "completed" and records[0]["usage_complete"]
    assert {key: records[0][key] for key in COUNTS} == COUNTS
    assert commits == ["admitted", "admitted", "admitted", "completed"]
    assert "private-terminal-ack-diagnostic" not in response.text
