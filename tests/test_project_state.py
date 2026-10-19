from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

from project_knowledge_mcp.services import get_project_state_from_config, index_project_from_config


def write_doc(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).strip() + "\n", encoding="utf-8")


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "init"], cwd=path, check=True)


def write_project_config(root: Path, *, ops: Path, work: Path, state: Path) -> Path:
    path = root / "project.yaml"
    path.write_text(
        f"""
schema_version: 1
project:
  id: test-project
  name: Test Project
storage:
  project_root: {root.as_posix()}
  state_dir: {state.as_posix()}
repos:
  - id: ops
    role: ops
    name: Ops
    path: {ops.as_posix()}
    writable: true
    include_globs: ["README.md", "docs/**/*.md", "*.md"]
    exclude_globs: [".git/**", ".project-knowledge/**"]
  - id: work
    role: work
    name: Work
    path: {work.as_posix()}
    writable: false
    include_globs: ["src/**/*.py", "*.py"]
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
    return path


def project_fixture(tmp_path: Path) -> Path:
    ops = tmp_path / "ops"
    work = tmp_path / "work"
    write_doc(
        ops,
        "docs/doctrine/retrieval.md",
        """
        ---
        title: Retrieval Doctrine
        type: doctrine
        status: current
        authority: canonical
        ---
        # Retrieval Doctrine
        Project state retrieval keeps authority boundaries explicit.
        """,
    )
    write_doc(
        ops,
        "docs/decisions/accepted/retrieval.md",
        """
        ---
        title: Retrieval Decision
        type: decision
        status: accepted
        authority: accepted_decision
        ---
        # Retrieval Decision
        Project state retrieval requires an explicit view.
        """,
    )
    write_doc(
        ops,
        "docs/open-questions/retrieval.md",
        """
        ---
        title: Retrieval Question
        type: open_question
        status: open
        authority: working
        ---
        # Retrieval Question
        How should project state retrieval show partial evidence?
        """,
    )
    write_doc(
        ops,
        "docs/proposals/retrieval.md",
        """
        ---
        title: Retrieval Proposal
        type: proposal
        status: draft
        authority: proposal
        ---
        # Retrieval Proposal
        A project state retrieval proposal remains non-authoritative.
        """,
    )
    write_doc(
        ops,
        "docs/proposals/decision-retrieval.md",
        """
        ---
        title: Retrieval Decision Proposal
        type: decision_proposal
        status: draft
        authority: proposal
        ---
        # Retrieval Decision Proposal
        A PKMCP-created decision proposal about project state retrieval.
        """,
    )
    write_doc(
        ops,
        "docs/notes/retrieval.md",
        """
        ---
        title: Retrieval Capture
        type: note
        status: captured
        authority: capture
        ---
        # Retrieval Capture
        Informal project state retrieval discussion and capture.
        """,
    )
    write_doc(
        ops,
        "docs/discussions/old-retrieval.md",
        """
        ---
        title: Superseded Retrieval Discussion
        type: discussion
        status: superseded
        authority: superseded
        superseded_by: docs/doctrine/retrieval.md
        ---
        # Superseded Retrieval Discussion
        Project state retrieval project state retrieval old discussion.
        """,
    )
    write_doc(
        work,
        "src/retrieval.py",
        """
        def get_project_state():
            '''Compile project state retrieval evidence.'''
            return "project state retrieval"
        """,
    )
    init_git_repo(ops)
    init_git_repo(work)
    config = write_project_config(tmp_path, ops=ops, work=work, state=tmp_path / "state")
    assert index_project_from_config(config_path=config)["status"] == "ok"
    return config


def test_doctrine_view_excludes_informal_sections(tmp_path: Path) -> None:
    config = project_fixture(tmp_path)

    packet = get_project_state_from_config(
        topic="project state retrieval", view="doctrine", config_path=config
    )

    assert packet["view"] == "doctrine"
    assert packet["sections"]["doctrine"]["retrieval_state"] == "complete"
    assert packet["sections"]["doctrine"]["match_state"] == "with_results"
    assert packet["sections"]["accepted_decisions"]["match_state"] == "with_results"
    assert packet["sections"]["open_questions"]["retrieval_state"] == "not_run"
    assert packet["sections"]["open_questions"]["results"] == []
    assert packet["sections"]["proposals_and_drafts"]["retrieval_state"] == "not_run"
    assert packet["sections"]["discussions_and_captures"]["retrieval_state"] == "not_run"
    assert packet["sections"]["implementation_evidence"]["results"]
    assert all(
        item["authority"] == "canonical"
        for item in packet["sections"]["doctrine"]["results"]
    )
    assert "## Open questions" not in packet["markdown"]


def test_doctrine_and_discussion_keeps_authority_classes_separate(tmp_path: Path) -> None:
    config = project_fixture(tmp_path)

    packet = get_project_state_from_config(
        topic="project state retrieval", view="doctrine_and_discussion", config_path=config
    )

    assert packet["sections"]["open_questions"]["results"][0]["doc_type"] == "open_question"
    assert packet["sections"]["proposals_and_drafts"]["results"][0]["authority"] == (
        "proposal"
    )
    assert {
        item["doc_type"] for item in packet["sections"]["proposals_and_drafts"]["results"]
    } >= {"proposal", "decision_proposal"}
    captures = packet["sections"]["discussions_and_captures"]["results"]
    assert captures and all(item["authority"] not in {"canonical", "accepted_decision"} for item in captures)
    assert "## Proposals and drafts — not authoritative" in packet["markdown"]


def test_no_authoritative_match_is_not_reported_as_retrieval_failure(tmp_path: Path) -> None:
    config = project_fixture(tmp_path)

    packet = get_project_state_from_config(
        topic="topic absent from authority", view="doctrine", config_path=config
    )

    assert packet["sections"]["doctrine"]["retrieval_state"] == "complete"
    assert packet["sections"]["doctrine"]["match_state"] == "no_match"


def test_unavailable_index_is_distinct_from_no_match(tmp_path: Path) -> None:
    ops = tmp_path / "ops"
    work = tmp_path / "work"
    init_git_repo(ops)
    init_git_repo(work)
    config = write_project_config(tmp_path, ops=ops, work=work, state=tmp_path / "missing-state")

    packet = get_project_state_from_config(
        topic="project state retrieval", view="doctrine", config_path=config
    )

    assert packet["sections"]["doctrine"]["retrieval_state"] == "unavailable"
    assert packet["sections"]["doctrine"]["match_state"] == "indeterminate"


def test_invalid_view_is_rejected(tmp_path: Path) -> None:
    config = project_fixture(tmp_path)

    packet = get_project_state_from_config(
        topic="project state retrieval", view="everything", config_path=config  # type: ignore[arg-type]
    )

    assert packet["error"]["code"] == "QUERY_INVALID"
    assert packet["sections"] == {}


def test_superseded_material_is_separate_and_explicitly_requested(tmp_path: Path) -> None:
    config = project_fixture(tmp_path)

    default_packet = get_project_state_from_config(
        topic="project state retrieval",
        view="doctrine_and_discussion",
        config_path=config,
    )
    assert "superseded_and_rejected" not in default_packet["sections"]
    assert all(
        item.get("status") != "superseded"
        for section in default_packet["sections"].values()
        for item in section["results"]
    )

    history_packet = get_project_state_from_config(
        topic="project state retrieval",
        view="doctrine_and_discussion",
        include_superseded=True,
        config_path=config,
    )
    history = history_packet["sections"]["superseded_and_rejected"]
    assert history["authority_class"] == "superseded"
    assert history["results"][0]["status"] == "superseded"
    assert "## Superseded and rejected — not authoritative" in history_packet["markdown"]


def test_stale_index_is_partial_and_no_match_is_indeterminate(tmp_path: Path) -> None:
    config = project_fixture(tmp_path)
    write_doc(config.parent / "ops", "docs/notes/new.md", "# New unindexed retrieval note")

    packet = get_project_state_from_config(
        topic="absent stale topic", view="doctrine", config_path=config
    )

    doctrine = packet["sections"]["doctrine"]
    assert doctrine["retrieval_state"] == "partial"
    assert doctrine["freshness_state"] == "stale"
    assert doctrine["match_state"] == "indeterminate"
    assert packet["overall_retrieval_state"] == "partial"
