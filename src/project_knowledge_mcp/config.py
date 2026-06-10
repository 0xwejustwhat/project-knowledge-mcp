from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

CONFIG_ENV_VAR = "PROJECT_KNOWLEDGE_CONFIG"
DEFAULT_CONTAINER_CONFIG = Path("/workspace/project.yaml")
DEFAULT_LOCAL_CONFIG = Path("project.yaml")
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ProjectMetadata(BaseModel):
    id: str
    name: str | None = None
    description: str | None = None
    timezone: str = "UTC"


class StorageConfig(BaseModel):
    project_root: Path = Path(".")
    state_dir: Path | None = None
    sqlite_path: Path | None = None
    cache_dir: Path | None = None
    lock_dir: Path | None = None


class RepoConfig(BaseModel):
    id: str
    role: Literal["ops", "work", "artifact"]
    path: Path
    name: str | None = None
    source_mode: Literal["workspace", "snapshot"] = "workspace"
    source_mode_defaulted: bool = False
    host_path: Path | None = None
    includes_uncommitted_changes: bool | None = None
    snapshot_ref: str | None = None
    snapshot_commit: str | None = None
    writable: bool = False
    canonical_priority: str | None = None
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)


class RetrievalConfig(BaseModel):
    provider: str = "sqlite_fts5"
    mode: str = "local_no_llm"
    llm_enabled: bool = False
    embeddings_enabled: bool = False
    cloud_parsers_enabled: bool = False
    strategies: list[str] = Field(default_factory=list)
    default_limit: int = 10
    brief_max_results_per_section: int = 8
    include_superseded_by_default: bool = False


class IndexingConfig(BaseModel):
    provider: str = "local_parser_registry"
    max_file_bytes: int = 1_000_000
    chunk_target_chars: int = 1800
    chunk_overlap_chars: int = 200
    file_watch: bool = False
    auto_reindex_after_note_write: bool = True
    initial_parsers: list[str] = Field(default_factory=lambda: ["markdown", "text"])
    future_parser_adapters: list[str] = Field(default_factory=list)


class WritePolicyConfig(BaseModel):
    default_capture_repo: str = "ops"
    default_capture_dir: str = "docs/notes"
    allow_direct_capture: bool = True
    blocked_direct_write_globs: list[str] = Field(default_factory=list)
    proposal_dirs: dict[str, str] = Field(default_factory=dict)


