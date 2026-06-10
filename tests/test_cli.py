from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from project_knowledge_mcp.server import app


def test_cli_help_lists_step1_index_commands():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "index-project" in result.output
    assert "search-index" in result.output


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
