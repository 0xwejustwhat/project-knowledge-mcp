from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from project_knowledge_mcp.config import load_project_config, validate_project_config


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def write_project_config(
    path: Path,
    *,
    project_root: Path,
    ops_repo: Path,
    state_dir: Path | None = None,
    retrieval_provider: str = "sqlite_fts5",
    default_limit: int = 7,
    writable: bool = True,
    default_capture_dir: str = "docs/notes",
    write_policy_extra: str = "",
    repo_extra: str = "",
) -> Path:
    state_dir = state_dir or project_root / ".project-knowledge"
    config_path = path / "project.yaml"
    config_path.write_text(
        f"""
schema_version: 1
project:
  id: project-knowledge-mcp
  name: Project Knowledge MCP
  description: Local-first MCP server for repo-grounded project context.
  timezone: UTC
storage:
  project_root: {project_root.as_posix()}
  state_dir: {state_dir.as_posix()}
repos:
  - id: ops
    role: ops
    name: Project Knowledge MCP Ops
    path: {ops_repo.as_posix()}
    writable: {str(writable).lower()}
{repo_extra.rstrip()}
    include_globs: ["README.md", "docs/**/*.md", "*.md"]
    exclude_globs: [".git/**", ".project-knowledge/**"]
retrieval:
  provider: {retrieval_provider}
  default_limit: {default_limit}
  include_superseded_by_default: false
write_policy:
  default_capture_repo: ops
  default_capture_dir: {default_capture_dir}
  allow_direct_capture: true
{write_policy_extra.rstrip()}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def test_load_project_config_from_explicit_path(tmp_path: Path):
    repo = tmp_path / "ops"
    init_git_repo(repo)
    config_path = write_project_config(tmp_path, project_root=tmp_path, ops_repo=repo)

    config = load_project_config(config_path)

    assert config.schema_version == 1
    assert config.project.id == "project-knowledge-mcp"
    assert config.project.name == "Project Knowledge MCP"
    assert config.storage.state_dir == tmp_path / ".project-knowledge"
    assert config.ops_repo.id == "ops"
    assert config.ops_repo.role == "ops"
    assert config.retrieval.provider == "sqlite_fts5"
    assert config.write_policy.capture_git_mode == "direct_push"
    assert config.write_policy.capture_branch == "main"
    assert config.write_policy.capture_remote == "origin"


def test_validate_project_config_reports_missing_repo_path(tmp_path: Path):
    missing_repo = tmp_path / "missing"
    config_path = write_project_config(tmp_path, project_root=tmp_path, ops_repo=missing_repo)

    result = validate_project_config(config_path)

    assert result["valid"] is False
    assert result["project_id"] == "project-knowledge-mcp"
    assert result["errors"][0]["code"] == "CONFIG_INVALID"
    assert "does not exist" in result["errors"][0]["message"]
    assert result["repos"][0]["exists"] is False


def test_validate_project_config_rejects_non_sqlite_retrieval_provider(tmp_path: Path):
    repo = tmp_path / "ops"
    init_git_repo(repo)
    config_path = write_project_config(
        tmp_path,
        project_root=tmp_path,
        ops_repo=repo,
        retrieval_provider="llamaindex",
    )

    result = validate_project_config(config_path)

    assert result["valid"] is False
    assert any(
        error["code"] == "CONFIG_INVALID" and "retrieval.provider" in error["message"]
        for error in result["errors"]
    )


def test_load_project_config_honors_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = tmp_path / "ops"
    init_git_repo(repo)
    config_path = write_project_config(tmp_path, project_root=tmp_path, ops_repo=repo)
    monkeypatch.setenv("PROJECT_KNOWLEDGE_CONFIG", str(config_path))

    config = load_project_config()

    assert config.config_path == config_path
    assert config.ops_repo.path == repo


def test_validate_project_config_rejects_invalid_limits_and_write_policy_paths(tmp_path: Path):
    repo = tmp_path / "ops"
    init_git_repo(repo)
    config_path = write_project_config(
        tmp_path,
        project_root=tmp_path,
        ops_repo=repo,
        default_limit=0,
        default_capture_dir="../doctrine",
    )

    result = validate_project_config(config_path)

    assert result["valid"] is False
    messages = [error["message"] for error in result["errors"]]
    assert any("retrieval.default_limit" in message for message in messages)
    assert any("write_policy.default_capture_dir" in message for message in messages)


def test_validate_project_config_rejects_invalid_capture_git_settings(tmp_path: Path):
    repo = tmp_path / "ops"
    init_git_repo(repo)
    config_path = write_project_config(
        tmp_path,
        project_root=tmp_path,
        ops_repo=repo,
        write_policy_extra="""
  capture_branch: ../main
  capture_remote: origin/main
""",
    )

    result = validate_project_config(config_path)

    assert result["valid"] is False
    messages = [error["message"] for error in result["errors"]]
    assert any("write_policy.capture_branch" in message for message in messages)
    assert any("write_policy.capture_remote" in message for message in messages)


def test_load_project_config_rejects_invalid_capture_git_mode(tmp_path: Path):
    repo = tmp_path / "ops"
    init_git_repo(repo)
    config_path = write_project_config(
        tmp_path,
        project_root=tmp_path,
        ops_repo=repo,
        write_policy_extra="  capture_git_mode: surprise",
    )

    result = validate_project_config(config_path)

    assert result["valid"] is False
    assert result["errors"][0]["code"] == "CONFIG_INVALID"
    assert "capture_git_mode" in result["errors"][0]["message"]


def test_validate_project_config_rejects_invalid_indexing_limits(tmp_path: Path):
    repo = tmp_path / "ops"
    init_git_repo(repo)
    config_path = write_project_config(tmp_path, project_root=tmp_path, ops_repo=repo)
    config_text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        config_text
        + """
indexing:
  provider: local_parser_registry
  max_file_bytes: 0
  chunk_target_chars: 100
  chunk_overlap_chars: 100
""",
        encoding="utf-8",
    )

    result = validate_project_config(config_path)

    assert result["valid"] is False
    messages = [error["message"] for error in result["errors"]]
    assert any("indexing.max_file_bytes" in message for message in messages)
    assert any("indexing.chunk_overlap_chars" in message for message in messages)


def test_validate_project_config_reports_source_mode_metadata_and_default_warning(
    tmp_path: Path,
):
    repo = tmp_path / "ops"
    init_git_repo(repo)
    config_path = write_project_config(tmp_path, project_root=tmp_path, ops_repo=repo)

    result = validate_project_config(config_path)

    assert result["valid"] is True
    assert any(
        "source_mode" in warning and "workspace" in warning for warning in result["warnings"]
    )
    assert result["repos"][0]["source_mode"] == "workspace"
    assert result["repos"][0]["includes_uncommitted_changes"] is True


def test_validate_project_config_rejects_invalid_source_mode(tmp_path: Path):
    repo = tmp_path / "ops"
    init_git_repo(repo)
    config_path = write_project_config(
        tmp_path,
        project_root=tmp_path,
        ops_repo=repo,
        repo_extra="    source_mode: invalid_mode",
    )

    result = validate_project_config(config_path)

    assert result["valid"] is False
    assert any("source_mode" in error["message"] for error in result["errors"])


def test_validate_project_config_rejects_snapshot_without_provenance(tmp_path: Path):
    repo = tmp_path / "ops"
    init_git_repo(repo)
    config_path = write_project_config(
        tmp_path,
        project_root=tmp_path,
        ops_repo=repo,
        repo_extra="    source_mode: snapshot",
    )

    result = validate_project_config(config_path)

    assert result["valid"] is False
    assert any("snapshot" in error["message"] for error in result["errors"])
