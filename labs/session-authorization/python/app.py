"""独立的会话授权教学 API；python app.py 仅监听 127.0.0.1:8022。"""

import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from auth import (
    Clock,
    MemorySessionStore,
    SessionStore,
    ascii_lower,
    authenticate,
    csrf_guard,
    refresh_authentication,
    revoke,
    session_cookie,
)
from errors import LabError
from repository import MemoryRepository, Repository
from resources import load_fixture
from service import DocumentService

MAX_BODY_BYTES = 4096


def error_response(status: int, code: str) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="session-authorization"'
    return JSONResponse({"error": code}, status, headers=headers)


class SafeResponses:
    """异常在服务器默认日志之前转成固定 JSON；全部响应禁止缓存。"""

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
            if started:
                raise
            await error_response(500, "request_failed")(scope, receive, private_send)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("invalid JSON constant")


async def write_body(request: Request, *, logout: bool = False) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise LabError(413, "request_too_large")
        body.extend(chunk)
    content_type = ascii_lower(
        request.headers.get("content-type", "").split(";", 1)[0].strip(" \t")
    )
    if content_type != "application/json":
        raise LabError(415, "unsupported_media_type")
    try:
        payload = json.loads(
            body.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
        if not isinstance(payload, dict):
            raise ValueError("object required")
        if logout:
            if payload:
                raise ValueError("empty object required")
        elif set(payload) != {"archived"} or type(payload["archived"]) is not bool:
            raise ValueError("exact boolean field required")
    except (ValueError, UnicodeError, RecursionError):
        raise LabError(422, "invalid_input") from None
    return payload


def create_app(
    *,
    clock: Clock = time.monotonic,
    session_store: SessionStore | None = None,
    repository: Repository | None = None,
    allow_cookie: bool = True,
    allowed_origin: str = "https://lab.example.test",
) -> FastAPI:
    fixtures = load_fixture()
    store = (
        session_store
        if session_store is not None
        else MemorySessionStore(fixtures["sessions"], clock)
    )
    documents = repository if repository is not None else MemoryRepository(fixtures["documents"])
    service = DocumentService(documents)
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    api.add_middleware(SafeResponses)

    @api.exception_handler(LabError)
    async def lab_error(_request, error):
        return error_response(error.status, error.code)

    @api.exception_handler(HTTPException)
    async def route_error(_request, error):
        code = {404: "route_not_found", 405: "method_not_allowed"}.get(error.status_code)
        return error_response(error.status_code if code else 500, code or "request_failed")

    def principal(request):
        auth = authenticate(request, store, allow_cookie)
        if request.scope.get("query_string"):
            raise LabError(422, "invalid_input")
        return auth

    @api.get("/health")
    async def health():
        return {"status": "ok", "lab": "session-authorization-v1"}

    @api.get("/me")
    async def me(request: Request):
        return {"user_id": principal(request).principal.user_id}

    @api.get("/csrf")
    async def csrf(request: Request):
        return {"csrf_token": principal(request).principal.csrf_token}

    @api.get("/documents")
    async def listing(request: Request):
        return service.listing(principal(request).principal)

    @api.get("/documents/{document_id}")
    async def document(request: Request, document_id: str):
        return service.find(principal(request).principal, document_id)

    @api.patch("/documents/{document_id}")
    async def patch(request: Request, document_id: str):
        auth = principal(request)
        payload = await write_body(request)
        auth = refresh_authentication(auth, store)
        csrf_guard(request, auth, allowed_origin)
        return service.patch(auth.principal, document_id, payload["archived"])

    @api.post("/logout")
    async def logout(request: Request):
        auth = principal(request)
        await write_body(request, logout=True)
        auth = refresh_authentication(auth, store)
        csrf_guard(request, auth, allowed_origin)
        revoke(store, auth.token)
        response = JSONResponse({"ok": True})
        if auth.cookie:
            session_cookie(response, "", clear=True)
        return response

    return api


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8022, access_log=False)
