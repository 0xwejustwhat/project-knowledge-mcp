from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

from project_knowledge_mcp.services import (
    generate_session_brief_from_config,
    get_project_state_from_config,
    index_project_from_config,
)


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


def write_doc(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).strip() + "\n", encoding="utf-8")


def write_project_config(
    root: Path,
    *,
    ops_repo: Path,
    state_dir: Path,
    work_repo: Path | None = None,
    brief_max_results_per_section: int = 8,
) -> Path:
    repo_lines = [
        "  - id: ops",
        "    role: ops",
        f"    path: {ops_repo.as_posix()}",
        "    writable: true",
        '    include_globs: ["README.md", "docs/**/*.md", "*.md"]',
    ]
    if work_repo is not None:
        repo_lines.extend(
            [
                "  - id: app",
                "    role: work",
                f"    path: {work_repo.as_posix()}",
                "    writable: false",
                '    include_globs: ["src/**/*.py", "tests/**/*.py", "schemas/**/*.json"]',
            ]
        )
    config_path = root / "project.yaml"
    config_path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "project:",
                "  id: project-knowledge-mcp",
                "  name: Project Knowledge MCP",
                "storage:",
                f"  project_root: {root.as_posix()}",
                f"  state_dir: {state_dir.as_posix()}",
                "repos:",
                *repo_lines,
                "retrieval:",
                "  provider: sqlite_fts5",
                "  default_limit: 5",
                f"  brief_max_results_per_section: {brief_max_results_per_section}",
                "  include_superseded_by_default: false",
                "code_context:",
                "  provider: codegraph",
                "  fallback_provider: text",
                "  required_for_code_repos: true",
                "  fallback_on_unhealthy: true",
                "  codegraph:",
                "    enabled: true",
                "    vector_resolve_enabled: false",
                "write_policy:",
                "  default_capture_repo: ops",
                "  default_capture_dir: docs/notes",
                "  allow_direct_capture: true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def build_indexed_project(
    tmp_path: Path, *, include_work: bool = True, brief_max_results_per_section: int = 8
) -> tuple[Path, Path, Path | None, Path]:
    ops_repo = tmp_path / "ops"
    work_repo = tmp_path / "work" if include_work else None
    state = tmp_path / ".project-knowledge"
    init_git_repo(ops_repo)
    if work_repo is not None:
        init_git_repo(work_repo)

    write_doc(
        ops_repo,
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

        Project Knowledge MCP compiles deterministic evidence packets before assistant synthesis.
        """,
    )
    write_doc(
        ops_repo,
        "docs/decisions/accepted/0001-briefs.md",
        """
        ---
        title: Accepted Brief Packet Decision
        type: decision
        status: accepted
        authority: accepted_decision
        tags: [context]
        ---
        # Accepted Brief Packet Decision

        Session briefs must cite source evidence packet context and keep synthesis outside the MCP server.
        """,
    )
    write_doc(
        ops_repo,
        "docs/open-questions/code-context.md",
        """
        ---
        title: Code Context Open Question
        type: open_question
        status: open
        authority: working
        owner: Amin
        related_docs: [docs/doctrine/context.md]
        ---
        # Code Context Open Question

        Should evidence packets include fallback code context gaps when code evidence is missing?
        """,
    )
    commit_all(ops_repo, "ops evidence")

    if work_repo is not None:
        write_doc(
            work_repo,
            "src/example.py",
            """
            class ExampleService:
                def compile_context(self, topic: str) -> str:
                    return f"compiled evidence packet for {topic}"
            """,
        )
        write_doc(
            work_repo,
            "tests/test_example.py",
            """
            from src.example import ExampleService

            def test_compile_context_returns_evidence_packet():
                assert ExampleService().compile_context("brief") == "compiled evidence packet for brief"
            """,
        )
        commit_all(work_repo, "work evidence")

    config_path = write_project_config(
        tmp_path,
        ops_repo=ops_repo,
        work_repo=work_repo,
        state_dir=state,
        brief_max_results_per_section=brief_max_results_per_section,
    )
    indexed = index_project_from_config(config_path=config_path)
    assert indexed["status"] == "ok"
    return ops_repo, state, work_repo, config_path


def test_project_state_groups_cited_ops_and_code_results(tmp_path: Path):
    _, _, _, config_path = build_indexed_project(tmp_path, include_work=True)

    packet = get_project_state_from_config(
        topic="evidence packet context",
        view="doctrine_and_discussion",
        config_path=config_path,
        limit=3,
    )

    assert packet["tool"] == "get_project_state"
    assert packet["topic"] == "evidence packet context"
    assert packet["project_id"] == "project-knowledge-mcp"
    assert packet["sections"]["doctrine"]["results"][0]["path"] == "docs/doctrine/context.md"
    assert packet["sections"]["doctrine"]["results"][0]["authority"] == "canonical"
    assert packet["sections"]["accepted_decisions"]["results"][0]["path"] == (
        "docs/decisions/accepted/0001-briefs.md"
    )
    assert packet["sections"]["open_questions"]["results"][0]["owner"] == "Amin"
    code = packet["sections"]["implementation_evidence"]["results"][0]
    assert code["repo_id"] == "app"
    assert code["path"] == "src/example.py"
    assert code["start_line"] >= 1
    assert packet["overall_retrieval_state"] == "complete"
    assert "## Authoritative doctrine" in packet["markdown"]
    assert "docs/doctrine/context.md" in packet["markdown"]
    assert "src/example.py" in packet["markdown"]


def test_project_state_reports_unavailable_implementation_without_work_repo(tmp_path: Path):
    _, _, _, config_path = build_indexed_project(tmp_path, include_work=False)

    packet = get_project_state_from_config(
        topic="evidence packet context", view="doctrine", config_path=config_path, limit=3
    )

    section = packet["sections"]["implementation_evidence"]
    assert section["results"] == []
    assert section["retrieval_state"] == "unavailable"
    assert section["match_state"] == "indeterminate"
    assert section["errors"][0]["code"] == "QUERY_INVALID"
    assert "absence cannot be inferred" in packet["markdown"]


def test_project_state_preserves_underlying_errors(tmp_path: Path):
    ops_repo = tmp_path / "ops"
    work_repo = tmp_path / "work"
    state = tmp_path / ".project-knowledge"
    init_git_repo(ops_repo)
    init_git_repo(work_repo)
    write_doc(
        ops_repo,
        "docs/doctrine/context.md",
        """
        ---
        title: Context Doctrine
        type: doctrine
        status: current
        authority: canonical
        ---
        # Context Doctrine

        Evidence packet context exists but has not been indexed.
        """,
    )
    write_doc(
        work_repo,
        "src/example.py",
        """
        def compile_context(topic: str) -> str:
            return topic
        """,
    )
    commit_all(ops_repo, "ops evidence")
    commit_all(work_repo, "work evidence")
    config_path = write_project_config(
        tmp_path, ops_repo=ops_repo, work_repo=work_repo, state_dir=state
    )

    packet = get_project_state_from_config(
        topic="evidence packet context",
        view="doctrine_and_discussion",
        config_path=config_path,
        limit=3,
    )

    assert packet["sections"]["implementation_evidence"]["results"] == []
    assert packet["sections"]["implementation_evidence"]["retrieval_state"] == "unavailable"
    assert {error["source"] for error in packet["errors"]} >= {
        "doctrine",
        "open_questions",
        "implementation_evidence",
    }
    assert {error["code"] for error in packet["errors"]} == {"INDEX_NOT_READY"}
    assert any("Index is not ready" in warning for warning in packet["warnings"])


def test_project_state_uses_brief_limit_when_omitted(tmp_path: Path):
    _, _, _, config_path = build_indexed_project(
        tmp_path, include_work=True, brief_max_results_per_section=1
    )

    packet = get_project_state_from_config(
        topic="evidence packet", view="doctrine", config_path=config_path
    )

    assert len(packet["sections"]["implementation_evidence"]["results"]) == 1


def test_generate_session_brief_includes_staleness_warnings_and_markdown(tmp_path: Path):
    ops_repo, _, _, config_path = build_indexed_project(tmp_path, include_work=True)
    write_doc(
        ops_repo,
        "docs/notes/untracked.md",
        "# Untracked\n\nNew unindexed note that makes the workspace stale.",
    )

    brief = generate_session_brief_from_config(
        task="Implement evidence packet context",
        view="doctrine_and_discussion",
        config_path=config_path,
        since="2026-06-01",
        limit=10,
    )

    assert brief["tool"] == "generate_session_brief"
    assert brief["task"] == "Implement evidence packet context"
    assert brief["evidence_topic"] == "evidence packet context"
    assert brief["since"] == "2026-06-01"
    assert brief["project_id"] == "project-knowledge-mcp"
    assert brief["generated_at"]
    assert brief["repositories"][0]["repo_id"] == "ops"
    assert brief["repositories"][0]["reindex_needed"] is True
    assert any("reindex" in warning.lower() for warning in brief["warnings"])
    assert brief["sections"]["doctrine"]["results"][0]["path"] == (
        "docs/doctrine/context.md"
    )
    assert brief["sections"]["implementation_evidence"]["results"][0]["path"] == (
        "src/example.py"
    )
    recent = brief["sections"]["recent_changes"]["results"]
    assert recent
    assert {change["repo_id"] for change in recent} >= {"ops", "app"}
    assert "## Session brief" in brief["markdown"]
    assert "## Recent indexed changes" in brief["markdown"]
    assert "Connected assistant should synthesize" in brief["markdown"]


def test_session_brief_without_since_marks_recent_changes_not_run(tmp_path: Path):
    _, _, _, config_path = build_indexed_project(tmp_path, include_work=True)

    brief = generate_session_brief_from_config(
        task="Implement evidence packet context",
        view="doctrine",
        config_path=config_path,
        limit=5,
    )

    recent = brief["sections"]["recent_changes"]
    assert recent["retrieval_state"] == "not_run"
    assert recent["match_state"] == "indeterminate"
    assert recent["results"] == []
    assert {item["section"] for item in brief["known_omissions"]} >= {"recent_changes"}
    assert "## Recent indexed changes" in brief["markdown"]
    assert "Not searched because this section was not requested." in brief["markdown"]
