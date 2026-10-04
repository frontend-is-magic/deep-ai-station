"""固定文本上传教学：memory 默认，可显式选择本地 SQLite。"""

import json
import os
import re
import sys
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException

from auth import (
    Clock,
    MemorySessionStore,
    SessionStore,
    authenticate,
    require_write,
    resolve_principal,
)
from errors import LabError
from repository import MemoryRepository, Repository
from resources import load_fixture
from service import DocumentService
from upload_policy import FILENAME, MAX_BODY_BYTES, MEDIA, ascii_lower, validate_text


def error_response(status: int, code: str):
    headers = {}
    if status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="text-upload"'
    return JSONResponse({"error": code}, status_code=status, headers=headers)


class SafeResponses:
    """包含路由错误和内部异常的所有响应都禁止缓存与类型嗅探。"""

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
                        if key.lower() not in {b"cache-control", b"x-content-type-options"}
                    ]
                    + [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")],
                }
            await send(message)

        try:
            await self.app(scope, receive, private_send)
        except Exception:
            if started:
                raise
            await error_response(500, "request_failed")(scope, receive, private_send)


async def read_bounded(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise LabError(413, "request_too_large")
        body.extend(chunk)
    return bytes(body)


def upload_headers(request: Request) -> tuple[str, str]:
    media_values = request.headers.getlist("content-type")
    filename_values = request.headers.getlist("x-filename")
    if len(media_values) > 1 or len(filename_values) > 1:
        raise LabError(400, "ambiguous_upload_headers")
    if request.headers.getlist("content-encoding"):
        raise LabError(415, "unsupported_media_type")
    media_value = ascii_lower(media_values[0].strip(" \t")) if media_values else ""
    match = MEDIA.fullmatch(media_value)
    if match is None:
        raise LabError(415, "unsupported_media_type")
    media = "text/" + match[1]
    filename = filename_values[0].strip(" \t") if filename_values else ""
    if FILENAME.fullmatch(filename) is None:
        raise LabError(422, "invalid_filename")
    extension = ascii_lower(filename.rsplit(".", 1)[1])
    if extension != ("txt" if media == "text/plain" else "md"):
        raise LabError(422, "invalid_filename")
    return filename, media


def create_app(
    *,
    clock: Clock = time.monotonic,
    session_store: SessionStore | None = None,
    repository: Repository | None = None,
) -> FastAPI:
    store = (
        session_store
        if session_store is not None
        else MemorySessionStore(load_fixture()["sessions"], clock)
    )
    service = DocumentService(repository if repository is not None else MemoryRepository())
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    api.add_middleware(SafeResponses)

    @api.exception_handler(LabError)
    async def known_error(_request, error):
        return error_response(error.status, error.code)

    @api.exception_handler(HTTPException)
    async def route_error(_request, error):
        code = {404: "route_not_found", 405: "method_not_allowed"}.get(error.status_code)
        return error_response(error.status_code if code else 500, code or "request_failed")

    def identity(request: Request):
        auth = authenticate(request, store)
        if request.scope.get("query_string"):
            raise LabError(422, "invalid_input")
        return auth

    @api.get("/health")
    async def health():
        return {"status": "ok", "lab": "text-upload-v1"}

    @api.post("/documents", status_code=201)
    async def upload(request: Request):
        auth = identity(request)
        require_write(auth.principal)
        body = await read_bounded(request)
        filename, media_type = upload_headers(request)
        validate_text(body)

        def authorize():
            principal = resolve_principal(store, auth.token)
            if principal.user_id != auth.principal.user_id:
                raise LabError(401, "authentication_required")
            require_write(principal)

        authorize()  # 保留正文后早拒绝；此时尚不调用仓储。
        return service.upload(auth.principal, filename, media_type, body, authorize=authorize)

    @api.get("/documents")
    async def listing(request: Request):
        return service.listing(identity(request).principal)

    @api.get("/documents/{document_id}")
    async def document(request: Request, document_id: str):
        return service.find(identity(request).principal, document_id).metadata()

    @api.get("/documents/{document_id}/content")
    async def content(request: Request, document_id: str):
        saved = service.find(identity(request).principal, document_id)
        extension = "txt" if saved.media_type == "text/plain" else "md"
        return Response(
            saved.content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="upload-{saved.id}.{extension}"'
            },
        )

    return api


def listen_port(value: str) -> int:
    if re.fullmatch(r"[0-9]+", value) is None or not 1 <= int(value) <= 65535:
        raise ValueError("PORT must be an integer between 1 and 65535")
    return int(value)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--help"]:
        print("Usage: python app.py [init | serve --storage sqlite | --help]")
        return 0
    if args not in ([], ["init"], ["serve", "--storage", "sqlite"]):
        print(json.dumps({"error": "invalid_input"}))
        return 1
    # 合法词法之后才导入持久仓储；memory 不访问固定数据目录。
    from sqlite_repository import SQLiteRepository, StorageError, initialize

    try:
        if args == ["init"]:
            initialize()
            print(json.dumps({"schema_version": 1, "storage_contract": "text-upload-sqlite-v1"}))
            return 0
        try:
            port = listen_port(os.getenv("PORT", "8023"))
        except ValueError:
            raise StorageError("invalid_input") from None
        repository = None
        if args:
            repository = SQLiteRepository()
            repository.validate()
        import uvicorn

        try:
            uvicorn.run(
                create_app(repository=repository),
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_level="critical",
            )
        except SystemExit as error:
            if error.code is None or error.code == 0:
                raise
            # Uvicorn 用非零 SystemExit 报绑定失败，CLI 仍只返回固定错误。
            raise StorageError("repository_unavailable") from None
        return 0
    except BrokenPipeError:
        raise
    except StorageError as error:
        print(json.dumps({"error": error.code}))
        return 1
    except Exception:
        print(json.dumps({"error": "repository_unavailable"}))
        return 1


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        # 不在已经关闭的 stdout 重写错误或打印原始 traceback。
        os._exit(1)
    raise SystemExit(exit_code)

else:
    app = create_app()
