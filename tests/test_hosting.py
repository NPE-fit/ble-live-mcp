"""Streamable HTTP hosting: bearer token gate and read-only tool removal."""

import asyncio
import json

import httpx
from mcp.server.mcpserver import MCPServer

from ble_live import hosting

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def fresh_server() -> MCPServer:
    mcp = MCPServer("t", version="0")

    @mcp.tool()
    def add(a: int, b: int) -> int:
        """Add."""
        return a + b

    @mcp.tool()
    def import_xlsx_file(path: str) -> dict:
        """Stand-in for the file tool."""
        return {"path": path}

    @mcp.tool()
    def scan_live(source: str) -> dict:
        """Stand-in for the bench tool."""
        return {"source": source}

    return mcp


def test_read_only_removes_file_and_bench_tools():
    mcp = fresh_server()
    removed = hosting.make_read_only(mcp)
    assert set(removed) == {"import_xlsx_file", "scan_live"}
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {"add"}


async def _post(app, headers):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/mcp", json=INIT, headers=headers)


def test_token_gate_and_initialize():
    mcp = fresh_server()
    app = hosting.build_http_app(mcp, token="s3cret", read_only=True)
    inner = app.app  # the Starlette app under the middleware, for its lifespan

    async def run():
        # the session manager's lifespan may only start once per instance
        async with inner.router.lifespan_context(inner):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                denied = await client.post("/mcp", json=INIT, headers=HEADERS)
                allowed = await client.post(
                    "/mcp", json=INIT, headers={**HEADERS, "authorization": "Bearer s3cret"}
                )
                return denied, allowed

    denied, allowed = asyncio.run(run())
    assert denied.status_code == 401 and denied.headers["www-authenticate"] == "Bearer"
    assert allowed.status_code == 200, allowed.text
    body = json.loads(allowed.text)
    assert body["result"]["serverInfo"]["name"] == "t"


def test_parser_defaults_and_refusal_without_token(monkeypatch, capsys):
    args = hosting.build_parser().parse_args([])
    assert args.transport == "stdio" and not args.read_only
    monkeypatch.delenv(hosting.TOKEN_ENV, raising=False)
    try:
        hosting.run_from_args(fresh_server(), ["--transport", "http", "--host", "0.0.0.0"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover
        raise AssertionError("expected a refusal")
    assert "without a token" in capsys.readouterr().err
