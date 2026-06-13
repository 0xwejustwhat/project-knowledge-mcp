from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from project_knowledge_mcp.code_context import CodeGraphContextProvider
from project_knowledge_mcp.config import load_project_config
from project_knowledge_mcp.index import index_repo
from project_knowledge_mcp.services import (
    get_code_context_from_config,
    get_code_provider_status_from_config,
    index_project_from_config,
    search_code_from_config,
)


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)


def write_file(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).strip() + "\n", encoding="utf-8")


def write_project_config(root: Path, *, ops_repo: Path, work_repo: Path, state_dir: Path) -> Path:
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
    path: {ops_repo.as_posix()}
    writable: true
    include_globs: ["README.md", "docs/**/*.md", "*.md"]
    exclude_globs: [".git/**", ".project-knowledge/**"]
  - id: app
    role: work
    path: {work_repo.as_posix()}
    writable: false
    include_globs: ["README.md", "src/**/*.py", "tests/**/*.py", "schemas/**/*.json"]
    exclude_globs: [".git/**", ".project-knowledge/**"]
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
    index_dir: {state_dir.as_posix()}/codegraph
    vector_resolve_enabled: false
write_policy:
  default_capture_repo: ops
  default_capture_dir: docs/notes
  allow_direct_capture: true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def configured_project(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    ops_repo = tmp_path / "ops"
    work_repo = tmp_path / "work"
    state_dir = tmp_path / ".project-knowledge"
    init_git_repo(ops_repo)
    init_git_repo(work_repo)
    write_file(ops_repo, "docs/doctrine/context.md", "# Context\n\nOps doctrine.")
    write_file(
        work_repo,
        "src/example.py",
        """
        class ExampleService:
            def compile_context(self, topic: str) -> str:
                return f"compiled evidence for {topic}"
        """,
    )
    write_file(
        work_repo,
        "tests/test_example.py",
        """
        from src.example import ExampleService

        def test_compile_context_returns_evidence():
            assert ExampleService().compile_context("brief") == "compiled evidence for brief"
        """,
    )
    write_file(
        work_repo,
        "schemas/example.schema.json",
        """
        {
          "$schema": "https://json-schema.org/draft/2020-12/schema",
          "title": "ExampleEvidence",
          "type": "object",
          "properties": {"topic": {"type": "string"}}
        }
        """,
    )
    config_path = write_project_config(
        tmp_path, ops_repo=ops_repo, work_repo=work_repo, state_dir=state_dir
    )
    return config_path, ops_repo, work_repo, state_dir


def activate_fake_codegraph(
    monkeypatch: pytest.MonkeyPatch, config_path: Path, work_repo: Path
) -> None:
    config = load_project_config(config_path)
    provider = CodeGraphContextProvider(config)
    work_config = next(repo for repo in config.repos if repo.id == "app")
    provider._write_provenance([work_config])
    monkeypatch.setattr(CodeGraphContextProvider, "_package_installed", lambda self: True)
    monkeypatch.setattr(
        CodeGraphContextProvider,
        "_indexed_repo_paths",
        lambda self: {"app": str(work_repo.resolve())},
    )


def fake_codegraph_rows(work_repo: Path) -> list[dict[str, object]]:
    return [
        {
            "label": "Function",
            "name": "compile_context",
            "path": str((work_repo / "src/example.py").resolve()),
            "line_number": 2,
            "end_line": 3,
            "source": "def compile_context(self, topic: str) -> str:\n    return f'compiled evidence for {topic}'",
            "provider_internal_shape": {"must_not_leak": True},
        }
    ]


def test_search_code_uses_text_fallback_for_indexed_work_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(CodeGraphContextProvider, "_package_installed", lambda self: False)
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    indexed = index_project_from_config(config_path=config_path)
    assert indexed["status"] == "ok"
    assert {repo["repo_id"] for repo in indexed["repos"]} == {"ops", "app"}

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=5
    )

    assert payload["tool"] == "search_code"
    assert payload["query"] == "compile_context evidence"
    assert payload["active_provider"] == "text"
    assert payload["configured_provider"] == "codegraph"
    assert payload["warnings"]
    assert payload["results"][0]["repo_id"] == "app"
    assert payload["results"][0]["path"] == "src/example.py"
    assert payload["results"][0]["kind"] == "code"
    assert payload["results"][0]["provider"] == "text"
    assert payload["results"][0]["symbol"] == "ExampleService.compile_context"
    assert payload["results"][0]["start_line"] <= payload["results"][0]["end_line"]
    assert "compile_context" in payload["results"][0]["snippet"]
    assert isinstance(payload["results"][0]["score"], float)
    assert isinstance(payload["results"][0]["related"], list)
    assert "## Code Search Results" in payload["markdown"]


