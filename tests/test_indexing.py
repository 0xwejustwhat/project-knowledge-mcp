from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from project_knowledge_mcp.index import ProjectIndex, index_repo


def write_doc(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).strip() + "\n", encoding="utf-8")


def test_step1_drift_regression_current_sqlite_decision_outranks_superseded_llamaindex(
    tmp_path: Path,
):
    repo = tmp_path / "ops"
    state = tmp_path / "state"
    write_doc(
        repo,
        "docs/discussions/2026-06-09-local-parser-sqlite-codegraph-decision.md",
        """
        ---
        title: Current retrieval architecture
        type: decision
        status: current
        authority: canonical
        tags: [retrieval, sqlite, mvp]
        ---
        # Current retrieval architecture

        The current default document retrieval architecture is SQLite FTS5/BM25
        with local Markdown/text parsers. LlamaIndex, LlamaParse, and LlamaCloud
        are not default MVP dependencies. Typed evidence compilers and authority
        ranking determine current truth.
        """,
    )
    write_doc(
        repo,
        "docs/discussions/2026-06-09-llamaindex-codegraph-retrieval-decision.md",
        """
        ---
        title: Superseded LlamaIndex retrieval draft
        type: discussion
        status: superseded
        authority: superseded
        superseded_by: docs/discussions/2026-06-09-local-parser-sqlite-codegraph-decision.md
        tags: [retrieval, llamaindex, mvp]
        ---
        # Superseded LlamaIndex retrieval draft

        The default document retrieval architecture should use LlamaIndex and
        LlamaParse as the core project-memory retrieval path. This old draft is
        superseded by the SQLite FTS5/BM25 local-parser decision.
        """,
    )
    write_doc(
        repo,
        "docs/notes/raw-capture.md",
        """
        ---
        title: Raw retrieval capture
        type: note
        status: captured
        authority: capture
        tags: [retrieval]
        ---
        # Raw retrieval capture

        SQLite FTS5/BM25 and LlamaIndex were both discussed as default document
        retrieval architecture options.
        """,
    )

    index_repo(repo, state_dir=state, repo_id="ops", role="ops")

    results = ProjectIndex.open(state).search("default document retrieval architecture", limit=5)

    assert [result.path for result in results] == [
        "docs/discussions/2026-06-09-local-parser-sqlite-codegraph-decision.md",
        "docs/notes/raw-capture.md",
    ]
    assert results[0].authority == "canonical"
    assert results[0].final_score > results[1].final_score
    assert all(result.authority != "superseded" for result in results)

    historical = ProjectIndex.open(state).search(
        "default document retrieval architecture", include_superseded=True, limit=5
    )
    superseded = [
        result
        for result in historical
        if result.path == "docs/discussions/2026-06-09-llamaindex-codegraph-retrieval-decision.md"
    ]
    assert superseded
    assert superseded[0].authority == "superseded"
    assert superseded[0].superseded_by == [
        "docs/discussions/2026-06-09-local-parser-sqlite-codegraph-decision.md"
    ]
    assert (
        historical[0].path
        == "docs/discussions/2026-06-09-local-parser-sqlite-codegraph-decision.md"
    )


def test_index_persists_metadata_chunks_and_normalization_warnings(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / "state"
    write_doc(
        repo,
        "docs/decisions/accepted/0001-context-compiler.md",
        """
        ---
        title: Context compiler boundary
        type: decision
        status: accepted
        authority: accepted_decision
        date: 2026-06-09
        tags: [briefs, evidence]
        ---
        # Context compiler boundary

        The MCP server returns evidence packets. The assistant synthesizes prose.
        """,
    )
    write_doc(
        repo,
        "docs/decisions/typo-status.md",
        """
        ---
        title: Typo status example
        type: decision
        status: depreciated
        tags: [warnings]
        ---
        # Typo status example

        Unknown frontmatter values should not crash indexing.
        """,
    )

    summary = index_repo(repo, state_dir=state, repo_id="ops", role="ops")

    assert summary.indexed_documents == 2
    assert summary.indexed_chunks == 2
    assert summary.warning_count == 1

    reopened = ProjectIndex.open(state)
    decisions = reopened.search(
        "evidence packets assistant synthesizes", filters={"type": "decision"}
    )

    assert len(decisions) == 1
    assert decisions[0].path == "docs/decisions/accepted/0001-context-compiler.md"
    assert decisions[0].repo_id == "ops"
    assert decisions[0].doc_type == "decision"
    assert decisions[0].status == "accepted"
    assert decisions[0].authority == "accepted_decision"
    assert decisions[0].tags == ["briefs", "evidence"]
    assert decisions[0].line_start == 1
    assert decisions[0].line_end > decisions[0].line_start

    warnings = reopened.index_events(event_type="frontmatter_normalization_warning")
    assert len(warnings) == 1
    assert warnings[0].path == "docs/decisions/typo-status.md"
    assert "Unknown status 'depreciated'" in warnings[0].message
    assert "authority='working'" in warnings[0].message
