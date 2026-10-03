"""Local SSE teaching service. No model credentials, external requests, or persistence."""

import asyncio
import math

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from streaming import (
    SCENARIOS,
    FixtureProducer,
    ProducerFactory,
    StreamResponse,
    TimeoutFactory,
    load_fixture,
)


def create_app(
    *,
    producer_factory: ProducerFactory | None = None,
    deadline_seconds: float | None = None,
    step_seconds: float | None = None,
    timeout_factory: TimeoutFactory = asyncio.timeout,
) -> FastAPI:
    fixture = load_fixture()
    deadline = fixture["deadline_ms"] / 1000 if deadline_seconds is None else deadline_seconds
    step = fixture["step_ms"] / 1000 if step_seconds is None else step_seconds
    if not math.isfinite(deadline) or deadline <= 0 or not math.isfinite(step) or step < 0:
        raise ValueError("invalid teaching timing configuration")
    factory = producer_factory or (
        lambda scenario: FixtureProducer(scenario, fixture["texts"], step)
    )
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)

    @api.get("/health")
    async def health():
        return {"ok": True, "lab": "sse-stream"}

    @api.get("/stream")
    async def stream(request: Request):
        pairs = request.query_params.multi_items()
        if len(pairs) != 1 or pairs[0][0] != "scenario" or pairs[0][1] not in SCENARIOS:
            return JSONResponse(
                {"error": {"code": "invalid_request", "message": "仅支持一个有效的 scenario 参数"}},
                status_code=400,
            )
        return StreamResponse(pairs[0][1], factory, deadline, timeout_factory)

    return api


app = create_app()
