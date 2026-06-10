from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

from project_knowledge_mcp.index import ProjectIndex, index_repo
from project_knowledge_mcp.services import (
    get_current_doctrine_from_config,
    index_project_from_config,
    search_decisions_from_config,
    search_open_questions_from_config,
)


def write_doc(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).strip() + "\n", encoding="utf-8")


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)


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


def test_sqlite_bm25_relevance_is_normalized_and_authority_ranked(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / "state"
    write_doc(
        repo,
        "docs/doctrine/context.md",
        """
        ---
        title: Canonical Context Doctrine
        type: doctrine
        status: current
        authority: canonical
        ---
        # Canonical Context Doctrine

        Retrieval authority ranking chooses current truth for context.
        """,
    )
    write_doc(
        repo,
        "docs/notes/raw-context.md",
        """
        ---
        title: Raw Context Capture
        type: note
        status: captured
        authority: capture
        ---
        # Raw Context Capture

        context context context context context retrieval authority ranking raw notes.
        """,
    )
    write_doc(
        repo,
        "docs/discussions/superseded-context.md",
        """
        ---
        title: Superseded Context Discussion
        type: discussion
        status: superseded
        authority: superseded
        superseded_by: docs/doctrine/context.md
        ---
        # Superseded Context Discussion

        context context context context context retrieval authority ranking old idea.
        """,
    )

    index_repo(repo, state_dir=state, repo_id="ops", role="ops")

    results = ProjectIndex.open(state).search("context retrieval authority ranking", limit=5)

    assert results[0].path == "docs/doctrine/context.md"
    assert results[0].authority == "canonical"
    assert all(0.0 <= result.relevance_score <= 1.0 for result in results)
    assert [result.final_score for result in results] == sorted(
        [result.final_score for result in results], reverse=True
    )
    assert "docs/discussions/superseded-context.md" not in [result.path for result in results]

    with_superseded = ProjectIndex.open(state).search(
        "context retrieval authority ranking", include_superseded=True, limit=5
    )
    assert with_superseded[0].path == "docs/doctrine/context.md"
    superseded = next(
        result
        for result in with_superseded
        if result.path == "docs/discussions/superseded-context.md"
    )
    assert superseded.relevance_score <= 1.0
    assert superseded.final_score < with_superseded[0].final_score
    assert superseded.superseded_by == ["docs/doctrine/context.md"]


def test_candidate_fetch_uses_bm25_before_authority_post_ranking(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / "state"
    for index in range(50):
        write_doc(
            repo,
            f"docs/notes/{index:02d}.md",
            """
            ---
            title: Low Authority Note
            type: note
            status: captured
            authority: capture
            ---
            # Low Authority Note

            Context retrieval background note.
            """,
        )
    write_doc(
        repo,
        "docs/doctrine/zz-canonical.md",
        """
        ---
        title: Canonical Retrieval Context
        type: doctrine
        status: current
        authority: canonical
        ---
        # Canonical Retrieval Context

        Context retrieval context retrieval canonical current truth.
        """,
    )

    index_repo(repo, state_dir=state, repo_id="ops", role="ops")

    results = ProjectIndex.open(state).search("context retrieval", limit=5)

    assert results[0].path == "docs/doctrine/zz-canonical.md"


def test_phase4_service_tools_scope_and_rank_typed_results(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    write_doc(
        repo,
        "docs/doctrine/context.md",
        """
        ---
        title: Context Doctrine
        type: doctrine
        status: current
        authority: canonical
        tags: [context]
        ---
        # Context Doctrine

        Context retrieval truth is grounded in canonical doctrine and accepted decisions.
        """,
    )
    write_doc(
        repo,
        "docs/decisions/accepted/0001-context.md",
        """
        ---
        title: Accepted Context Decision
        type: decision
        status: accepted
        authority: accepted_decision
        tags: [context]
        ---
        # Accepted Context Decision

        Accepted decisions explain the context retrieval design rationale.
        """,
    )
    write_doc(
        repo,
        "docs/decisions/draft/0002-context.md",
        """
        ---
        title: Draft Context Decision
        type: decision
        status: draft
        authority: working
        tags: [context]
        ---
        # Draft Context Decision

        Draft decisions discuss context retrieval alternatives.
        """,
    )
    write_doc(
        repo,
        "docs/decisions/accepted/0000-old-context.md",
        """
        ---
        title: Old Context Decision
        type: decision
        status: superseded
        authority: superseded
        superseded_by: docs/decisions/accepted/0001-context.md
        tags: [context]
        ---
        # Old Context Decision

        Old superseded context retrieval decision.
        """,
    )
    write_doc(
        repo,
        "docs/open-questions/context.md",
        """
        ---
        title: Context Open Question
        type: open_question
        status: open
        authority: working
        owner: Amin
        related_docs: [docs/doctrine/context.md]
        tags: [context]
        ---
        # Context Open Question

        How should context retrieval surface unresolved questions?
        """,
    )
    write_doc(
        repo,
        "docs/notes/raw-context.md",
        "# Raw Context\n\nRaw context note should not appear in typed decision results.",
    )
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    indexed = index_project_from_config(config_path=config_path)
    assert indexed["status"] == "ok"

    decisions = search_decisions_from_config(query="context retrieval", config_path=config_path)
    assert [result["doc_type"] for result in decisions["results"]] == ["decision", "decision"]
    assert decisions["results"][0]["path"] == "docs/decisions/accepted/0001-context.md"
    assert decisions["results"][0]["authority"] == "accepted_decision"
    assert all(result["status"] != "superseded" for result in decisions["results"])

    with_superseded = search_decisions_from_config(
        query="context retrieval",
        config_path=config_path,
        filters={"include_superseded": True},
        limit=5,
    )
    superseded = next(
        result for result in with_superseded["results"] if result["status"] == "superseded"
    )
    assert superseded["superseded_by"] == ["docs/decisions/accepted/0001-context.md"]
    assert superseded["warnings"]

    doctrine = get_current_doctrine_from_config(topic="context retrieval", config_path=config_path)
    assert doctrine["topic"] == "context retrieval"
    assert doctrine["doctrine"][0]["path"] == "docs/doctrine/context.md"
    assert doctrine["doctrine"][0]["authority"] == "canonical"
    assert doctrine["decisions"][0]["path"] == "docs/decisions/accepted/0001-context.md"
    assert doctrine["results"] == [*doctrine["doctrine"], *doctrine["decisions"]]
    assert "## Current Doctrine" in doctrine["markdown"]

    questions = search_open_questions_from_config(
        query="context retrieval", config_path=config_path
    )
    assert [result["doc_type"] for result in questions["results"]] == ["open_question"]
    assert questions["results"][0]["owner"] == "Amin"
    assert questions["results"][0]["related_docs"] == ["docs/doctrine/context.md"]

    invalid_scope = search_decisions_from_config(
        query="context", config_path=config_path, filters={"repo_id": "work"}
    )
    assert invalid_scope["results"] == []
    assert invalid_scope["error"]["code"] == "QUERY_INVALID"

    invalid_type = search_decisions_from_config(
        query="context", config_path=config_path, filters={"doc_type": "note"}
    )
    assert invalid_type["results"] == []
    assert invalid_type["error"]["code"] == "QUERY_INVALID"

    invalid_type_alias_conflict = search_decisions_from_config(
        query="context",
        config_path=config_path,
        filters={"type": "decision", "doc_type": "note"},
    )
    assert invalid_type_alias_conflict["results"] == []
    assert invalid_type_alias_conflict["error"]["code"] == "QUERY_INVALID"

    invalid_superseded_flag = search_decisions_from_config(
        query="context", config_path=config_path, filters={"include_superseded": "false"}
    )
    assert invalid_superseded_flag["results"] == []
    assert invalid_superseded_flag["error"]["code"] == "QUERY_INVALID"

    invalid_doctrine_status = get_current_doctrine_from_config(
        topic="context", config_path=config_path, filters={"status": "draft"}
    )
    assert invalid_doctrine_status["results"] == []
    assert invalid_doctrine_status["error"]["code"] == "QUERY_INVALID"

    invalid_filter = search_open_questions_from_config(
        query="context", config_path=config_path, filters={"status": ["open"]}
    )
    assert invalid_filter["results"] == []
    assert invalid_filter["error"]["code"] == "QUERY_INVALID"