class ProjectKnowledgeConfig(BaseModel):
    schema_version: int
    project: ProjectMetadata
    storage: StorageConfig = Field(default_factory=StorageConfig)
    repos: list[RepoConfig]
    indexing: IndexingConfig = Field(default_factory=IndexingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    write_policy: WritePolicyConfig = Field(default_factory=WritePolicyConfig)
    config_path: Path | None = None

    @property
    def ops_repo(self) -> RepoConfig:
        ops_repos = [repo for repo in self.repos if repo.role == "ops"]
        if not ops_repos:
            raise ValueError("project config has no ops repo")
        return ops_repos[0]

    def repo_by_id(self, repo_id: str) -> RepoConfig | None:
        return next((repo for repo in self.repos if repo.id == repo_id), None)


def resolve_config_path(explicit_path: Path | str | None = None) -> Path:
    if explicit_path is not None:
        return Path(explicit_path).expanduser().resolve()
    env_path = os.getenv(CONFIG_ENV_VAR)
    if env_path:
        return Path(env_path).expanduser().resolve()
    if DEFAULT_CONTAINER_CONFIG.exists():
        return DEFAULT_CONTAINER_CONFIG.resolve()
    return DEFAULT_LOCAL_CONFIG.resolve()


def load_project_config(config_path: Path | str | None = None) -> ProjectKnowledgeConfig:
    resolved_path = resolve_config_path(config_path)
    data = yaml.safe_load(resolved_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a YAML mapping: {resolved_path}")

    for repo in data.get("repos", []) or []:
        if isinstance(repo, dict) and "source_mode" not in repo:
            repo["source_mode"] = "workspace"
            repo["source_mode_defaulted"] = True

    config = ProjectKnowledgeConfig.model_validate(data)
    config.config_path = resolved_path
    _resolve_paths(config, base_dir=resolved_path.parent)
    for repo in config.repos:
        if repo.source_mode == "snapshot":
            repo.includes_uncommitted_changes = False
        elif repo.includes_uncommitted_changes is None:
            repo.includes_uncommitted_changes = True
    return config


def validate_project_config(config_path: Path | str | None = None) -> dict[str, Any]:
    try:
        config = load_project_config(config_path)
    except Exception as exc:
        return {
            "valid": False,
            "project_id": None,
            "config_path": str(resolve_config_path(config_path)),
            "repos": [],
            "warnings": [],
            "errors": [_error("CONFIG_INVALID", str(exc), recoverable=True)],
        }

    errors: list[dict[str, Any]] = []
    warnings: list[str] = []
    repo_reports = [_repo_report(repo) for repo in config.repos]

    if config.schema_version != 1:
        errors.append(_error("CONFIG_INVALID", "schema_version must be 1"))
    if not config.project.id or not _PROJECT_ID_RE.match(config.project.id):
        errors.append(
            _error(
                "CONFIG_INVALID",
                "project.id must be non-empty and URL/path safe",
                details={"project_id": config.project.id},
            )
        )
    if not config.repos:
        errors.append(_error("CONFIG_INVALID", "At least one repo must be configured"))

    ops_repos = [repo for repo in config.repos if repo.role == "ops"]
    if len(ops_repos) != 1:
        errors.append(_error("CONFIG_INVALID", "Exactly one repo must have role: ops"))

    for repo, report in zip(config.repos, repo_reports, strict=True):
        if not report["exists"]:
            errors.append(
                _error(
                    "CONFIG_INVALID",
                    f"Repo path does not exist: {repo.path}",
                    details={"repo_id": repo.id, "path": str(repo.path)},
                )
            )
        elif not report["is_git_repo"]:
            errors.append(
                _error(
                    "REPO_NOT_GIT",
                    f"Repo path is not a Git worktree: {repo.path}",
                    details={"repo_id": repo.id, "path": str(repo.path)},
                )
            )
        if repo.writable and repo.path.exists() and not os.access(repo.path, os.W_OK):
            errors.append(
                _error(
                    "CONFIG_INVALID",
                    f"Repo is configured writable but filesystem is not writable: {repo.path}",
                    details={"repo_id": repo.id, "path": str(repo.path)},
                )
            )
        for glob in [*repo.include_globs, *repo.exclude_globs]:
            if _is_absolute_or_parent_glob(glob):
                errors.append(
                    _error(
                        "CONFIG_INVALID",
                        f"Include/exclude globs must be relative to repo root: {glob}",
                        details={"repo_id": repo.id, "glob": glob},
                    )
                )
        if repo.source_mode_defaulted:
            warnings.append(
                f"Repo {repo.id} omitted source_mode; normalized to workspace for MVP compatibility."
            )
        if repo.source_mode == "snapshot" and (not repo.snapshot_ref or not repo.snapshot_commit):
            errors.append(
                _error(
                    "CONFIG_INVALID",
                    "snapshot repos must declare snapshot_ref and snapshot_commit provenance",
                    details={"repo_id": repo.id},
                )
            )

    capture_repo = config.repo_by_id(config.write_policy.default_capture_repo)
    if capture_repo is None:
        errors.append(
            _error(
                "CONFIG_INVALID",
                "write_policy.default_capture_repo must refer to a configured repo",
                details={"default_capture_repo": config.write_policy.default_capture_repo},
            )
        )
    elif capture_repo.role != "ops" or not capture_repo.writable:
        errors.append(
            _error(
                "CONFIG_INVALID",
                "write_policy.default_capture_repo must refer to a writable ops repo",
                details={"default_capture_repo": capture_repo.id},
            )
        )

    write_policy_paths = [
        ("write_policy.default_capture_dir", config.write_policy.default_capture_dir),
        *[
            (f"write_policy.proposal_dirs.{key}", value)
            for key, value in config.write_policy.proposal_dirs.items()
        ],
        *[
            ("write_policy.blocked_direct_write_globs", value)
            for value in config.write_policy.blocked_direct_write_globs
        ],
    ]
    for field_name, pattern in write_policy_paths:
        if _is_absolute_or_parent_glob(pattern):
            errors.append(
                _error(
                    "CONFIG_INVALID",
                    f"{field_name} must be relative and must not traverse above repo root: {pattern}",
                    details={"field": field_name, "path": pattern},
                )
            )

    if not _is_relative_to(config.storage.state_dir, config.storage.project_root):
        errors.append(
            _error(
                "CONFIG_INVALID",
                "storage.state_dir must resolve under storage.project_root by default",
                details={
                    "project_root": str(config.storage.project_root),
                    "state_dir": str(config.storage.state_dir),
                },
            )
        )

    if config.retrieval.provider != "sqlite_fts5":
        errors.append(
            _error(
                "CONFIG_INVALID",
                "retrieval.provider must be sqlite_fts5 for the MVP default path",
                details={"provider": config.retrieval.provider},
            )
        )
    if config.retrieval.default_limit < 1 or config.retrieval.default_limit > 1000:
        errors.append(
            _error(
                "CONFIG_INVALID",
                "retrieval.default_limit must be between 1 and 1000",
                details={"default_limit": config.retrieval.default_limit},
            )
        )
    if (
        config.retrieval.brief_max_results_per_section < 1
        or config.retrieval.brief_max_results_per_section > 1000
    ):
        errors.append(
            _error(
                "CONFIG_INVALID",
                "retrieval.brief_max_results_per_section must be between 1 and 1000",
                details={
                    "brief_max_results_per_section": config.retrieval.brief_max_results_per_section
                },
            )
        )
    if config.retrieval.llm_enabled:
        errors.append(_error("CONFIG_INVALID", "retrieval.llm_enabled must be false by default"))
    if config.retrieval.embeddings_enabled:
        errors.append(
            _error("CONFIG_INVALID", "retrieval.embeddings_enabled must be false by default")
        )
    if config.retrieval.cloud_parsers_enabled:
        errors.append(
            _error("CONFIG_INVALID", "retrieval.cloud_parsers_enabled must be false by default")
        )

    if config.indexing.provider != "local_parser_registry":
        errors.append(
            _error(
                "CONFIG_INVALID",
                "indexing.provider must be local_parser_registry for the MVP default path",
                details={"provider": config.indexing.provider},
            )
        )
    if config.indexing.max_file_bytes < 1 or config.indexing.max_file_bytes > 100_000_000:
        errors.append(
            _error(
                "CONFIG_INVALID",
                "indexing.max_file_bytes must be between 1 and 100000000",
                details={"max_file_bytes": config.indexing.max_file_bytes},
            )
        )
    if config.indexing.chunk_target_chars < 1:
        errors.append(
            _error(
                "CONFIG_INVALID",
                "indexing.chunk_target_chars must be at least 1",
                details={"chunk_target_chars": config.indexing.chunk_target_chars},
            )
        )
    if config.indexing.chunk_overlap_chars < 0:
        errors.append(
            _error(
                "CONFIG_INVALID",
                "indexing.chunk_overlap_chars must be at least 0",
                details={"chunk_overlap_chars": config.indexing.chunk_overlap_chars},
            )
        )
    if config.indexing.chunk_overlap_chars >= config.indexing.chunk_target_chars:
        errors.append(
            _error(
                "CONFIG_INVALID",
                "indexing.chunk_overlap_chars must be smaller than indexing.chunk_target_chars",
                details={
                    "chunk_overlap_chars": config.indexing.chunk_overlap_chars,
                    "chunk_target_chars": config.indexing.chunk_target_chars,
                },
            )
        )

    return {
        "valid": not errors,
        "project_id": config.project.id,
        "project": {
            "id": config.project.id,
            "name": config.project.name,
            "description": config.project.description,
            "timezone": config.project.timezone,
        },
        "config_path": str(config.config_path),
        "state_dir": str(config.storage.state_dir),
        "repos": repo_reports,
        "warnings": warnings,
        "errors": errors,
    }


def _resolve_paths(config: ProjectKnowledgeConfig, *, base_dir: Path) -> None:
    project_root = _resolve_path(config.storage.project_root, base_dir=base_dir)
    config.storage.project_root = project_root
    if config.storage.state_dir is None:
        config.storage.state_dir = project_root / ".project-knowledge"
    else:
        config.storage.state_dir = _resolve_path(config.storage.state_dir, base_dir=project_root)
    if config.storage.sqlite_path is not None:
        config.storage.sqlite_path = _resolve_path(
            config.storage.sqlite_path, base_dir=project_root
        )
    if config.storage.cache_dir is not None:
        config.storage.cache_dir = _resolve_path(config.storage.cache_dir, base_dir=project_root)
    if config.storage.lock_dir is not None:
        config.storage.lock_dir = _resolve_path(config.storage.lock_dir, base_dir=project_root)
    for repo in config.repos:
        repo.path = _resolve_path(repo.path, base_dir=project_root)
        if repo.host_path is not None:
            repo.host_path = _resolve_path(repo.host_path, base_dir=project_root)


def _resolve_path(path: Path, *, base_dir: Path) -> Path:
    expanded = Path(path).expanduser()
    if expanded.is_absolute():
        return expanded.resolve()
    return (base_dir / expanded).resolve()


def _repo_report(repo: RepoConfig) -> dict[str, Any]:
    exists = repo.path.exists()
    is_git_repo = _is_git_worktree(repo.path) if exists else False
    return {
        "id": repo.id,
        "repo_id": repo.id,
        "path": str(repo.path),
        "role": repo.role,
        "name": repo.name,
        "source_mode": repo.source_mode,
        "host_path": str(repo.host_path) if repo.host_path is not None else None,
        "includes_uncommitted_changes": repo.includes_uncommitted_changes,
        "snapshot_ref": repo.snapshot_ref,
        "snapshot_commit": repo.snapshot_commit,
        "writable": repo.writable,
        "exists": exists,
        "is_git_repo": is_git_repo,
    }


def _is_git_worktree(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _is_absolute_or_parent_glob(pattern: str) -> bool:
    path = Path(pattern)
    return path.is_absolute() or ".." in path.parts


def _is_relative_to(child: Path | None, parent: Path) -> bool:
    if child is None:
        return False
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _error(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    recoverable: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "details": details or {},
        "recoverable": recoverable,
    }
