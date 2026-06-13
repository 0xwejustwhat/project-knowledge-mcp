#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from fastmcp import Client


EXPECTED_TOOL_NAMES = {
    "health",
    "validate_config",
    "index_project",
    "search_ops",
    "search_decisions",
    "get_current_doctrine",
    "search_open_questions",
    "search_code",
    "get_code_context",
    "get_code_provider_status",
    "retrieve_ops_code_evidence",
    "generate_session_brief",
    "add_project_note",
    "create_draft_artifact",
    "propose_authority_change",
    "check_project_staleness",
}


def result_text(result) -> str:
    return "".join(getattr(part, "text", str(part)) for part in result.content)


async def smoke_registry(url: str, token: str | None) -> None:
    kwargs = {"auth": token} if token else {}
    async with Client(url, **kwargs) as client:
        tool_names = {tool.name for tool in await client.list_tools()}
        if tool_names != EXPECTED_TOOL_NAMES:
            missing = sorted(EXPECTED_TOOL_NAMES - tool_names)
            extra = sorted(tool_names - EXPECTED_TOOL_NAMES)
            raise AssertionError(f"Tool registry mismatch; missing={missing}; extra={extra}")

        health = json.loads(result_text(await client.call_tool("health")))
        if health.get("status") != "ok":
            raise AssertionError(f"Unexpected health status: {health!r}")
        if health.get("default_network_exposure") != "loopback_or_stdio_only":
            raise AssertionError(f"Unexpected exposure policy: {health!r}")

    print(
        f"registry-ok url={url} token={'yes' if token else 'no'} tools={len(EXPECTED_TOOL_NAMES)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke a Project Knowledge MCP StreamableHTTP registry."
    )
    parser.add_argument("url", help="MCP endpoint, for example http://127.0.0.1:8000/mcp")
    parser.add_argument("--token", help="Bearer token to send through FastMCP auth support")
    args = parser.parse_args()

    asyncio.run(smoke_registry(args.url, args.token))
    return 0


if __name__ == "__main__":
    sys.exit(main())
