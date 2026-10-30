"""Transports for the server: stdio (default) or streamable HTTP for a hosted instance.

A hosted instance can require a bearer token (`--token` or BLE_LIVE_MCP_TOKEN) and run
read-only, which removes the tools that touch the server's file system or bench
(xlsx files, serial ports, live scanning). Everything else is pure computation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Any

from mcp.server.mcpserver import MCPServer

from .hardware_tools import HARDWARE_TOOLS

READ_ONLY_REMOVED = ("import_xlsx_file", "export_xlsx_file", *HARDWARE_TOOLS)
TOKEN_ENV = "BLE_LIVE_MCP_TOKEN"


def make_read_only(mcp: MCPServer) -> list[str]:
    """Remove the tools a hosted, shared instance must not expose. Returns what was removed."""
    removed = []
    for name in READ_ONLY_REMOVED:
        try:
            mcp.remove_tool(name)
            removed.append(name)
        except Exception:  # noqa: BLE001 - already absent
            continue
    return removed


class BearerTokenMiddleware:
    """Minimal ASGI middleware: every HTTP request needs `Authorization: Bearer <token>`."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        if headers.get(b"authorization") != self.expected:
            body = json.dumps({"error": "unauthorized"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def build_http_app(
    mcp: MCPServer,
    token: str | None = None,
    read_only: bool = False,
    path: str = "/mcp",
    host: str = "127.0.0.1",
) -> Any:
    """The Starlette app for streamable HTTP, optionally read-only and token-protected."""
    if read_only:
        make_read_only(mcp)
    security = None
    if token:
        # The token is the access control. The SDK's DNS-rebinding guard (Host header must
        # match the bind address) would reject a public hostname behind a proxy, so it is off
        # when a token is required and on for an open localhost server.
        from mcp.server.transport_security import TransportSecuritySettings

        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    app = mcp.streamable_http_app(
        streamable_http_path=path,
        json_response=True,
        stateless_http=True,
        host=host,
        transport_security=security,
    )
    if token:
        return BearerTokenMiddleware(app, token)
    return app


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ble-live-mcp", description="BLE Live MCP server")
    p.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    p.add_argument("--host", default="127.0.0.1", help="http: bind address")
    p.add_argument("--port", type=int, default=8765, help="http: bind port")
    p.add_argument("--path", default="/mcp", help="http: endpoint path")
    p.add_argument(
        "--token",
        default=None,
        help=f"http: required bearer token (default: ${TOKEN_ENV}); none = open access",
    )
    p.add_argument(
        "--read-only",
        action="store_true",
        help="remove tools that touch the server's files, serial ports or radios",
    )
    return p


def run_from_args(mcp: MCPServer, argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.transport == "stdio":
        if args.read_only:
            make_read_only(mcp)
        mcp.run("stdio")
        return
    token = args.token if args.token is not None else os.environ.get(TOKEN_ENV)
    if not token and args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"refusing to listen on {args.host} without a token; pass --token or set {TOKEN_ENV}",
            file=sys.stderr,
        )
        sys.exit(2)
    import uvicorn

    app = build_http_app(mcp, token=token, read_only=args.read_only, path=args.path, host=args.host)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
