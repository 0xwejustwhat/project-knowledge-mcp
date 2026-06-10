from __future__ import annotations

import json
import subprocess
from pathlib import Path
from textwrap import dedent

from fastmcp import Client

from project_knowledge_mcp.server import create_mcp


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


def result_text(result) -> str:
    return "".join(getattr(part, "text", str(part)) for part in result.content)


def write_doc(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).strip() + "\n", encoding="utf-8")


def write_project_config(
    root: Path,
    *,
    repo: Path,
    state_dir: Path,
    source_mode: str = "workspace",
    snapshot_ref: str | None = None,
    snapshot_commit: str | None = None,
) -> Path:
    config_path = root / "project.yaml"
    snapshot_lines = ""
    if snapshot_ref is not None:
        snapshot_lines += f"    snapshot_ref: {snapshot_ref}\n"
    if snapshot_commit is not None:
        snapshot_lines += f"    snapshot_commit: {snapshot_commit}\n"
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
    source_mode: {source_mode}
    path: {repo.as_posix()}
    writable: true
{snapshot_lines}    include_globs: ["README.md", "docs/**/*.md", "*.md"]
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


def test_mcp_check_project_staleness_reports_dirty_and_index_freshness(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    write_doc(repo, "docs/doctrine/context.md", "# Context\n\nBaseline indexed evidence.")
    indexed_commit = commit_all(repo, "baseline")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    import asyncio

    indexed = asyncio.run(call_tool("index_project", {"config_path": str(config_path)}))
    assert indexed["status"] == "ok"

    write_doc(repo, "docs/doctrine/context.md", "# Context\n\nChanged workspace evidence.")
    write_doc(repo, "docs/notes/untracked.md", "# Untracked\n\nNew note.")
    write_doc(repo, "docs/notes/nested/second.md", "# Second\n\nAnother untracked note.")

    status = asyncio.run(call_tool("check_project_staleness", {"config_path": str(config_path)}))
    assert status["status"] == "ok"
    assert status["project_id"] == "project-knowledge-mcp"
    repo_status = status["repos"][0]
    assert repo_status["repo_id"] == "ops"
    assert repo_status["role"] == "ops"
    assert repo_status["source_mode"] == "workspace"
    assert repo_status["path"] == str(repo.resolve())
    assert repo_status["branch"] in {"main", "master"}
    assert repo_status["head_commit"] == indexed_commit
    assert repo_status["last_indexed_commit"] == indexed_commit
    assert repo_status["last_indexed_at"] is not None
    assert repo_status["includes_uncommitted_changes"] is True
    assert repo_status["dirty"] is True
    assert repo_status["untracked_count"] == 2
    assert repo_status["reindex_needed"] is True
    assert "markdown" in status
    assert "ops" in status["markdown"]


def test_mcp_check_project_staleness_preserves_snapshot_provenance(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    write_doc(repo, "docs/doctrine/context.md", "# Context\n\nSnapshot evidence.")
    commit = commit_all(repo, "snapshot")
    config_path = write_project_config(
        tmp_path,
        repo=repo,
        state_dir=state,
        source_mode="snapshot",
        snapshot_ref="refs/heads/main",
        snapshot_commit=commit,
    )

    import asyncio

    indexed = asyncio.run(call_tool("index_project", {"config_path": str(config_path)}))
    assert indexed["status"] == "ok"

    status = asyncio.run(call_tool("check_project_staleness", {"config_path": str(config_path)}))
    repo_status = status["repos"][0]
    assert repo_status["source_mode"] == "snapshot"
    assert repo_status["snapshot_ref"] == "refs/heads/main"
    assert repo_status["snapshot_commit"] == commit
    assert repo_status["includes_uncommitted_changes"] is False
    assert repo_status["reindex_needed"] is False


def test_mcp_check_project_staleness_treats_indexed_dirty_workspace_as_current(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    write_doc(repo, "docs/doctrine/context.md", "# Context\n\nCommitted evidence.")
    commit_all(repo, "baseline")
    write_doc(repo, "docs/doctrine/context.md", "# Context\n\nDirty indexed evidence.")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    import asyncio

    indexed = asyncio.run(call_tool("index_project", {"config_path": str(config_path)}))
    assert indexed["status"] == "ok"

    status = asyncio.run(call_tool("check_project_staleness", {"config_path": str(config_path)}))
    repo_status = status["repos"][0]
    assert repo_status["dirty"] is True
    assert repo_status["reindex_needed"] is False


def test_mcp_check_project_staleness_ignores_state_dir_inside_repo(tmp_path: Path):
    repo = tmp_path / "ops"
    state = repo / ".project-knowledge"
    init_git_repo(repo)
    write_doc(repo, "docs/doctrine/context.md", "# Context\n\nCommitted evidence.")
    commit_all(repo, "baseline")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    import asyncio

    indexed = asyncio.run(call_tool("index_project", {"config_path": str(config_path)}))
    assert indexed["status"] == "ok"

    status = asyncio.run(call_tool("check_project_staleness", {"config_path": str(config_path)}))
    repo_status = status["repos"][0]
    assert repo_status["dirty"] is False
    assert repo_status["untracked_count"] == 0
    assert repo_status["reindex_needed"] is False


async def call_tool(tool_name: str, args: dict) -> dict:
    async with Client(create_mcp()) as client:
        return json.loads(result_text(await client.call_tool(tool_name, args)))


def test_mcp_validate_config_indexes_and_searches_ops(tmp_path: Path):
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

        Project Knowledge MCP returns evidence packets before synthesis.
        """,
    )
    write_doc(repo, "secret.md", "# Secret\n\nExcluded secret token material must not be indexed.")
    write_doc(repo, ".git/leak.md", "# Git Leak\n\nGit internals must not be indexed.")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    import asyncio

    validation = asyncio.run(call_tool("validate_config", {"config_path": str(config_path)}))
    assert validation["valid"] is True
    assert validation["project_id"] == "project-knowledge-mcp"
    assert validation["repos"][0]["id"] == "ops"
    assert validation["repos"][0]["exists"] is True
    assert validation["repos"][0]["is_git_repo"] is True

    indexed = asyncio.run(call_tool("index_project", {"config_path": str(config_path)}))
    assert indexed["status"] == "ok"
    assert indexed["repos"][0]["repo_id"] == "ops"
    assert indexed["repos"][0]["documents_indexed"] == 1
    assert indexed["repos"][0]["chunks_indexed"] == 1

    excluded = asyncio.run(
        call_tool(
            "search_ops", {"config_path": str(config_path), "query": "secret token", "limit": 3}
        )
    )
    assert excluded["results"] == []

    search = asyncio.run(
        call_tool(
            "search_ops",
            {"config_path": str(config_path), "query": "evidence packets synthesis", "limit": 3},
        )
    )
    assert search["query"] == "evidence packets synthesis"
    assert search["warnings"] == []
    assert search["results"][0]["repo_id"] == "ops"
    assert search["results"][0]["path"] == "docs/doctrine/context.md"
    assert search["results"][0]["doc_type"] == "doctrine"
    assert search["results"][0]["status"] == "current"
    assert search["results"][0]["authority"] == "canonical"
    assert search["results"][0]["start_line"] == 1
    assert search["results"][0]["end_line"] >= search["results"][0]["start_line"]
    assert "evidence packets" in search["results"][0]["excerpt"]
    assert isinstance(search["results"][0]["score"], float)
    assert search["results"][0]["warnings"] == []
    assert "## Search Results" in search["markdown"]
    assert "docs/doctrine/context.md" in search["markdown"]

    invalid_query = asyncio.run(
        call_tool("search_ops", {"config_path": str(config_path), "query": "!!!"})
    )
    assert invalid_query["results"] == []
    assert invalid_query["error"]["code"] == "QUERY_INVALID"
    assert "search query" in invalid_query["error"]["message"]

    invalid_filter = asyncio.run(
        call_tool(
            "search_ops",
            {
                "config_path": str(config_path),
                "query": "evidence",
                "filters": {"date": "2026-06-10"},
            },
        )
    )
    assert invalid_filter["results"] == []
    assert invalid_filter["error"]["code"] == "QUERY_INVALID"
    assert "unsupported search filter" in invalid_filter["error"]["message"]

    invalid_limit = asyncio.run(
        call_tool("search_ops", {"config_path": str(config_path), "query": "evidence", "limit": 0})
    )
    assert invalid_limit["results"] == []
    assert invalid_limit["error"]["code"] == "QUERY_INVALID"


def test_search_ops_cannot_widen_scope_or_use_non_scalar_filters(tmp_path: Path):
    ops_repo = tmp_path / "ops"
    work_repo = tmp_path / "work"
    state = tmp_path / ".project-knowledge"
    init_git_repo(ops_repo)
    init_git_repo(work_repo)
    write_doc(
        ops_repo, "docs/doctrine/context.md", "# Ops Doctrine\n\nCanonical ops evidence only."
    )
    write_doc(
        work_repo, "docs/impl.md", "# Work Implementation\n\nPrivate work implementation evidence."
    )
    config_path = tmp_path / "project.yaml"
    config_path.write_text(
        f"""
schema_version: 1
project:
  id: project-knowledge-mcp
storage:
  project_root: {tmp_path.as_posix()}
  state_dir: {state.as_posix()}
repos:
  - id: ops
    role: ops
    path: {ops_repo.as_posix()}
    writable: true
    include_globs: ["**/*.md", "*.md"]
  - id: work
    role: work
    path: {work_repo.as_posix()}
    writable: false
    include_globs: ["**/*.md", "*.md"]
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

    import asyncio

    indexed = asyncio.run(call_tool("index_project", {"config_path": str(config_path)}))
    assert indexed["status"] == "ok"
    assert {repo["repo_id"] for repo in indexed["repos"]} == {"ops", "work"}

    widened = asyncio.run(
        call_tool(
            "search_ops",
            {
                "config_path": str(config_path),
                "query": "implementation evidence",
                "filters": {"repo_id": "work"},
            },
        )
    )
    assert widened["results"] == []
    assert widened["error"]["code"] == "QUERY_INVALID"
    assert "repo_id" in widened["error"]["message"]

    invalid_filter_value = asyncio.run(
        call_tool(
            "search_ops",
            {
                "config_path": str(config_path),
                "query": "evidence",
                "filters": {"status": ["current"]},
            },
        )
    )
    assert invalid_filter_value["results"] == []
    assert invalid_filter_value["error"]["code"] == "QUERY_INVALID"
    assert "scalar" in invalid_filter_value["error"]["message"]


def test_index_project_skips_binary_and_excludes_state_dir_and_symlink_escape(tmp_path: Path):
    repo = tmp_path / "ops"
    state = repo / ".pkmcp-state"
    outside = tmp_path / "outside"
    init_git_repo(repo)
    outside.mkdir()
    write_doc(repo, "docs/doctrine/context.md", "# Context\n\nCanonical searchable doctrine.")
    write_doc(state, "recursive.md", "# Recursive\n\nrecursive sqlite evidence must not index.")
    (repo / "docs" / "binary.md").write_bytes(b"\xff\xfe\x00not utf8")
    write_doc(outside, "escape.md", "# Outside\n\nsymlink escape evidence must not index.")
    (repo / "docs" / "escape.md").symlink_to(outside / "escape.md")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    import asyncio

    indexed = asyncio.run(call_tool("index_project", {"config_path": str(config_path)}))
    assert indexed["status"] == "ok"
    assert indexed["repos"][0]["documents_indexed"] == 1
    assert indexed["repos"][0]["documents_skipped"] == 1

    for query in ["recursive sqlite evidence", "symlink escape evidence"]:
        search = asyncio.run(
            call_tool("search_ops", {"config_path": str(config_path), "query": query})
        )
        assert search["results"] == []


def test_tag_filter_overfetches_until_matching_results_are_returned(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    for index in range(30):
        write_doc(repo, f"docs/notes/{index:02d}.md", "# Note\n\nneedle shared evidence.")
    write_doc(
        repo,
        "docs/notes/zz-wanted.md",
        """
        ---
        title: Wanted Note
        tags: [wanted]
        ---
        # Wanted Note

        needle shared evidence.
        """,
    )
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    import asyncio

    indexed = asyncio.run(call_tool("index_project", {"config_path": str(config_path)}))
    assert indexed["status"] == "ok"

    search = asyncio.run(
        call_tool(
            "search_ops",
            {
                "config_path": str(config_path),
                "query": "needle shared evidence",
                "filters": {"tags": ["wanted"]},
                "limit": 1,
            },
        )
    )
    assert [result["path"] for result in search["results"]] == ["docs/notes/zz-wanted.md"]


def test_docs_recursive_glob_includes_docs_root_files(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    write_doc(repo, "docs/PRD.md", "# PRD\n\nroot docs product requirements evidence.")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    import asyncio

    indexed = asyncio.run(call_tool("index_project", {"config_path": str(config_path)}))
    assert indexed["status"] == "ok"
    assert indexed["repos"][0]["documents_indexed"] == 1

    search = asyncio.run(
        call_tool(
            "search_ops",
            {"config_path": str(config_path), "query": "product requirements evidence"},
        )
    )
    assert [result["path"] for result in search["results"]] == ["docs/PRD.md"]


def test_oversized_files_are_skipped_with_warning_records(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    write_doc(repo, "docs/notes/small.md", "# Small\n\nsmall searchable evidence.")
    write_doc(repo, "docs/notes/large.md", "# Large\n\n" + "oversized hidden evidence " * 20)
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + """
indexing:
  provider: local_parser_registry
  max_file_bytes: 80
  chunk_target_chars: 1800
  chunk_overlap_chars: 200
""",
        encoding="utf-8",
    )

    import asyncio

    indexed = asyncio.run(call_tool("index_project", {"config_path": str(config_path)}))
    assert indexed["status"] == "ok"
    assert indexed["repos"][0]["documents_indexed"] == 1
    assert indexed["repos"][0]["documents_skipped"] == 1
    assert indexed["repos"][0]["warning_count"] == 1

    search = asyncio.run(
        call_tool(
            "search_ops",
            {"config_path": str(config_path), "query": "oversized hidden evidence"},
        )
    )
    assert search["results"] == []