def test_search_code_prefers_healthy_codegraph_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    indexed = index_project_from_config(config_path=config_path)
    assert indexed["status"] == "ok"
    activate_fake_codegraph(monkeypatch, config_path, work_repo)
    monkeypatch.setattr(
        CodeGraphContextProvider,
        "_query_code_elements",
        lambda self, repo, query, per_label_limit: fake_codegraph_rows(work_repo),
    )
    monkeypatch.setattr(
        CodeGraphContextProvider,
        "_graph_call_related",
        lambda self, repo, path, symbol, limit: [
            {
                "repo_id": repo.id,
                "path": "tests/test_example.py",
                "symbol": "test_compile_context_returns_evidence",
                "kind": "test",
                "relation": "called_by",
                "provider": "codegraph",
            }
        ],
    )

    status = get_code_provider_status_from_config(config_path=config_path)
    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=5
    )

    assert status["active_provider"] == "codegraph"
    assert status["codegraph_healthy"] is True
    assert payload["active_provider"] == "codegraph"
    assert payload["codegraph_healthy"] is True
    result = payload["results"][0]
    assert result == {
        "repo_id": "app",
        "path": "src/example.py",
        "start_line": 2,
        "end_line": 3,
        "symbol": "compile_context",
        "kind": "code",
        "snippet": "def compile_context(self, topic: str) -> str:\n    return f'compiled evidence for {topic}'",
        "provider": "codegraph",
        "score": result["score"],
        "related": result["related"],
    }
    assert isinstance(result["score"], float)
    assert result["related"][0]["path"] == "tests/test_example.py"
    assert "provider_internal_shape" not in result


def test_get_code_context_uses_codegraph_file_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    index_project_from_config(config_path=config_path)
    activate_fake_codegraph(monkeypatch, config_path, work_repo)
    monkeypatch.setattr(
        CodeGraphContextProvider,
        "_file_context",
        lambda self, repo, relative_path, limit: [
            self._row_to_result(repo, fake_codegraph_rows(work_repo)[0], query=relative_path)
        ],
    )
    monkeypatch.setattr(
        CodeGraphContextProvider,
        "_graph_call_related",
        lambda self, repo, path, symbol, limit: [],
    )

    payload = get_code_context_from_config(
        symbol_or_file="src/example.py", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["active_provider"] == "codegraph"
    assert payload["results"][0]["provider"] == "codegraph"
    assert payload["results"][0]["path"] == "src/example.py"
    assert payload["results"][0]["symbol"] == "compile_context"


def test_codegraph_query_failure_falls_back_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    index_project_from_config(config_path=config_path)
    activate_fake_codegraph(monkeypatch, config_path, work_repo)

    def fail_query(self, repo, query, per_label_limit):
        raise RuntimeError("synthetic graph failure")

    monkeypatch.setattr(CodeGraphContextProvider, "_query_code_elements", fail_query)

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=5
    )

    assert payload["active_provider"] == "text"
    assert payload["results"][0]["provider"] == "text"
    assert any("CodeGraph query failed" in warning for warning in payload["warnings"])


def test_codegraph_ignore_file_changes_mark_graph_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    index_project_from_config(config_path=config_path)
    activate_fake_codegraph(monkeypatch, config_path, work_repo)

    healthy = get_code_provider_status_from_config(config_path=config_path)
    assert healthy["active_provider"] == "codegraph"
    assert healthy["codegraph_healthy"] is True

    (work_repo / ".cgcignore").write_text("src/**\n", encoding="utf-8")

    stale = get_code_provider_status_from_config(config_path=config_path)
    assert stale["active_provider"] == "text"
    assert stale["codegraph_healthy"] is False
    assert stale["details"]["stale_repos"] == ["app"]


def test_index_project_reports_codegraph_skipped_when_disabled(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("    enabled: true", "    enabled: false"),
        encoding="utf-8",
    )

    indexed = index_project_from_config(config_path=config_path)

    assert indexed["status"] == "ok"
    assert indexed["codegraph_indexed"] is False
    assert indexed["codegraph_status"] == "skipped"
    assert indexed["codegraph_warnings"] == []


