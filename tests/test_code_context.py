from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from project_knowledge_mcp.index import index_repo
from project_knowledge_mcp.services import (
    get_code_context_from_config,
    get_code_provider_status_from_config,
    index_project_from_config,
    retrieve_ops_code_evidence_from_config,
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


def write_fake_codegraph(
    root: Path,
    *,
    initialized: bool = True,
    fail_explore: bool = False,
    unparseable_explore: bool = False,
    unfenced_explore: bool = False,
    unsafe_explore_path: bool = False,
    env_explore_path: bool = False,
    env_dir_explore_path: bool = False,
    hostile_explore_snippet: bool = False,
    unnumbered_explore_snippet: bool = False,
    malformed_fence_explore: bool = False,
    symlink_explore_path: bool = False,
    symlink_dir_explore_path: bool = False,
    unparseable_node: bool = False,
    wrong_status_path: bool = False,
    omit_status_project_path: bool = False,
    omit_status_index_path: bool = False,
    initialized_text_false: bool = False,
    hostile_status_fields: bool = False,
    empty_node_snippet: bool = False,
    hostile_node_metadata: bool = False,
) -> Path:
    command = root / "fake-codegraph"
    command.write_text(
        dedent(
            f'''
            #!/usr/bin/env python3
            import json
            import os
            import sys

            args = sys.argv[1:]
            if os.environ.get("DO_NOT_TRACK") != "1" or os.environ.get("CODEGRAPH_TELEMETRY") != "0":
                print("telemetry env not disabled", file=sys.stderr)
                sys.exit(9)

            if args and args[0] == "status":
                project_path = "/tmp/not-the-configured-repo" if {str(wrong_status_path)} else args[1] if len(args) > 1 else "."
                status = {{
                    "initialized": "false" if {str(initialized_text_false)} else {str(initialized)},
                    "version": "1.0.0",
                    "fileCount": 3,
                    "nodeCount": 8,
                    "edgeCount": 5,
                    "languages": ["python", "json"],
                    "pendingChanges": {{"added": 0, "modified": 0, "removed": 0}},
                }}
                if not {str(omit_status_index_path)}:
                    status["indexPath"] = project_path + "/.codegraph"
                if not {str(omit_status_project_path)}:
                    status["projectPath"] = project_path
                if {str(hostile_status_fields)}:
                    status["version"] = "--token=supersecret"
                    status["languages"] = ["python", "../../secret"]
                    status["pendingChanges"] = {{"added": 1, "secret": "do-not-return"}}
                print(json.dumps(status))
                sys.exit(0)

            if args and args[0] == "explore":
                if {str(fail_explore)}:
                    print("explore failed", file=sys.stderr)
                    sys.exit(7)
                if {str(unparseable_explore)}:
                    print("""## Provider diagnostic

No concrete code result was found.
Raw provider internals should not be exposed.
""")
                    sys.exit(0)
                if {str(unfenced_explore)}:
                    print("""## Exploration: compile_context evidence

Found 1 symbol across 1 file.

#### src/example.py — ExampleService(class)

Provider diagnostic with no fenced source block.
Raw provider internals should not be exposed.
""")
                    sys.exit(0)
                if {str(unsafe_explore_path)}:
                    print("""## Exploration: compile_context evidence

Found 1 symbol across 1 file.

#### ../secret_config.py — leaked(function)

```python
1\tPLACEHOLDER = \"do-not-return\"
```
""")
                    sys.exit(0)
                if {str(env_explore_path)}:
                    print("""## Exploration: compile_context evidence

Found 1 symbol across 1 file.

#### .env.py — leaked(function)

```python
1\tPLACEHOLDER = \"do-not-return\"
```
""")
                    sys.exit(0)
                if {str(env_dir_explore_path)}:
                    print("""## Exploration: compile_context evidence

Found 1 symbol across 1 file.

#### config/.env/settings.py — leaked(function)

```python
1\tPLACEHOLDER = \"do-not-return\"
```
""")
                    sys.exit(0)
                if {str(hostile_explore_snippet)}:
                    print("""## Exploration: compile_context evidence

Found 1 symbol across 1 file.

#### src/example.py — --token=supersecret(secret)

```python
1\tPLACEHOLDER = \"do-not-return\"
```
""")
                    sys.exit(0)
                if {str(unnumbered_explore_snippet)}:
                    print("""## Exploration: compile_context evidence

Found 1 symbol across 1 file.

#### src/example.py — ExampleService(class)

```python
class ExampleService:
    PLACEHOLDER = \"do-not-return\"
```
""")
                    sys.exit(0)
                if {str(malformed_fence_explore)}:
                    print("""## Exploration: compile_context evidence

Found 1 symbol across 1 file.

#### src/example.py — ExampleService(class)

```python
1\tclass ExampleService:
2\t    PLACEHOLDER = \"do-not-return\"
""")
                    sys.exit(0)
                if {str(symlink_explore_path)}:
                    print("""## Exploration: compile_context evidence

Found 1 symbol across 1 file.

#### src/link.py — linked(function)

```python
1\tprint(\"symlink target\")
```
""")
                    sys.exit(0)
                if {str(symlink_dir_explore_path)}:
                    print("""## Exploration: compile_context evidence

Found 1 symbol across 1 file.

#### src/alias/example.py — linked(function)

```python
1\tprint(\"symlink directory target\")
```
""")
                    sys.exit(0)
                print("""## Exploration: compile_context evidence

Found 3 symbols across 3 files.

#### src/example.py — ExampleService(class), compile_context(method)

```python
1\tclass ExampleService:
2\t    def compile_context(self, topic: str) -> str:
3\t        return f\"compiled evidence for {{topic}}\"
```

#### tests/test_example.py — test_compile_context_returns_evidence(function)

```python
1\tfrom src.example import ExampleService
2\tdef test_compile_context_returns_evidence():
3\t    assert ExampleService().compile_context(\"brief\")
```

#### schemas/example.schema.json — ExampleEvidence(schema)

```json
1\t{{\"title\": \"ExampleEvidence\"}}
```
""")
                sys.exit(0)

            if args and args[0] == "node":
                if {str(unparseable_node)}:
                    print("""## Provider diagnostic

No concrete node location was found for this query.

Raw provider internals should not be exposed.
""")
                    sys.exit(0)
                name = args[-1]
                if {str(empty_node_snippet)}:
                    print("""## ExampleService (class)

**Location:** src/example.py:1
**Signature:** `class ExampleService`

```python

```

Provider diagnostic with no source should not leak.
""")
                    sys.exit(0)
                if {str(hostile_node_metadata)}:
                    print("""## --token=supersecret (secret)

**Location:** src/example.py:1
**Signature:** `class ExampleService`

```python
1\tPLACEHOLDER = \"do-not-return\"
```

**Called by ←** --secret=do-not-return (tests/test_example.py:3)
**Related files:** schemas/example.schema.json, ../secret.py
""")
                    sys.exit(0)
                if name == "ExampleService":
                    print("""## ExampleService (class)

**Location:** src/example.py:1
**Signature:** `class ExampleService`

```python
1\tclass ExampleService:
2\t    def compile_context(self, topic: str) -> str:
3\t        return f\"compiled evidence for {{topic}}\"
```

**Called by ←** test_compile_context_returns_evidence (tests/test_example.py:3)
**Related files:** schemas/example.schema.json
""")
                    sys.exit(0)
                print("""**src/example.py** — 3 lines, 1 symbol

```python
1\tclass ExampleService:
2\t    def compile_context(self, topic: str) -> str:
3\t        return f\"compiled evidence for {{topic}}\"
```
""")
                sys.exit(0)

            print("unexpected args: " + repr(args), file=sys.stderr)
            sys.exit(2)
            '''
        )
        .replace("\n            ", "\n")
        .strip()
        + "\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    return command


def configure_codegraph_command(config_path: Path, command: Path) -> None:
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "    enabled: true\n", f"    enabled: true\n    command: {command.as_posix()}\n", 1
        ),
        encoding="utf-8",
    )


