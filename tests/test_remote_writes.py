"""Remote authorship uses real Git remotes, never the managed read checkout."""

import subprocess
from pathlib import Path

import pytest

from project_knowledge_mcp import services
from project_knowledge_mcp.config import load_project_config
from project_knowledge_mcp.index import ProjectIndex
from test_write_policy import add_bare_remote, init_git_repo, write_doc, write_project_config


def git(path, *args):
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def remote_project(tmp_path):
    seed = tmp_path / "seed"
    remote = tmp_path / "remote.git"
    state = tmp_path / "state"
    init_git_repo(seed)
    write_doc(seed, "README.md", "# Canonical baseline")
    add_bare_remote(seed, remote)
    path = write_project_config(tmp_path, repo=seed, state_dir=state)
    path.write_text(
        path.read_text().replace(
            f"path: {seed}",
            f"source_mode: remote\n    remote_url: {remote}\n    canonical_branch: main",
        )
    )
    config = load_project_config(path)
    services.sync_project(config)
    managed = config.ops_repo.path
    git(managed, "config", "user.name", "Test User")
    git(managed, "config", "user.email", "test@example.com")
    return path, config, seed, remote


def assert_clean(config):
    managed = config.ops_repo.path
    assert git(managed, "status", "--porcelain") == ""
    assert git(managed, "branch", "--show-current") == "main"
    assert len(git(managed, "worktree", "list", "--porcelain").split("worktree ")) == 2
    assert not list((config.storage.state_dir / "tmp").glob("pkmcp-*"))


def test_remote_note_push_syncs_and_is_retrievable(remote_project):
    path, config, _, remote = remote_project
    result = services.add_project_note_from_config(
        title="Remote capture", body="quartz lighthouse capture", config_path=path
    )
    assert result["status"] == "written_and_pushed"
    assert result["indexed"] is True
    assert result["index_scope"] == "repository"
    assert result["freshness"]["reindex_needed"] is False
    assert result["url"].endswith(f"/blob/main/{result['path']}")
    assert "quartz lighthouse" in git(remote, "show", f"main:{result['path']}")
    hits = ProjectIndex.open(config.storage.state_dir).search("quartz lighthouse")
    assert hits[0].path == result["path"]
    assert_clean(config)


def test_remote_draft_push_syncs_and_preserves_proposal_metadata(remote_project):
    path, config, _, remote = remote_project
    result = services.create_draft_artifact_from_config(
        kind="doctrine_delta",
        title="Remote draft",
        body="amber telescope proposal",
        tags=["review"],
        source="pytest",
        config_path=path,
    )
    assert result["status"] == "written_and_pushed"
    assert result["indexed"] is True
    assert result["index_scope"] == "repository"
    assert result["authority"] == "proposal"
    assert result["authority_boundary"] == "review_required_before_promotion"
    assert result["suggested_actions"] == ["propose_authority_change"]
    assert result["url"].endswith(f"/blob/main/{result['path']}")
    content = git(remote, "show", f"main:{result['path']}")
    assert "authority: proposal" in content
    assert "status: draft" in content
    assert "source: pytest" in content
    hits = ProjectIndex.open(config.storage.state_dir).search("amber telescope")
    assert hits[0].path == result["path"]
    assert_clean(config)


@pytest.mark.parametrize("tool", ["note", "draft", "authority"])
def test_remote_local_only_rejected(remote_project, tool):
    path, config, _, _ = remote_project
    path.write_text(
        path.read_text().replace("capture_git_mode: direct_push", "capture_git_mode: local_only")
    )
    if tool == "note":
        result = services.add_project_note_from_config(
            title="Forbidden", body="no", config_path=path
        )
    elif tool == "draft":
        result = services.create_draft_artifact_from_config(
            kind="doctrine_delta", title="Forbidden", body="no", config_path=path
        )
    else:
        result = propose(path)
    assert result["status"] == "error"
    assert result["error"]["code"] == "WRITE_POLICY_DENIED"
    assert "local_only" in result["error"]["message"]
    assert_clean(config)


