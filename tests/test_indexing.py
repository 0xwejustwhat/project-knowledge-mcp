from __future__ import annotations

import sqlite3
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


def test_open_migrates_step1_index_before_searching_source_metadata(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    db_path = state / "index.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE repos (
          id TEXT PRIMARY KEY,
          role TEXT NOT NULL,
          name TEXT NOT NULL,
          path TEXT NOT NULL,
          writable INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE documents (
          id TEXT PRIMARY KEY,
          repo_id TEXT NOT NULL,
          path TEXT NOT NULL,
          parser TEXT NOT NULL,
          title TEXT,
          doc_type TEXT,
          status TEXT,
          authority TEXT,
          tags_json TEXT,
          frontmatter_json TEXT,
          superseded_by_json TEXT,
          mtime REAL,
          size_bytes INTEGER,
          content_hash TEXT,
          skipped INTEGER NOT NULL DEFAULT 0,
          skip_reason TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(repo_id, path)
        );
        CREATE TABLE chunks (
          rowid INTEGER PRIMARY KEY,
          id TEXT UNIQUE NOT NULL,
          document_id TEXT NOT NULL,
          repo_id TEXT NOT NULL,
          path TEXT NOT NULL,
          heading_path_json TEXT,
          chunk_index INTEGER NOT NULL,
          start_line INTEGER,
          end_line INTEGER,
          page INTEGER,
          text TEXT NOT NULL,
          authority TEXT,
          doc_type TEXT,
          status TEXT,
          content_hash TEXT
        );
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
          text,
          content='chunks',
          content_rowid='rowid',
          tokenize='porter unicode61'
        );
        """
    )
    conn.execute(
        "INSERT INTO repos(id, role, name, path, writable) VALUES ('ops', 'ops', 'Ops', '/tmp/ops', 1)"
    )
    conn.execute(
        """
        INSERT INTO documents(
          id, repo_id, path, parser, title, doc_type, status, authority,
          tags_json, frontmatter_json, superseded_by_json, mtime, size_bytes,
          content_hash, created_at, updated_at
        ) VALUES (
          'doc1', 'ops', 'docs/context.md', 'markdown', 'Context', 'note', 'draft', 'working',
          '[]', '{}', '[]', 0, 10, 'hash', 'now', 'now'
        )
        """
    )
    cursor = conn.execute(
        """
        INSERT INTO chunks(
          id, document_id, repo_id, path, heading_path_json, chunk_index, start_line, end_line,
          text, authority, doc_type, status, content_hash
        ) VALUES (
          'chunk1', 'doc1', 'ops', 'docs/context.md', '[]', 0, 1, 1,
          'legacy source metadata search', 'working', 'note', 'draft', 'hash'
        )
        """
    )
    conn.execute(
        "INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)",
        (cursor.lastrowid, "legacy source metadata search"),
    )
    conn.commit()
    conn.close()

    results = ProjectIndex.open(state).search("legacy source metadata", limit=1)

    assert results[0].path == "docs/context.md"
    assert results[0].source_mode == "workspace"
    assert results[0].includes_uncommitted_changes is False