def test_search_code_uses_text_fallback_for_indexed_work_repo(tmp_path: Path):
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


def test_get_code_context_resolves_symbol_and_file_path(tmp_path: Path):
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


def test_code_provider_status_reports_healthy_codegraph_from_configured_command(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path)
    configure_codegraph_command(config_path, command)

    status = get_code_provider_status_from_config(config_path=config_path)

    assert status["status"] == "ok"
    assert status["configured_provider"] == "codegraph"
    assert status["active_provider"] == "codegraph"
    assert status["codegraph_healthy"] is True
    assert status["fallback_available"] is True
    assert status["warnings"] == []
    assert status["details"]["cli_path"] == command.name
    assert status["details"]["indexed_repos"] == ["app"]
    assert status["details"]["repo_statuses"][0]["file_count"] == 3
    assert status["details"]["repo_statuses"][0]["index_present"] is True
    assert status["details"]["repo_statuses"][0]["project_matches_config"] is True
    assert status["details"]["repo_statuses"][0]["languages"] == ["python", "json"]


def test_code_provider_status_sanitizes_hostile_provider_fields(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, hostile_status_fields=True)
    configure_codegraph_command(config_path, command)

    status = get_code_provider_status_from_config(config_path=config_path)

    repo_status = status["details"]["repo_statuses"][0]
    assert status["active_provider"] == "codegraph"
    assert repo_status["version"] is None
    assert repo_status["languages"] == ["python"]
    assert repo_status["pending_changes"] == {"added": 1}
    assert "supersecret" not in str(status)
    assert "--token" not in str(status)
    assert "do-not-return" not in str(status)
    assert "../../secret" not in str(status)


