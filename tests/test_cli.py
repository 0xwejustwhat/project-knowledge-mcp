from __future__ import annotations

import json
import subprocess
from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from project_knowledge_mcp.server import app


def test_cli_help_lists_step1_index_commands():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "index-project" in result.output
    assert "search-index" in result.output
    assert "search-decisions" in result.output
    assert "get-current-doctrine" in result.output
    assert "search-open-questions" in result.output
    assert "search-code" in result.output
    assert "get-code-context" in result.output
    assert "get-code-provider-status" in result.output
    assert "retrieve-ops-code-evidence" in result.output
    assert "generate-session-brief" in result.output
    assert "setup" in result.output
    assert "status" in result.output
    assert "print-client-config" in result.output


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


def test_setup_dry_run_generates_config_mount_and_client_instructions(tmp_path: Path):
    ops_repo = tmp_path / "ops"
    work_repo = tmp_path / "work"
    init_git_repo(ops_repo)
    init_git_repo(work_repo)
    config_path = tmp_path / "project.yaml"

    result = CliRunner().invoke(
        app,
        [
            "setup",
            "--non-interactive",
            "--dry-run",
            "--config",
            str(config_path),
            "--project-root",
            str(tmp_path),
            "--ops-repo",
            str(ops_repo),
            "--work-repo",
            str(work_repo),
            "--client",
            "hermes",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["dry_run"] is True
    assert payload["would_write"] is False
    assert payload["artifacts"]["config"]["path"] == str(config_path.resolve())
    assert config_path.exists() is False
    assert payload["config"]["retrieval"]["mode"] == "local_no_llm"
    assert payload["config"]["retrieval"]["llm_enabled"] is False
    assert payload["config"]["repos"][0]["role"] == "ops"
    assert payload["docker"]["network_exposure"] == "loopback_only"
    assert "127.0.0.1" in payload["docker"]["run_command"]
    assert payload["docker"]["compose_command"] == (
        "docker compose -f docker-compose.example.yaml up --build project-knowledge-mcp"
    )
    assert payload["client_configs"]["hermes"]["transport"] == "stdio"
    assert payload["client_configs"]["hermes"]["config"]["env"]["PROJECT_KNOWLEDGE_CONFIG"] == str(
        config_path.resolve()
    )
    assert payload["safety"]["starts_services"] is False
    assert payload["safety"]["secrets_written"] is False
    assert payload["remote_bridge"]["enabled_by_default"] is False
    assert payload["remote_bridge"]["requires_https"] is True
    assert payload["remote_bridge"]["authorization_header"] == "Authorization: Bearer [REDACTED]"


def test_setup_writes_valid_config_and_refuses_overwrite_without_force(tmp_path: Path):
    ops_repo = tmp_path / "ops"
    work_repo = tmp_path / "work"
    init_git_repo(ops_repo)
    init_git_repo(work_repo)
    config_path = tmp_path / "project.yaml"

    result = CliRunner().invoke(
        app,
        [
            "setup",
            "--non-interactive",
            "--config",
            str(config_path),
            "--project-root",
            str(tmp_path),
            "--ops-repo",
            str(ops_repo),
            "--work-repo",
            str(work_repo),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["would_write"] is True
    assert payload["artifacts"]["config"]["written"] is True
    assert config_path.exists()

    validation = CliRunner().invoke(app, ["validate-config", "--config", str(config_path)])
    assert validation.exit_code == 0, validation.output
    assert json.loads(validation.output)["valid"] is True

    refused = CliRunner().invoke(
        app,
        [
            "setup",
            "--non-interactive",
            "--config",
            str(config_path),
            "--project-root",
            str(tmp_path),
            "--ops-repo",
            str(ops_repo),
        ],
    )
    assert refused.exit_code != 0
    assert "already exists" in refused.output


def test_print_client_config_outputs_policy_enforced_connection_snippets(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    stdio_result = CliRunner().invoke(
        app,
        [
            "print-client-config",
            "--config",
            str(config_path),
            "--client",
            "hermes",
            "--transport",
            "stdio",
        ],
    )
    assert stdio_result.exit_code == 0, stdio_result.output
    stdio = json.loads(stdio_result.output)
    assert stdio["client"] == "hermes"
    assert stdio["transport"] == "stdio"
    assert stdio["config"]["command"] == "project-knowledge"
    assert stdio["config"]["args"] == ["serve"]
    assert stdio["config"]["env"]["PROJECT_KNOWLEDGE_CONFIG"] == str(config_path.resolve())
    assert "token" not in json.dumps(stdio).lower()

    remote_result = CliRunner().invoke(
        app,
        [
            "print-client-config",
            "--config",
            str(config_path),
            "--client",
            "generic",
            "--transport",
            "remote-https",
            "--remote-url",
            "https://pkmcp.example.com/mcp",
        ],
    )
    assert remote_result.exit_code == 0, remote_result.output
    remote = json.loads(remote_result.output)
    assert remote["client"] == "generic"
    assert remote["transport"] == "remote-https"
    assert remote["config"]["url"] == "https://pkmcp.example.com/mcp"
    assert remote["config"]["headers"]["Authorization"] == "Bearer [REDACTED]"
    assert remote["safety"]["remote_requires_explicit_bridge"] is True
    assert "secret" not in json.dumps(remote).lower()

    insecure_remote = CliRunner().invoke(
        app,
        [
            "print-client-config",
            "--config",
            str(config_path),
            "--client",
            "generic",
            "--transport",
            "remote-https",
            "--remote-url",
            "http://pkmcp.example.com/mcp",
        ],
    )
    assert insecure_remote.exit_code != 0
    assert "https" in insecure_remote.output.lower()


def test_status_reports_config_and_repo_freshness(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    (repo / "README.md").write_text("# Ops\n", encoding="utf-8")
    commit_all(repo, "initial")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    result = CliRunner().invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["config"]["valid"] is True
    assert payload["project_id"] == "project-knowledge-mcp"
    assert payload["staleness"]["status"] == "ok"
    assert payload["staleness"]["repos"][0]["repo_id"] == "ops"
    assert "## Project Staleness" in payload["markdown"]


def test_print_client_config_rejects_unknown_client_or_transport(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    bad_transport = CliRunner().invoke(
        app,
        [
            "print-client-config",
            "--config",
            str(config_path),
            "--client",
            "generic",
            "--transport",
            "bogus",
        ],
    )
    assert bad_transport.exit_code != 0
    assert "transport" in bad_transport.output

    bad_client = CliRunner().invoke(
        app,
        [
            "print-client-config",
            "--config",
            str(config_path),
            "--client",
            "unknown",
            "--transport",
            "stdio",
        ],
    )
    assert bad_client.exit_code != 0
    assert "client" in bad_client.output


def test_setup_dry_run_can_load_existing_config_without_repo_arguments(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    result = CliRunner().invoke(
        app,
        [
            "setup",
            "--non-interactive",
            "--dry-run",
            "--config",
            str(config_path),
            "--client",
            "hermes",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["artifacts"]["config"]["exists"] is True
    assert payload["config"]["project"]["id"] == "project-knowledge-mcp"
    assert payload["client_configs"]["hermes"]["config"]["env"]["PROJECT_KNOWLEDGE_CONFIG"] == str(
        config_path.resolve()
    )


def test_setup_docker_guidance_starts_loopback_http_and_mounts_repo_paths(tmp_path: Path):
    root_with_space = tmp_path / "project root"
    ops_repo = tmp_path / "external ops"
    work_repo = tmp_path / "external work"
    root_with_space.mkdir()
    init_git_repo(ops_repo)
    init_git_repo(work_repo)
    config_path = root_with_space / "project.yaml"

    result = CliRunner().invoke(
        app,
        [
            "setup",
            "--non-interactive",
            "--dry-run",
            "--config",
            str(config_path),
            "--project-root",
            str(root_with_space),
            "--ops-repo",
            str(ops_repo),
            "--work-repo",
            str(work_repo),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    run_command = payload["docker"]["run_command"]
    assert "start --transport streamable-http --host 0.0.0.0 --port 8000" in run_command
    assert "'" in run_command
    mount_host_paths = {mount["host_path"] for mount in payload["docker"]["repo_mounts"]}
    assert str(ops_repo.resolve()) in mount_host_paths
    assert str(work_repo.resolve()) in mount_host_paths


def test_cli_indexes_and_searches_project(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / "state"
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)
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

    config_search_result = CliRunner().invoke(
        app,
        [
            "search-index",
            "evidence packets synthesis",
            "--config",
            str(config_path),
            "--limit",
            "3",
        ],
    )

    assert config_search_result.exit_code == 0, config_search_result.output
    config_payload = json.loads(config_search_result.output)
    assert config_payload["results"][0]["path"] == "docs/doctrine/context.md"


def test_cli_validate_config_index_project_and_search_ops_from_config(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    doc = repo / "docs" / "doctrine" / "context.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        """---
title: Context Doctrine
type: doctrine
status: current
authority: canonical
tags: [context]
---
# Context Doctrine

Project Knowledge MCP returns evidence packets before synthesis.
""",
        encoding="utf-8",
    )
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    validation_result = CliRunner().invoke(app, ["validate-config", "--config", str(config_path)])
    assert validation_result.exit_code == 0, validation_result.output
    validation = json.loads(validation_result.output)
    assert validation["valid"] is True
    assert validation["project_id"] == "project-knowledge-mcp"
    assert validation["repos"][0]["id"] == "ops"

    index_result = CliRunner().invoke(app, ["index-project", "--config", str(config_path)])
    assert index_result.exit_code == 0, index_result.output
    summary = json.loads(index_result.output)
    assert summary["status"] == "ok"
    assert summary["repos"][0]["repo_id"] == "ops"
    assert summary["repos"][0]["documents_indexed"] == 1
    assert summary["repos"][0]["chunks_indexed"] == 1

    scoped_index_result = CliRunner().invoke(
        app, ["index-project", "--config", str(config_path), "--repo-id", "ops"]
    )
    assert scoped_index_result.exit_code == 0, scoped_index_result.output
    scoped_summary = json.loads(scoped_index_result.output)
    assert [repo_summary["repo_id"] for repo_summary in scoped_summary["repos"]] == ["ops"]

    missing_repo_result = CliRunner().invoke(
        app, ["index-project", "--config", str(config_path), "--repo-id", "missing"]
    )
    assert missing_repo_result.exit_code != 0
    assert "Unknown repo_id" in missing_repo_result.output

    search_result = CliRunner().invoke(
        app,
        [
            "search-ops",
            "evidence packets synthesis",
            "--config",
            str(config_path),
            "--limit",
            "3",
        ],
    )
    assert search_result.exit_code == 0, search_result.output
    payload = json.loads(search_result.output)
    assert payload["query"] == "evidence packets synthesis"
    assert payload["results"][0]["path"] == "docs/doctrine/context.md"
    assert payload["results"][0]["authority"] == "canonical"
    assert "markdown" in payload
    assert "## Search Results" in payload["markdown"]


def test_cli_phase4_search_commands_from_config(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    docs = {
        "docs/doctrine/context.md": """---
title: Context Doctrine
type: doctrine
status: current
authority: canonical
---
# Context Doctrine

Context retrieval doctrine is current truth.
""",
        "docs/decisions/accepted/0001-context.md": """---
title: Accepted Context Decision
type: decision
status: accepted
authority: accepted_decision
---
# Accepted Context Decision

Accepted context retrieval decision.
""",
        "docs/open-questions/context.md": """---
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
    }
    for relative_path, text in docs.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    index_result = CliRunner().invoke(app, ["index-project", "--config", str(config_path)])
    assert index_result.exit_code == 0, index_result.output

    decision_result = CliRunner().invoke(
        app, ["search-decisions", "context retrieval", "--config", str(config_path), "--limit", "3"]
    )
    assert decision_result.exit_code == 0, decision_result.output
    decisions = json.loads(decision_result.output)
    assert decisions["results"][0]["path"] == "docs/decisions/accepted/0001-context.md"
    assert "markdown" in decisions

    doctrine_result = CliRunner().invoke(
        app, ["get-current-doctrine", "context retrieval", "--config", str(config_path)]
    )
    assert doctrine_result.exit_code == 0, doctrine_result.output
    doctrine = json.loads(doctrine_result.output)
    assert doctrine["doctrine"][0]["path"] == "docs/doctrine/context.md"
    assert doctrine["decisions"][0]["path"] == "docs/decisions/accepted/0001-context.md"

    question_result = CliRunner().invoke(
        app,
        [
            "search-open-questions",
            "context retrieval",
            "--config",
            str(config_path),
            "--limit",
            "3",
        ],
    )
    assert question_result.exit_code == 0, question_result.output
    questions = json.loads(question_result.output)
    assert questions["results"][0]["path"] == "docs/open-questions/context.md"
    assert questions["results"][0]["owner"] == "Amin"


def test_cli_code_context_commands_from_config(tmp_path: Path):
    ops_repo = tmp_path / "ops"
    work_repo = tmp_path / "work"
    state = tmp_path / ".project-knowledge"
    init_git_repo(ops_repo)
    init_git_repo(work_repo)
    ops_doc = ops_repo / "docs" / "doctrine" / "context.md"
    ops_doc.parent.mkdir(parents=True, exist_ok=True)
    ops_doc.write_text("# Context\n\nOps doctrine.\n", encoding="utf-8")
    code_file = work_repo / "src" / "example.py"
    code_file.parent.mkdir(parents=True, exist_ok=True)
    code_file.write_text(
        """
class ExampleService:
    def compile_context(self, topic: str) -> str:
        return f"compiled evidence for {topic}"
""".strip()
        + "\n",
        encoding="utf-8",
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
                include_globs: ["src/**/*.py"]
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

    index_result = CliRunner().invoke(app, ["index-project", "--config", str(config_path)])
    assert index_result.exit_code == 0, index_result.output

    status_result = CliRunner().invoke(
        app, ["get-code-provider-status", "--config", str(config_path)]
    )
    assert status_result.exit_code == 0, status_result.output
    status = json.loads(status_result.output)
    assert status["configured_provider"] == "codegraph"
    assert status["active_provider"] == "text"
    assert status["work_repos"] == ["app"]

    search_result = CliRunner().invoke(
        app,
        [
            "search-code",
            "compile_context evidence",
            "--config",
            str(config_path),
            "--repo-id",
            "app",
        ],
    )
    assert search_result.exit_code == 0, search_result.output
    search = json.loads(search_result.output)
    assert search["tool"] == "search_code"
    assert search["results"][0]["path"] == "src/example.py"

    context_result = CliRunner().invoke(
        app,
        ["get-code-context", "ExampleService", "--config", str(config_path), "--repo-id", "app"],
    )
    assert context_result.exit_code == 0, context_result.output
    context = json.loads(context_result.output)
    assert context["tool"] == "get_code_context"
    assert context["results"][0]["symbol"] == "ExampleService.compile_context"

    evidence_result = CliRunner().invoke(
        app,
        [
            "retrieve-ops-code-evidence",
            "compile_context evidence",
            "--config",
            str(config_path),
            "--limit",
            "3",
        ],
    )
    assert evidence_result.exit_code == 0, evidence_result.output
    evidence = json.loads(evidence_result.output)
    assert evidence["tool"] == "retrieve_ops_code_evidence"
    assert sorted(evidence["sections"]) == ["code", "decisions", "doctrine", "open_questions"]
    assert evidence["sections"]["code"][0]["path"] == "src/example.py"

    brief_result = CliRunner().invoke(
        app,
        [
            "generate-session-brief",
            "Implement compile_context evidence",
            "--config",
            str(config_path),
            "--since",
            "2026-06-01",
            "--limit",
            "3",
        ],
    )
    assert brief_result.exit_code == 0, brief_result.output
    brief = json.loads(brief_result.output)
    assert brief["tool"] == "generate_session_brief"
    assert brief["since"] == "2026-06-01"
    assert brief["sections"]["code"][0]["path"] == "src/example.py"
    assert "## Session Brief" in brief["markdown"]


def test_cli_check_project_staleness_from_config(tmp_path: Path):
    repo = tmp_path / "ops"
    state = tmp_path / ".project-knowledge"
    init_git_repo(repo)
    doc = repo / "docs" / "doctrine" / "context.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Context\n\nBaseline indexed evidence.\n", encoding="utf-8")
    indexed_commit = commit_all(repo, "baseline")
    config_path = write_project_config(tmp_path, repo=repo, state_dir=state)

    index_result = CliRunner().invoke(app, ["index-project", "--config", str(config_path)])
    assert index_result.exit_code == 0, index_result.output

    doc.write_text("# Context\n\nChanged workspace evidence.\n", encoding="utf-8")
    (repo / "docs" / "notes").mkdir(parents=True)
    (repo / "docs" / "notes" / "untracked.md").write_text("# Note\n", encoding="utf-8")

    status_result = CliRunner().invoke(
        app, ["check-project-staleness", "--config", str(config_path)]
    )
    assert status_result.exit_code == 0, status_result.output
    payload = json.loads(status_result.output)
    assert payload["status"] == "ok"
    repo_status = payload["repos"][0]
    assert repo_status["repo_id"] == "ops"
    assert repo_status["head_commit"] == indexed_commit
    assert repo_status["last_indexed_commit"] == indexed_commit
    assert repo_status["dirty"] is True
    assert repo_status["untracked_count"] == 1
    assert repo_status["reindex_needed"] is True
    assert "## Project Staleness" in payload["markdown"]