def test_index_project_reports_codegraph_warning_when_package_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(CodeGraphContextProvider, "_package_installed", lambda self: False)
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)

    indexed = index_project_from_config(config_path=config_path)

    assert indexed["status"] == "ok"
    assert indexed["codegraph_indexed"] is False
    assert indexed["codegraph_status"] == "warning"
    assert indexed["codegraph_warnings"] == [
        "CodeGraphContext package is not installed; code graph indexing skipped."
    ]


def test_get_code_context_resolves_symbol_and_file_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(CodeGraphContextProvider, "_package_installed", lambda self: False)
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    index_project_from_config(config_path=config_path)

    by_symbol = get_code_context_from_config(
        symbol_or_file="ExampleService", config_path=config_path, repo_id="app", limit=3
    )
    assert by_symbol["tool"] == "get_code_context"
    assert by_symbol["results"][0]["path"] == "src/example.py"
    assert by_symbol["results"][0]["symbol"] == "ExampleService.compile_context"

    by_file = get_code_context_from_config(
        symbol_or_file="schemas/example.schema.json",
        config_path=config_path,
        repo_id="app",
        limit=3,
    )
    assert by_file["results"][0]["path"] == "schemas/example.schema.json"
    assert by_file["results"][0]["kind"] == "schema"


def test_code_provider_status_soft_fails_to_text_fallback(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)

    status = get_code_provider_status_from_config(config_path=config_path)

    assert status["status"] == "ok"
    assert status["configured_provider"] == "codegraph"
    assert status["active_provider"] == "text"
    assert status["codegraph_enabled"] is True
    assert status["codegraph_healthy"] is False
    assert status["fallback_available"] is True
    assert status["work_repo_count"] == 1
    assert status["work_repos"] == ["app"]
    assert any("CodeGraph" in warning for warning in status["warnings"])


def test_search_code_rejects_repo_scope_widening_and_invalid_limit(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    index_project_from_config(config_path=config_path)

    ops_scope = search_code_from_config(
        query="Ops doctrine", config_path=config_path, repo_id="ops", limit=3
    )
    assert ops_scope["results"] == []
    assert ops_scope["error"]["code"] == "QUERY_INVALID"
    assert "work repo" in ops_scope["error"]["message"]

    invalid_limit = search_code_from_config(
        query="compile_context", config_path=config_path, repo_id="app", limit=0
    )
    assert invalid_limit["results"] == []
    assert invalid_limit["error"]["code"] == "QUERY_INVALID"

    invalid_query = search_code_from_config(query="!!!", config_path=config_path, repo_id="app")
    assert invalid_query["results"] == []
    assert invalid_query["error"]["code"] == "QUERY_INVALID"
    assert "search query" in invalid_query["error"]["message"]


def test_direct_file_context_respects_index_exclusions_and_include_globs(tmp_path: Path):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    write_file(
        work_repo,
        "src/secret_config.py",
        """
        LEAK_MARKER = "placeholder-value"
        """,
    )
    write_file(
        work_repo,
        "private/hidden.py",
        """
        class HiddenService:
            pass
        """,
    )
    index_project_from_config(config_path=config_path)

    excluded_secret = get_code_context_from_config(
        symbol_or_file="src/secret_config.py", config_path=config_path, repo_id="app"
    )
    assert excluded_secret["results"] == []

    outside_include_globs = get_code_context_from_config(
        symbol_or_file="private/hidden.py", config_path=config_path, repo_id="app"
    )
    assert outside_include_globs["results"] == []


def test_direct_file_context_requires_ready_index(tmp_path: Path):
    config_path, _ops_repo, _work_repo, state_dir = configured_project(tmp_path)
    assert not state_dir.exists()

    payload = get_code_context_from_config(
        symbol_or_file="src/example.py", config_path=config_path, repo_id="app"
    )

    assert payload["results"] == []
    assert payload["error"]["code"] == "INDEX_NOT_READY"
    assert "compiled evidence" not in payload["markdown"]


def test_direct_file_context_requires_indexed_work_repo_and_file(tmp_path: Path):
    config_path, ops_repo, _work_repo, state_dir = configured_project(tmp_path)
    index_repo(
        ops_repo,
        state_dir=state_dir,
        repo_id="ops",
        role="ops",
        writable=True,
        include_globs=["README.md", "docs/**/*.md", "*.md"],
        exclude_globs=[".git/**", ".project-knowledge/**"],
    )
    assert state_dir.exists()

    payload = get_code_context_from_config(
        symbol_or_file="src/example.py", config_path=config_path, repo_id="app"
    )

    assert payload["results"] == []
    assert "compiled evidence" not in payload["markdown"]


def test_direct_file_context_fails_closed_after_unindexed_file_edits(tmp_path: Path):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    index_project_from_config(config_path=config_path)
    (work_repo / "src/example.py").write_text(
        "UNINDEXED_SECRET_MARKER = 'should not leak'\n", encoding="utf-8"
    )

    payload = get_code_context_from_config(
        symbol_or_file="src/example.py", config_path=config_path, repo_id="app"
    )

    assert payload["results"] == []
    assert payload["error"]["code"] == "INDEX_NOT_READY"
    assert "UNINDEXED_SECRET_MARKER" not in payload["markdown"]
    assert "compile_context" not in payload["markdown"]


def test_search_code_fails_closed_after_unindexed_file_edits(tmp_path: Path):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    index_project_from_config(config_path=config_path)
    (work_repo / "src/example.py").write_text(
        "class compile_context_SECRET_TOKEN:\n    pass\n", encoding="utf-8"
    )

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["results"] == []
    assert payload["error"]["code"] == "INDEX_NOT_READY"
    assert "SECRET_TOKEN" not in payload["markdown"]
    assert "ExampleService" not in payload["markdown"]


def test_direct_file_context_does_not_return_live_related_paths(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'include_globs: ["README.md", "src/**/*.py", "tests/**/*.py", "schemas/**/*.json"]',
            'include_globs: ["README.md", "src/**/*.py", "schemas/**/*.json"]',
        ),
        encoding="utf-8",
    )
    index_project_from_config(config_path=config_path)

    payload = get_code_context_from_config(
        symbol_or_file="src/example.py", config_path=config_path, repo_id="app"
    )

    assert payload["results"]
    assert payload["results"][0]["related"] == []


