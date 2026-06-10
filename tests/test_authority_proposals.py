from __future__ import annotations

import subprocess
from pathlib import Path

import project_knowledge_mcp.services as services
from project_knowledge_mcp.services import propose_authority_change_from_config


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--initial-branch", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)


def commit_all(path: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True)


def write_project_config(root: Path, *, repo: Path, state_dir: Path) -> Path:
    config_path = root / "project.yaml"
    config_path.write_text(
        f"""
schema_version: 1
project:
  id: authority-proposal-test
storage:
  project_root: {root.as_posix()}
  state_dir: {state_dir.as_posix()}
repos:
  - id: ops
    role: ops
    path: {repo.as_posix()}
    writable: true
    include_globs: ["docs/**/*.md", "*.md"]
    exclude_globs: [".git/**", ".project-knowledge/**"]
retrieval:
  provider: sqlite_fts5
write_policy:
  default_capture_repo: ops
  default_capture_dir: docs/notes
  allow_direct_capture: true
  blocked_direct_write_globs: ["docs/canonical/**"]
  proposal_dirs:
    doctrine_delta: docs/proposals/doctrine-deltas
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def test_propose_authority_change_commits_caller_supplied_changes_without_pr(
    monkeypatch, tmp_path: Path
):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    (repo / "docs" / "specs").mkdir(parents=True)
    (repo / "docs" / "specs" / "current.md").write_text("old spec\n", encoding="utf-8")
    commit_all(repo, "initial")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)
    monkeypatch.setattr(services, "_command_exists", lambda _command: False)

    result = propose_authority_change_from_config(
        title="Clarify authority path",
        rationale="Caller supplied a reviewed spec delta.",
        changes=[
            {
                "operation": "replace_file",
                "path": "docs/specs/current.md",
                "content": "new caller supplied spec\n",
            },
            {
                "operation": "add_file",
                "path": "docs/decisions/0004-proposed.md",
                "content": "# Proposed decision\n\nCaller supplied content.\n",
            },
        ],
        source="pytest",
        tags=["step7"],
        branch_name="pkmcp/authority-proposal/test-clarify",
        config_path=config_path,
    )

    assert result["status"] == "branch_prepared_pr_not_opened"
    assert result["authority_boundary"] == "review_required_before_promotion"
    assert result["changed_paths"] == ["docs/specs/current.md", "docs/decisions/0004-proposed.md"]
    assert result["next_action"] == "push branch and open PR manually"
    assert "GitHub authentication unavailable" in result["warnings"][0]
    assert (repo / "docs" / "specs" / "current.md").read_text(
        encoding="utf-8"
    ) == "new caller supplied spec\n"
    assert (
        (repo / "docs" / "decisions" / "0004-proposed.md")
        .read_text(encoding="utf-8")
        .startswith("# Proposed")
    )
    assert (
        subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == "pkmcp/authority-proposal/test-clarify"
    )
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=%B"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert "Authority boundary: review_required_before_promotion" in log
    assert "Caller supplied a reviewed spec delta." in log


def test_propose_authority_change_pushes_branch_and_opens_pr(monkeypatch, tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    remote = tmp_path / "remote.git"
    init_git_repo(repo)
    subprocess.run(["git", "init", "--bare", "-q", remote.as_posix()], check=True)
    subprocess.run(["git", "remote", "add", "origin", remote.as_posix()], cwd=repo, check=True)
    (repo / "docs" / "specs").mkdir(parents=True)
    (repo / "docs" / "specs" / "current.md").write_text("old spec\n", encoding="utf-8")
    commit_all(repo, "initial")
    hooks_dir = repo / ".git" / "hooks"
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (hooks_dir / "pre-push").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (hooks_dir / "pre-commit").chmod(0o755)
    (hooks_dir / "pre-push").chmod(0o755)
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)
    monkeypatch.setattr(services, "_command_exists", lambda command: command == "gh")
    original_run = subprocess.run
    gh_commands: list[list[str]] = []

    def fake_run(args, *pargs, **kwargs):
        if args[:3] == ["gh", "auth", "status"]:
            gh_commands.append(list(args))
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[:3] == ["gh", "pr", "create"]:
            gh_commands.append(list(args))
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="https://github.com/example/project-knowledge-mcp/pull/7\n",
                stderr="",
            )
        return original_run(args, *pargs, **kwargs)

    monkeypatch.setattr(services.subprocess, "run", fake_run)

    result = propose_authority_change_from_config(
        title="Open review PR",
        rationale="Caller supplied review content.",
        changes=[
            {
                "operation": "replace_file",
                "path": "docs/specs/current.md",
                "content": "new spec\n",
            }
        ],
        branch_name="pkmcp/authority-proposal/test-pr",
        config_path=config_path,
    )

    assert result["status"] == "pr_opened"
    assert result["pr_url"] == "https://github.com/example/project-knowledge-mcp/pull/7"
    pr_create = gh_commands[-1]
    assert pr_create[:3] == ["gh", "pr", "create"]
    assert "--head" in pr_create
    assert pr_create[pr_create.index("--head") + 1] == "pkmcp/authority-proposal/test-pr"
    pushed = original_run(
        ["git", "ls-remote", "--heads", remote.as_posix(), "pkmcp/authority-proposal/test-pr"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "refs/heads/pkmcp/authority-proposal/test-pr" in pushed.stdout


def test_propose_authority_change_cleans_up_failed_commit(monkeypatch, tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    commit_all(repo, "initial")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)
    original_git_run = services._git_run

    def fake_git_run(repo_path: Path, *args: str, check: bool = False):
        if args and args[0] == "commit":
            return subprocess.CompletedProcess(
                ["git", *args], 1, stdout="", stderr="simulated commit failure"
            )
        return original_git_run(repo_path, *args, check=check)

    monkeypatch.setattr(services, "_git_run", fake_git_run)

    result = propose_authority_change_from_config(
        title="Failed commit cleanup",
        rationale="commit fails in test",
        changes=[{"operation": "add_file", "path": "docs/decisions/0006.md", "content": "x\n"}],
        branch_name="pkmcp/authority-proposal/cleanup-test",
        config_path=config_path,
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "GIT_COMMIT_FAILED"
    assert not (repo / "docs" / "decisions" / "0006.md").exists()
    current_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert current_branch == "main"
    branch_check = subprocess.run(
        ["git", "rev-parse", "--verify", "pkmcp/authority-proposal/cleanup-test"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert branch_check.returncode != 0
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert status == ""


def test_propose_authority_change_cleans_up_failed_add(monkeypatch, tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    commit_all(repo, "initial")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)
    original_git_run = services._git_run

    def fake_git_run(repo_path: Path, *args: str, check: bool = False):
        if args and args[0] == "add":
            return subprocess.CompletedProcess(
                ["git", *args], 1, stdout="", stderr="simulated add failure"
            )
        return original_git_run(repo_path, *args, check=check)

    monkeypatch.setattr(services, "_git_run", fake_git_run)

    result = propose_authority_change_from_config(
        title="Failed add cleanup",
        rationale="add fails in test",
        changes=[{"operation": "add_file", "path": "docs/decisions/0007.md", "content": "x\n"}],
        branch_name="pkmcp/authority-proposal/add-cleanup-test",
        config_path=config_path,
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "GIT_ADD_FAILED"
    assert not (repo / "docs" / "decisions" / "0007.md").exists()
    current_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert current_branch == "main"
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert status == ""


def test_propose_authority_change_rejects_dirty_workspace(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    commit_all(repo, "initial")
    (repo / "dirty.md").write_text("dirty\n", encoding="utf-8")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    result = propose_authority_change_from_config(
        title="Cannot proceed dirty",
        rationale="should block",
        changes=[{"operation": "add_file", "path": "docs/decisions/0005.md", "content": "x\n"}],
        config_path=config_path,
    )

    assert result["status"] == "blocked"
    assert result["authority_boundary"] == "review_required_before_promotion"
    assert (
        result["next_action"] == "commit/stash current changes or use a clean worktree, then retry"
    )


def test_propose_authority_change_rejects_traversal_duplicate_and_missing_replace(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    commit_all(repo, "initial")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    traversal = propose_authority_change_from_config(
        title="Bad traversal",
        rationale="should reject",
        changes=[{"operation": "add_file", "path": "../escape.md", "content": "x"}],
        config_path=config_path,
    )
    assert traversal["status"] == "error"
    assert traversal["error"]["code"] == "INVALID_TARGET"

    outside = tmp_path / "outside-authority-proposal.md"
    link = repo / "docs" / "decisions" / "dangling.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    commit_all(repo, "tracked dangling symlink")
    symlink = propose_authority_change_from_config(
        title="Bad symlink",
        rationale="should reject",
        changes=[{"operation": "add_file", "path": "docs/decisions/dangling.md", "content": "x"}],
        config_path=config_path,
    )
    assert symlink["status"] == "error"
    assert symlink["error"]["code"] == "INVALID_TARGET"
    assert not outside.exists()

    outside_hardlink = tmp_path / "outside-hardlink.md"
    outside_hardlink.write_text("external original\n", encoding="utf-8")
    hardlink = repo / "docs" / "specs" / "hardlinked.md"
    hardlink.parent.mkdir(parents=True, exist_ok=True)
    hardlink.hardlink_to(outside_hardlink)
    commit_all(repo, "tracked hardlink file")
    hardlink_result = propose_authority_change_from_config(
        title="Bad hardlink",
        rationale="should reject",
        changes=[
            {
                "operation": "replace_file",
                "path": "docs/specs/hardlinked.md",
                "content": "changed\n",
            }
        ],
        config_path=config_path,
    )
    assert hardlink_result["status"] == "error"
    assert hardlink_result["error"]["code"] == "INVALID_TARGET"
    assert outside_hardlink.read_text(encoding="utf-8") == "external original\n"

    missing = propose_authority_change_from_config(
        title="Missing replace",
        rationale="should reject",
        changes=[{"operation": "replace_file", "path": "docs/missing.md", "content": "x"}],
        config_path=config_path,
    )
    assert missing["status"] == "error"
    assert missing["error"]["code"] == "INVALID_CHANGES"

    duplicate = propose_authority_change_from_config(
        title="Duplicate",
        rationale="should reject",
        changes=[
            {"operation": "add_file", "path": "docs/new.md", "content": "x"},
            {"operation": "add_file", "path": "docs/new.md", "content": "y"},
        ],
        config_path=config_path,
    )
    assert duplicate["status"] == "error"
    assert duplicate["error"]["code"] == "INVALID_CHANGES"
