"""Only a fixed stdio MCP server. No HTTP, remote URI loading, or model calls."""

import argparse
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import replace

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from mcp.types import (
    CallToolResult,
    ErrorData,
    JSONRPCError,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
    Resource,
    TextContent,
    TextResourceContents,
    Tool,
    ToolAnnotations,
)
from pydantic import ValidationError

from protocol import (
    CONTRACT_VERSION,
    COURSES,
    PROTOCOL_VERSION,
    SearchArguments,
    SearchOutput,
    search,
)


def event(name: str) -> None:
    print(json.dumps({"event": name, "pid": os.getpid()}), file=sys.stderr, flush=True)


def make_server(fault: str = "none", disconnect: anyio.Event | None = None) -> Server:
    if fault not in ("none", "timeout", "disconnect"):
        raise ValueError("invalid_fault")
    fault_pending = fault != "none"

    @asynccontextmanager
    async def lifespan(_server):
        event("server_started")
        try:
            yield None
        finally:
            event("server_cleanup")

    async def list_tools(_context, _params):
        return ListToolsResult(
            tools=[
                Tool(
                    name="knowledge_search",
                    description="Search the three fixed public course summaries. No model or external access.",
                    input_schema=SearchArguments.contract_schema(),
                    output_schema=SearchOutput.model_json_schema(),
                    annotations=ToolAnnotations(
                        read_only_hint=True,
                        destructive_hint=False,
                        idempotent_hint=True,
                        open_world_hint=False,
                    ),
                )
            ]
        )

    async def call_tool(_context, params):
        nonlocal fault_pending
        if params.name != "knowledge_search":
            raise MCPError(-32602, "unknown_tool")
        try:
            arguments = SearchArguments.model_validate(
                params.arguments if params.arguments is not None else {}
            )
        except ValidationError:
            return CallToolResult(
                content=[TextContent(type="text", text="invalid_arguments")], is_error=True
            )
        if fault_pending:
            fault_pending = False
            event("request_started")
            try:
                if fault == "disconnect":
                    if disconnect is None:
                        raise MCPError(-32603, "request_failed")
                    disconnect.set()
                await anyio.sleep_forever()
            finally:
                event("request_cleanup")
        payload = search(arguments).model_dump(mode="json")
        return CallToolResult(
            content=[
                TextContent(
                    type="text", text=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                )
            ],
            structured_content=payload,
            is_error=False,
        )

    async def list_resources(_context, _params):
        return ListResourcesResult(
            resources=[
                Resource(uri=c["uri"], name=c["title"], mime_type="text/plain") for c in COURSES
            ]
        )

    async def read_resource(_context, params):
        course = next((course for course in COURSES if course["uri"] == str(params.uri)), None)
        if course is None:
            raise MCPError(-32602, "resource_not_found")
        return ReadResourceResult(
            contents=[
                TextResourceContents(uri=course["uri"], mime_type="text/plain", text=course["text"])
            ]
        )

    def safe(handler):
        async def wrapped(context, params):
            try:
                return await handler(context, params)
            except MCPError as error:
                if error.code == -32602 and error.message in ("unknown_tool", "resource_not_found"):
                    raise
                raise MCPError(-32603, "request_failed") from None
            except Exception:
                raise MCPError(-32603, "request_failed") from None

        return wrapped

    return Server(
        "deep-ai-mcp-readonly",
        version=CONTRACT_VERSION,
        lifespan=lifespan,
        on_list_tools=safe(list_tools),
        on_call_tool=safe(call_tool),
        on_list_resources=safe(list_resources),
        on_read_resource=safe(read_resource),
    )


class SanitizedWrite:
    """Keep protocol IDs/codes, never relay SDK diagnostic data or peer input."""

    def __init__(self, stream):
        self.stream = stream

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.stream.aclose()

    async def send(self, item):
        message = item.message
        if isinstance(message, JSONRPCError):
            error = message.error
            fixed = {
                -32601: "method_not_found",
                -32602: "invalid_request_parameters",
                -32603: "request_failed",
                -32000: "transport_closed",
                -32022: "unsupported_protocol_version",
            }
            public = fixed.get(error.code, "protocol_error")
            if error.code == -32602 and error.message in ("unknown_tool", "resource_not_found"):
                public = error.message
            safe_error = ErrorData(code=error.code, message=public)
            if error.code == -32022:
                safe_error.data = {"supported": [PROTOCOL_VERSION]}
            item = replace(
                item, message=JSONRPCError(jsonrpc="2.0", id=message.id, error=safe_error)
            )
        await self.stream.send(item)

    async def aclose(self):
        await self.stream.aclose()


async def serve(fault: str) -> None:
    disconnected = anyio.Event()
    server = make_server(fault, disconnected)
    async with stdio_server() as (read, write):
        async with anyio.create_task_group() as group:

            async def stop_on_disconnect():
                await disconnected.wait()
                group.cancel_scope.cancel()

            group.start_soon(stop_on_disconnect)
            try:
                await server.run(
                    read, SanitizedWrite(write), server.create_initialization_options()
                )
            finally:
                group.cancel_scope.cancel()
        if fault == "disconnect" and disconnected.is_set():
            # This fixed, local fault ends the dedicated process after the real
            # request/lifespan finally blocks. The SDK stdin thread can still be
            # blocked; do not pretend transport shutdown was graceful or wait
            # for the client to close its pipe. Buffered terminal frames may drop.
            sys.stderr.flush()
            os._exit(0)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "invalid_cli_arguments\n")


def main() -> int:
    parser = SafeParser(description="Fixed read-only stdio MCP server")
    parser.add_argument("--fault", choices=("none", "timeout", "disconnect"), default="none")
    args = parser.parse_args()
    # This dedicated process emits only our fixed diagnostics, never SDK payload logs.
    logging.disable(logging.CRITICAL)
    try:
        anyio.run(serve, args.fault)
    except Exception:
        event("server_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
