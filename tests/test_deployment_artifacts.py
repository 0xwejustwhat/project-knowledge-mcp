from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_dockerfile_packages_same_project_knowledge_cli():
    dockerfile = read_text("Dockerfile")
    assert "FROM python:3.12-slim" in dockerfile
    assert "COPY pyproject.toml poetry.lock README.md" in dockerfile
    assert "COPY src ./src" in dockerfile
    assert "apt-get install -y --no-install-recommends git" in dockerfile
    assert "python -m pip install ." in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert 'ENTRYPOINT ["project-knowledge"]' in dockerfile
    assert 'CMD ["start", "--transport", "stdio"]' in dockerfile


def test_compose_publishes_mcp_loopback_and_keeps_work_repos_read_only():
    compose = yaml.safe_load(read_text("docker-compose.example.yaml"))
    service = compose["services"]["project-knowledge-mcp"]
    assert service["ports"] == ["127.0.0.1:8000:8000"]
    assert service["environment"]["PROJECT_KNOWLEDGE_CONFIG"] == "/workspace/project.yaml"
    assert "./:/workspace:rw" in service["volumes"]
    compose_text = read_text("docker-compose.example.yaml")
    assert "${MCP_AUTH_TOKEN:?" not in compose_text
    caddy = compose["services"]["caddy"]
    assert caddy["environment"]["MCP_AUTH_TOKEN"] == "${MCP_AUTH_TOKEN:-}"
    assert "MCP_AUTH_TOKEN is required before enabling the remote bridge" in caddy["command"][0]
    assert "../example-work-repo:/repos/example-work-repo:ro" in read_text(
        "docker-compose.example.yaml"
    )
    command = service["command"]
    assert ["--transport", "streamable-http"] == command[
        command.index("--transport") : command.index("--transport") + 2
    ]
    assert ["--host", "0.0.0.0"] == command[command.index("--host") : command.index("--host") + 2]
    assert ["--port", "8000"] == command[command.index("--port") : command.index("--port") + 2]


def test_project_example_is_local_no_key_workspace_config():
    config = yaml.safe_load(read_text("project.example.yaml"))
    assert config["schema_version"] == 1
    assert config["storage"]["project_root"] == "/workspace"
    assert config["storage"]["state_dir"] == "/workspace/.project-knowledge"
    assert config["repos"][0]["path"] == "/workspace"
    assert config["repos"][0]["writable"] is True
    assert config["retrieval"]["provider"] == "sqlite_fts5"
    assert config["retrieval"]["llm_enabled"] is False
    assert config["retrieval"]["embeddings_enabled"] is False
    assert config["retrieval"]["cloud_parsers_enabled"] is False
    assert config["write_policy"]["capture_git_mode"] == "direct_push"
    assert config["write_policy"]["capture_branch"] == "main"
    assert config["write_policy"]["capture_remote"] == "origin"
    assert "docs/doctrine/**" in config["write_policy"]["blocked_direct_write_globs"]


def test_caddyfile_requires_bearer_before_reverse_proxy():
    caddyfile = read_text("deploy/Caddyfile.example")
    assert "{$PKMCP_SITE_ADDRESS:pkmcp.example.com}" in caddyfile
    assert '@missingAuth not header Authorization "Bearer {$MCP_AUTH_TOKEN}"' in caddyfile
    assert "respond @missingAuth 401" in caddyfile
    assert "reverse_proxy {$PKMCP_UPSTREAM:project-knowledge-mcp:8000}" in caddyfile


def test_dockerignore_keeps_local_state_and_secrets_out_of_build_context():
    dockerignore = read_text(".dockerignore")
    for pattern in [".git", ".venv", ".project-knowledge", ".env", "*.sqlite"]:
        assert pattern in dockerignore


def test_readme_documents_same_tool_surface_and_remote_bridge_boundaries():
    readme = read_text("README.md")
    for tool in [
        "add_project_note",
        "create_draft_artifact",
        "propose_authority_change",
        "check_project_staleness",
    ]:
        assert f"`{tool}`" in readme
    assert "http://127.0.0.1:8000/mcp" in readme
    assert "127.0.0.1:8000:8000" in readme
    assert "Missing token: `401` before MCP" in readme
    assert "Wrong token: `401` before MCP" in readme
    assert "same FastMCP `/mcp` endpoint" in readme
    assert "cannot approve, merge, or mark proposals accepted" in readme
    assert "does not require `MCP_AUTH_TOKEN` for local-only" in readme
    assert (
        "remote-bridge service exits before Caddy starts unless you set a non-empty token" in readme
    )


def test_caddy_validate_when_binary_is_available():
    caddy = shutil.which("caddy")
    if caddy is None:
        import pytest

        pytest.skip("caddy binary is not installed in this environment")
    env = os.environ.copy()
    env.update(
        {
            "MCP_AUTH_TOKEN": "test-token",
            "PKMCP_SITE_ADDRESS": "http://127.0.0.1:18080",
            "PKMCP_UPSTREAM": "127.0.0.1:8000",
        }
    )
    subprocess.run(
        [caddy, "validate", "--config", "deploy/Caddyfile.example", "--adapter", "caddyfile"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )
