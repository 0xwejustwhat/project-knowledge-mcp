from __future__ import annotations

import json
import subprocess
from pathlib import Path
from textwrap import dedent

import project_knowledge_mcp.services as services
from fastmcp import Client

from project_knowledge_mcp.server import create_mcp


EXPECTED_TOOL_NAMES = {
    "health",
    "validate_config",
    "index_project",
    "search_ops",
    "search_decisions",
    "get_current_doctrine",
    "search_open_questions",
    "search_code",
    "get_code_context",
    "get_code_provider_status",
    "retrieve_ops_code_evidence",
    "generate_session_brief",
    "add_project_note",
    "create_draft_artifact",
    "propose_authority_change",
    "check_project_staleness",
}


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--initial-branch", "main"], cwd=path, check=True)
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


def add_bare_remote(repo: Path, remote: Path) -> None:
    subprocess.run(["git", "init", "--bare", "-q", "--initial-branch", "main", remote], check=True)
    subprocess.run(["git", "remote", "add", "origin", remote.as_posix()], cwd=repo, check=True)
    commit_all(repo, "initial")
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo, check=True)


def remote_file_text(remote: Path, relative_path: str) -> str:
    return subprocess.run(
        ["git", "--git-dir", remote.as_posix(), "show", f"main:{relative_path}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def require_symlink(path: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        path.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        import pytest

        pytest.skip(f"symlink privilege unavailable: {exc}")


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


async def list_tool_names() -> set[str]:
    async with Client(create_mcp()) as client:
        return {tool.name for tool in await client.list_tools()}


async def tool_metadata(tool_name: str) -> dict:
    async with Client(create_mcp()) as client:
        for tool in await client.list_tools():
            if tool.name == tool_name:
                if hasattr(tool, "model_dump"):
                    return tool.model_dump()
                return dict(tool)
    raise AssertionError(f"Tool {tool_name!r} was not registered")


def test_mcp_tool_registry_matches_phase7_surface():
    import asyncio

    assert asyncio.run(list_tool_names()) == EXPECTED_TOOL_NAMES
    health = asyncio.run(call_tool("health", {}))
    assert health == {
        "status": "ok",
        "phase": "step2_config_backed_mcp_tools",
        "llm_required": False,
        "default_network_exposure": "loopback_or_stdio_only",
    }


def test_mcp_propose_authority_change_metadata_matches_change_schema():
    import asyncio

    metadata = asyncio.run(tool_metadata("propose_authority_change"))
    schema = metadata.get("inputSchema") or metadata.get("input_schema")
    assert schema is not None
    changes_description = schema["properties"]["changes"]["description"]

    assert "operation" in changes_description
    assert "path" in changes_description
    assert "content" in changes_description
    assert "action" not in changes_description
    assert "old_body" not in changes_description
    assert "new_body" not in changes_description


def test_mcp_step7_write_tools_are_client_callable(monkeypatch, tmp_path: Path):
    repo = tmp_path / "ops"
    remote = tmp_path / "remote.git"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    write_doc(repo, "README.md", "# Ops Repo\n\nInitial content.")
    add_bare_remote(repo, remote)
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)
    monkeypatch.setattr(services, "_command_exists", lambda _command: False)

    import asyncio

    note = asyncio.run(
        call_tool(
            "add_project_note",
            {
                "config_path": str(config_path),
                "title": "Client Capture",
                "body": "Client-callable safe capture note.",
                "tags": ["step8"],
                "source": "pytest-mcp-client",
            },
        )
    )
    assert note["status"] == "written_and_pushed"
    assert note["authority"] == "capture"
    assert note["branch"] == "main"
    assert note["remote"] == "origin"
    assert "Client-callable safe capture note." in remote_file_text(remote, note["path"])

    draft = asyncio.run(
        call_tool(
            "create_draft_artifact",
            {
                "config_path": str(config_path),
                "kind": "doctrine_delta",
                "title": "Client Draft",
                "body": "Reviewable client-callable draft.",
                "tags": ["step8"],
            },
        )
    )
    assert draft["status"] == "written"
    assert draft["authority"] == "proposal"
    assert draft["path"].startswith("docs/proposals/doctrine-deltas/")
    commit_all(repo, "commit mcp client writes before proposal branch")

    proposal = asyncio.run(
        call_tool(
            "propose_authority_change",
            {
                "config_path": str(config_path),
                "title": "Client Proposal",
                "rationale": "MCP client supplied reviewable content.",
                "changes": [
                    {
                        "operation": "add_file",
                        "path": "docs/decisions/0008-client-proposal.md",
                        "content": "# Client Proposal\n\nReviewable content.\n",
                    }
                ],
                "branch_name": "pkmcp/authority-proposal/client-step8",
            },
        )
    )
    assert proposal["status"] == "branch_prepared_pr_not_opened"
    assert proposal["authority_boundary"] == "review_required_before_promotion"
    assert proposal["changed_paths"] == ["docs/decisions/0008-client-proposal.md"]
    assert proposal["next_action"] == "push branch and open PR manually"


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
    require_symlink(repo / "docs" / "escape.md", outside / "escape.md")
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


def test_mcp_phase4_search_tools_return_typed_results(tmp_path: Path):
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
        ---
        # Context Doctrine

        Context retrieval doctrine is current truth.
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
        ---
        # Accepted Context Decision

        Accepted context retrieval decision.
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
        ---
        # Context Open Question

        Context retrieval unresolved question.
        """,
    )
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    import asyncio

    indexed = asyncio.run(call_tool("index_project", {"config_path": str(config_path)}))
    assert indexed["status"] == "ok"

    decisions = asyncio.run(
        call_tool(
            "search_decisions", {"config_path": str(config_path), "query": "context retrieval"}
        )
    )
    assert decisions["results"][0]["path"] == "docs/decisions/accepted/0001-context.md"

    doctrine = asyncio.run(
        call_tool(
            "get_current_doctrine", {"config_path": str(config_path), "topic": "context retrieval"}
        )
    )
    assert doctrine["doctrine"][0]["path"] == "docs/doctrine/context.md"
    assert doctrine["decisions"][0]["path"] == "docs/decisions/accepted/0001-context.md"

    questions = asyncio.run(
        call_tool(
            "search_open_questions", {"config_path": str(config_path), "query": "context retrieval"}
        )
    )
    assert questions["results"][0]["path"] == "docs/open-questions/context.md"
    assert questions["results"][0]["owner"] == "Amin"


def test_mcp_code_context_tools_return_text_fallback_results(tmp_path: Path):
    ops_repo = tmp_path / "ops"
    work_repo = tmp_path / "work"
    state = tmp_path / ".project-knowledge"
    init_git_repo(ops_repo)
    init_git_repo(work_repo)
    write_doc(ops_repo, "docs/doctrine/context.md", "# Ops\n\nOps-only evidence.")
    write_doc(
        work_repo,
        "src/example.py",
        """
        class ExampleService:
            def compile_context(self, topic: str) -> str:
                return f"compiled evidence for {topic}"
        """,
    )
    write_doc(
        work_repo,
        "tests/test_example.py",
        """
        from src.example import ExampleService

        def test_compile_context_returns_evidence():
            assert ExampleService().compile_context("brief") == "compiled evidence for brief"
        """,
    )
    config_path = tmp_path / "project.yaml"
    config_path.write_text(
        dedent(
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
                include_globs: ["docs/**/*.md"]
              - id: app
                role: work
                path: {work_repo.as_posix()}
                writable: false
                include_globs: ["src/**/*.py", "tests/**/*.py"]
            retrieval:
              provider: sqlite_fts5
              default_limit: 5
              include_superseded_by_default: false
            code_context:
              provider: codegraph
              fallback_provider: text
              required_for_code_repos: true
              fallback_on_unhealthy: true
              codegraph:
                enabled: true
                vector_resolve_enabled: false
            write_policy:
              default_capture_repo: ops
              default_capture_dir: docs/notes
              allow_direct_capture: true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    import asyncio

    indexed = asyncio.run(call_tool("index_project", {"config_path": str(config_path)}))
    assert indexed["status"] == "ok"

    status = asyncio.run(call_tool("get_code_provider_status", {"config_path": str(config_path)}))
    assert status["status"] == "ok"
    assert status["configured_provider"] == "codegraph"
    assert status["active_provider"] == "text"
    assert status["fallback_available"] is True
    assert status["work_repos"] == ["app"]

    search = asyncio.run(
        call_tool(
            "search_code",
            {
                "config_path": str(config_path),
                "query": "compile_context evidence",
                "repo_id": "app",
            },
        )
    )
    assert search["tool"] == "search_code"
    assert search["active_provider"] == "text"
    assert search["results"][0]["path"] == "src/example.py"
    assert search["results"][0]["kind"] == "code"
    assert search["results"][0]["provider"] == "fts5"
    assert search["warnings"]

    context = asyncio.run(
        call_tool(
            "get_code_context",
            {"config_path": str(config_path), "symbol_or_file": "ExampleService", "repo_id": "app"},
        )
    )
    assert context["tool"] == "get_code_context"
    assert context["results"][0]["path"] == "src/example.py"
    assert context["results"][0]["symbol"] == "ExampleService.compile_context"

    evidence = asyncio.run(
        call_tool(
            "retrieve_ops_code_evidence",
            {"config_path": str(config_path), "topic": "compile_context evidence", "limit": 3},
        )
    )
    assert evidence["tool"] == "retrieve_ops_code_evidence"
    assert sorted(evidence["sections"]) == ["code", "decisions", "doctrine", "open_questions"]
    assert evidence["sections"]["code"][0]["path"] == "src/example.py"

    brief = asyncio.run(
        call_tool(
            "generate_session_brief",
            {
                "config_path": str(config_path),
                "task": "Implement compile_context evidence",
                "since": "2026-06-01",
                "limit": 3,
            },
        )
    )
    assert brief["tool"] == "generate_session_brief"
    assert brief["since"] == "2026-06-01"
    assert brief["sections"]["code"][0]["path"] == "src/example.py"
    assert "## Session Brief" in brief["markdown"]

    widened = asyncio.run(
        call_tool(
            "search_code",
            {"config_path": str(config_path), "query": "Ops-only evidence", "repo_id": "ops"},
        )
    )
    assert widened["results"] == []
    assert widened["error"]["code"] == "QUERY_INVALID"


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
