from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import typer
from fastmcp import FastMCP

from project_knowledge_mcp.index import ProjectIndex, index_repo

app = typer.Typer(add_completion=False, help="Project Knowledge MCP local tooling.")


def create_mcp() -> FastMCP:
    """Create the minimal Project Knowledge MCP server."""
    mcp = FastMCP(
        "Project Knowledge MCP",
        instructions=(
            "Deterministic project-context access layer. Returns evidence and health metadata; "
            "the client assistant performs synthesis."
        ),
    )

    @mcp.tool
    def health() -> dict[str, Any]:
        """Return server health."""
        return {
            "status": "ok",
            "phase": "step1_authority_aware_sqlite_index",
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


@app.command("serve")
def serve(
    transport: Literal["stdio", "http", "streamable-http", "sse"] = typer.Option(
        "stdio", help="FastMCP transport. Use stdio for local MCP clients; http for loopback smoke."
    ),
    host: str = typer.Option("127.0.0.1", help="Host for HTTP-like transports."),
    port: int = typer.Option(8765, help="Port for HTTP-like transports."),
) -> None:
    """Run the MCP server."""
    mcp = create_mcp()
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=transport, host=host, port=port)


@app.command("index-project")
def index_project_command(
    repo_path: Path = typer.Option(..., help="Repository path to index."),
    state_dir: Path = typer.Option(..., help="Project Knowledge state directory."),
    repo_id: str = typer.Option("ops", help="Stable repository ID."),
    role: str = typer.Option("ops", help="Repository role: ops, work, or artifact."),
    writable: bool = typer.Option(False, help="Whether the repository is writable by PKMCP."),
) -> None:
    """Index Markdown and text files into the authority-aware SQLite store."""
    summary = index_repo(
        repo_path,
        state_dir=state_dir,
        repo_id=repo_id,
        role=role,
        writable=writable,
    )
    typer.echo(
        json.dumps(
            {
                "db_path": str(summary.db_path),
                "repo_id": summary.repo_id,
                "indexed_documents": summary.indexed_documents,
                "indexed_chunks": summary.indexed_chunks,
                "warning_count": summary.warning_count,
            },
            sort_keys=True,
        )
    )


@app.command("search-index")
def search_index_command(
    query: str = typer.Argument(..., help="Lexical query for the SQLite FTS5 index."),
    state_dir: Path = typer.Option(..., help="Project Knowledge state directory."),
    limit: int = typer.Option(10, min=1, help="Maximum results to return."),
    include_superseded: bool = typer.Option(
        False, help="Include superseded/rejected content with visible authority labels."
    ),
) -> None:
    """Search the local index with authority-aware post-ranking."""
    results = ProjectIndex.open(state_dir).search(
        query, include_superseded=include_superseded, limit=limit
    )
    typer.echo(
        json.dumps(
            {
                "query": query,
                "results": [
                    {
                        "path": result.path,
                        "title": result.title,
                        "repo_id": result.repo_id,
                        "type": result.doc_type,
                        "status": result.status,
                        "authority": result.authority,
                        "tags": result.tags,
                        "superseded_by": result.superseded_by,
                        "line_start": result.line_start,
                        "line_end": result.line_end,
                        "snippet": result.snippet,
                        "bm25_score": result.bm25_score,
                        "final_score": result.final_score,
                    }
                    for result in results
                ],
            },
            sort_keys=True,
        )
    )


def cli() -> None:
    app()


if __name__ == "__main__":
    import sys

    # Compatibility for Step 0 spike invocations that ran this module directly as
    # an MCP server: `python server.py --transport http ...`.
    if len(sys.argv) == 1 or sys.argv[1].startswith("--"):
        sys.argv.insert(1, "serve")
    cli()