def test_code_context_rejects_stale_index_repo_path_mismatch(tmp_path: Path):
    config_path, ops_repo, old_work_repo, state_dir = configured_project(tmp_path)
    (old_work_repo / "src/example.py").write_text(
        "class StaleOutOfScopeSecretMarker:\n    pass\n", encoding="utf-8"
    )
    index_project_from_config(config_path=config_path)

    new_work_repo = tmp_path / "new-work"
    init_git_repo(new_work_repo)
    write_file(
        new_work_repo,
        "src/example.py",
        """
        class NewInScopeService:
            pass
        """,
    )
    config_path = write_project_config(
        tmp_path, ops_repo=ops_repo, work_repo=new_work_repo, state_dir=state_dir
    )

    search_payload = search_code_from_config(
        query="StaleOutOfScopeSecretMarker", config_path=config_path, repo_id="app"
    )
    assert search_payload["results"] == []
    assert search_payload["error"]["code"] == "INDEX_NOT_READY"
    assert "StaleOutOfScopeSecretMarker" not in search_payload["markdown"]

    context_payload = get_code_context_from_config(
        symbol_or_file="src/example.py", config_path=config_path, repo_id="app"
    )
    assert context_payload["results"] == []
    assert context_payload["error"]["code"] == "INDEX_NOT_READY"
    assert "StaleOutOfScopeSecretMarker" not in context_payload["markdown"]


def test_code_context_rejects_stale_index_source_provenance_mismatch(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    index_project_from_config(config_path=config_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "    writable: false\n",
            "    source_mode: snapshot\n    snapshot_ref: refs/heads/main\n    snapshot_commit: abc123\n    writable: false\n",
            1,
        ),
        encoding="utf-8",
    )

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app"
    )

    assert payload["results"] == []
    assert payload["error"]["code"] == "INDEX_NOT_READY"
    assert "compile_context" not in payload["markdown"]


def test_code_context_rejects_stale_index_scope_glob_mismatch(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    index_project_from_config(config_path=config_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'exclude_globs: [".git/**", ".project-knowledge/**"]',
            'exclude_globs: [".git/**", ".project-knowledge/**", "src/**"]',
            2,
        ),
        encoding="utf-8",
    )

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app"
    )

    assert payload["results"] == []
    assert payload["error"]["code"] == "INDEX_NOT_READY"
    assert "compile_context" not in payload["markdown"]


def test_code_context_rejects_stale_index_max_file_bytes_mismatch(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    index_project_from_config(config_path=config_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "retrieval:",
            "indexing:\n  provider: local_parser_registry\n  max_file_bytes: 10\n  chunk_target_chars: 1800\n  chunk_overlap_chars: 200\nretrieval:",
        ),
        encoding="utf-8",
    )

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app"
    )

    assert payload["results"] == []
    assert payload["error"]["code"] == "INDEX_NOT_READY"
    assert "compile_context" not in payload["markdown"]


