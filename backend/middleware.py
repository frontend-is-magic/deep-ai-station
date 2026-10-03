"""Bound actual request bytes, including bodies without Content-Length."""

from uuid import uuid4

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestLimits:
    def __init__(self, app: ASGIApp, max_bytes: int = 50_000):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4()).encode()

        async def send_headers(message: Message):
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-request-id", request_id),
                    ],
                }
            await send(message)

        async def reject():
            await JSONResponse({"detail": "请求内容过大"}, status_code=413)(
                scope, receive, send_headers
            )

        length = dict(scope.get("headers", [])).get(b"content-length")
        if length and (not length.isdigit() or int(length) > self.max_bytes):
            await reject()
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                await reject()
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def cached_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, cached_receive, send_headers)