def test_code_provider_status_does_not_expose_configured_command_arguments(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path)
    configure_codegraph_command(config_path, command)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            f"command: {command.as_posix()}", f"command: {command.as_posix()} --token=supersecret"
        ),
        encoding="utf-8",
    )

    status = get_code_provider_status_from_config(config_path=config_path)

    assert "command" not in status["details"]
    assert status["details"]["command_configured"] is True
    assert "supersecret" not in str(status)
    assert "--token" not in str(status)


def test_search_code_prefers_codegraph_and_normalizes_public_results(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path)
    configure_codegraph_command(config_path, command)

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=5
    )

    assert payload["tool"] == "search_code"
    assert payload["active_provider"] == "codegraph"
    assert payload["warnings"] == []
    assert [result["provider"] for result in payload["results"]] == [
        "codegraph",
        "codegraph",
        "codegraph",
    ]
    first = payload["results"][0]
    assert first == {
        "repo_id": "app",
        "path": "src/example.py",
        "start_line": 1,
        "end_line": 3,
        "symbol": "ExampleService",
        "kind": "class",
        "snippet": (
            "class ExampleService:\n"
            "    def compile_context(self, topic: str) -> str:\n"
            '        return f"compiled evidence for {topic}"'
        ),
        "provider": "codegraph",
        "score": 1.0,
        "related": [],
    }
    assert "raw" not in str(payload["results"]).lower()
    assert "## Exploration" not in str(payload["results"])


def test_search_code_uses_repo_source_not_provider_fenced_text(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, hostile_explore_snippet=True)
    configure_codegraph_command(config_path, command)

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=5
    )

    assert payload["active_provider"] == "codegraph"
    assert payload["results"][0]["path"] == "src/example.py"
    assert payload["results"][0]["symbol"] is None
    assert payload["results"][0]["kind"] == "file"
    assert payload["results"][0]["snippet"] == "class ExampleService:"
    assert "supersecret" not in str(payload)
    assert "--token" not in str(payload)
    assert "do-not-return" not in str(payload)


