from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import typer
from fastmcp import FastMCP

from project_knowledge_mcp.index import ProjectIndex, index_repo
from project_knowledge_mcp.setup import (
    build_client_config,
    build_setup_plan,
    write_setup_artifacts,
)
from project_knowledge_mcp.setup_ui import run_setup_ui
from project_knowledge_mcp.services import (
    add_project_note_from_config,
    check_project_staleness_from_config,
    create_draft_artifact_from_config,
    generate_session_brief_from_config,
    get_code_context_from_config,
    get_code_provider_status_from_config,
    get_current_doctrine_from_config,
    index_project_from_config,
    propose_authority_change_from_config,
    retrieve_ops_code_evidence_from_config,
    search_code_from_config,
    search_decisions_from_config,
    search_open_questions_from_config,
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

    @mcp.tool
    def search_decisions(
        query: str,
        config_path: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Search accepted and draft decision docs with authority-aware ranking."""
        return search_decisions_from_config(
            query=query,
            config_path=config_path,
            filters=filters,
            limit=limit,
        )

    @mcp.tool
    def get_current_doctrine(
        topic: str,
        config_path: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Return canonical/current doctrine and accepted decisions for a topic."""
        return get_current_doctrine_from_config(
            topic=topic,
            config_path=config_path,
            filters=filters,
            limit=limit,
        )

    @mcp.tool
    def search_open_questions(
        query: str,
        config_path: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Search open questions with source and owner metadata."""
        return search_open_questions_from_config(
            query=query,
            config_path=config_path,
            filters=filters,
            limit=limit,
        )

    @mcp.tool
    def search_code(
        query: str,
        config_path: str | None = None,
        repo_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Search configured work repo code/test/schema evidence."""
        return search_code_from_config(
            query=query, config_path=config_path, repo_id=repo_id, limit=limit
        )

    @mcp.tool
    def get_code_context(
        symbol_or_file: str,
        config_path: str | None = None,
        repo_id: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Return symbol or file context via CodeGraph when healthy, else text fallback."""
        return get_code_context_from_config(
            symbol_or_file=symbol_or_file,
            config_path=config_path,
            repo_id=repo_id,
            limit=limit,
        )

    @mcp.tool
    def get_code_provider_status(config_path: str | None = None) -> dict[str, Any]:
        """Return CodeGraph/text fallback provider health."""
        return get_code_provider_status_from_config(config_path=config_path)

    @mcp.tool
    def retrieve_ops_code_evidence(
        topic: str,
        config_path: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Retrieve grouped ops and code evidence for a task/topic."""
        return retrieve_ops_code_evidence_from_config(
            topic=topic, config_path=config_path, limit=limit
        )

    @mcp.tool
    def generate_session_brief(
        task: str,
        config_path: str | None = None,
        since: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Compile a deterministic evidence packet for assistant session context."""
        return generate_session_brief_from_config(
            task=task, config_path=config_path, since=since, limit=limit
        )

    @mcp.tool
    def add_project_note(
        title: str,
        body: str,
        type: str = "note",
        tags: list[str] | None = None,
        source: str | None = None,
        target: str | None = None,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        """Write a low-authority capture note and index only that document."""
        return add_project_note_from_config(
            title=title,
            body=body,
            type=type,
            tags=tags,
            source=source,
            target=target,
            config_path=config_path,
        )

    @mcp.tool
    def create_draft_artifact(
        kind: str,
        title: str,
        body: str,
        source: str | None = None,
        tags: list[str] | None = None,
        target: str | None = None,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        """Create a non-canonical proposal/draft artifact."""
        return create_draft_artifact_from_config(
            kind=kind,
            title=title,
            body=body,
            source=source,
            tags=tags,
            target=target,
            config_path=config_path,
        )

    @mcp.tool
    def propose_authority_change(
        title: str,
        rationale: str,
        changes: list[dict[str, Any]],
        source: str | None = None,
        tags: list[str] | None = None,
        branch_name: str | None = None,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        """Prepare a branch/commit/optional PR for caller-supplied authority changes."""
        return propose_authority_change_from_config(
            title=title,
            rationale=rationale,
            changes=changes,
            source=source,
            tags=tags,
            branch_name=branch_name,
            config_path=config_path,
        )

    @mcp.tool
    def check_project_staleness(config_path: str | None = None) -> dict[str, Any]:
        """Report configured repo Git freshness and index staleness."""
        return check_project_staleness_from_config(config_path=config_path)

    return mcp


@app.command("start")
@app.command("serve")
def serve(
    transport: Literal["stdio", "http", "streamable-http", "sse"] = typer.Option(
        "stdio", help="FastMCP transport. Use stdio for local MCP clients; http for loopback smoke."
    ),
    host: str = typer.Option("127.0.0.1", help="Host for HTTP-like transports."),
    port: int = typer.Option(8000, help="Port for HTTP-like transports."),
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
    limit: int | None = typer.Option(None, help="Maximum results to return."),
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


@app.command("search-decisions")
def search_decisions_command(
    query: str = typer.Argument(..., help="Lexical query for decision docs."),
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
    limit: int | None = typer.Option(None, help="Maximum results to return."),
    include_superseded: bool = typer.Option(
        False, help="Include superseded/rejected decisions with visible authority labels."
    ),
    status: str | None = typer.Option(None, help="Filter by decision status."),
    authority: str | None = typer.Option(None, help="Filter by authority label."),
) -> None:
    """Search accepted and draft decision docs with MCP-compatible JSON and Markdown."""
    filters: dict[str, Any] = {"include_superseded": include_superseded}
    if status is not None:
        filters["status"] = status
    if authority is not None:
        filters["authority"] = authority
    typer.echo(
        json.dumps(
            search_decisions_from_config(
                query=query, config_path=config, filters=filters, limit=limit
            ),
            sort_keys=True,
        )
    )


@app.command("get-current-doctrine")
def get_current_doctrine_command(
    topic: str = typer.Argument(..., help="Topic to retrieve current doctrine for."),
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
    limit: int | None = typer.Option(None, help="Maximum results per section."),
    include_superseded: bool = typer.Option(
        False, help="Include superseded/rejected results with visible authority labels."
    ),
) -> None:
    """Return canonical/current doctrine and accepted decisions for a topic."""
    filters: dict[str, Any] = {"include_superseded": include_superseded}
    typer.echo(
        json.dumps(
            get_current_doctrine_from_config(
                topic=topic, config_path=config, filters=filters, limit=limit
            ),
            sort_keys=True,
        )
    )


@app.command("search-open-questions")
def search_open_questions_command(
    query: str = typer.Argument(..., help="Lexical query for open questions."),
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
    limit: int | None = typer.Option(None, help="Maximum results to return."),
    include_superseded: bool = typer.Option(
        False, help="Include superseded/rejected results with visible authority labels."
    ),
    status: str | None = typer.Option(None, help="Filter by question status."),
) -> None:
    """Search open questions with MCP-compatible JSON and Markdown."""
    filters: dict[str, Any] = {"include_superseded": include_superseded}
    if status is not None:
        filters["status"] = status
    typer.echo(
        json.dumps(
            search_open_questions_from_config(
                query=query, config_path=config, filters=filters, limit=limit
            ),
            sort_keys=True,
        )
    )


@app.command("search-code")
def search_code_command(
    query: str = typer.Argument(..., help="Lexical query for code/test/schema evidence."),
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
    repo_id: str | None = typer.Option(None, "--repo-id", help="Optional work repo id."),
    limit: int | None = typer.Option(None, help="Maximum results to return."),
) -> None:
    """Search configured work repo code/test/schema evidence."""
    typer.echo(
        json.dumps(
            search_code_from_config(query=query, config_path=config, repo_id=repo_id, limit=limit),
            sort_keys=True,
        )
    )


@app.command("get-code-context")
def get_code_context_command(
    symbol_or_file: str = typer.Argument(..., help="Symbol name or repo-relative file path."),
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
    repo_id: str | None = typer.Option(None, "--repo-id", help="Optional work repo id."),
    limit: int | None = typer.Option(None, help="Maximum results to return."),
) -> None:
    """Return symbol/file code context with text fallback when CodeGraph is unhealthy."""
    typer.echo(
        json.dumps(
            get_code_context_from_config(
                symbol_or_file=symbol_or_file, config_path=config, repo_id=repo_id, limit=limit
            ),
            sort_keys=True,
        )
    )


@app.command("get-code-provider-status")
def get_code_provider_status_command(
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
) -> None:
    """Report CodeGraph/text fallback provider health."""
    typer.echo(json.dumps(get_code_provider_status_from_config(config_path=config), sort_keys=True))


@app.command("retrieve-ops-code-evidence")
def retrieve_ops_code_evidence_command(
    topic: str = typer.Argument(..., help="Topic to retrieve ops and code evidence for."),
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
    limit: int | None = typer.Option(None, help="Maximum results per section."),
) -> None:
    """Retrieve grouped ops and code evidence for a task/topic."""
    typer.echo(
        json.dumps(
            retrieve_ops_code_evidence_from_config(topic=topic, config_path=config, limit=limit),
            sort_keys=True,
        )
    )


@app.command("generate-session-brief")
def generate_session_brief_command(
    task: str = typer.Argument(..., help="Task to compile deterministic session context for."),
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
    since: str | None = typer.Option(
        None, "--since", help="Optional user-provided recency marker."
    ),
    limit: int | None = typer.Option(None, help="Maximum results per section."),
) -> None:
    """Compile a deterministic evidence packet for assistant session context."""
    typer.echo(
        json.dumps(
            generate_session_brief_from_config(
                task=task, config_path=config, since=since, limit=limit
            ),
            sort_keys=True,
        )
    )


@app.command("check-project-staleness")
def check_project_staleness_command(
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
) -> None:
    """Report configured repo Git freshness and index staleness."""
    typer.echo(json.dumps(check_project_staleness_from_config(config_path=config), sort_keys=True))


@app.command("setup")
def setup_command(
    config: Path = typer.Option(Path("project.yaml"), "--config", help="Config file to generate."),
    project_root: Path = typer.Option(Path("."), "--project-root", help="Project workspace root."),
    ops_repo: Path | None = typer.Option(None, "--ops-repo", help="Writable ops repository path."),
    work_repo: list[Path] | None = typer.Option(
        None, "--work-repo", help="Repeatable read-only work repository path."
    ),
    client: list[str] | None = typer.Option(
        None, "--client", help="Repeatable client snippet to include."
    ),
    project_id: str | None = typer.Option(
        None, "--project-id", help="Project ID for generated config."
    ),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Fail instead of prompting for missing values."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview setup without writing files."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config file."),
) -> None:
    """Generate safe local setup config, mount guidance, and client snippets."""
    selected_clients = [str(item) for item in client] if client else ["hermes"]
    try:
        plan = build_setup_plan(
            config_path=config,
            project_root=project_root,
            ops_repo=ops_repo,
            work_repos=[Path(path) for path in work_repo or []],
            clients=selected_clients,
            dry_run=dry_run,
            project_id=project_id,
        )
        if not dry_run:
            plan = write_setup_artifacts(plan, force=force)
    except (FileExistsError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(plan, sort_keys=True))


@app.command("setup-ui")
def setup_ui_command(
    host: str = typer.Option(
        "127.0.0.1", "--host", help="Local setup UI bind host. Must be loopback."
    ),
    port: int = typer.Option(8765, "--port", help="Local setup UI port."),
) -> None:
    """Start the localhost browser setup wizard for no-terminal onboarding."""
    try:
        run_setup_ui(host=host, port=port)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("print-client-config")
def print_client_config_command(
    config: Path = typer.Option(
        Path("project.yaml"), "--config", help="Project Knowledge config path."
    ),
    client: str = typer.Option("hermes", "--client", help="Client type."),
    transport: str = typer.Option(
        "stdio", "--transport", help="stdio, streamable-http, or remote-https."
    ),
    http_url: str | None = typer.Option(None, "--http-url", help="Loopback HTTP MCP URL."),
    remote_url: str | None = typer.Option(None, "--remote-url", help="Remote HTTPS MCP URL."),
) -> None:
    """Print a redacted MCP client connection snippet."""
    try:
        payload = build_client_config(
            config_path=config,
            client=client,
            transport=transport,
            http_url=http_url,
            remote_url=remote_url,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(payload, sort_keys=True))


@app.command("status")
def status_command(
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
) -> None:
    """Validate config and report repo/index freshness in one packet."""
    validation = validate_config_service(config)
    if validation["valid"]:
        staleness = check_project_staleness_from_config(config_path=config)
    else:
        staleness = {
            "status": "error",
            "repos": [],
            "warnings": validation["warnings"],
            "errors": validation["errors"],
            "markdown": "## Project Staleness\n\nConfig is invalid; no staleness check was run.",
        }
    payload = {
        "status": "ok" if validation["valid"] else "error",
        "project_id": validation.get("project_id"),
        "config": validation,
        "staleness": staleness,
        "warnings": [*validation.get("warnings", []), *staleness.get("warnings", [])],
        "markdown": staleness.get("markdown", ""),
    }
    typer.echo(json.dumps(payload, sort_keys=True))


def _parse_changes_json(value: str) -> list[dict[str, Any]]:
    candidate = Path(value)
    if candidate.exists() and candidate.is_file():
        raw = candidate.read_text(encoding="utf-8")
    else:
        raw = value
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"changes must be JSON array or path to JSON file: {exc}") from exc
    if not isinstance(parsed, list):
        raise typer.BadParameter("changes JSON must be an array")
    return parsed


@app.command("add-project-note")
def add_project_note_command(
    title: str = typer.Option(..., help="Note title."),
    body: str = typer.Option(..., help="Markdown note body."),
    note_type: str = typer.Option("note", "--type", help="Low-authority note type."),
    tag: list[str] | None = typer.Option(None, "--tag", help="Repeatable tag."),
    source: str | None = typer.Option(None, help="Source/provenance label."),
    target: str | None = typer.Option(None, help="Optional repo-relative target dir/file."),
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
) -> None:
    """Write a low-authority project note and emit JSON."""
    typer.echo(
        json.dumps(
            add_project_note_from_config(
                title=title,
                body=body,
                type=note_type,
                tags=tag,
                source=source,
                target=target,
                config_path=config,
            ),
            sort_keys=True,
        )
    )


@app.command("create-draft-artifact")
def create_draft_artifact_command(
    kind: str = typer.Option(..., help="Draft kind."),
    title: str = typer.Option(..., help="Artifact title."),
    body: str = typer.Option(..., help="Markdown artifact body."),
    source: str | None = typer.Option(None, help="Source/provenance label."),
    tag: list[str] | None = typer.Option(None, "--tag", help="Repeatable tag."),
    target: str | None = typer.Option(None, help="Optional repo-relative target dir/file."),
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
) -> None:
    """Create a non-canonical draft/proposal artifact and emit JSON."""
    typer.echo(
        json.dumps(
            create_draft_artifact_from_config(
                kind=kind,
                title=title,
                body=body,
                source=source,
                tags=tag,
                target=target,
                config_path=config,
            ),
            sort_keys=True,
        )
    )


@app.command("propose-authority-change")
def propose_authority_change_command(
    title: str = typer.Option(..., help="Proposal title / commit subject."),
    rationale: str = typer.Option(..., help="Proposal rationale."),
    changes: str = typer.Option(..., help="JSON array or path to JSON file containing changes."),
    source: str | None = typer.Option(None, help="Source/provenance label."),
    tag: list[str] | None = typer.Option(None, "--tag", help="Repeatable tag."),
    branch_name: str | None = typer.Option(None, "--branch-name", help="Optional branch name."),
    config: Path | None = typer.Option(None, "--config", help="Project Knowledge config path."),
) -> None:
    """Prepare a branch/commit/optional PR for caller-supplied changes."""
    parsed_changes = _parse_changes_json(changes)
    typer.echo(
        json.dumps(
            propose_authority_change_from_config(
                title=title,
                rationale=rationale,
                changes=parsed_changes,
                source=source,
                tags=tag,
                branch_name=branch_name,
                config_path=config,
            ),
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
