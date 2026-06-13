from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
import uvicorn
from fastmcp import Client

from project_knowledge_mcp.server import create_mcp
from test_mcp_tools import EXPECTED_TOOL_NAMES, result_text


class BearerGateMiddleware:
    """Tiny ASGI equivalent of the example Caddy bearer-token gate."""

    def __init__(self, app: Callable, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if headers.get(b"authorization") != self.expected:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-length", b"0")],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return
        await self.app(scope, receive, send)


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_bearer_gated_mcp(port: int, token: str) -> uvicorn.Server:
    app = BearerGateMiddleware(
        create_mcp().http_app(path="/mcp", transport="streamable-http"), token=token
    )
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    server._pkmcp_thread = thread  # type: ignore[attr-defined]
    return server


def wait_for_401(port: int) -> None:
    deadline = time.monotonic() + 20
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/mcp", timeout=1)
            if response.status_code == 401:
                return
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)
    raise AssertionError(f"Bearer-gated MCP server did not become ready: {last_error!r}")


async def list_remote_tools_with_token(port: int, token: str) -> tuple[set[str], dict]:
    async with Client(f"http://127.0.0.1:{port}/mcp", auth=token) as client:
        tools = {tool.name for tool in await client.list_tools()}
        health = json.loads(result_text(await client.call_tool("health")))
        return tools, health


def test_caddy_bearer_gate_semantics_and_remote_tool_parity():
    port = free_loopback_port()
    token = "step8-secret-token"
    server = start_bearer_gated_mcp(port, token)
    try:
        wait_for_401(port)

        no_auth = httpx.get(f"http://127.0.0.1:{port}/mcp", timeout=3)
        assert no_auth.status_code == 401

        wrong_auth = httpx.get(
            f"http://127.0.0.1:{port}/mcp",
            headers={"Authorization": "Bearer wrong-token"},
            timeout=3,
        )
        assert wrong_auth.status_code == 401

        remote_tools, health = asyncio.run(list_remote_tools_with_token(port, token))
        assert remote_tools == EXPECTED_TOOL_NAMES
        assert "shell" not in remote_tools
        assert "merge" not in " ".join(sorted(remote_tools))
        assert health["status"] == "ok"
        assert health["default_network_exposure"] == "loopback_or_stdio_only"
    finally:
        server.should_exit = True
        thread = getattr(server, "_pkmcp_thread")
        thread.join(timeout=5)
