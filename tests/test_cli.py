from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from project_knowledge_mcp.server import app


def test_cli_help_lists_step1_index_commands():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "index-project" in result.output
    assert "search-index" in result.output
    assert "search-decisions" in result.output
    assert "get-current-doctrine" in result.output
    assert "search-open-questions" in result.output


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)


def commit_all(path: Path, message: str = "commit") -> str:
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write_project_config(root: Path, *, repo: Path, state_dir: Path) -> Path:
    config_path = root / "project.yaml"
    config_path.write_text(
        f"""
schema_version: 1
project:
  id: project-knowledge-mcp
  name: Project Knowledge MCP
storage:
  project_root: {root.as_posix()}
  state_dir: {state_dir.as_posix()}
repos:
  - id: ops
    role: ops
    name: Ops Repo
    path: {repo.as_posix()}
    writable: true
    include_globs: ["README.md", "docs/**/*.md", "*.md"]
    exclude_globs: [".git/**", ".project-knowledge/**"]
retrieval:
  provider: sqlite_fts5
  default_limit: 5
  include_superseded_by_default: false
write_policy:
  default_capture_repo: ops
  default_capture_dir: docs/notes
  allow_direct_capture: true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def test_cli_indexes_and_searches_project(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / "state"
    doc = repo / "docs" / "doctrine" / "context.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        """---
title: Context Doctrine
authority: canonical
status: current
tags: [context]
---
# Context Doctrine

Project Knowledge MCP returns evidence packets before synthesis.
""",
        encoding="utf-8",
    )

    index_result = CliRunner().invoke(
        app,
        [
            "index-project",
            "--repo-path",
            str(repo),
            "--state-dir",
            str(state),
            "--repo-id",
            "ops",
            "--role",
            "ops",
        ],
    )

    assert index_result.exit_code == 0, index_result.output
    summary = json.loads(index_result.output)
    assert summary["indexed_documents"] == 1
    assert summary["indexed_chunks"] == 1

    search_result = CliRunner().invoke(
        app,
        [
            "search-index",
            "evidence packets synthesis",
            "--state-dir",
            str(state),
            "--limit",
            "3",
        ],
    )

    assert search_result.exit_code == 0, search_result.output
    payload = json.loads(search_result.output)
    assert payload["query"] == "evidence packets synthesis"
    assert payload["results"][0]["path"] == "docs/doctrine/context.md"
    assert payload["results"][0]["authority"] == "canonical"


def test_cli_validate_config_index_project_and_search_ops_from_config(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    doc = repo / "docs" / "doctrine" / "context.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        """---
title: Context Doctrine
type: doctrine
status: current
authority: canonical
tags: [context]
---
# Context Doctrine

Project Knowledge MCP returns evidence packets before synthesis.
""",
        encoding="utf-8",
    )
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    validation_result = CliRunner().invoke(app, ["validate-config", "--config", str(config_path)])
    assert validation_result.exit_code == 0, validation_result.output
    validation = json.loads(validation_result.output)
    assert validation["valid"] is True
    assert validation["project_id"] == "project-knowledge-mcp"
    assert validation["repos"][0]["id"] == "ops"

    index_result = CliRunner().invoke(app, ["index-project", "--config", str(config_path)])
    assert index_result.exit_code == 0, index_result.output
    summary = json.loads(index_result.output)
    assert summary["status"] == "ok"
    assert summary["repos"][0]["repo_id"] == "ops"
    assert summary["repos"][0]["documents_indexed"] == 1
    assert summary["repos"][0]["chunks_indexed"] == 1

    scoped_index_result = CliRunner().invoke(
        app, ["index-project", "--config", str(config_path), "--repo-id", "ops"]
    )
    assert scoped_index_result.exit_code == 0, scoped_index_result.output
    scoped_summary = json.loads(scoped_index_result.output)
    assert [repo_summary["repo_id"] for repo_summary in scoped_summary["repos"]] == ["ops"]

    missing_repo_result = CliRunner().invoke(
        app, ["index-project", "--config", str(config_path), "--repo-id", "missing"]
    )
    assert missing_repo_result.exit_code != 0
    assert "Unknown repo_id" in missing_repo_result.output

    search_result = CliRunner().invoke(
        app,
        [
            "search-ops",
            "evidence packets synthesis",
            "--config",
            str(config_path),
            "--limit",
            "3",
        ],
    )
    assert search_result.exit_code == 0, search_result.output
    payload = json.loads(search_result.output)
    assert payload["query"] == "evidence packets synthesis"
    assert payload["results"][0]["path"] == "docs/doctrine/context.md"
    assert payload["results"][0]["authority"] == "canonical"
    assert "markdown" in payload
    assert "## Search Results" in payload["markdown"]


def test_cli_phase4_search_commands_from_config(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    docs = {
        "docs/doctrine/context.md": """---
title: Context Doctrine
type: doctrine
status: current
authority: canonical
---
# Context Doctrine

Context retrieval doctrine is current truth.
""",
        "docs/decisions/accepted/0001-context.md": """---
title: Accepted Context Decision
type: decision
status: accepted
authority: accepted_decision
---
# Accepted Context Decision

Accepted context retrieval decision.
""",
        "docs/open-questions/context.md": """---
title: Context Open Question
type: open_question
status: open
authority: working
owner: Amin
related_docs: [docs/doctrine/context.md]
---
# Context Open Question

Context retrieval unresolved question.
""",
    }
    for relative_path, text in docs.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    index_result = CliRunner().invoke(app, ["index-project", "--config", str(config_path)])
    assert index_result.exit_code == 0, index_result.output

    decision_result = CliRunner().invoke(
        app, ["search-decisions", "context retrieval", "--config", str(config_path), "--limit", "3"]
    )
    assert decision_result.exit_code == 0, decision_result.output
    decisions = json.loads(decision_result.output)
    assert decisions["results"][0]["path"] == "docs/decisions/accepted/0001-context.md"
    assert "markdown" in decisions

    doctrine_result = CliRunner().invoke(
        app, ["get-current-doctrine", "context retrieval", "--config", str(config_path)]
    )
    assert doctrine_result.exit_code == 0, doctrine_result.output
    doctrine = json.loads(doctrine_result.output)
    assert doctrine["doctrine"][0]["path"] == "docs/doctrine/context.md"
    assert doctrine["decisions"][0]["path"] == "docs/decisions/accepted/0001-context.md"

    question_result = CliRunner().invoke(
        app,
        [
            "search-open-questions",
            "context retrieval",
            "--config",
            str(config_path),
            "--limit",
            "3",
        ],
    )
    assert question_result.exit_code == 0, question_result.output
    questions = json.loads(question_result.output)
    assert questions["results"][0]["path"] == "docs/open-questions/context.md"
    assert questions["results"][0]["owner"] == "Amin"


def test_cli_check_project_staleness_from_config(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    doc = repo / "docs" / "doctrine" / "context.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Context\n\nBaseline indexed evidence.\n", encoding="utf-8")
    indexed_commit = commit_all(repo, "baseline")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    index_result = CliRunner().invoke(app, ["index-project", "--config", str(config_path)])
    assert index_result.exit_code == 0, index_result.output

    doc.write_text("# Context\n\nChanged workspace evidence.\n", encoding="utf-8")
    (repo / "docs" / "notes").mkdir(parents=True)
    (repo / "docs" / "notes" / "untracked.md").write_text("# Note\n", encoding="utf-8")

    status_result = CliRunner().invoke(
        app, ["check-project-staleness", "--config", str(config_path)]
    )
    assert status_result.exit_code == 0, status_result.output
    payload = json.loads(status_result.output)
    assert payload["status"] == "ok"
    repo_status = payload["repos"][0]
    assert repo_status["repo_id"] == "ops"
    assert repo_status["head_commit"] == indexed_commit
    assert repo_status["last_indexed_commit"] == indexed_commit
    assert repo_status["dirty"] is True
    assert repo_status["untracked_count"] == 1
    assert repo_status["reindex_needed"] is True
    assert "## Project Staleness" in payload["markdown"]
