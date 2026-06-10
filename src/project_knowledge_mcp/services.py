from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from project_knowledge_mcp.config import (
    ProjectKnowledgeConfig,
    load_project_config,
    validate_project_config,
)
from project_knowledge_mcp.index import ProjectIndex, SearchResult, index_repo


def validate_config_service(config_path: Path | str | None = None) -> dict[str, Any]:
    return validate_project_config(config_path)


def index_project_from_config(
    *,
    config_path: Path | str | None = None,
    repo_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    validation = validate_project_config(config_path)
    if not validation["valid"]:
        return {
            "status": "error",
            "error": validation["errors"][0] if validation["errors"] else None,
            "errors": validation["errors"],
            "warnings": validation["warnings"],
        }

    config = load_project_config(config_path)
    assert config.storage.state_dir is not None
    selected_repos = _select_repos(config, repo_id=repo_id)
    started_at = _now()
    repo_summaries: list[dict[str, Any]] = []
    warnings: list[str] = []
    for repo in selected_repos:
        summary = index_repo(
            repo.path,
            state_dir=config.storage.state_dir,
            repo_id=repo.id,
            role=repo.role,
            writable=repo.writable,
            source_mode=repo.source_mode,
            host_path=repo.host_path,
            includes_uncommitted_changes=repo.includes_uncommitted_changes,
            snapshot_ref=repo.snapshot_ref,
            snapshot_commit=repo.snapshot_commit,
            include_globs=repo.include_globs,
            exclude_globs=repo.exclude_globs,
            max_file_bytes=config.indexing.max_file_bytes,
        )
        repo_warnings: list[str] = []
        if summary.warning_count:
            repo_warnings.append(f"{summary.warning_count} index warning(s) recorded")
        repo_summaries.append(
            {
                "repo_id": summary.repo_id,
                "role": repo.role,
                "path": str(repo.path),
                "source_mode": repo.source_mode,
                "host_path": str(repo.host_path) if repo.host_path is not None else None,
                "includes_uncommitted_changes": repo.includes_uncommitted_changes,
                "snapshot_ref": repo.snapshot_ref,
                "snapshot_commit": repo.snapshot_commit,
                "documents_indexed": summary.indexed_documents,
                "chunks_indexed": summary.indexed_chunks,
                "documents_skipped": summary.skipped_documents,
                "warning_count": summary.warning_count,
                "warnings": repo_warnings,
            }
        )
        warnings.extend(f"{repo.id}: {warning}" for warning in repo_warnings)

    return {
        "status": "ok",
        "started_at": started_at,
        "finished_at": _now(),
        "project_id": config.project.id,
        "state_dir": str(config.storage.state_dir),
        "force": force,
        "repos": repo_summaries,
        "warnings": warnings,
    }


def search_ops_from_config(
    *,
    query: str,
    config_path: Path | str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    validation = validate_project_config(config_path)
    if not validation["valid"]:
        return {
            "query": query,
            "results": [],
            "warnings": validation["warnings"],
            "markdown": "## Search Results\n\nConfig is invalid; no search was run.",
            "error": validation["errors"][0] if validation["errors"] else None,
            "errors": validation["errors"],
        }

    config = load_project_config(config_path)
    assert config.storage.state_dir is not None
    ops_repo = config.ops_repo
    effective_filters = dict(filters or {})
    include_superseded = bool(
        effective_filters.pop("include_superseded", config.retrieval.include_superseded_by_default)
    )
    tag_filters = _normalize_tags(effective_filters.pop("tags", None))
    requested_repo_id = effective_filters.pop("repo_id", None)
    if requested_repo_id is not None and requested_repo_id != ops_repo.id:
        return _query_invalid(
            query,
            f"repo_id filter cannot widen search_ops beyond ops repo: {ops_repo.id}",
            details={"repo_id": requested_repo_id, "ops_repo_id": ops_repo.id},
        )
    if "type" in effective_filters and "doc_type" not in effective_filters:
        effective_filters["doc_type"] = effective_filters.pop("type")
    effective_filters = {
        key: value for key, value in effective_filters.items() if value is not None
    }
    non_scalar_filters = {
        key: value
        for key, value in effective_filters.items()
        if not isinstance(value, str | int | float | bool)
    }
    if non_scalar_filters:
        return _query_invalid(
            query,
            "search filters must use scalar values",
            details={"filters": non_scalar_filters},
        )
    effective_filters["repo_id"] = ops_repo.id
    result_limit = config.retrieval.default_limit if limit is None else limit
    if result_limit < 1 or result_limit > 1000:
        return _query_invalid(
            query,
            f"limit must be between 1 and 1000: {result_limit}",
            details={"limit": result_limit},
        )

    try:
        search_limit = 1000 if tag_filters else max(result_limit * 2, result_limit)
        results = ProjectIndex.open(config.storage.state_dir).search(
            query,
            filters=effective_filters,
            include_superseded=include_superseded,
            limit=search_limit,
        )
    except FileNotFoundError:
        return {
            "query": query,
            "results": [],
            "warnings": ["Index is not ready; run index_project first."],
            "markdown": "## Search Results\n\nIndex is not ready; run `index_project` first.",
            "error": {
                "code": "INDEX_NOT_READY",
                "message": "Index is not ready; run index_project first.",
                "details": {"state_dir": str(config.storage.state_dir)},
                "recoverable": True,
            },
        }
    except ValueError as exc:
        return _query_invalid(
            query,
            str(exc),
            details={"filters": effective_filters, "limit": result_limit},
        )

    if tag_filters:
        results = [result for result in results if set(tag_filters).issubset(result.tags)]
    results = results[:result_limit]
    payload_results = [_search_result_payload(result) for result in results]
    return {
        "query": query,
        "project_id": config.project.id,
        "results": payload_results,
        "warnings": [],
        "markdown": _render_search_markdown(query, payload_results),
    }


def _query_invalid(
    query: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "query": query,
        "results": [],
        "warnings": [],
        "markdown": "## Search Results\n\nQuery invalid; no search was run.",
        "error": {
            "code": "QUERY_INVALID",
            "message": message,
            "details": details or {},
            "recoverable": True,
        },
        "errors": [
            {
                "code": "QUERY_INVALID",
                "message": message,
                "details": details or {},
                "recoverable": True,
            }
        ],
    }


def _select_repos(config: ProjectKnowledgeConfig, *, repo_id: str | None) -> list:
    if repo_id is None:
        return list(config.repos)
    repo = config.repo_by_id(repo_id)
    if repo is None:
        raise ValueError(f"Unknown repo_id: {repo_id}")
    return [repo]


def _search_result_payload(result: SearchResult) -> dict[str, Any]:
    return {
        "repo_id": result.repo_id,
        "source_mode": result.source_mode,
        "includes_uncommitted_changes": result.includes_uncommitted_changes,
        "snapshot_ref": result.snapshot_ref,
        "snapshot_commit": result.snapshot_commit,
        "path": result.path,
        "title": result.title,
        "doc_type": result.doc_type,
        "type": result.doc_type,
        "status": result.status,
        "authority": result.authority,
        "tags": result.tags,
        "superseded_by": result.superseded_by,
        "chunk_id": result.chunk_id,
        "heading_path": result.heading_path,
        "start_line": result.line_start,
        "end_line": result.line_end,
        "excerpt": result.snippet.replace("[", "").replace("]", ""),
        "score": result.final_score,
        "bm25_score": result.bm25_score,
        "warnings": _authority_warnings(result),
    }


def _authority_warnings(result: SearchResult) -> list[str]:
    warnings: list[str] = []
    if result.authority in {"superseded", "rejected"} or result.status in {
        "superseded",
        "rejected",
    }:
        warnings.append(
            f"Result is {result.authority}/{result.status}; do not treat as current truth."
        )
    return warnings


def _render_search_markdown(query: str, results: list[dict[str, Any]]) -> str:
    lines = ["## Search Results", "", f"Query: `{query}`", ""]
    if not results:
        lines.append("No results.")
        return "\n".join(lines)
    for index, result in enumerate(results, start=1):
        source = f"{result['path']}:{result['start_line']}-{result['end_line']}"
        lines.extend(
            [
                f"### {index}. {result['title']}",
                f"Source: `{source}`",
                f"Authority: `{result['authority']}` | Status: `{result['status']}` | Repo: `{result['repo_id']}`",
            ]
        )
        if result["warnings"]:
            lines.append(f"Warnings: {'; '.join(result['warnings'])}")
        lines.extend(["", f"> {result['excerpt']}", ""])
    return "\n".join(lines).rstrip() + "\n"


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
