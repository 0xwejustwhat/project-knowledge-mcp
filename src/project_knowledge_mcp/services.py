from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml

from project_knowledge_mcp.code_context import (
    CodeGraphProvider,
    CodeResult,
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
    index_document,
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
            repo_warnings = list(summary.warnings)
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


SUPPORTED_DRAFT_KINDS = {
    "open_question",
    "doctrine_delta",
    "adr_draft",
    "decision_proposal",
    "review_packet",
    "handover",
}

DEFAULT_PROPOSAL_DIRS = {
    "open_question": "docs/open-questions",
    "doctrine_delta": "docs/proposals/doctrine-deltas",
    "adr_draft": "docs/proposals/adr-drafts",
    "decision_proposal": "docs/proposals/decision-proposals",
    "review_packet": "docs/proposals/review-packets",
    "handover": "docs/handovers",
}

BUILTIN_BLOCKED_DIRECT_WRITE_GLOBS = [
    "docs/doctrine/**",
    "doctrine/**",
    "docs/decisions/**",
    "decisions/**",
    "docs/decisions/accepted/**",
    "decisions/accepted/**",
    "docs/PRD.md",
    "project-brief.md",
    "docs/terminology/**",
    "docs/acceptance-criteria/**",
]


def add_project_note_from_config(
    title: str,
    body: str,
    type: str = "note",
    tags: list[str] | None = None,
    source: str | None = None,
    target: str | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    validation, config = _load_validated_config_for_write(config_path)
    if config is None:
        return validation
    repo = _writable_capture_repo(config)
    if repo is None:
        return _write_error("WRITE_POLICY_DENIED", "Configured capture repo is not writable.")
    if not config.write_policy.allow_direct_capture:
        return _write_error("WRITE_POLICY_DENIED", "Direct capture writes are disabled.")

    relative_target = _target_file_or_generated(
        target=target,
        default_dir=config.write_policy.default_capture_dir,
        title=title,
    )
    blocked = _blocked_direct_write_response(config, relative_target, title=title)
    if blocked is not None:
        return blocked
    normalized, error = _safe_repo_write_path(
        repo.path, relative_target, state_dir=config.storage.state_dir
    )
    if error is not None:
        return error

    now = _now()
    note_type = type or "note"
    status = "open" if note_type == "open_question" else "captured"
    authority = "working" if note_type == "open_question" else "capture"
    frontmatter = {
        "title": title,
        "type": note_type,
        "status": status,
        "authority": authority,
        "tags": tags or [],
        "source": source,
        "created_at": now,
        "updated_at": now,
    }
    content = _render_markdown(frontmatter=frontmatter, body=body)
    if config.write_policy.capture_git_mode == "local_only":
        return _write_local_capture(config, repo, normalized, content, authority=authority)
    return _write_and_push_capture(config, repo, normalized, content, title=title, authority=authority)


def create_draft_artifact_from_config(
    kind: str,
    title: str,
    body: str,
    source: str | None = None,
    tags: list[str] | None = None,
    target: str | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    validation, config = _load_validated_config_for_write(config_path)
    if config is None:
        return validation
    repo = _writable_capture_repo(config)
    if repo is None:
        return _write_error("WRITE_POLICY_DENIED", "Configured capture repo is not writable.")
    if kind not in SUPPORTED_DRAFT_KINDS:
        return _write_error(
            "UNSUPPORTED_DRAFT_KIND",
            f"Unsupported draft kind: {kind}",
            details={"supported_kinds": sorted(SUPPORTED_DRAFT_KINDS)},
        )

    default_dir = config.write_policy.proposal_dirs.get(kind, DEFAULT_PROPOSAL_DIRS[kind])
    relative_target = _target_file_or_generated(target=target, default_dir=default_dir, title=title)
    allowed_dirs = set(DEFAULT_PROPOSAL_DIRS.values()) | set(
        config.write_policy.proposal_dirs.values()
    )
    if not _path_is_inside_any(relative_target, sorted(allowed_dirs)):
        return _write_error(
            "WRITE_POLICY_DENIED",
            "Draft artifacts must be written inside configured proposal/draft directories.",
            details={"target": relative_target, "allowed_dirs": sorted(allowed_dirs)},
            extra={
                "status": "blocked",
                "suggested_actions": ["create_draft_artifact", "propose_authority_change"],
                "authority_boundary": "review_required_before_promotion",
            },
        )
    blocked = _blocked_direct_write_response(config, relative_target, title=title)
    if blocked is not None:
        return blocked
    normalized, error = _safe_repo_write_path(
        repo.path, relative_target, state_dir=config.storage.state_dir
    )
    if error is not None:
        return error

    now = _now()
    frontmatter = {
        "title": title,
        "type": kind,
        "status": "open" if kind == "open_question" else "draft",
        "authority": "proposal",
        "tags": tags or [],
        "source": source,
        "created_at": now,
        "updated_at": now,
    }
    write_error = _write_markdown_file(repo.path / normalized, frontmatter=frontmatter, body=body)
    if write_error is not None:
        return write_error
    indexed, index_warnings = _index_written_document(config, repo, normalized)
    return {
        "status": "written",
        "repo_id": repo.id,
        "path": normalized,
        "authority": "proposal",
        "indexed": indexed,
        "index_scope": "single_document" if indexed else None,
        "full_reindex_required": not indexed,
        "suggested_actions": ["propose_authority_change"],
        "authority_boundary": "review_required_before_promotion",
        "warnings": index_warnings,
    }


def propose_authority_change_from_config(
    title: str,
    rationale: str,
    changes: list[dict[str, Any]],
    source: str | None = None,
    tags: list[str] | None = None,
    branch_name: str | None = None,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    validation, config = _load_validated_config_for_write(config_path)
    if config is None:
        return validation
    repo = _writable_capture_repo(config)
    if repo is None:
        return _write_error("WRITE_POLICY_DENIED", "Configured capture repo is not writable.")
    if not isinstance(changes, list) or not changes:
        return _write_error("INVALID_CHANGES", "changes must be a non-empty array")

    porcelain = _git_status_porcelain(repo.path, state_dir=config.storage.state_dir)
    if porcelain is None:
        return _write_error("GIT_STATUS_FAILED", "Could not determine Git workspace status.")
    if porcelain.strip():
        return {
            "status": "blocked",
            "repo_id": repo.id,
            "reason": "Workspace has uncommitted changes; authority proposals require a clean workspace.",
            "authority_boundary": "review_required_before_promotion",
            "next_action": "commit/stash current changes or use a clean worktree, then retry",
            "warnings": [],
        }

    normalized_changes: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for index, change in enumerate(changes):
        if not isinstance(change, Mapping):
            return _write_error(
                "INVALID_CHANGES", "each change must be an object", details={"index": index}
            )
        operation = change.get("operation")
        if operation not in {"add_file", "replace_file"}:
            return _write_error(
                "INVALID_CHANGES",
                "change operation must be add_file or replace_file",
                details={"index": index, "operation": operation},
            )
        content = change.get("content")
        if not isinstance(content, str):
            return _write_error(
                "INVALID_CHANGES",
                "change content must be caller-supplied text",
                details={"index": index},
            )
        normalized, error = _safe_repo_write_path(
            repo.path,
            str(change.get("path") or ""),
            state_dir=config.storage.state_dir,
            allow_existing=True,
        )
        if error is not None:
            return error
        target_path = repo.path / normalized
        if operation == "add_file" and target_path.exists():
            return _write_error(
                "INVALID_CHANGES",
                "add_file target already exists",
                details={"path": normalized},
            )
        if operation == "replace_file" and (not target_path.exists() or not target_path.is_file()):
            return _write_error(
                "INVALID_CHANGES",
                "replace_file target must be an existing file",
                details={"path": normalized},
            )
        if normalized in seen_paths:
            return _write_error(
                "INVALID_CHANGES", "duplicate changed path", details={"path": normalized}
            )
        seen_paths.add(normalized)
        normalized_changes.append({"operation": operation, "path": normalized, "content": content})

    branch = _authority_branch_name(title, branch_name)
    if branch is None:
        return _write_error("INVALID_BRANCH", "branch_name contains unsafe characters")
    if _git_run(repo.path, "rev-parse", "--verify", branch, check=False).returncode == 0:
        return _write_error(
            "BRANCH_EXISTS", "Authority proposal branch already exists", details={"branch": branch}
        )
    original_branch = _git_output(repo.path, "branch", "--show-current")
    original_head = _git_output(repo.path, "rev-parse", "HEAD")
    created = _git_run(repo.path, "switch", "-c", branch, check=False)
    if created.returncode != 0:
        return _write_error(
            "GIT_BRANCH_FAILED",
            "Could not create authority proposal branch",
            details={"branch": branch, "stderr": created.stderr.strip()},
        )

    changed_paths = [change["path"] for change in normalized_changes]
    created_paths = [
        change["path"] for change in normalized_changes if change["operation"] == "add_file"
    ]
    try:
        for change in normalized_changes:
            path = repo.path / change["path"]
            write_error = _write_text_file_no_symlink(path, change["content"])
            if write_error is not None:
                _cleanup_failed_authority_branch(
                    repo.path, branch, original_branch, original_head, created_paths=created_paths
                )
                return write_error
        add_result = _git_run(repo.path, "add", "--", *changed_paths, check=False)
        if add_result.returncode != 0:
            _cleanup_failed_authority_branch(
                repo.path, branch, original_branch, original_head, created_paths=created_paths
            )
            return _write_error("GIT_ADD_FAILED", "Could not stage authority proposal changes")
        commit_message = _authority_commit_message(title, rationale, source, tags, changed_paths)
        commit_result = _git_run(
            repo.path, "commit", "--no-verify", "-m", title, "-m", commit_message, check=False
        )
        if commit_result.returncode != 0:
            _cleanup_failed_authority_branch(
                repo.path, branch, original_branch, original_head, created_paths=created_paths
            )
            return _write_error(
                "GIT_COMMIT_FAILED",
                "Could not commit authority proposal changes",
                details={"stderr": commit_result.stderr.strip()},
            )
    except OSError as exc:
        _cleanup_failed_authority_branch(
            repo.path, branch, original_branch, original_head, created_paths=created_paths
        )
        return _write_error("WRITE_FAILED", str(exc))

    commit = _git_output(repo.path, "rev-parse", "HEAD")
    pr_url: str | None = None
    warnings: list[str] = []
    gh_auth = (
        subprocess.run(
            ["gh", "auth", "status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "GH_PROMPT_DISABLED": "1"},
        )
        if _command_exists("gh")
        else None
    )
    if gh_auth is not None and gh_auth.returncode == 0:
        pushed = _git_run(
            repo.path, "push", "--no-verify", "--set-upstream", "origin", branch, check=False
        )
        if pushed.returncode == 0:
            pr_body = _authority_pr_body(rationale, source, tags, changed_paths)
            pr = subprocess.run(
                ["gh", "pr", "create", "--title", title, "--body", pr_body, "--head", branch],
                cwd=repo.path,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "GH_PROMPT_DISABLED": "1"},
            )
            if pr.returncode == 0:
                pr_url = pr.stdout.strip().splitlines()[-1] if pr.stdout.strip() else None
            else:
                warnings.append("GitHub PR creation failed; branch and commit were prepared.")
        else:
            warnings.append("Git push failed; branch and commit were prepared locally.")
    else:
        warnings.append("GitHub authentication unavailable; no PR was opened.")

    status = "pr_opened" if pr_url else "branch_prepared_pr_not_opened"
    result = {
        "status": status,
        "repo_id": repo.id,
        "branch": branch,
        "commit": commit,
        "changed_paths": changed_paths,
        "authority_boundary": "review_required_before_promotion",
        "warnings": warnings,
    }
    if pr_url:
        result["pr_url"] = pr_url
    else:
        result["next_action"] = "push branch and open PR manually"
    return result


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
    codegraph_health = CodeGraphProvider(config).health()
    active_provider = codegraph_health.active_provider
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
    graph_provider = CodeGraphProvider(config)
    graph_health = graph_provider.health()
    warnings = list(graph_health.warnings)
    active_provider = graph_health.active_provider
    graph_results: list[CodeResult] = []
    if graph_health.codegraph_healthy:
        try:
            graph_results = graph_provider.search_code(query, repo_id=repo_id, limit=result_limit)
        except RuntimeError:
            if not config.code_context.fallback_on_unhealthy:
                return _provider_unavailable(
                    query,
                    "CodeGraph search failed and text fallback is disabled",
                    details={
                        "configured_provider": config.code_context.provider,
                        "active_provider": "unavailable",
                        "codegraph_enabled": config.code_context.codegraph.enabled,
                    },
                    warnings=[*warnings, "CodeGraph search failed."],
                )
            warnings.append("CodeGraph search failed; using text fallback.")
            active_provider = config.code_context.fallback_provider
        else:
            try:
                fts_results = _search_code_fts_results(
                    config=config,
                    query=query,
                    repo_id=repo_id,
                    limit=result_limit,
                    include_ops=repo_id is None,
                )
            except (FileNotFoundError, sqlite3.Error, OSError):
                warnings.append("SQLite FTS5 search is not ready; run index_project first.")
                merged_results = graph_results
                active_provider = "codegraph"
            except ValueError as exc:
                return _query_invalid(
                    query,
                    str(exc),
                    details={"repo_id": repo_id, "limit": result_limit},
                )
            else:
                merged_results = _merge_code_results([*graph_results, *fts_results], result_limit)
                active_provider = "codegraph+fts5"
            payload_results = [result.to_payload() for result in merged_results]
            return {
                "tool": "search_code",
                "query": query,
                "project_id": config.project.id,
                "configured_provider": graph_health.configured_provider,
                "active_provider": active_provider,
                "results": payload_results,
                "warnings": warnings,
                "markdown": _render_code_markdown("Code Search Results", query, payload_results),
            }
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
    return {
        "tool": "search_code",
        "query": query,
        "project_id": config.project.id,
        "configured_provider": graph_health.configured_provider,
        "active_provider": active_provider,
        "results": payload_results,
        "warnings": warnings,
        "markdown": _render_code_markdown("Code Search Results", query, payload_results),
    }


def _search_code_fts_results(
    *,
    config: ProjectKnowledgeConfig,
    query: str,
    repo_id: str | None,
    limit: int,
    include_ops: bool,
) -> list[CodeResult]:
    provider = TextFallbackCodeContextProvider(config)
    per_source_limit = max(limit * 3, 10)
    work_repos = [repo for repo in config.repos if repo.role == "work"]
    if repo_id is not None:
        work_repos = [repo for repo in work_repos if repo.id == repo_id]
    results = provider.search_repos(
        query,
        repos=work_repos,
        limit=per_source_limit,
        provider="fts5",
        include_all_doc_types=True,
    )
    if include_ops:
        results.extend(
            provider.search_repos(
                query,
                repos=[config.ops_repo],
                limit=per_source_limit,
                provider="ops",
                include_all_doc_types=True,
            )
        )
    return _merge_code_results(results, per_source_limit)


def _merge_code_results(results: list[CodeResult], limit: int) -> list[CodeResult]:
    deduped: dict[tuple[str, str, int | None, int | None, str | None, str], CodeResult] = {}
    for result in results:
        key = (
            result.repo_id,
            result.path,
            result.start_line,
            result.end_line,
            result.symbol,
            result.provider,
        )
        existing = deduped.get(key)
        if existing is None or result.score > existing.score:
            deduped[key] = result
    merged = list(deduped.values())
    merged.sort(
        key=lambda result: (
            _code_result_kind_rank(result.kind),
            -result.score,
            result.path,
            result.symbol or "",
            result.provider,
        )
    )
    return merged[:limit]


def _code_result_kind_rank(kind: str) -> int:
    return {
        "class": 0,
        "function": 0,
        "method": 0,
        "code": 1,
        "schema": 2,
        "test": 3,
        "file": 4,
    }.get(kind, 5)


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
    graph_provider = CodeGraphProvider(config)
    graph_health = graph_provider.health()
    warnings = list(graph_health.warnings)
    active_provider = graph_health.active_provider
    if graph_health.codegraph_healthy:
        try:
            results = graph_provider.get_code_context(
                symbol_or_file, repo_id=repo_id, limit=result_limit
            )
        except RuntimeError:
            if not config.code_context.fallback_on_unhealthy:
                return _provider_unavailable(
                    symbol_or_file,
                    "CodeGraph context lookup failed and text fallback is disabled",
                    details={
                        "configured_provider": config.code_context.provider,
                        "active_provider": "unavailable",
                        "codegraph_enabled": config.code_context.codegraph.enabled,
                    },
                    warnings=[*warnings, "CodeGraph context lookup failed."],
                )
            warnings.append("CodeGraph context lookup failed; using text fallback.")
            active_provider = config.code_context.fallback_provider
        else:
            payload_results = [result.to_payload() for result in results]
            return {
                "tool": "get_code_context",
                "query": symbol_or_file,
                "symbol_or_file": symbol_or_file,
                "project_id": config.project.id,
                "configured_provider": graph_health.configured_provider,
                "active_provider": "codegraph",
                "results": payload_results,
                "warnings": warnings,
                "markdown": _render_code_markdown("Code Context", symbol_or_file, payload_results),
            }
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
    return {
        "tool": "get_code_context",
        "query": symbol_or_file,
        "symbol_or_file": symbol_or_file,
        "project_id": config.project.id,
        "configured_provider": graph_health.configured_provider,
        "active_provider": active_provider,
        "results": payload_results,
        "warnings": warnings,
        "markdown": _render_code_markdown("Code Context", symbol_or_file, payload_results),
    }


def retrieve_ops_code_evidence_from_config(
    *,
    topic: str,
    config_path: Path | str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    result_limit = _brief_result_limit(config_path=config_path, limit=limit)
    doctrine_packet = get_current_doctrine_from_config(
        topic=topic, config_path=config_path, limit=result_limit
    )
    open_questions_packet = search_open_questions_from_config(
        query=topic, config_path=config_path, limit=result_limit
    )
    code_packet = search_code_from_config(query=topic, config_path=config_path, limit=result_limit)
    staleness_packet = check_project_staleness_from_config(config_path=config_path)

    doctrine_results = list(doctrine_packet.get("doctrine", []))
    decision_results = list(doctrine_packet.get("decisions", []))
    open_question_results = (
        [] if open_questions_packet.get("error") else list(open_questions_packet.get("results", []))
    )
    code_results = [] if code_packet.get("error") else list(code_packet.get("results", []))
    sections = {
        "doctrine": doctrine_results,
        "decisions": decision_results,
        "open_questions": open_question_results,
        "code": code_results,
    }
    gaps = _packet_gaps(code_results, code_packet=code_packet)
    errors = _packet_errors(
        ("doctrine", doctrine_packet),
        ("open_questions", open_questions_packet),
        ("code", code_packet),
        ("staleness", staleness_packet),
    )
    warnings = _merge_unique_warnings(
        doctrine_packet.get("warnings", []),
        open_questions_packet.get("warnings", []),
        code_packet.get("warnings", []),
        staleness_packet.get("warnings", []),
        _packet_staleness_warnings(staleness_packet.get("repos", [])),
        _packet_error_warnings(
            doctrine_packet, open_questions_packet, code_packet, staleness_packet
        ),
    )
    project_id = (
        doctrine_packet.get("project_id")
        or open_questions_packet.get("project_id")
        or code_packet.get("project_id")
        or staleness_packet.get("project_id")
    )
    packet = {
        "tool": "retrieve_ops_code_evidence",
        "topic": topic,
        "project_id": project_id,
        "generated_at": _now(),
        "sections": sections,
        "staleness": staleness_packet.get("repos", []),
        "warnings": warnings,
        "gaps": gaps,
        "errors": errors,
    }
    packet["markdown"] = _render_ops_code_evidence_markdown(packet)
    return packet


def generate_session_brief_from_config(
    *,
    task: str,
    config_path: Path | str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    topic = _topic_from_task(task)
    result_limit = _brief_result_limit(config_path=config_path, limit=limit)
    evidence = retrieve_ops_code_evidence_from_config(
        topic=topic, config_path=config_path, limit=result_limit
    )
    recent_changes = _recent_indexed_changes_from_config(
        config_path=config_path, since=since, limit=result_limit
    )
    repo_staleness = list(evidence.get("staleness", []))
    sections = dict(evidence.get("sections", {}))
    sections["recent_changes"] = recent_changes.get("results", [])
    warnings = _merge_unique_warnings(
        evidence.get("warnings", []), recent_changes.get("warnings", [])
    )
    errors = [*list(evidence.get("errors", [])), *list(recent_changes.get("errors", []))]
    brief = {
        "tool": "generate_session_brief",
        "task": task,
        "evidence_topic": topic,
        "since": since,
        "project_id": evidence.get("project_id"),
        "generated_at": _now(),
        "repo_staleness": repo_staleness,
        "sections": sections,
        "warnings": warnings,
        "gaps": list(evidence.get("gaps", [])),
        "errors": errors,
    }
    brief["markdown"] = _render_session_brief_markdown(brief)
    return brief


def _brief_result_limit(*, config_path: Path | str | None, limit: Any) -> Any:
    if limit is not None:
        return limit
    validation = validate_project_config(config_path)
    if not validation["valid"]:
        return limit
    config = load_project_config(config_path)
    return config.retrieval.brief_max_results_per_section


def _packet_gaps(
    code_results: list[dict[str, Any]], *, code_packet: dict[str, Any]
) -> list[dict[str, Any]]:
    if code_results:
        return []
    if code_packet.get("error"):
        return [
            {
                "code": "CODE_EVIDENCE_UNAVAILABLE",
                "message": "Code evidence search could not run for this topic.",
                "recoverable": True,
            }
        ]
    return [
        {
            "code": "CODE_EVIDENCE_MISSING",
            "message": "No code evidence was found for this topic.",
            "recoverable": True,
        }
    ]


def _topic_from_task(task: str) -> str:
    words = task.strip().split()
    if len(words) > 1 and words[0].lower().rstrip(":") in {
        "add",
        "build",
        "create",
        "implement",
        "plan",
        "ship",
    }:
        return " ".join(words[1:])
    return task


def _packet_errors(*named_packets: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for source, packet in named_packets:
        packet_errors = list(packet.get("errors", []))
        if packet.get("error") and packet.get("error") not in packet_errors:
            packet_errors.append(packet["error"])
        for error in packet_errors:
            if isinstance(error, dict):
                normalized = {"source": source, **error}
            else:
                normalized = {"source": source, "message": str(error)}
            key = (
                source,
                str(normalized.get("code", "")),
                str(normalized.get("message", "")),
            )
            if key not in seen:
                seen.add(key)
                errors.append(normalized)
    return errors


def _recent_indexed_changes_from_config(
    *, config_path: Path | str | None, since: str | None, limit: Any
) -> dict[str, Any]:
    if not since:
        return {"results": [], "warnings": [], "errors": []}
    validation = validate_project_config(config_path)
    if not validation["valid"]:
        return {
            "results": [],
            "warnings": validation["warnings"],
            "errors": _packet_errors(("recent_changes", {"errors": validation["errors"]})),
        }
    config = load_project_config(config_path)
    result_limit, limit_error = _normalize_limit(
        since, limit, default_limit=config.retrieval.brief_max_results_per_section
    )
    if limit_error is not None:
        return {
            "results": [],
            "warnings": limit_error.get("warnings", []),
            "errors": _packet_errors(("recent_changes", limit_error)),
        }

    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    for repo in config.repos:
        output = _git_output(
            repo.path,
            "log",
            f"--since={since}",
            "--name-status",
            "--pretty=format:__PKMCP_COMMIT__%x09%H%x09%cI%x09%s",
            warnings=warnings,
            warning_context="recent change lookup",
        )
        current_commit: dict[str, str] | None = None
        for line in (output or "").splitlines():
            if not line:
                continue
            if line.startswith("__PKMCP_COMMIT__\t"):
                parts = line.split("\t", 3)
                if len(parts) == 4:
                    current_commit = {
                        "commit": parts[1],
                        "committed_at": parts[2],
                        "subject": parts[3],
                    }
                continue
            if current_commit is None or "\t" not in line:
                continue
            status, path = _parse_git_name_status(line)
            if not _repo_path_in_scope(
                path, include_globs=repo.include_globs, exclude_globs=repo.exclude_globs
            ):
                continue
            results.append(
                {
                    "repo_id": repo.id,
                    "role": repo.role,
                    "path": path,
                    "status": status,
                    "commit": current_commit["commit"],
                    "committed_at": current_commit["committed_at"],
                    "title": current_commit["subject"],
                    "excerpt": f"{status} {path}",
                    "source_mode": repo.source_mode,
                }
            )
            if len(results) >= result_limit:
                return {"results": results, "warnings": warnings, "errors": []}
    return {"results": results, "warnings": warnings, "errors": []}


def _parse_git_name_status(line: str) -> tuple[str, str]:
    parts = line.split("\t")
    if len(parts) >= 3 and parts[0].startswith(("R", "C")):
        return parts[0], parts[2]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "changed", line


def _repo_path_in_scope(path: str, *, include_globs: list[str], exclude_globs: list[str]) -> bool:
    included = not include_globs or _packet_path_matches_any(path, include_globs)
    excluded = _packet_path_matches_any(path, exclude_globs)
    return included and not excluded


def _packet_path_matches_any(relative_path: str, patterns: list[str]) -> bool:
    path = Path(relative_path)
    for pattern in patterns:
        if fnmatch(relative_path, pattern) or path.match(pattern):
            return True
        if "/**/" in pattern:
            direct_child_pattern = pattern.replace("/**/", "/")
            if fnmatch(relative_path, direct_child_pattern) or path.match(direct_child_pattern):
                return True
    return False


def _packet_error_warnings(*packets: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for packet in packets:
        error = packet.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if message:
                warnings.append(str(message))
    return warnings


def _packet_staleness_warnings(repos: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for repo in repos:
        if repo.get("reindex_needed"):
            warnings.append(
                f"Repo {repo.get('repo_id')} may need reindex before relying on evidence."
            )
    return warnings


def _merge_unique_warnings(*warning_groups: Any) -> list[str]:
    merged: list[str] = []
    for group in warning_groups:
        if not group:
            continue
        for warning in group:
            text = str(warning)
            if text and text not in merged:
                merged.append(text)
    return merged


def _render_ops_code_evidence_markdown(packet: dict[str, Any]) -> str:
    lines = ["## Ops + Code Evidence", "", f"Topic: `{packet['topic']}`", ""]
    _append_packet_section(lines, "Doctrine", packet["sections"].get("doctrine", []))
    _append_packet_section(lines, "Accepted Decisions", packet["sections"].get("decisions", []))
    _append_packet_section(lines, "Open Questions", packet["sections"].get("open_questions", []))
    _append_packet_section(lines, "Code Evidence", packet["sections"].get("code", []))
    _append_packet_gaps(lines, packet.get("gaps", []))
    _append_packet_warnings(lines, packet.get("warnings", []))
    lines.extend(
        [
            "### Boundary",
            "",
            "Connected assistant should synthesize from these cited evidence items; the MCP server does not generate final claims.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_session_brief_markdown(brief: dict[str, Any]) -> str:
    lines = ["## Session Brief", "", f"Task: `{brief['task']}`"]
    if brief.get("since"):
        lines.append(f"Since: `{brief['since']}`")
    lines.append("")
    if brief.get("repo_staleness"):
        lines.extend(["### Repo Freshness", ""])
        for repo in brief["repo_staleness"]:
            freshness = "reindex needed" if repo.get("reindex_needed") else "current"
            lines.append(
                f"- `{repo.get('repo_id')}`: **{freshness}** @ `{repo.get('head_commit')}`"
            )
        lines.append("")
    _append_packet_section(lines, "Doctrine", brief["sections"].get("doctrine", []))
    _append_packet_section(lines, "Accepted Decisions", brief["sections"].get("decisions", []))
    _append_packet_section(lines, "Open Questions", brief["sections"].get("open_questions", []))
    _append_packet_section(lines, "Code Evidence", brief["sections"].get("code", []))
    if brief.get("since"):
        _append_packet_section(
            lines, "Recent Indexed Changes", brief["sections"].get("recent_changes", [])
        )
    _append_packet_gaps(lines, brief.get("gaps", []))
    _append_packet_warnings(lines, brief.get("warnings", []))
    lines.extend(
        [
            "### Boundary",
            "",
            "Connected assistant should synthesize the user-facing answer from this packet and cite sources where making project claims.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _append_packet_section(lines: list[str], heading: str, results: list[dict[str, Any]]) -> None:
    lines.extend([f"### {heading}", ""])
    if not results:
        lines.extend(["No evidence found.", ""])
        return
    for index, result in enumerate(results, start=1):
        source = _packet_source_label(result)
        title = result.get("title") or result.get("symbol") or result.get("path") or "Untitled"
        label_parts = [
            str(part) for part in (result.get("authority"), result.get("status")) if part
        ]
        label = f" — `{' / '.join(label_parts)}`" if label_parts else ""
        excerpt = result.get("excerpt") or result.get("snippet") or ""
        lines.extend(
            [
                f"{index}. **{title}**{label}",
                f"   Source: `{source}`",
            ]
        )
        if result.get("provider"):
            lines.append(f"   provider: `{result['provider']}`")
        lines.extend(
            [
                f"   Excerpt: {excerpt}",
                "",
            ]
        )


def _append_packet_gaps(lines: list[str], gaps: list[dict[str, Any]]) -> None:
    if not gaps:
        return
    lines.extend(["### Gaps", ""])
    for gap in gaps:
        lines.append(f"- `{gap.get('code')}`: {gap.get('message')}")
    lines.append("")


def _append_packet_warnings(lines: list[str], warnings: list[str]) -> None:
    if not warnings:
        return
    lines.extend(["### Warnings", ""])
    for warning in warnings:
        lines.append(f"- {warning}")
    lines.append("")


def _packet_source_label(result: dict[str, Any]) -> str:
    path = result.get("path", "unknown")
    if result.get("commit"):
        return f"{result.get('commit')}:{path}"
    start = result.get("start_line")
    end = result.get("end_line")
    if start is not None and end is not None:
        return f"{path}:{start}-{end}"
    return str(path)


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
    codegraph_health = CodeGraphProvider(config).health()
    if not codegraph_health.codegraph_healthy and not config.code_context.fallback_on_unhealthy:
        return (
            config,
            0,
            _provider_unavailable(
                query,
                "CodeGraph is unavailable and text fallback is disabled",
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


def _load_validated_config_for_write(
    config_path: Path | str | None,
) -> tuple[dict[str, Any], ProjectKnowledgeConfig | None]:
    validation = validate_project_config(config_path)
    if not validation["valid"]:
        return (
            {
                "status": "error",
                "error": validation["errors"][0] if validation["errors"] else None,
                "errors": validation["errors"],
                "warnings": validation["warnings"],
            },
            None,
        )
    config = load_project_config(config_path)
    assert config.storage.state_dir is not None
    return validation, config


def _writable_capture_repo(config: ProjectKnowledgeConfig):
    repo = config.repo_by_id(config.write_policy.default_capture_repo)
    if repo is None or repo.role != "ops" or not repo.writable:
        return None
    return repo


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:80].strip("-") or "untitled"


def _target_file_or_generated(*, target: str | None, default_dir: str, title: str) -> str:
    if target is None or not target.strip():
        directory = default_dir.strip("/")
        return f"{directory}/{datetime.now(UTC).date().isoformat()}-{_slugify(title)}.md"
    cleaned = target.strip().replace("\\", "/").strip("/")
    if Path(cleaned).suffix.lower() in {".md", ".mdx", ".txt"}:
        return cleaned
    return f"{cleaned}/{datetime.now(UTC).date().isoformat()}-{_slugify(title)}.md"


def _all_blocked_globs(config: ProjectKnowledgeConfig) -> list[str]:
    return [*BUILTIN_BLOCKED_DIRECT_WRITE_GLOBS, *config.write_policy.blocked_direct_write_globs]


def _blocked_direct_write_response(
    config: ProjectKnowledgeConfig, target: str, *, title: str
) -> dict[str, Any] | None:
    normalized = target.strip("/")
    if not _matches_any(normalized, _all_blocked_globs(config)):
        return None
    return {
        "status": "blocked",
        "reason": "Target path is canonical/high-authority. Direct MCP writes are not allowed.",
        "target": normalized,
        "suggested_actions": ["create_draft_artifact", "propose_authority_change"],
        "suggested_draft_target": (
            f"{DEFAULT_PROPOSAL_DIRS['doctrine_delta']}/"
            f"{datetime.now(UTC).date().isoformat()}-{_slugify(title)}.md"
        ),
        "authority_boundary": "review_required_before_promotion",
        "warnings": [],
    }


def _safe_repo_write_path(
    repo_path: Path,
    relative_path: str,
    *,
    state_dir: Path | None,
    allow_existing: bool = False,
) -> tuple[str, dict[str, Any] | None]:
    try:
        relative = Path(relative_path.replace("\\", "/"))
    except Exception:
        return "", _write_error("INVALID_TARGET", "Target path is invalid.")
    if (
        not str(relative_path).strip()
        or relative.is_absolute()
        or ".." in relative.parts
        or ".git" in relative.parts
    ):
        return "", _write_error(
            "INVALID_TARGET", "Target path must stay under repo root and avoid .git."
        )
    relative_posix = relative.as_posix().strip("/")
    if not relative_posix or relative_posix.endswith("/"):
        return "", _write_error("INVALID_TARGET", "Target path must name a file.")
    if any("secret" in part.casefold() or "token" in part.casefold() for part in relative.parts):
        return "", _write_error(
            "INVALID_TARGET", "Target path may not contain secret/token components."
        )

    repo_root = repo_path.resolve()
    target_path = repo_root / relative
    if target_path.is_symlink():
        return "", _write_error("INVALID_TARGET", "Target path is a symlink.")
    if target_path.exists() and target_path.is_dir():
        return "", _write_error("INVALID_TARGET", "Target path is a directory.")
    if target_path.exists() and target_path.is_file() and target_path.stat().st_nlink > 1:
        return "", _write_error("INVALID_TARGET", "Target path has multiple hardlinks.")
    current = repo_root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            return "", _write_error("INVALID_TARGET", "Target parent is a symlink.")
    parent = target_path.parent
    existing_parent = parent
    while not existing_parent.exists() and existing_parent != repo_root:
        existing_parent = existing_parent.parent
    if existing_parent.exists() and existing_parent.is_symlink():
        return "", _write_error("INVALID_TARGET", "Target parent is a symlink.")
    try:
        resolved_existing_parent = existing_parent.resolve()
        resolved_existing_parent.relative_to(repo_root)
    except (OSError, ValueError):
        return "", _write_error("INVALID_TARGET", "Target path escapes repo root.")
    if state_dir is not None:
        try:
            target_path.resolve(strict=False).relative_to(state_dir.resolve())
        except ValueError:
            pass
        else:
            return "", _write_error("INVALID_TARGET", "Target path may not be in state dir.")
    if not allow_existing and target_path.exists():
        return "", _write_error(
            "TARGET_EXISTS", "Target file already exists.", details={"path": relative_posix}
        )
    return relative_posix, None


def _path_is_inside_any(path: str, directories: list[str]) -> bool:
    clean = path.strip("/")
    for directory in directories:
        prefix = directory.strip("/")
        if clean == prefix or clean.startswith(prefix + "/"):
            return True
    return False


def _render_markdown(*, frontmatter: dict[str, Any], body: str) -> str:
    frontmatter_text = yaml.safe_dump(
        {key: value for key, value in frontmatter.items() if value is not None},
        sort_keys=False,
        allow_unicode=True,
    )
    return f"---\n{frontmatter_text}---\n\n{body.rstrip()}\n"


def _write_markdown_file(
    path: Path, *, frontmatter: dict[str, Any], body: str
) -> dict[str, Any] | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent.is_symlink() or path.is_symlink():
            return _write_error("INVALID_TARGET", "Refusing to write through symlink.")
        path.write_text(_render_markdown(frontmatter=frontmatter, body=body), encoding="utf-8")
    except OSError as exc:
        return _write_error("WRITE_FAILED", str(exc))
    return None


def _write_local_capture(
    config: ProjectKnowledgeConfig, repo, relative_path: str, content: str, *, authority: str
) -> dict[str, Any]:
    write_error = _write_text_file_no_symlink(repo.path / relative_path, content)
    if write_error is not None:
        return write_error
    indexed, index_warnings = _index_written_document(config, repo, relative_path)
    return {
        "status": "local_only",
        "repo_id": repo.id,
        "path": relative_path,
        "authority": authority,
        "indexed": indexed,
        "index_scope": "single_document" if indexed else None,
        "full_reindex_required": not indexed,
        "next_action": "commit and push the note manually, or use capture_git_mode: direct_push",
        "warnings": index_warnings,
    }


def _write_and_push_capture(
    config: ProjectKnowledgeConfig,
    repo,
    relative_path: str,
    content: str,
    *,
    title: str,
    authority: str,
) -> dict[str, Any]:
    if repo.source_mode != "workspace":
        return _capture_persistence_failure(
            "local_only",
            repo_id=repo.id,
            message="Capture repo is not workspace-backed; direct push is unavailable.",
            next_action="Set write_policy.capture_git_mode: local_only or use a writable workspace repo.",
        )
    if _git_run(repo.path, "rev-parse", "--is-inside-work-tree", check=False).returncode != 0:
        return _capture_persistence_failure(
            "local_only",
            repo_id=repo.id,
            message="Capture repo is not a Git worktree; direct push is unavailable.",
            next_action="Set write_policy.capture_git_mode: local_only or configure a Git repo.",
        )

    remote = config.write_policy.capture_remote
    branch = config.write_policy.capture_branch
    lock = _CaptureLock(_capture_lock_path(config, repo.id, remote, branch))
    lock_error = lock.acquire()
    if lock_error is not None:
        return _capture_persistence_failure(
            "push_failed",
            repo_id=repo.id,
            message=lock_error,
            branch=branch,
            remote=remote,
            next_action="Retry after the in-progress capture completes.",
        )
    try:
        last_push_failure: subprocess.CompletedProcess[str] | None = None
        for attempt in range(2):
            result = _write_and_push_capture_once(
                config,
                repo,
                relative_path,
                content,
                title=title,
                authority=authority,
                remote=remote,
                branch=branch,
            )
            if result["status"] == "written_and_pushed":
                return result
            if result["status"] != "push_failed" or not _is_push_rejection(result):
                return result
            last_push_failure = result.pop("_push_result", None)
        details = {}
        if last_push_failure is not None:
            details["stderr"] = last_push_failure.stderr.strip()
        return _capture_persistence_failure(
            "sync_conflict",
            repo_id=repo.id,
            message="Remote branch advanced while writing the capture note.",
            branch=branch,
            remote=remote,
            details=details,
            next_action="Retry add_project_note after the remote branch settles.",
        )
    finally:
        lock.release()


def _write_and_push_capture_once(
    config: ProjectKnowledgeConfig,
    repo,
    relative_path: str,
    content: str,
    *,
    title: str,
    authority: str,
    remote: str,
    branch: str,
) -> dict[str, Any]:
    remote_url = _git_output(repo.path, "remote", "get-url", remote, warn_on_failure=False)
    if remote_url is None:
        return _capture_persistence_failure(
            "push_failed",
            repo_id=repo.id,
            message="Configured capture remote is not available.",
            branch=branch,
            remote=remote,
            next_action="Configure the capture remote or set capture_git_mode: local_only.",
        )
    fetched = _git_run(repo.path, "fetch", "--no-tags", remote, branch, check=False)
    if fetched.returncode != 0:
        return _capture_persistence_failure(
            "push_failed",
            repo_id=repo.id,
            message="Could not fetch configured capture branch.",
            branch=branch,
            remote=remote,
            details={"stderr": fetched.stderr.strip()},
            next_action="Fix remote access or set capture_git_mode: local_only.",
        )
    remote_ref = f"refs/remotes/{remote}/{branch}"
    verified = _git_run(repo.path, "rev-parse", "--verify", remote_ref, check=False)
    if verified.returncode != 0:
        return _capture_persistence_failure(
            "push_failed",
            repo_id=repo.id,
            message="Configured capture branch is not available on the remote.",
            branch=branch,
            remote=remote,
            details={"remote_ref": remote_ref},
            next_action="Create the capture branch or update write_policy.capture_branch.",
        )

    parent = Path(
        tempfile.mkdtemp(
            prefix=f"pkmcp-capture-{_slugify(repo.id)}-",
            dir=str(_capture_temp_parent(config)),
        )
    )
    worktree = parent / "worktree"
    worktree_added = False
    try:
        added = _git_run(repo.path, "worktree", "add", "--detach", str(worktree), remote_ref)
        if added.returncode != 0:
            return _capture_persistence_failure(
                "push_failed",
                repo_id=repo.id,
                message="Could not create isolated capture worktree.",
                branch=branch,
                remote=remote,
                details={"stderr": added.stderr.strip()},
                next_action="Retry after cleaning stale Git worktrees.",
            )
        worktree_added = True
        normalized, error = _safe_repo_write_path(worktree, relative_path, state_dir=None)
        if error is not None:
            return error
        write_error = _write_text_file_no_symlink(worktree / normalized, content)
        if write_error is not None:
            return write_error
        added_note = _git_run(worktree, "add", "--", normalized, check=False)
        if added_note.returncode != 0:
            return _capture_persistence_failure(
                "push_failed",
                repo_id=repo.id,
                message="Could not stage capture note.",
                branch=branch,
                remote=remote,
                details={"stderr": added_note.stderr.strip()},
                next_action="Retry after checking the capture target path.",
            )
        commit = _git_run(
            worktree,
            "commit",
            "--no-verify",
            "-m",
            f"Capture project note: {title}",
            check=False,
        )
        if commit.returncode != 0:
            return _capture_persistence_failure(
                "push_failed",
                repo_id=repo.id,
                message="Could not commit capture note.",
                branch=branch,
                remote=remote,
                details={"stderr": commit.stderr.strip()},
                next_action="Retry after checking Git author configuration.",
            )
        commit_sha = _git_output(worktree, "rev-parse", "HEAD") or ""
        pushed = _git_run(worktree, "push", "--no-verify", remote, f"HEAD:{branch}", check=False)
        if pushed.returncode != 0:
            failure = _capture_persistence_failure(
                "push_failed",
                repo_id=repo.id,
                message="Could not push capture note.",
                branch=branch,
                remote=remote,
                details={"stderr": pushed.stderr.strip()},
                next_action="Retry after resolving remote access or branch conflicts.",
            )
            failure["_push_result"] = pushed
            return failure
        workspace_warnings = _maybe_fast_forward_capture_workspace(
            config, repo, remote=remote, branch=branch
        )
        if (repo.path / normalized).exists():
            indexed, index_warnings = _index_written_document(config, repo, normalized)
        else:
            indexed = False
            index_warnings = [
                "committed note is not present in the active workspace; run index_project after updating the workspace."
            ]
        return {
            "status": "written_and_pushed",
            "repo_id": repo.id,
            "path": normalized,
            "authority": authority,
            "branch": branch,
            "remote": remote,
            "commit": commit_sha,
            "url": _capture_durable_url(remote_url, branch, normalized),
            "indexed": indexed,
            "index_scope": "single_document" if indexed else None,
            "full_reindex_required": not indexed,
            "warnings": [*workspace_warnings, *index_warnings],
        }
    finally:
        if worktree_added:
            _git_run(repo.path, "worktree", "remove", "--force", str(worktree), check=False)
        shutil.rmtree(parent, ignore_errors=True)


def _capture_persistence_failure(
    status: str,
    *,
    repo_id: str,
    message: str,
    next_action: str,
    branch: str | None = None,
    remote: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "repo_id": repo_id,
        "reason": message,
        "next_action": next_action,
        "indexed": False,
        "index_scope": None,
        "full_reindex_required": True,
        "warnings": [],
    }
    if branch is not None:
        result["branch"] = branch
    if remote is not None:
        result["remote"] = remote
    if details:
        result["details"] = details
    return result


def _maybe_fast_forward_capture_workspace(
    config: ProjectKnowledgeConfig, repo, *, remote: str, branch: str
) -> list[str]:
    warnings: list[str] = []
    current_branch = _git_output(repo.path, "branch", "--show-current", warn_on_failure=False)
    if current_branch != branch:
        return [
            "active workspace is not on the capture branch; run index_project after updating the workspace."
        ]
    porcelain = _git_status_porcelain(repo.path, state_dir=config.storage.state_dir)
    if porcelain is None or porcelain.strip():
        return [
            "active workspace has local changes; committed note was not checked out locally."
        ]
    fetched = _git_run(repo.path, "fetch", "--no-tags", remote, branch, check=False)
    if fetched.returncode != 0:
        return ["capture pushed, but active workspace refresh failed; run index_project after pull."]
    merged = _git_run(repo.path, "merge", "--ff-only", f"refs/remotes/{remote}/{branch}", check=False)
    if merged.returncode != 0:
        warnings.append("capture pushed, but active workspace fast-forward failed; run index_project after pull.")
    return warnings


def _capture_lock_path(
    config: ProjectKnowledgeConfig, repo_id: str, remote: str, branch: str
) -> Path:
    root = config.storage.lock_dir or config.storage.state_dir
    assert root is not None
    return root / "locks" / f"capture-{_slugify(repo_id)}-{_slugify(remote)}-{_slugify(branch)}.lock"


def _capture_temp_parent(config: ProjectKnowledgeConfig) -> Path:
    assert config.storage.state_dir is not None
    parent = config.storage.state_dir / "tmp"
    parent.mkdir(parents=True, exist_ok=True)
    return parent


class _CaptureLock:
    def __init__(self, path: Path, *, timeout_seconds: float = 10.0) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._fd: int | None = None

    def acquire(self) -> str | None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return None
            except FileExistsError:
                if time.monotonic() >= deadline:
                    return "Timed out waiting for capture persistence lock."
                time.sleep(0.05)
            except OSError as exc:
                return f"Could not acquire capture persistence lock: {exc}"

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _is_push_rejection(result: dict[str, Any]) -> bool:
    push_result = result.get("_push_result")
    if not isinstance(push_result, subprocess.CompletedProcess):
        return False
    output = f"{push_result.stdout}\n{push_result.stderr}".casefold()
    return "rejected" in output or "non-fast-forward" in output or "fetch first" in output


def _capture_durable_url(remote_url: str, branch: str, relative_path: str) -> str:
    path = relative_path.replace("\\", "/")
    github_match = re.match(r"(?:https://github\.com/|git@github\.com:)([^/]+)/([^/.]+)(?:\.git)?$", remote_url)
    if github_match:
        owner, repo_name = github_match.groups()
        return f"https://github.com/{owner}/{repo_name}/blob/{branch}/{path}"
    return f"{remote_url.rstrip('/')}/blob/{branch}/{path}"


def _write_text_file_no_symlink(path: Path, content: str) -> dict[str, Any] | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent.is_symlink() or path.is_symlink():
            return _write_error("INVALID_TARGET", "Refusing to write through symlink.")
        if path.exists() and path.is_file() and path.stat().st_nlink > 1:
            return _write_error("INVALID_TARGET", "Refusing to write through hardlink.")
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return _write_error("WRITE_FAILED", str(exc))
    return None


def _index_written_document(
    config: ProjectKnowledgeConfig, repo, relative_path: str
) -> tuple[bool, list[str]]:
    if not config.indexing.auto_reindex_after_note_write:
        return False, ["auto_reindex_after_note_write is disabled; run index_project."]
    try:
        summary = index_document(
            repo.path,
            relative_path,
            state_dir=config.storage.state_dir,
            repo_id=repo.id,
            role=repo.role,
            max_file_bytes=config.indexing.max_file_bytes,
            include_globs=repo.include_globs,
            exclude_globs=repo.exclude_globs,
        )
    except (OSError, ValueError) as exc:
        return False, [f"single-document indexing failed: {exc}"]
    warnings = []
    if summary.warning_count:
        warnings = list(summary.warnings)
    return True, warnings


def _write_error(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "status": "error",
        "error": {"code": code, "message": message, "details": details or {}, "recoverable": True},
        "errors": [
            {"code": code, "message": message, "details": details or {}, "recoverable": True}
        ],
        "warnings": [],
    }
    if extra:
        payload.update(extra)
    return payload


def _authority_branch_name(title: str, branch_name: str | None) -> str | None:
    if branch_name:
        branch = branch_name.strip().strip("/")
    else:
        branch = (
            f"pkmcp/authority-proposal/{datetime.now(UTC).date().isoformat()}-{_slugify(title)}"
        )
    if (
        not branch
        or branch.startswith("-")
        or ".." in branch
        or branch.endswith(".")
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch)
    ):
        return None
    return branch


def _authority_commit_message(
    title: str, rationale: str, source: str | None, tags: list[str] | None, changed_paths: list[str]
) -> str:
    return _authority_pr_body(rationale, source, tags, changed_paths, title=title)


def _authority_pr_body(
    rationale: str,
    source: str | None,
    tags: list[str] | None,
    changed_paths: list[str],
    *,
    title: str | None = None,
) -> str:
    lines = []
    if title:
        lines.extend([f"Title: {title}", ""])
    lines.extend(
        [
            "Authority boundary: review_required_before_promotion",
            "",
            "Rationale:",
            rationale,
            "",
            f"Source: {source or 'unspecified'}",
            f"Tags: {', '.join(tags or []) if tags else 'none'}",
            "",
            "Changed paths:",
        ]
    )
    lines.extend(f"- {path}" for path in changed_paths)
    return "\n".join(lines)


def _git_run(repo_path: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_path), "-c", "core.hooksPath=/dev/null", *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _cleanup_failed_authority_branch(
    repo_path: Path,
    branch: str,
    original_branch: str | None,
    original_head: str | None,
    *,
    created_paths: list[str] | None = None,
) -> None:
    # The workspace was clean before branch creation, so local reset/delete only
    # discards caller-supplied proposal files from the failed transient branch.
    _git_run(repo_path, "reset", "--hard", check=False)
    _remove_created_paths(repo_path, created_paths)
    if original_branch:
        _git_run(repo_path, "switch", original_branch, check=False)
    elif original_head:
        _git_run(repo_path, "switch", "--detach", original_head, check=False)
    _remove_created_paths(repo_path, created_paths)
    _git_run(repo_path, "branch", "-D", branch, check=False)


def _remove_created_paths(repo_path: Path, created_paths: list[str] | None) -> None:
    if not created_paths:
        return
    repo_root = repo_path.resolve()
    for relative_path in created_paths:
        path = repo_root / relative_path
        try:
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
            parent = path.parent
            while parent != repo_root:
                parent.rmdir()
                parent = parent.parent
        except OSError:
            continue


def _command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def _matches_any(relative_path: str, patterns: list[str]) -> bool:
    path = Path(relative_path)
    for pattern in patterns:
        if fnmatch(relative_path, pattern) or path.match(pattern):
            return True
    return False


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
