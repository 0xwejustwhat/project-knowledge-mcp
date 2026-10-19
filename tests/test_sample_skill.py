from pathlib import Path

import yaml


SKILL_PATH = Path("samples/skills/pkmcp-codebase-questions/SKILL.md")


def _skill_parts() -> tuple[dict, str]:
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    _, frontmatter, body = content.split("---", 2)
    return yaml.safe_load(frontmatter), body


def test_sample_skill_has_portable_trigger_metadata() -> None:
    frontmatter, body = _skill_parts()

    assert frontmatter["name"] == "pkmcp-codebase-questions"
    assert frontmatter["version"] == "1.1.0"
    assert len(frontmatter["description"]) <= 60
    assert frontmatter["description"].endswith(".")
    assert frontmatter["platforms"] == ["linux", "macos", "windows"]
    assert "0xwejustwhat" in frontmatter["author"]
    assert frontmatter["metadata"]["hermes"]["related_skills"] == []
    assert "## Installation" in body
    assert "/root/" not in body
    assert "/home/" not in body


def test_sample_skill_documents_current_trustworthy_retrieval_contract() -> None:
    _, body = _skill_parts()

    for required in (
        "get_project_state",
        "doctrine",
        "doctrine_and_discussion",
        "retrieval_state",
        "match_state",
        "freshness_state",
        "proposals_and_drafts",
        "discussions_and_captures",
        "check_project_staleness",
        "repo_id",
        "not_run",
        "indeterminate",
    ):
        assert required in body

    assert "get_current_doctrine" not in body
    assert "retrieve_ops_code_evidence" not in body


def test_sample_skill_keeps_user_workflow_separate_from_maintenance() -> None:
    _, body = _skill_parts()
    lower_body = body.lower()

    assert "maintaining pkmcp itself" in lower_body
    assert "do not use for" in lower_body
    assert "propose_authority_change" in body
    assert "explicitly asks" in lower_body
