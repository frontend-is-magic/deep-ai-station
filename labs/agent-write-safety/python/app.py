"""Run a local, persistent teaching service: python app.py --db lab.db --port 8042."""

import argparse
import asyncio
import json
import re
import sys
import time
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from auth import MemorySessionStore, authenticate, refresh, require
from errors import LabError
from repository import Repository
from resources import load_fixture
from service import Service

VERSION = "agent-write-safety-v1"
MAX_BODY = 4096


def error_response(status, code):
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="agent-write-safety"'
    return JSONResponse({"error": code}, status_code=status, headers=headers)


class SafeResponses:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def private_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message = {
                    **message,
                    "headers": [
                        (key, value)
                        for key, value in message.get("headers", [])
                        if key.lower() != b"cache-control"
                    ]
                    + [(b"cache-control", b"no-store")],
                }
            await send(message)

        try:
            await self.app(scope, receive, private_send)
        except Exception:
            if not started:
                await error_response(500, "request_failed")(scope, receive, private_send)
            # A disconnected client may not receive a response; never log raw diagnostics.


def valid_document(value):
    return type(value) is str and re.fullmatch(r"[a-z0-9-]{1,80}", value, re.ASCII) is not None


def valid_operation(value):
    if type(value) is not str:
        return False
    try:
        parsed = UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except ValueError:
        return False


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("invalid constant")


def parse_body(raw):
    text = raw.decode("utf-8", errors="strict")
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 32:
                raise ValueError("deep JSON")
        elif char in "]}":
            depth -= 1
    value = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    if type(value) is not dict:
        raise ValueError("object required")
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is str:
            item.encode("utf-8", errors="strict")
        elif type(item) is dict:
            pending.extend(item.keys())
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
    return value


def validate_payload(payload, prepare):
    if prepare:
        if (
            set(payload) != {"operation_id", "tool", "arguments"}
            or not valid_operation(payload["operation_id"])
            or payload["tool"] != "publish_revision"
        ):
            raise ValueError("invalid preparation")
        arguments = payload["arguments"]
        if type(arguments) is not dict or set(arguments) != {
            "document_id",
            "expected_version",
            "content",
        }:
            raise ValueError("invalid arguments")
        if (
            not valid_document(arguments["document_id"])
            or type(arguments["expected_version"]) is not int
            or not 1 <= arguments["expected_version"] <= 2147483647
            or type(arguments["content"]) is not str
            or not 1 <= len(arguments["content"]) <= 2000
            or "\x00" in arguments["content"]
        ):
            raise ValueError("invalid arguments")
    elif (
        set(payload) != {"intent_hash"}
        or type(payload["intent_hash"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", payload["intent_hash"], re.ASCII) is None
    ):
        raise ValueError("invalid intent")


async def write_body(request, prepare):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BODY:
            raise LabError(413, "request_too_large")
        raw.extend(chunk)
    media = request.headers.get("content-type", "").split(";", 1)[0].strip(" \t")
    if (
        media.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))
        != "application/json"
    ):
        raise LabError(415, "unsupported_media_type")
    try:
        value = parse_body(raw)
        validate_payload(value, prepare)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise LabError(422, "invalid_input") from None


