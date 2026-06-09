from __future__ import annotations

from typing import Any, Literal

import typer
from fastmcp import FastMCP

app = typer.Typer(add_completion=False)


def create_mcp() -> FastMCP:
    """Create the minimal Step 0 FastMCP server."""
    mcp = FastMCP(
        "Project Knowledge MCP",
        instructions=(
            "Deterministic project-context access layer. Returns evidence and health metadata; "
            "the client assistant performs synthesis."
        ),
    )

    @mcp.tool
    def health() -> dict[str, Any]:
        """Return Step 0 server health."""
        return {
            "status": "ok",
            "phase": "step0_provider_evaluation",
            "llm_required": False,
            "default_network_exposure": "loopback_or_stdio_only",
        }

    @mcp.tool
    def validate_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Placeholder config validator proving stable MCP tool IO shape."""
        config = config or {}
        return {
            "valid": True,
            "ops_repo_configured": bool(config.get("ops_repo")),
            "work_repo_count": len(config.get("work_repos", [])),
            "warnings": [],
        }

    return mcp


@app.command()
def main(
    transport: Literal["stdio", "http", "streamable-http", "sse"] = typer.Option(
        "stdio", help="FastMCP transport. Use stdio for local MCP clients; http for loopback smoke."
    ),
    host: str = typer.Option("127.0.0.1", help="Host for HTTP-like transports."),
    port: int = typer.Option(8765, help="Port for HTTP-like transports."),
) -> None:
    mcp = create_mcp()
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=transport, host=host, port=port)


if __name__ == "__main__":
    app()