@pytest.fixture
def gh(monkeypatch):
    original = subprocess.run
    calls = []

    def run(args, *pos, **kw):
        if args[0] == "gh":
            calls.append(args)
            return subprocess.CompletedProcess(
                args, 0, stdout="https://github.com/example/ops/pull/1\n", stderr=""
            )
        return original(args, *pos, **kw)

    monkeypatch.setattr(services, "_command_exists", lambda cmd: cmd == "gh")
    monkeypatch.setattr(subprocess, "run", run)
    return calls


def propose(path):
    return services.propose_authority_change_from_config(
        title="Canonical proposal",
        rationale="Caller supplied full files",
        changes=[
            {
                "operation": "replace_file",
                "path": "docs/specs/new.md",
                "content": "proposed full file\n",
            }
        ],
        branch_name="pkmcp/authority-proposal/remote",
        config_path=path,
    )


def advance(seed):
    write_doc(seed, "docs/specs/new.md", "canonical original")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "Out of band canonical advance")
    git(seed, "push", "origin", "main")


def test_remote_authority_uses_fetched_canonical_worktree(remote_project, gh):
    path, config, seed, remote = remote_project
    head = git(config.ops_repo.path, "rev-parse", "HEAD")
    advance(seed)
    result = propose(path)
    assert result["status"] == "pr_opened"
    assert result["authority_boundary"] == "review_required_before_promotion"
    assert git(remote, "show", f"{result['branch']}:docs/specs/new.md") == "proposed full file"
    assert git(remote, "show", "main:docs/specs/new.md") == "canonical original"
    assert git(config.ops_repo.path, "rev-parse", "HEAD") == head
    assert result["branch"] not in git(config.ops_repo.path, "branch", "--list")
    assert gh[-1][:3] == ["gh", "pr", "create"]
    assert_clean(config)


@pytest.mark.parametrize("failure", ["commit", "push"])
def test_remote_authority_failure_cleans_up(remote_project, gh, failure):
    path, config, seed, remote = remote_project
    advance(seed)
    head = git(config.ops_repo.path, "rev-parse", "HEAD")
    if failure == "commit":
        git(config.ops_repo.path, "config", "user.name", "")
    else:
        hook = remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
    result = propose(path)
    assert result["status"] == "error"
    assert result["error"]["code"] == f"GIT_{failure.upper()}_FAILED"
    assert git(config.ops_repo.path, "rev-parse", "HEAD") == head
    assert "pkmcp/authority-proposal/remote" not in git(config.ops_repo.path, "branch", "--list")
    assert "pkmcp/authority-proposal/remote" not in git(remote, "branch", "--list")
    assert_clean(config)


@pytest.mark.parametrize("tool", ["note", "draft"])
@pytest.mark.parametrize("failure", ["commit", "push"])
def test_remote_capture_failure_is_clean_and_does_not_expose_stderr(remote_project, tool, failure):
    path, config, _, remote = remote_project
    head = git(config.ops_repo.path, "rev-parse", "HEAD")
    if failure == "commit":
        git(config.ops_repo.path, "config", "user.name", "")
    else:
        hook = remote / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\necho 'https://user:credential-sentinel@example.com/repo' >&2\nexit 1\n"
        )
        hook.chmod(0o755)
    if tool == "note":
        result = services.add_project_note_from_config(title="Failure", body="no", config_path=path)
    else:
        result = services.create_draft_artifact_from_config(
            kind="doctrine_delta", title="Failure", body="no", config_path=path
        )
    assert result["status"] == ("push_failed" if failure == "commit" else "sync_conflict")
    assert result["indexed"] is False
    assert "credential-sentinel" not in str(result)
    assert "_push_result" not in result
    assert git(config.ops_repo.path, "rev-parse", "HEAD") == head
    assert_clean(config)


def test_remote_capture_selects_own_freshness(remote_project):
    path, _, seed, _ = remote_project
    path.write_text(
        path.read_text().replace(
            "repos:\n", f"repos:\n  - id: work\n    role: work\n    path: {seed}\n"
        )
    )
    result = services.add_project_note_from_config(
        title="Targeted", body="targeted freshness", config_path=path
    )
    assert result["indexed"] is True
    assert result["freshness"]["repo_id"] == "ops"


def test_durable_url_excludes_credentials():
    url = services._capture_durable_url(
        "https://user:credential-sentinel@example.com/ops.git?token=hidden", "main", "docs/note.md"
    )
    assert url == "https://example.com/ops.git/blob/main/docs/note.md"