def test_get_code_context_uses_codegraph_related_evidence(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path)
    configure_codegraph_command(config_path, command)

    payload = get_code_context_from_config(
        symbol_or_file="ExampleService", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["tool"] == "get_code_context"
    assert payload["active_provider"] == "codegraph"
    assert payload["results"][0]["repo_id"] == "app"
    assert payload["results"][0]["path"] == "src/example.py"
    assert payload["results"][0]["symbol"] == "ExampleService"
    assert payload["results"][0]["kind"] == "class"
    assert payload["results"][0]["related"] == [
        {
            "kind": "caller",
            "path": "tests/test_example.py",
            "symbol": "test_compile_context_returns_evidence",
            "line": "3",
        },
        {"kind": "related_file", "path": "schemas/example.schema.json"},
    ]


def test_get_code_context_sanitizes_provider_metadata_and_related_fields(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, hostile_node_metadata=True)
    configure_codegraph_command(config_path, command)

    payload = get_code_context_from_config(
        symbol_or_file="ExampleService", config_path=config_path, repo_id="app", limit=3
    )

    result = payload["results"][0]
    assert payload["active_provider"] == "codegraph"
    assert result["path"] == "src/example.py"
    assert result["symbol"] is None
    assert result["kind"] == "file"
    assert result["snippet"] == "class ExampleService:"
    assert result["related"] == [
        {"kind": "caller", "path": "tests/test_example.py", "line": "3"},
        {"kind": "related_file", "path": "schemas/example.schema.json"},
    ]
    assert "supersecret" not in str(payload)
    assert "--token" not in str(payload)
    assert "do-not-return" not in str(payload)
    assert "../secret.py" not in str(payload)


def test_evidence_packet_includes_graph_backed_code_results(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path)
    configure_codegraph_command(config_path, command)
    indexed = index_project_from_config(config_path=config_path, repo_id="ops")
    assert indexed["status"] == "ok"

    packet = retrieve_ops_code_evidence_from_config(
        topic="compile_context evidence", config_path=config_path, limit=3
    )

    assert packet["tool"] == "retrieve_ops_code_evidence"
    assert packet["sections"]["code"]
    assert packet["sections"]["code"][0]["provider"] == "codegraph"
    assert packet["gaps"] == []
    assert "provider: `codegraph`" in packet["markdown"]


def test_codegraph_search_failure_falls_back_with_explicit_warning(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, fail_explore=True)
    configure_codegraph_command(config_path, command)
    index_project_from_config(config_path=config_path)

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["active_provider"] == "text"
    assert payload["results"][0]["provider"] == "text"
    assert any("CodeGraph search failed" in warning for warning in payload["warnings"])
    assert "explore failed" not in str(payload)


def test_unrecognized_codegraph_explore_output_falls_back_without_raw_provider_text(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, unparseable_explore=True)
    configure_codegraph_command(config_path, command)
    index_project_from_config(config_path=config_path)

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["active_provider"] == "text"
    assert payload["results"][0]["provider"] == "text"
    assert any("CodeGraph search failed" in warning for warning in payload["warnings"])
    assert "Provider diagnostic" not in str(payload)
    assert "Raw provider internals" not in str(payload)


def test_unfenced_codegraph_explore_output_falls_back_without_raw_provider_text(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, unfenced_explore=True)
    configure_codegraph_command(config_path, command)
    index_project_from_config(config_path=config_path)

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["active_provider"] == "text"
    assert payload["results"][0]["provider"] == "text"
    assert any("CodeGraph search failed" in warning for warning in payload["warnings"])
    assert "Provider diagnostic" not in str(payload)
    assert "Raw provider internals" not in str(payload)


def test_malformed_codegraph_explore_fence_falls_back_without_raw_text(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, malformed_fence_explore=True)
    configure_codegraph_command(config_path, command)
    index_project_from_config(config_path=config_path)

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["active_provider"] == "text"
    assert payload["results"][0]["provider"] == "text"
    assert any("CodeGraph search failed" in warning for warning in payload["warnings"])
    assert "do-not-return" not in str(payload)
    assert "Provider diagnostic" not in str(payload)


def test_unnumbered_codegraph_explore_snippet_falls_back_without_raw_text(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, unnumbered_explore_snippet=True)
    configure_codegraph_command(config_path, command)
    index_project_from_config(config_path=config_path)

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["active_provider"] == "text"
    assert payload["results"][0]["provider"] == "text"
    assert any("CodeGraph search failed" in warning for warning in payload["warnings"])
    assert "do-not-return" not in str(payload)
    assert "PLACEHOLDER" not in str(payload)


def test_codegraph_rejects_env_like_provider_paths(tmp_path: Path):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    write_file(work_repo, ".env.py", 'PLACEHOLDER = "do-not-return"')
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'include_globs: ["README.md", "src/**/*.py", "tests/**/*.py", "schemas/**/*.json"]',
            'include_globs: ["README.md", "src/**/*.py", "tests/**/*.py", "schemas/**/*.json", ".env.py"]',
        ),
        encoding="utf-8",
    )
    command = write_fake_codegraph(tmp_path, env_explore_path=True)
    configure_codegraph_command(config_path, command)
    index_project_from_config(config_path=config_path)

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["active_provider"] == "text"
    assert payload["results"][0]["provider"] == "text"
    assert ".env.py" not in str(payload)
    assert "do-not-return" not in str(payload)


