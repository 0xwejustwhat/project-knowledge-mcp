from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

from project_knowledge_mcp.index import ProjectIndex, index_repo
from project_knowledge_mcp.services import (
    add_project_note_from_config,
    create_draft_artifact_from_config,
)


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)


def write_doc(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).strip() + "\n", encoding="utf-8")


def write_project_config(root: Path, *, repo: Path, state_dir: Path) -> Path:
    config_path = root / "project.yaml"
    config_path.write_text(
        f"""
schema_version: 1
project:
  id: step7-test
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


def test_add_project_note_writes_capture_frontmatter_and_single_document_indexes(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    write_doc(
        repo,
        "docs/doctrine/current.md",
        """
        ---
        title: Current Doctrine
        type: doctrine
        status: current
        authority: canonical
        ---
        # Current Doctrine

        Existing canonical context remains indexed.
        """,
    )
    index_repo(repo, state_dir=state, repo_id="ops", role="ops")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    result = add_project_note_from_config(
        title="Fresh Capture",
        body="A uniquely searchable step seven safe-write note.",
        tags=["step7"],
        source="pytest",
        config_path=config_path,
    )

    assert result["status"] == "written"
    assert result["authority"] == "capture"
    assert result["indexed"] is True
    assert result["index_scope"] == "single_document"
    note_path = repo / result["path"]
    assert note_path.exists()
    text = note_path.read_text(encoding="utf-8")
    assert "authority: capture" in text
    assert "status: captured" in text

    index = ProjectIndex.open(state)
    note_results = index.search("uniquely searchable safe-write", filters={"repo_id": "ops"})
    assert note_results[0].path == result["path"]
    assert note_results[0].authority == "capture"
    existing = index.search("Existing canonical context", filters={"repo_id": "ops"})
    assert existing[0].path == "docs/doctrine/current.md"


def test_add_project_note_open_question_is_safe_working_authority(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    result = add_project_note_from_config(
        title="Needs Clarification",
        body="What should the authority boundary do here?",
        type="open_question",
        tags=["step7"],
        config_path=config_path,
    )

    assert result["status"] == "written"
    assert result["authority"] == "working"
    note_text = (repo / result["path"]).read_text(encoding="utf-8")
    assert "type: open_question" in note_text
    assert "status: open" in note_text
    assert "authority: working" in note_text

    index = ProjectIndex.open(state)
    indexed = index.search("authority boundary", filters={"repo_id": "ops"})
    assert indexed[0].path == result["path"]
    assert indexed[0].authority == "working"


def test_add_project_note_blocks_canonical_targets_with_proposal_actions(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    result = add_project_note_from_config(
        title="Bad Canonical Write",
        body="should not write",
        target="docs/doctrine/current.md",
        config_path=config_path,
    )

    assert result["status"] == "blocked"
    assert result["authority_boundary"] == "review_required_before_promotion"
    assert result["suggested_actions"] == ["create_draft_artifact", "propose_authority_change"]
    assert not (repo / "docs/doctrine/current.md").exists()

    decision = add_project_note_from_config(
        title="Bad Decision Write",
        body="should not write",
        target="docs/decisions/0004-direct.md",
        config_path=config_path,
    )
    assert decision["status"] == "blocked"
    assert not (repo / "docs/decisions/0004-direct.md").exists()


def test_create_draft_artifact_constrains_targets_and_rejects_traversal_symlink(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    written = create_draft_artifact_from_config(
        kind="doctrine_delta",
        title="Reviewable Change",
        body="Draft only; not canonical.",
        config_path=config_path,
    )
    assert written["status"] == "written"
    assert written["authority"] == "proposal"
    assert written["path"].startswith("docs/proposals/doctrine-deltas/")
    assert written["warnings"] == []

    blocked = create_draft_artifact_from_config(
        kind="doctrine_delta",
        title="Wrong Dir",
        body="no",
        target="docs/notes/wrong.md",
        config_path=config_path,
    )
    assert blocked["status"] == "blocked"

    traversal = add_project_note_from_config(
        title="Traversal",
        body="no",
        target="../escape.md",
        config_path=config_path,
    )
    assert traversal["status"] == "error"

    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "linked").symlink_to(tmp_path)
    symlink = add_project_note_from_config(
        title="Symlink Escape",
        body="no",
        target="docs/linked/escape.md",
        config_path=config_path,
    )
    assert symlink["status"] == "error"
    assert symlink["error"]["code"] == "INVALID_TARGET"

    dangling_target = tmp_path / "outside-created-by-broken-symlink.md"
    dangling_link = repo / "docs" / "notes" / "dangling.md"
    dangling_link.parent.mkdir(parents=True, exist_ok=True)
    dangling_link.symlink_to(dangling_target)
    dangling = add_project_note_from_config(
        title="Dangling Symlink Escape",
        body="no",
        target="docs/notes/dangling.md",
        config_path=config_path,
    )
    assert dangling["status"] == "error"
    assert dangling["error"]["code"] == "INVALID_TARGET"
    assert not dangling_target.exists()