def test_remote_capture_reports_sync_failure_after_durable_push(remote_project):
    path, config, _, remote = remote_project
    head = git(config.ops_repo.path, "rev-parse", "HEAD")
    hook = remote / "hooks" / "post-receive"
    hook.write_text('#!/bin/sh\nmv "$PWD" "$PWD.offline"\n')
    hook.chmod(0o755)
    result = services.add_project_note_from_config(
        title="Offline after push", body="durable offline marker", config_path=path
    )
    assert result["status"] == "written_and_pushed"
    assert result["indexed"] is False
    assert result["full_reindex_required"] is True
    assert result["freshness"]["reindex_needed"] is True
    assert result["warnings"]
    assert "durable offline marker" in git(
        Path(str(remote) + ".offline"), "show", f"main:{result['path']}"
    )
    assert git(config.ops_repo.path, "rev-parse", "HEAD") == head
    assert_clean(config)


def test_remote_capture_retries_real_remote_advancement(remote_project):
    path, config, seed, remote = remote_project
    write_doc(seed, "docs/notes/concurrent.md", "concurrent canonical note")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "Concurrent advance")
    git(seed, "push", "origin", "HEAD:race")
    hook = remote / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\nrm hooks/pre-receive\nenv -u GIT_QUARANTINE_PATH git update-ref refs/heads/main refs/heads/race\n"
    )
    hook.chmod(0o755)
    result = services.add_project_note_from_config(
        title="Retry", body="retry marker", config_path=path
    )
    assert result["status"] == "written_and_pushed"
    assert result["indexed"] is True
    assert git(remote, "show", "main:docs/notes/concurrent.md") == "concurrent canonical note"
    assert_clean(config)


def test_remote_authority_pushes_without_gh_auth(remote_project, monkeypatch):
    path, config, seed, remote = remote_project
    advance(seed)
    original = subprocess.run

    def run(args, *pos, **kw):
        if args[0] == "gh":
            assert args[:3] == ["gh", "auth", "status"]
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="not authenticated")
        return original(args, *pos, **kw)

    monkeypatch.setattr(services, "_command_exists", lambda cmd: cmd == "gh")
    monkeypatch.setattr(subprocess, "run", run)
    result = propose(path)
    assert result["status"] == "branch_prepared_pr_not_opened"
    assert result["next_action"] == "open PR manually"
    assert git(remote, "show", f"{result['branch']}:docs/specs/new.md") == "proposed full file"
    assert_clean(config)


@pytest.mark.parametrize("tool", ["note", "draft", "authority"])
def test_remote_rejects_canonical_symlink_targets(remote_project, tool, gh):
    path, config, seed, _ = remote_project
    target = "docs/proposals/doctrine-deltas/link.md"
    link = seed / target
    link.parent.mkdir(parents=True)
    link.symlink_to("../../../README.md")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "Canonical symlink")
    git(seed, "push", "origin", "main")
    if tool == "note":
        result = services.add_project_note_from_config(
            title="Link", body="no", target=target, config_path=path
        )
    elif tool == "draft":
        result = services.create_draft_artifact_from_config(
            kind="doctrine_delta", title="Link", body="no", target=target, config_path=path
        )
    else:
        result = services.propose_authority_change_from_config(
            title="Link",
            rationale="no",
            changes=[{"operation": "replace_file", "path": target, "content": "no"}],
            config_path=path,
        )
    assert result["error"]["code"] == "INVALID_TARGET"
    assert git(config.ops_repo.path, "show", "HEAD:README.md") == "# Canonical baseline"
    assert_clean(config)


def test_remote_capture_does_not_claim_excluded_document_is_indexed(remote_project):
    path, config, _, remote = remote_project
    path.write_text(
        path.read_text().replace(
            'include_globs: ["docs/**/*.md", "*.md"]', 'include_globs: ["README.md"]'
        )
    )
    result = services.add_project_note_from_config(
        title="Excluded", body="excluded marker", config_path=path
    )
    assert result["status"] == "written_and_pushed"
    assert result["indexed"] is False
    assert result["warnings"]
    assert result["freshness"]["reindex_needed"] is False
    assert "excluded marker" in git(remote, "show", f"main:{result['path']}")
    assert_clean(config)