def test_codegraph_rejects_env_directory_provider_paths(tmp_path: Path):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    write_file(work_repo, "config/.env/settings.py", 'PLACEHOLDER = "do-not-return"')
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'include_globs: ["README.md", "src/**/*.py", "tests/**/*.py", "schemas/**/*.json"]',
            'include_globs: ["README.md", "src/**/*.py", "tests/**/*.py", "schemas/**/*.json", "config/**/*.py"]',
        ),
        encoding="utf-8",
    )
    command = write_fake_codegraph(tmp_path, env_dir_explore_path=True)
    configure_codegraph_command(config_path, command)
    index_project_from_config(config_path=config_path)

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["active_provider"] == "text"
    assert payload["results"][0]["provider"] == "text"
    assert "config/.env/settings.py" not in str(payload)
    assert "do-not-return" not in str(payload)


def test_codegraph_rejects_provider_paths_outside_repo_scope(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, unsafe_explore_path=True)
    configure_codegraph_command(config_path, command)
    index_project_from_config(config_path=config_path)

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["active_provider"] == "text"
    assert payload["results"][0]["provider"] == "text"
    assert "../secret_config.py" not in str(payload)
    assert "do-not-return" not in str(payload)


def test_codegraph_rejects_symlinked_provider_paths_before_resolution(tmp_path: Path):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    (work_repo / "src" / "link.py").symlink_to(work_repo / "src" / "example.py")
    command = write_fake_codegraph(tmp_path, symlink_explore_path=True)
    configure_codegraph_command(config_path, command)
    index_project_from_config(config_path=config_path)

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["active_provider"] == "text"
    assert payload["results"][0]["provider"] == "text"
    assert "src/link.py" not in str(payload)
    assert "symlink target" not in str(payload)