def create_app(
    db_path="lab.db",
    *,
    clock=time.time,
    session_store=None,
    repository=None,
    fault_after_commit=False,
    response_delay_ms=0,
    approval_seconds=60,
):
    if (
        type(fault_after_commit) is not bool
        or type(response_delay_ms) is not int
        or not 0 <= response_delay_ms <= 2000
    ):
        raise ValueError("invalid startup configuration")
    fixtures = load_fixture()
    sessions = (
        session_store
        if session_store is not None
        else MemorySessionStore(fixtures["sessions"], clock)
    )
    database = repository if repository is not None else Repository(db_path, clock=clock)
    database.initialize(fixtures["documents"])
    service = Service(database, sessions, approval_seconds=approval_seconds)
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    api.add_middleware(SafeResponses)
    api.state.repository, api.state.sessions = database, sessions

    @api.exception_handler(LabError)
    async def lab_error(_request, error):
        return error_response(error.status, error.code)

    @api.exception_handler(HTTPException)
    async def route_error(_request, error):
        code = {404: "route_not_found", 405: "method_not_allowed"}.get(error.status_code)
        return error_response(error.status_code if code else 500, code or "request_failed")

    def identity(request):
        auth = authenticate(request, sessions)
        if request.scope.get("query_string"):
            raise LabError(422, "invalid_input")
        return auth

    @api.get("/health")
    async def health():
        return {"status": "ok", "lab": VERSION}

    @api.get("/me")
    async def me(request: Request):
        principal = refresh(identity(request), sessions)
        require(principal, "read")
        return {
            "owner_id": principal.owner_id,
            "requester_id": principal.requester_id,
            "capabilities": sorted(principal.capabilities),
        }

    @api.get("/documents")
    async def documents(request: Request):
        return await run_in_threadpool(service.call, identity(request), "documents")

    @api.get("/documents/{document_id}")
    async def document(request: Request, document_id: str):
        auth = identity(request)
        if not valid_document(document_id):
            raise LabError(422, "invalid_input")
        return await run_in_threadpool(service.call, auth, "document", document_id)

    @api.post("/operations")
    async def prepare(request: Request):
        auth = identity(request)
        payload = await write_body(request, True)
        refresh(auth, sessions)
        return await run_in_threadpool(service.call, auth, "prepare", None, payload)

    @api.get("/operations/{operation_id}")
    async def operation(request: Request, operation_id: str):
        auth = identity(request)
        if not valid_operation(operation_id):
            raise LabError(422, "invalid_input")
        return await run_in_threadpool(service.call, auth, "operation", operation_id)

    async def act(request, operation_id, action):
        auth = identity(request)
        if not valid_operation(operation_id):
            raise LabError(422, "invalid_input")
        payload = await write_body(request, False)
        refresh(auth, sessions)
        result = await run_in_threadpool(service.call, auth, action, operation_id, payload)
        if action == "execute" and result["replayed"] is False:
            try:
                database.fault_hook("after_commit", None)
            except Exception:
                raise LabError(503, "result_unconfirmed") from None
            if response_delay_ms:
                await asyncio.sleep(response_delay_ms / 1000)
            if fault_after_commit:
                raise LabError(503, "result_unconfirmed")
        return result

    @api.post("/operations/{operation_id}/approve")
    async def approve(request: Request, operation_id: str):
        return await act(request, operation_id, "approve")

    @api.post("/operations/{operation_id}/revoke")
    async def revoke(request: Request, operation_id: str):
        return await act(request, operation_id, "revoke")

    @api.post("/operations/{operation_id}/execute")
    async def execute(request: Request, operation_id: str):
        return await act(request, operation_id, "execute")

    return api


def bounded_integer(minimum, maximum):
    def parse(value):
        try:
            number = int(value)
            if not minimum <= number <= maximum:
                raise ValueError
            return number
        except ValueError:
            raise argparse.ArgumentTypeError("integer outside permitted range") from None

    return parse


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="lab.db")
    parser.add_argument("--port", type=bounded_integer(1, 65535), default=8042)
    parser.add_argument("--fault-after-commit", action="store_true")
    parser.add_argument("--response-delay-ms", type=bounded_integer(0, 2000), default=0)
    args = parser.parse_args(argv)
    try:
        import uvicorn

        api = create_app(
            args.db,
            fault_after_commit=args.fault_after_commit,
            response_delay_ms=args.response_delay_ms,
        )
        uvicorn.run(api, host="127.0.0.1", port=args.port, access_log=False, log_level="critical")
    except Exception:
        print("实验服务启动失败，请检查本地数据库与端口。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
