from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from fastmcp import Client

SERVER = Path(__file__).resolve().parent / "server.py"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def result_text(result) -> str:
    return "".join(getattr(part, "text", str(part)) for part in result.content)


async def call_stdio() -> dict:
    config = {"mcpServers": {"pkmcp": {"command": sys.executable, "args": [str(SERVER)]}}}
    async with Client(config) as client:
        tools = [tool.name for tool in await client.list_tools()]
        health = json.loads(result_text(await client.call_tool("health")))
    return {"tools": tools, "health": health}


async def call_http(port: int) -> dict:
    async with Client(f"http://127.0.0.1:{port}/mcp") as client:
        tools = [tool.name for tool in await client.list_tools()]
        validation = json.loads(
            result_text(
                await client.call_tool(
                    "validate_config",
                    {"config": {"ops_repo": "ops", "work_repos": ["work"]}},
                )
            )
        )
    return {"tools": tools, "validation": validation}


def main() -> None:
    stdio = asyncio.run(call_stdio())
    port = free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(SERVER),
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        last_error = None
        http = None
        for _ in range(40):
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"HTTP server exited early: {output}")
            try:
                http = asyncio.run(call_http(port))
                break
            except Exception as exc:  # server may still be starting
                last_error = exc
                time.sleep(0.25)
        if http is None:
            raise RuntimeError(f"HTTP client failed: {last_error}")
        print(
            json.dumps(
                {"stdio": stdio, "http": http, "verdict": "VALIDATED"}, indent=2, sort_keys=True
            )
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
