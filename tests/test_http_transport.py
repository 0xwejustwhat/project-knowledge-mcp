from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from fastmcp import Client

from project_knowledge_mcp.server import create_mcp
from test_mcp_tools import EXPECTED_TOOL_NAMES, result_text


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def fetch_http_registry(port: int) -> tuple[set[str], dict]:
    async with Client(f"http://127.0.0.1:{port}/mcp") as client:
        tools = {tool.name for tool in await client.list_tools()}
        health = json.loads(result_text(await client.call_tool("health")))
        return tools, health


def wait_for_http_mcp(port: int, process: subprocess.Popen, timeout_seconds: float = 20.0):
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(
                f"MCP HTTP server exited early with {process.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            )
        try:
            return asyncio.run(fetch_http_registry(port))
        except Exception as exc:  # server may not be ready yet
            last_error = exc
            time.sleep(0.25)
    raise AssertionError(f"MCP HTTP server did not become ready: {last_error!r}")


def test_streamable_http_transport_exposes_same_policy_enforced_tools():
    port = free_loopback_port()
    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = f"{repo_root / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "project_knowledge_mcp.server",
            "start",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        http_tools, health = wait_for_http_mcp(port, process)
        local_tools = asyncio.run(_local_registry())
        assert http_tools == local_tools == EXPECTED_TOOL_NAMES
        assert health["status"] == "ok"
        assert health["default_network_exposure"] == "loopback_or_stdio_only"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


async def _local_registry() -> set[str]:
    async with Client(create_mcp()) as client:
        return {tool.name for tool in await client.list_tools()}