def test_snapshot_code_context_rejects_dirty_worktree_after_indexing(tmp_path: Path):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    subprocess.run(["git", "add", "."], cwd=work_repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=work_repo, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work_repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "    writable: false\n",
            f"    source_mode: snapshot\n    snapshot_ref: refs/heads/master\n    snapshot_commit: {commit}\n    writable: false\n",
            1,
        ),
        encoding="utf-8",
    )
    index_project_from_config(config_path=config_path, repo_id="app")
    (work_repo / "src/example.py").write_text(
        "class DirtyAfterIndexSecretMarker:\n    pass\n", encoding="utf-8"
    )

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app"
    )

    assert payload["results"] == []
    assert payload["error"]["code"] == "INDEX_NOT_READY"
    assert "DirtyAfterIndexSecretMarker" not in payload["markdown"]
    assert "compile_context" not in payload["markdown"]


def test_workspace_indexing_rejects_dirty_tree_when_uncommitted_changes_excluded(
    tmp_path: Path,
):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    subprocess.run(["git", "add", "."], cwd=work_repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=work_repo, check=True)
    (work_repo / "src/example.py").write_text(
        "class DirtyWorkspaceSecretMarker:\n    pass\n", encoding="utf-8"
    )
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "    writable: false\n",
            "    includes_uncommitted_changes: false\n    writable: false\n",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="includes_uncommitted_changes"):
        index_project_from_config(config_path=config_path, repo_id="app")


def test_snapshot_code_context_indexing_rejects_dirty_worktree(tmp_path: Path):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    subprocess.run(["git", "add", "."], cwd=work_repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=work_repo, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work_repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (work_repo / "src/example.py").write_text(
        "class DirtySnapshotSecretMarker:\n    pass\n", encoding="utf-8"
    )
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "    writable: false\n",
            f"    source_mode: snapshot\n    snapshot_ref: refs/heads/master\n    snapshot_commit: {commit}\n    writable: false\n",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="snapshot"):
        index_project_from_config(config_path=config_path, repo_id="app")


def test_snapshot_code_context_indexing_rejects_unreadable_git_status(tmp_path: Path):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    subprocess.run(["git", "add", "."], cwd=work_repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=work_repo, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work_repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (work_repo / ".git" / "index").write_text("not a valid git index", encoding="utf-8")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "    writable: false\n",
            f"    source_mode: snapshot\n    snapshot_ref: refs/heads/master\n    snapshot_commit: {commit}\n    writable: false\n",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="git status"):
        index_project_from_config(config_path=config_path, repo_id="app")


def test_direct_file_context_respects_max_file_bytes(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "retrieval:",
            "indexing:\n  provider: local_parser_registry\n  max_file_bytes: 10\n  chunk_target_chars: 1800\n  chunk_overlap_chars: 200\nretrieval:",
        ),
        encoding="utf-8",
    )
    index_project_from_config(config_path=config_path)

    payload = get_code_context_from_config(
        symbol_or_file="src/example.py", config_path=config_path, repo_id="app"
    )

    assert payload["results"] == []
    assert "compiled evidence" not in payload["markdown"]


def test_code_context_fails_closed_on_corrupt_index_db(tmp_path: Path):
    config_path, _ops_repo, _work_repo, state_dir = configured_project(tmp_path)
    index_project_from_config(config_path=config_path)
    (state_dir / "index.sqlite3").write_text("not sqlite", encoding="utf-8")

    search_payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app"
    )
    assert search_payload["results"] == []
    assert search_payload["error"]["code"] == "INDEX_NOT_READY"
    assert "compile_context" not in search_payload["markdown"]

    context_payload = get_code_context_from_config(
        symbol_or_file="src/example.py", config_path=config_path, repo_id="app"
    )
    assert context_payload["results"] == []
    assert context_payload["error"]["code"] == "INDEX_NOT_READY"
    assert "compiled evidence" not in context_payload["markdown"]


def test_code_context_honors_disabled_text_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(CodeGraphContextProvider, "_package_installed", lambda self: False)
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "fallback_on_unhealthy: true", "fallback_on_unhealthy: false"
        ),
        encoding="utf-8",
    )
    index_project_from_config(config_path=config_path)

    status = get_code_provider_status_from_config(config_path=config_path)
    assert status["active_provider"] == "unavailable"
    assert status["fallback_available"] is False

    payload = search_code_from_config(
        query="compile_context", config_path=config_path, repo_id="app"
    )
    assert payload["results"] == []
    assert payload["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert payload["error"]["details"]["active_provider"] == "unavailable"
