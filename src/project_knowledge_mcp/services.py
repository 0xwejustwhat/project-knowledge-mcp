from __future__ import annotations

import sqlite3
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from project_knowledge_mcp.code_context import (
    CodeGraphContextProvider,
    TextFallbackCodeContextProvider,
)
from project_knowledge_mcp.config import (
    ProjectKnowledgeConfig,
    load_project_config,
    validate_project_config,
)
from project_knowledge_mcp.index import (
    INDEX_DB_NAME,
    ProjectIndex,
    SearchResult,
    index_repo,
    index_scope_fingerprint,
    worktree_fingerprint,
)


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
    effective_filters, filter_error = _normalize_filter_mapping(query, filters)
    if filter_error is not None:
        return filter_error
    include_superseded_value = effective_filters.pop(
        "include_superseded", config.retrieval.include_superseded_by_default
    )
    if not isinstance(include_superseded_value, bool):
        return _query_invalid(
            query,
            "include_superseded must be a boolean value",
            details={"include_superseded": include_superseded_value},
        )
    include_superseded = include_superseded_value
    tag_filters = _normalize_tags(effective_filters.pop("tags", None))
    allowed_statuses_value = effective_filters.pop("_allowed_statuses", None)
    allowed_statuses, allowed_statuses_error = _normalize_allowed_statuses(allowed_statuses_value)
    if allowed_statuses_error is not None:
        return _query_invalid(
            query,
            allowed_statuses_error,
            details={"allowed_statuses": allowed_statuses_value},
        )
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
    result_limit, limit_error = _normalize_limit(
        query, limit, default_limit=config.retrieval.default_limit
    )
    if limit_error is not None:
        return limit_error

    try:
        search_limit = (
            1000 if tag_filters or allowed_statuses else max(result_limit * 2, result_limit)
        )
        results = ProjectIndex.open(config.storage.state_dir).search(
            query,
            filters=effective_filters,
            include_superseded=include_superseded,
            limit=search_limit,
        )
    except (FileNotFoundError, sqlite3.Error, OSError):
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
    if allowed_statuses:
        results = [result for result in results if result.status in allowed_statuses]
    results = results[:result_limit]
    payload_results = [_search_result_payload(result) for result in results]
    return {
        "query": query,
        "project_id": config.project.id,
        "results": payload_results,
        "warnings": [],
        "markdown": _render_search_markdown(query, payload_results),
    }


