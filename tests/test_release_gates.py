from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_ci_release_deployment_gate_runs_docker_caddy_and_remote_bridge_smokes():
    workflow = yaml.safe_load(read_text(".github/workflows/ci.yml"))
    jobs = workflow["jobs"]
    assert "release-deployment-gates" in jobs

    job = jobs["release-deployment-gates"]
    assert job["name"] == "release-deployment-gates"
    assert job["needs"] == ["test"]

    run_blocks = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "docker build" in run_blocks
    assert "project-knowledge-mcp:ci" in run_blocks
    assert "scripts/ci/release_deployment_gates.sh" in run_blocks


def test_release_gate_script_enforces_step10c_smoke_contracts():
    script = read_text("scripts/ci/release_deployment_gates.sh")
    assert "docker run --rm project-knowledge-mcp:ci --help" in script
    assert "docker run --rm project-knowledge-mcp:ci start --help" in script
    assert "start --transport streamable-http --host 0.0.0.0 --port 8000" in script
    assert "caddy:2-alpine" in script
    assert "caddy validate" in script
    assert "MCP_AUTH_TOKEN=" in script
    assert "Authorization: Bearer" in script
    assert "scripts/ci/smoke_mcp_registry.py" in script


def test_release_gate_registry_smoke_checks_remote_bridge_tool_parity():
    script = read_text("scripts/ci/smoke_mcp_registry.py")
    assert "EXPECTED_TOOL_NAMES" in script
    assert "Client(" in script
    assert "health" in script
    assert "default_network_exposure" in script
    assert "loopback_or_stdio_only" in script


def test_release_checklist_documents_exact_required_pre_tag_gates():
    checklist = read_text("docs/release-checklist.md")
    for phrase in [
        "poetry run ruff format --check .",
        "poetry run ruff check .",
        "poetry run pytest -q",
        "docker build -t project-knowledge-mcp:ci .",
        "scripts/ci/release_deployment_gates.sh",
        "Missing token returns `401` before MCP",
        "Wrong token returns `401` before MCP",
        "Correct token reaches the same policy-enforced MCP tool registry",
        "No secrets, public network exposure, GPU, model keys, or hosted parser credentials",
    ]:
        assert phrase in checklist
