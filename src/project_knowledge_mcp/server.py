from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import typer
from fastmcp import FastMCP

from project_knowledge_mcp.index import ProjectIndex, index_repo
from project_knowledge_mcp.services import (
    index_project_from_config,
    search_ops_from_config,
    validate_config_service,
)

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
            "phase": "step2_config_backed_mcp_tools",
            "llm_required": False,
            "default_network_exposure": "loopback_or_stdio_only",
        }

    @mcp.tool
    def validate_config(
        config_path: str | None = None, config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Validate real project config, with legacy inline smoke compatibility."""
        if config_path is None and config is not None and "schema_version" not in config:
            return {
                "valid": True,
                "project_id": None,
                "repos": [],
                "ops_repo_configured": bool(config.get("ops_repo")),
                "work_repo_count": len(config.get("work_repos", [])),
                "warnings": ["legacy inline config shape accepted for transport smoke only"],
                "errors": [],
            }
        return validate_config_service(config_path)

    @mcp.tool
    def index_project(
        config_path: str | None = None, repo_id: str | None = None, force: bool = False
    ) -> dict[str, Any]:
        """Index configured repos into the authority-aware SQLite store."""
        try:
            return index_project_from_config(config_path=config_path, repo_id=repo_id, force=force)
        except ValueError as exc:
            return {
                "status": "error",
                "error": {
                    "code": "CONFIG_INVALID",
                    "message": str(exc),
                    "details": {"repo_id": repo_id},
                    "recoverable": True,
                },
                "errors": [],
                "warnings": [],
            }

    @mcp.tool
    def search_ops(
        query: str,
        config_path: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Search configured ops repo docs with authority-aware ranking."""
        return search_ops_from_config(
            query=query,
            config_path=config_path,
            filters=filters,
            limit=limit,
        )

    return mcp


@app.command("start")
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


@app.command("validate-config")
def validate_config_command(
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
) -> None:
    """Validate Project Knowledge config and repo accessibility."""
    typer.echo(json.dumps(validate_config_service(config), sort_keys=True))


@app.command("index-project")
def index_project_command(
    repo_path: Path | None = typer.Option(None, help="Repository path to index."),
    state_dir: Path | None = typer.Option(None, help="Project Knowledge state directory."),
    repo_id: str | None = typer.Option(None, help="Stable repository ID."),
    role: str = typer.Option("ops", help="Repository role: ops, work, or artifact."),
    writable: bool = typer.Option(False, help="Whether the repository is writable by PKMCP."),
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
    force: bool = typer.Option(
        False, help="Force configured reindex even if freshness checks later say current."
    ),
) -> None:
    """Index Markdown and text files into the authority-aware SQLite store."""
    if config is not None:
        try:
            typer.echo(
                json.dumps(
                    index_project_from_config(config_path=config, repo_id=repo_id, force=force),
                    sort_keys=True,
                )
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        return

    if repo_path is None or state_dir is None:
        raise typer.BadParameter("Either --config or both --repo-path and --state-dir are required")
    direct_repo_id = repo_id or "ops"
    summary = index_repo(
        repo_path,
        state_dir=state_dir,
        repo_id=direct_repo_id,
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


@app.command("search-ops")
def search_ops_command(
    query: str = typer.Argument(..., help="Lexical query for configured ops docs."),
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
    limit: int | None = typer.Option(None, min=1, help="Maximum results to return."),
    include_superseded: bool = typer.Option(
        False, help="Include superseded/rejected content with visible authority labels."
    ),
    doc_type: str | None = typer.Option(None, help="Filter by document type."),
    authority: str | None = typer.Option(None, help="Filter by authority label."),
    status: str | None = typer.Option(None, help="Filter by status."),
) -> None:
    """Search configured ops docs with MCP-compatible JSON and Markdown."""
    filters: dict[str, Any] = {"include_superseded": include_superseded}
    if doc_type is not None:
        filters["doc_type"] = doc_type
    if authority is not None:
        filters["authority"] = authority
    if status is not None:
        filters["status"] = status
    typer.echo(
        json.dumps(
            search_ops_from_config(query=query, config_path=config, filters=filters, limit=limit),
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
                        "source_mode": result.source_mode,
                        "includes_uncommitted_changes": result.includes_uncommitted_changes,
                        "snapshot_ref": result.snapshot_ref,
                        "snapshot_commit": result.snapshot_commit,
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


def main() -> None:
    """Backward-compatible console entrypoint for previously generated scripts."""
    cli()


if __name__ == "__main__":
    import sys

    # Compatibility for Step 0 spike invocations that ran this module directly as
    # an MCP server: `python server.py --transport http ...`.
    if len(sys.argv) == 1 or sys.argv[1].startswith("--"):
        sys.argv.insert(1, "serve")
    cli()