def search_decisions_from_config(
    *,
    query: str,
    config_path: Path | str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    scoped_filters, error = _filters_with_required_doc_type(
        query,
        filters,
        required_doc_type="decision",
        default_statuses=("accepted", "current", "draft"),
        include_superseded_statuses=True,
    )
    if error is not None:
        return error
    result = search_ops_from_config(
        query=query, config_path=config_path, filters=scoped_filters, limit=limit
    )
    result["tool"] = "search_decisions"
    return result


def search_open_questions_from_config(
    *,
    query: str,
    config_path: Path | str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    scoped_filters, error = _filters_with_required_doc_type(
        query,
        filters,
        required_doc_type="open_question",
        default_statuses=("open",),
        include_superseded_statuses=True,
    )
    if error is not None:
        return error
    result = search_ops_from_config(
        query=query, config_path=config_path, filters=scoped_filters, limit=limit
    )
    result["tool"] = "search_open_questions"
    for search_result in result.get("results", []):
        frontmatter = search_result.get("frontmatter", {})
        search_result["owner"] = frontmatter.get("owner")
        search_result["related_docs"] = _normalize_metadata_list(
            frontmatter.get("related_docs", frontmatter.get("related"))
        )
    return result


def get_current_doctrine_from_config(
    *,
    topic: str,
    config_path: Path | str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    doctrine_filters, error = _filters_with_required_doc_type(
        topic, filters, required_doc_type="doctrine", default_status="current"
    )
    if error is not None:
        return error
    decision_filters, error = _filters_with_required_doc_type(
        topic, filters, required_doc_type="decision", default_status="accepted"
    )
    if error is not None:
        return error

    doctrine = search_ops_from_config(
        query=topic, config_path=config_path, filters=doctrine_filters, limit=limit
    )
    if doctrine.get("error"):
        doctrine["topic"] = topic
        return doctrine
    decisions = search_ops_from_config(
        query=topic, config_path=config_path, filters=decision_filters, limit=limit
    )
    if decisions.get("error"):
        decisions["topic"] = topic
        return decisions

    doctrine_results = doctrine.get("results", [])
    decision_results = decisions.get("results", [])
    warnings = [*doctrine.get("warnings", []), *decisions.get("warnings", [])]
    return {
        "tool": "get_current_doctrine",
        "topic": topic,
        "project_id": doctrine.get("project_id") or decisions.get("project_id"),
        "doctrine": doctrine_results,
        "decisions": decision_results,
        "results": [*doctrine_results, *decision_results],
        "warnings": warnings,
        "markdown": _render_current_doctrine_markdown(topic, doctrine_results, decision_results),
    }


def check_project_staleness_from_config(
    *,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    validation = validate_project_config(config_path)
    if not validation["valid"]:
        return {
            "status": "error",
            "project_id": validation.get("project_id"),
            "repos": [],
            "warnings": validation["warnings"],
            "markdown": "## Project Staleness\n\nConfig is invalid; no staleness check was run.",
            "error": validation["errors"][0] if validation["errors"] else None,
            "errors": validation["errors"],
        }

    config = load_project_config(config_path)
    assert config.storage.state_dir is not None
    indexed_metadata = _load_indexed_repo_metadata(config.storage.state_dir)
    repo_statuses = [
        _repo_staleness_status(
            repo,
            indexed_metadata.get(repo.id),
            state_dir=config.storage.state_dir,
            max_file_bytes=config.indexing.max_file_bytes,
        )
        for repo in config.repos
    ]
    _persist_repo_statuses(config.storage.state_dir, repo_statuses)
    warnings = [warning for repo in repo_statuses for warning in repo["warnings"]]
    return {
        "status": "ok",
        "project_id": config.project.id,
        "checked_at": _now(),
        "state_dir": str(config.storage.state_dir),
        "repos": repo_statuses,
        "warnings": warnings,
        "markdown": _render_staleness_markdown(config.project.id, repo_statuses),
    }


def get_code_provider_status_from_config(
    *,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    validation = validate_project_config(config_path)
    if not validation["valid"]:
        return {
            "status": "error",
            "project_id": validation.get("project_id"),
            "configured_provider": None,
            "active_provider": None,
            "codegraph_enabled": False,
            "codegraph_healthy": False,
            "fallback_available": False,
            "work_repo_count": 0,
            "work_repos": [],
            "warnings": validation["warnings"],
            "error": validation["errors"][0] if validation["errors"] else None,
            "errors": validation["errors"],
        }
    config = load_project_config(config_path)
    work_repos = [repo for repo in config.repos if repo.role == "work"]
    codegraph_health = CodeGraphContextProvider(config).health()
    active_provider = "text" if config.code_context.fallback_on_unhealthy else "unavailable"
    warnings = list(codegraph_health.warnings)
    if not work_repos:
        warnings.append("No work repos are configured for code context.")
    return {
        "status": "ok",
        "project_id": config.project.id,
        "configured_provider": config.code_context.provider,
        "active_provider": active_provider,
        "codegraph_enabled": config.code_context.codegraph.enabled,
        "codegraph_healthy": codegraph_health.codegraph_healthy,
        "fallback_available": config.code_context.fallback_on_unhealthy and bool(work_repos),
        "work_repo_count": len(work_repos),
        "work_repos": [repo.id for repo in work_repos],
        "warnings": warnings,
        "details": codegraph_health.details,
    }


def search_code_from_config(
    *,
    query: str,
    config_path: Path | str | None = None,
    repo_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    config, result_limit, preflight_error = _code_context_preflight(
        query=query, config_path=config_path, repo_id=repo_id, limit=limit
    )
    if preflight_error is not None:
        return preflight_error
    assert config is not None
    try:
        provider = TextFallbackCodeContextProvider(config)
        results = provider.search_code(query, repo_id=repo_id, limit=result_limit)
    except (FileNotFoundError, sqlite3.Error, OSError):
        return {
            "tool": "search_code",
            "query": query,
            "results": [],
            "warnings": ["Index is not ready; run index_project first."],
            "markdown": "## Code Search Results\n\nIndex is not ready; run `index_project` first.",
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
            details={"repo_id": repo_id, "limit": result_limit},
        )
    payload_results = [result.to_payload() for result in results]
    status = get_code_provider_status_from_config(config_path=config_path)
    warnings = list(status.get("warnings", []))
    return {
        "tool": "search_code",
        "query": query,
        "project_id": config.project.id,
        "configured_provider": status.get("configured_provider"),
        "active_provider": status.get("active_provider"),
        "results": payload_results,
        "warnings": warnings,
        "markdown": _render_code_markdown("Code Search Results", query, payload_results),
    }


def get_code_context_from_config(
    *,
    symbol_or_file: str,
    config_path: Path | str | None = None,
    repo_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    config, result_limit, preflight_error = _code_context_preflight(
        query=symbol_or_file, config_path=config_path, repo_id=repo_id, limit=limit
    )
    if preflight_error is not None:
        return preflight_error
    assert config is not None
    try:
        provider = TextFallbackCodeContextProvider(config)
        results = provider.get_code_context(symbol_or_file, repo_id=repo_id, limit=result_limit)
    except (FileNotFoundError, sqlite3.Error, OSError):
        return {
            "tool": "get_code_context",
            "query": symbol_or_file,
            "symbol_or_file": symbol_or_file,
            "results": [],
            "warnings": ["Index is not ready; run index_project first."],
            "markdown": "## Code Context\n\nIndex is not ready; run `index_project` first.",
            "error": {
                "code": "INDEX_NOT_READY",
                "message": "Index is not ready; run index_project first.",
                "details": {"state_dir": str(config.storage.state_dir)},
                "recoverable": True,
            },
        }
    except ValueError as exc:
        return _query_invalid(
            symbol_or_file,
            str(exc),
            details={"repo_id": repo_id, "limit": result_limit},
        )
    payload_results = [result.to_payload() for result in results]
    status = get_code_provider_status_from_config(config_path=config_path)
    return {
        "tool": "get_code_context",
        "query": symbol_or_file,
        "symbol_or_file": symbol_or_file,
        "project_id": config.project.id,
        "configured_provider": status.get("configured_provider"),
        "active_provider": status.get("active_provider"),
        "results": payload_results,
        "warnings": list(status.get("warnings", [])),
        "markdown": _render_code_markdown("Code Context", symbol_or_file, payload_results),
    }


def _code_context_preflight(
    *,
    query: str,
    config_path: Path | str | None,
    repo_id: str | None,
    limit: Any,
) -> tuple[ProjectKnowledgeConfig | None, int, dict[str, Any] | None]:
    validation = validate_project_config(config_path)
    if not validation["valid"]:
        return (
            None,
            0,
            {
                "query": query,
                "results": [],
                "warnings": validation["warnings"],
                "markdown": "## Code Search Results\n\nConfig is invalid; no code search was run.",
                "error": validation["errors"][0] if validation["errors"] else None,
                "errors": validation["errors"],
            },
        )
    config = load_project_config(config_path)
    work_repos = [repo for repo in config.repos if repo.role == "work"]
    if not work_repos:
        return (
            config,
            0,
            _query_invalid(
                query,
                "No work repos are configured for code context",
                details={"repo_id": repo_id},
            ),
        )
    if repo_id is not None and not any(repo.id == repo_id for repo in work_repos):
        return (
            config,
            0,
            _query_invalid(
                query,
                f"repo_id must name a configured work repo: {repo_id}",
                details={"repo_id": repo_id, "work_repos": [repo.id for repo in work_repos]},
            ),
        )
    codegraph_health = CodeGraphContextProvider(config).health()
    if not codegraph_health.codegraph_healthy and not config.code_context.fallback_on_unhealthy:
        return (
            config,
            0,
            _provider_unavailable(
                query,
                "CodeGraphContext is unhealthy and text fallback is disabled",
                details={
                    "configured_provider": config.code_context.provider,
                    "active_provider": "unavailable",
                    "codegraph_enabled": config.code_context.codegraph.enabled,
                },
                warnings=codegraph_health.warnings,
            ),
        )
    result_limit, limit_error = _normalize_limit(
        query, limit, default_limit=config.retrieval.default_limit
    )
    if limit_error is not None:
        return config, 0, limit_error
    return config, result_limit, None


def _render_code_markdown(title: str, query: str, results: list[dict[str, Any]]) -> str:
    lines = [f"## {title}", "", f"Query: `{query}`", ""]
    if not results:
        lines.append("No results.")
        return "\n".join(lines)
    for index, result in enumerate(results, start=1):
        location = f"{result['path']}:{result['start_line']}-{result['end_line']}"
        symbol = f" — `{result['symbol']}`" if result.get("symbol") else ""
        lines.extend(
            [
                f"{index}. **{result['kind']}** `{location}`{symbol}",
                f"   - repo: `{result['repo_id']}` provider: `{result['provider']}` score: `{result['score']:.3f}`",
                f"   - {result['snippet']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _provider_unavailable(
    query: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    error = {
        "code": "PROVIDER_UNAVAILABLE",
        "message": message,
        "details": details or {},
        "recoverable": True,
    }
    return {
        "query": query,
        "results": [],
        "warnings": warnings or [],
        "markdown": "## Code Search Results\n\nProvider unavailable; no code search was run.",
        "error": error,
        "errors": [error],
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


def _normalize_filter_mapping(
    query: str, filters: Any
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if filters is None:
        return {}, None
    if not isinstance(filters, Mapping):
        return {}, _query_invalid(
            query,
            "filters must be an object",
            details={"filters_type": type(filters).__name__},
        )
    return dict(filters), None


def _filters_with_required_doc_type(
    query: str,
    filters: dict[str, Any] | None,
    *,
    required_doc_type: str,
    default_status: str | None = None,
    default_statuses: tuple[str, ...] | None = None,
    include_superseded_statuses: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    scoped_filters, filter_error = _normalize_filter_mapping(query, filters)
    if filter_error is not None:
        return {}, filter_error
    requested_type = scoped_filters.pop("type", None)
    requested_doc_type = scoped_filters.get("doc_type")
    conflicting = next(
        (
            value
            for value in (requested_type, requested_doc_type)
            if value is not None and value != required_doc_type
        ),
        None,
    )
    if conflicting is not None:
        return {}, _query_invalid(
            query,
            f"doc_type filter cannot widen this tool beyond {required_doc_type}: {conflicting}",
            details={"requested_doc_type": conflicting, "required_doc_type": required_doc_type},
        )
    scoped_filters["doc_type"] = required_doc_type
    requested_status = scoped_filters.get("status")
    if requested_status is not None and not isinstance(requested_status, str):
        return {}, _query_invalid(
            query,
            "status filter must be a string value",
            details={"requested_status": requested_status},
        )
    if default_status is not None and default_statuses is not None:
        raise ValueError("default_status and default_statuses are mutually exclusive")
    if (
        default_status is not None
        and requested_status is not None
        and requested_status != default_status
    ):
        return {}, _query_invalid(
            query,
            f"status filter cannot widen this tool beyond {default_status}: {requested_status}",
            details={"requested_status": requested_status, "required_status": default_status},
        )
    if default_status is not None:
        scoped_filters["status"] = default_status
    if default_statuses is not None:
        allowed_statuses = set(default_statuses)
        if include_superseded_statuses and scoped_filters.get("include_superseded") is True:
            allowed_statuses.update({"superseded", "rejected"})
        if requested_status is not None and requested_status not in allowed_statuses:
            return {}, _query_invalid(
                query,
                "status filter cannot widen this tool beyond "
                f"{', '.join(sorted(allowed_statuses))}: {requested_status}",
                details={
                    "requested_status": requested_status,
                    "allowed_statuses": sorted(allowed_statuses),
                },
            )
        if requested_status is None:
            scoped_filters["_allowed_statuses"] = sorted(allowed_statuses)
    return scoped_filters, None


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
        "frontmatter": result.frontmatter,
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


def _render_current_doctrine_markdown(
    topic: str, doctrine: list[dict[str, Any]], decisions: list[dict[str, Any]]
) -> str:
    lines = ["## Current Doctrine", "", f"Topic: `{topic}`", ""]
    sections = [("Doctrine", doctrine), ("Accepted Decisions", decisions)]
    for heading, results in sections:
        lines.extend([f"### {heading}", ""])
        if not results:
            lines.extend(["No results.", ""])
            continue
        for index, result in enumerate(results, start=1):
            source = f"{result['path']}:{result['start_line']}-{result['end_line']}"
            lines.extend(
                [
                    f"{index}. **{result['title']}** — `{result['authority']}` / `{result['status']}`",
                    f"   Source: `{source}`",
                    f"   Excerpt: {result['excerpt']}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _normalize_metadata_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _normalize_allowed_statuses(value: Any) -> tuple[set[str], str | None]:
    if value is None:
        return set(), None
    if isinstance(value, str):
        return {value}, None
    if not isinstance(value, list | tuple | set):
        return set(), "allowed statuses must be a string or list of strings"
    statuses = {str(item) for item in value if item is not None}
    if not statuses:
        return set(), "allowed statuses must not be empty"
    return statuses, None


def _normalize_limit(
    query: str, limit: Any, *, default_limit: int
) -> tuple[int, dict[str, Any] | None]:
    if limit is None:
        result_limit = default_limit
    elif isinstance(limit, bool):
        return 0, _query_invalid(
            query,
            f"limit must be an integer between 1 and 1000: {limit}",
            details={"limit": limit},
        )
    elif isinstance(limit, int):
        result_limit = limit
    elif isinstance(limit, str) and limit.isdecimal():
        result_limit = int(limit)
    else:
        return 0, _query_invalid(
            query,
            f"limit must be an integer between 1 and 1000: {limit}",
            details={"limit": limit},
        )

    if result_limit < 1 or result_limit > 1000:
        return 0, _query_invalid(
            query,
            f"limit must be between 1 and 1000: {result_limit}",
            details={"limit": result_limit},
        )
    return result_limit, None


def _repo_staleness_status(
    repo, indexed: dict[str, Any] | None, *, state_dir: Path, max_file_bytes: int | None
) -> dict[str, Any]:
    warnings: list[str] = []
    branch = _git_output(repo.path, "rev-parse", "--abbrev-ref", "HEAD", warnings=warnings)
    head_commit = _git_output(repo.path, "rev-parse", "HEAD", warnings=warnings)
    remote_name = None
    remote_branch = None
    remote_tracking_branch = None
    remote_head_commit = None
    remote_check_status = "not_configured"
    ahead_count = None
    behind_count = None
    if branch:
        configured_remote = _git_output(
            repo.path, "config", "--get", f"branch.{branch}.remote", warn_on_failure=False
        )
        configured_merge = _git_output(
            repo.path, "config", "--get", f"branch.{branch}.merge", warn_on_failure=False
        )
        if configured_remote and configured_merge:
            remote_tracking_branch = _git_output(
                repo.path,
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{u}",
                warnings=warnings,
                warning_context="remote tracking branch",
            )
            remote_check_status = "ok" if remote_tracking_branch else "warning"
    if remote_tracking_branch:
        remote_name, _, remote_branch = remote_tracking_branch.partition("/")
        remote_head_commit = _git_output(
            repo.path, "rev-parse", "@{u}", warnings=warnings, warning_context="remote HEAD"
        )
        ahead_behind = _git_output(
            repo.path,
            "rev-list",
            "--left-right",
            "--count",
            "HEAD...@{u}",
            warnings=warnings,
            warning_context="ahead/behind counts",
        )
        if ahead_behind:
            parts = ahead_behind.split()
            if len(parts) == 2:
                ahead_count = int(parts[0])
                behind_count = int(parts[1])

    porcelain = _git_status_porcelain(repo.path, state_dir=state_dir, warnings=warnings)
    status_check_status = "ok" if porcelain is not None else "warning"
    status_lines = [line for line in (porcelain or "").splitlines() if line]
    untracked_count = (
        None if porcelain is None else sum(1 for line in status_lines if line.startswith("??"))
    )
    dirty = None if porcelain is None else any(not line.startswith("??") for line in status_lines)
    current_worktree_fingerprint = (
        None if porcelain is None else worktree_fingerprint(repo.path, porcelain)
    )

    indexed = indexed or {}
    last_indexed_commit = indexed.get("last_indexed_commit")
    last_indexed_at = indexed.get("last_indexed_at")
    last_indexed_worktree_fingerprint = indexed.get("last_indexed_worktree_fingerprint")
    expected_scope_fingerprint = index_scope_fingerprint(
        role=repo.role,
        include_globs=repo.include_globs,
        exclude_globs=repo.exclude_globs,
        max_file_bytes=max_file_bytes,
    )
    indexed_scope_fingerprint = indexed.get("index_scope_fingerprint")
    scope_mismatch = indexed_scope_fingerprint != expected_scope_fingerprint
    metadata_mismatch = _repo_metadata_mismatch(repo, indexed)
    includes_uncommitted_changes = bool(repo.includes_uncommitted_changes)
    expected_commit = repo.snapshot_commit if repo.source_mode == "snapshot" else head_commit
    snapshot_mismatch = (
        repo.source_mode == "snapshot"
        and repo.snapshot_commit is not None
        and head_commit is not None
        and head_commit != repo.snapshot_commit
    )
    if snapshot_mismatch:
        warnings.append(
            f"Repo {repo.id} snapshot_commit does not match checked-out HEAD; snapshot provenance is stale."
        )
    if metadata_mismatch:
        warnings.append(f"Repo {repo.id} indexed metadata no longer matches config.")
    if scope_mismatch:
        warnings.append(
            f"Repo {repo.id} index scope settings changed; reindex before serving code context."
        )
    if not includes_uncommitted_changes and ((dirty is True) or (untracked_count or 0) > 0):
        warnings.append(
            f"Repo {repo.id} has uncommitted changes but source metadata says they are excluded."
        )
    reindex_needed = (
        last_indexed_commit is None
        or metadata_mismatch
        or scope_mismatch
        or (expected_commit is not None and last_indexed_commit != expected_commit)
        or snapshot_mismatch
        or status_check_status != "ok"
        or (
            includes_uncommitted_changes
            and current_worktree_fingerprint != last_indexed_worktree_fingerprint
        )
    )
    if last_indexed_commit is None:
        warnings.append(f"Repo {repo.id} has not been indexed yet.")

    return {
        "repo_id": repo.id,
        "role": repo.role,
        "source_mode": repo.source_mode,
        "host_path": str(repo.host_path) if repo.host_path is not None else None,
        "container_path": str(repo.path),
        "path": str(repo.path),
        "branch": branch,
        "head_commit": head_commit,
        "remote_name": remote_name,
        "remote_branch": remote_branch,
        "remote_tracking_branch": remote_tracking_branch,
        "remote_head_commit": remote_head_commit,
        "remote_check_status": remote_check_status,
        "ahead_count": ahead_count,
        "behind_count": behind_count,
        "dirty": dirty,
        "untracked_count": untracked_count,
        "status_check_status": status_check_status,
        "last_indexed_commit": last_indexed_commit,
        "last_indexed_at": last_indexed_at,
        "last_indexed_worktree_fingerprint": last_indexed_worktree_fingerprint,
        "index_scope_fingerprint": indexed_scope_fingerprint,
        "expected_index_scope_fingerprint": expected_scope_fingerprint,
        "metadata_mismatch": metadata_mismatch,
        "scope_mismatch": scope_mismatch,
        "includes_uncommitted_changes": includes_uncommitted_changes,
        "snapshot_ref": repo.snapshot_ref,
        "snapshot_commit": repo.snapshot_commit,
        "reindex_needed": reindex_needed,
        "warnings": warnings,
    }


def _repo_metadata_mismatch(repo, indexed: dict[str, Any]) -> bool:
    if not indexed:
        return False
    try:
        path_mismatch = Path(str(indexed.get("path"))).resolve() != repo.path.resolve()
    except OSError:
        return True
    indexed_host_path = indexed.get("host_path")
    try:
        host_path_mismatch = (
            indexed_host_path is None
            if repo.host_path is not None
            else indexed_host_path is not None
        )
        if repo.host_path is not None:
            host_path_mismatch = (
                indexed_host_path is None
                or Path(str(indexed_host_path)).resolve() != repo.host_path.resolve()
            )
    except OSError:
        return True
    return (
        path_mismatch
        or host_path_mismatch
        or indexed.get("role") != repo.role
        or indexed.get("source_mode") != repo.source_mode
        or bool(indexed.get("includes_uncommitted_changes"))
        != bool(repo.includes_uncommitted_changes)
        or indexed.get("snapshot_ref") != repo.snapshot_ref
        or indexed.get("snapshot_commit") != repo.snapshot_commit
    )


def _load_indexed_repo_metadata(state_dir: Path) -> dict[str, dict[str, Any]]:
    db_path = state_dir / INDEX_DB_NAME
    if not db_path.exists():
        return {}
    try:
        ProjectIndex.open(state_dir)
    except (FileNotFoundError, sqlite3.Error):
        return {}
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, path, role, source_mode, host_path, includes_uncommitted_changes,
                   snapshot_ref, snapshot_commit, last_indexed_commit, last_indexed_at,
                   last_indexed_worktree_fingerprint, index_scope_fingerprint
            FROM repos
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return {
        row[0]: {
            "path": row[1],
            "role": row[2],
            "source_mode": row[3],
            "host_path": row[4],
            "includes_uncommitted_changes": bool(row[5]),
            "snapshot_ref": row[6],
            "snapshot_commit": row[7],
            "last_indexed_commit": row[8],
            "last_indexed_at": row[9],
            "last_indexed_worktree_fingerprint": row[10],
            "index_scope_fingerprint": row[11],
        }
        for row in rows
    }


def _persist_repo_statuses(state_dir: Path, repo_statuses: list[dict[str, Any]]) -> None:
    db_path = state_dir / INDEX_DB_NAME
    if not db_path.exists():
        return
    try:
        ProjectIndex.open(state_dir)
    except (FileNotFoundError, sqlite3.Error):
        return
    conn = sqlite3.connect(db_path)
    try:
        checked_at = _now()
        for repo in repo_statuses:
            conn.execute(
                """
                UPDATE repos
                SET current_branch = ?, head_commit = ?, remote_name = ?, remote_branch = ?,
                    remote_head_commit = ?, ahead_count = ?, behind_count = ?, dirty = ?,
                    untracked_count = ?, last_status_checked_at = ?
                WHERE id = ?
                """,
                (
                    repo["branch"],
                    repo["head_commit"],
                    repo["remote_name"],
                    repo["remote_branch"],
                    repo["remote_head_commit"],
                    repo["ahead_count"],
                    repo["behind_count"],
                    None if repo["dirty"] is None else int(repo["dirty"]),
                    repo["untracked_count"],
                    checked_at,
                    repo["repo_id"],
                ),
            )
        conn.commit()
    except sqlite3.Error:
        return
    finally:
        conn.close()


def _git_output(
    repo_path: Path,
    *args: str,
    warnings: list[str] | None = None,
    warn_on_failure: bool = True,
    warning_context: str = "Git command",
) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        if warnings is not None and warn_on_failure:
            warnings.append(f"{warning_context} failed for {repo_path}: {exc}")
        return None
    if result.returncode != 0:
        if warnings is not None and warn_on_failure:
            message = (result.stderr or result.stdout).strip() or "unknown Git error"
            warnings.append(f"{warning_context} failed for {repo_path}: {message}")
        return None
    return result.stdout.strip() or None


def _git_status_porcelain(
    repo_path: Path, *, state_dir: Path | None = None, warnings: list[str] | None = None
) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain=v1", "--untracked-files=all"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"worktree status failed for {repo_path}: {exc}")
        return None
    if result.returncode != 0:
        if warnings is not None:
            message = (result.stderr or result.stdout).strip() or "unknown Git error"
            warnings.append(f"worktree status failed for {repo_path}: {message}")
        return None
    return _filter_state_dir_status(repo_path, result.stdout, state_dir=state_dir)


def _filter_state_dir_status(repo_path: Path, porcelain: str, *, state_dir: Path | None) -> str:
    if state_dir is None:
        return porcelain
    try:
        state_relative = state_dir.resolve().relative_to(repo_path.resolve()).as_posix()
    except ValueError:
        return porcelain
    state_prefix = state_relative.rstrip("/") + "/"
    kept_lines: list[str] = []
    for line in porcelain.splitlines():
        relative = _status_relative_path(line)
        if relative == state_relative or (relative and relative.startswith(state_prefix)):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines) + ("\n" if kept_lines else "")


def _status_relative_path(line: str) -> str | None:
    if len(line) < 4:
        return None
    relative = line[3:]
    if " -> " in relative:
        relative = relative.rsplit(" -> ", 1)[1]
    return relative.strip('"') or None


def _render_staleness_markdown(project_id: str, repos: list[dict[str, Any]]) -> str:
    lines = ["## Project Staleness", "", f"Project: `{project_id}`", ""]
    for repo in repos:
        freshness = "reindex needed" if repo["reindex_needed"] else "current"
        dirty = "dirty" if repo["dirty"] or repo["untracked_count"] else "clean"
        lines.extend(
            [
                f"### {repo['repo_id']} ({repo['role']})",
                f"- Source mode: `{repo['source_mode']}`",
                f"- Path: `{repo['path']}`",
                f"- Branch: `{repo['branch']}` @ `{repo['head_commit']}`",
                f"- Workspace: `{dirty}`; untracked files: `{repo['untracked_count']}`",
                f"- Last indexed commit: `{repo['last_indexed_commit']}`",
                f"- Status: **{freshness}**",
                "",
            ]
        )
        for warning in repo["warnings"]:
            lines.append(f"  - Warning: {warning}")
        if repo["warnings"]:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