def test_codegraph_rejects_symlinked_directory_provider_paths(tmp_path: Path):
    config_path, _ops_repo, work_repo, _state_dir = configured_project(tmp_path)
    (work_repo / "src" / "alias").symlink_to(work_repo / "src", target_is_directory=True)
    command = write_fake_codegraph(tmp_path, symlink_dir_explore_path=True)
    configure_codegraph_command(config_path, command)
    index_project_from_config(config_path=config_path)

    payload = search_code_from_config(
        query="compile_context evidence", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["active_provider"] == "text"
    assert payload["results"][0]["provider"] == "text"
    assert "src/alias/example.py" not in str(payload)
    assert "symlink directory target" not in str(payload)


def test_codegraph_status_rejects_wrong_provider_project_path(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, wrong_status_path=True)
    configure_codegraph_command(config_path, command)

    status = get_code_provider_status_from_config(config_path=config_path)

    assert status["active_provider"] == "text"
    assert status["codegraph_healthy"] is False
    assert status["details"]["repo_statuses"][0]["project_matches_config"] is False
    assert "/tmp/not-the-configured-repo" not in str(status)


def test_codegraph_status_rejects_missing_provider_project_path(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, omit_status_project_path=True)
    configure_codegraph_command(config_path, command)

    status = get_code_provider_status_from_config(config_path=config_path)

    assert status["active_provider"] == "text"
    assert status["codegraph_healthy"] is False
    assert status["details"]["repo_statuses"][0]["project_matches_config"] is False


def test_codegraph_status_requires_boolean_initialized_true(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, initialized_text_false=True)
    configure_codegraph_command(config_path, command)

    status = get_code_provider_status_from_config(config_path=config_path)

    repo_status = status["details"]["repo_statuses"][0]
    assert status["active_provider"] == "text"
    assert status["codegraph_healthy"] is False
    assert repo_status["initialized"] is False
    assert repo_status["index_present"] is True


def test_codegraph_status_requires_index_path(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, omit_status_index_path=True)
    configure_codegraph_command(config_path, command)

    status = get_code_provider_status_from_config(config_path=config_path)

    repo_status = status["details"]["repo_statuses"][0]
    assert status["active_provider"] == "text"
    assert status["codegraph_healthy"] is False
    assert repo_status["initialized"] is True
    assert repo_status["index_present"] is False


def test_malformed_codegraph_command_soft_fails_to_text_fallback(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "    enabled: true\n", "    enabled: true\n    command: '\"unterminated'\n", 1
        ),
        encoding="utf-8",
    )

    status = get_code_provider_status_from_config(config_path=config_path)

    assert status["active_provider"] == "text"
    assert status["codegraph_healthy"] is False
    assert any("command configuration is invalid" in warning for warning in status["warnings"])
    assert "unterminated" not in str(status)


def test_option_only_codegraph_command_soft_fails_without_argv_leak(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "    enabled: true\n", "    enabled: true\n    command: --token=supersecret\n", 1
        ),
        encoding="utf-8",
    )

    status = get_code_provider_status_from_config(config_path=config_path)

    assert status["active_provider"] == "text"
    assert status["codegraph_healthy"] is False
    assert status["details"]["cli_path"] is None
    assert any("command configuration is invalid" in warning for warning in status["warnings"])
    assert "supersecret" not in str(status)
    assert "--token" not in str(status)


def test_unrecognized_codegraph_node_output_falls_back_without_raw_provider_text(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, unparseable_node=True)
    configure_codegraph_command(config_path, command)
    index_project_from_config(config_path=config_path)

    payload = get_code_context_from_config(
        symbol_or_file="ExampleService", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["active_provider"] == "text"
    assert payload["results"][0]["provider"] == "text"
    assert any("CodeGraph context lookup failed" in warning for warning in payload["warnings"])
    assert "Provider diagnostic" not in str(payload)
    assert "Raw provider internals" not in str(payload)


def test_empty_codegraph_node_snippet_falls_back_without_raw_provider_text(tmp_path: Path):
    config_path, _ops_repo, _work_repo, _state_dir = configured_project(tmp_path)
    command = write_fake_codegraph(tmp_path, empty_node_snippet=True)
    configure_codegraph_command(config_path, command)
    index_project_from_config(config_path=config_path)

    payload = get_code_context_from_config(
        symbol_or_file="ExampleService", config_path=config_path, repo_id="app", limit=3
    )

    assert payload["active_provider"] == "text"
    assert payload["results"][0]["provider"] == "text"
    assert any("CodeGraph context lookup failed" in warning for warning in payload["warnings"])
    assert "Provider diagnostic" not in str(payload)
    assert "no source should not leak" not in str(payload)


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


def test_code_context_honors_disabled_text_fallback(tmp_path: Path):
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
