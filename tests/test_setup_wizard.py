from __future__ import annotations

import json
import shlex
import subprocess
import sys
import textwrap
import threading
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from typer.testing import CliRunner

from project_knowledge_mcp.server import app
from project_knowledge_mcp.setup_ui import _HTML, create_setup_ui_server
from project_knowledge_mcp.setup_wizard import (
    GuidedSetupInput,
    build_client_handoff,
    build_guided_setup_state,
    validate_repo_path,
    write_guided_setup_config,
)


def init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)


def post_json(url: str, payload: dict, *, token: str) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Setup-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def test_guided_setup_state_machine_happy_path_writes_valid_config(tmp_path: Path):
    ops_repo = tmp_path / "ops"
    work_repo = tmp_path / "code"
    init_git_repo(ops_repo)
    init_git_repo(work_repo)
    config_path = tmp_path / "project.yaml"
    setup_input = GuidedSetupInput(
        config_path=config_path,
        project_root=tmp_path,
        ops_repo=ops_repo,
        work_repos=[work_repo],
        clients=["hermes"],
    )

    state = build_guided_setup_state(setup_input)

    assert state["status"] == "ready"
    assert [step["id"] for step in state["steps"]] == [
        "welcome",
        "repo_selection",
        "validation",
        "config_preview",
        "write_confirmation",
        "service_lifecycle",
        "indexing",
        "remote_bridge",
        "client_handoff",
    ]
    assert state["surface"] == "localhost_browser_setup"
    assert state["config_preview"]["summary"] == (
        "Project Knowledge will use local files only and keep remote access off."
    )
    assert state["safety"]["starts_services_by_default"] is False
    assert state["safety"]["remote_enabled_by_default"] is False

    result = write_guided_setup_config(setup_input)

    assert result["status"] == "ok"
    assert result["current_step"] == "service_lifecycle"
    assert result["validation"]["valid"] is True
    assert config_path.exists()


def test_guided_setup_invalid_repo_explains_failure_in_plain_language(tmp_path: Path):
    missing_repo = tmp_path / "missing"

    check = validate_repo_path(missing_repo, role="ops")

    assert check["ok"] is False
    assert check["code"] == "REPO_PATH_MISSING"
    assert "could not find" in check["message"]
    assert "Git" not in check["message"]


def write_fake_npm_installer(path: Path) -> Path:
    command = path / "npm"
    command.write_text(
        textwrap.dedent(
            f"""
            #!{sys.executable}
            import os
            import stat
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            if "install" not in args or "--prefix" not in args:
                print("unexpected npm args: " + repr(args), file=sys.stderr)
                sys.exit(2)
            prefix = Path(args[args.index("--prefix") + 1])
            bin_dir = prefix / "node_modules" / ".bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            codegraph = bin_dir / "codegraph"
            codegraph.write_text(
                "#!{sys.executable}\\n"
                "import sys\\n"
                "from pathlib import Path\\n"
                "if '--version' in sys.argv:\\n"
                "    print('1.0.0')\\n"
                "    raise SystemExit(0)\\n"
                "if len(sys.argv) >= 3 and sys.argv[1] == 'init':\\n"
                f"    log = Path({(path / ".project-knowledge" / "codegraph-init.log").as_posix()!r})\\n"
                "    log.parent.mkdir(parents=True, exist_ok=True)\\n"
                "    log.write_text(sys.argv[2] + '\\\\n', encoding='utf-8')\\n"
                "    print('initialized fake codegraph')\\n"
                "    raise SystemExit(0)\\n"
                "print('fake codegraph installed')\\n",
                encoding="utf-8",
            )
            codegraph.chmod(codegraph.stat().st_mode | stat.S_IXUSR)
            print("installed fake codegraph")
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    return command


def test_guided_setup_preview_reports_codegraph_will_be_installed_when_missing(
    tmp_path: Path, monkeypatch
):
    ops_repo = tmp_path / "ops"
    work_repo = tmp_path / "code"
    init_git_repo(ops_repo)
    init_git_repo(work_repo)
    monkeypatch.setattr("project_knowledge_mcp.codegraph_installer.shutil.which", lambda name: None)

    state = build_guided_setup_state(
        GuidedSetupInput(
            config_path=tmp_path / "project.yaml",
            project_root=tmp_path,
            ops_repo=ops_repo,
            work_repos=[work_repo],
            clients=["hermes"],
        )
    )

    assert state["status"] == "ready"
    assert state["codegraph_setup"]["status"] == "missing"
    assert state["codegraph_setup"]["will_install_on_write"] is True
    assert "CodeGraph" in state["codegraph_setup"]["message"]


def test_guided_setup_installs_and_configures_codegraph_when_missing(tmp_path: Path, monkeypatch):
    project_root = tmp_path / "project with spaces"
    ops_repo = project_root / "ops"
    work_repo = project_root / "code"
    init_git_repo(ops_repo)
    init_git_repo(work_repo)
    fake_npm = write_fake_npm_installer(project_root)

    def fake_which(name: str) -> str | None:
        if name == "codegraph":
            return None
        if name == "npm":
            return str(fake_npm)
        return None

    monkeypatch.setattr("project_knowledge_mcp.codegraph_installer.shutil.which", fake_which)
    config_path = project_root / "project.yaml"

    result = write_guided_setup_config(
        GuidedSetupInput(
            config_path=config_path,
            project_root=project_root,
            ops_repo=ops_repo,
            work_repos=[work_repo],
            clients=["hermes"],
            project_id="project-with-spaces",
        )
    )

    assert result["status"] == "ok"
    assert result["written"]["codegraph_setup"]["status"] == "installed"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    command_string = config["code_context"]["codegraph"]["command"]
    command_argv = shlex.split(command_string)
    assert len(command_argv) == 1
    command = Path(command_argv[0])
    assert command.exists()
    assert command.is_file()
    assert ".project-knowledge/tools/codegraph-cli" in command.as_posix()
    assert " " in command.as_posix()
    assert (project_root / ".project-knowledge" / "codegraph-init.log").read_text(
        encoding="utf-8"
    ).strip() == work_repo.as_posix()


def test_guided_setup_requires_explicit_overwrite_confirmation(tmp_path: Path):
    ops_repo = tmp_path / "ops"
    init_git_repo(ops_repo)
    config_path = tmp_path / "project.yaml"
    config_path.write_text("existing: true\n", encoding="utf-8")
    setup_input = GuidedSetupInput(
        config_path=config_path,
        project_root=tmp_path,
        ops_repo=ops_repo,
        work_repos=[],
        clients=["hermes"],
    )

    state = build_guided_setup_state(setup_input)

    assert state["status"] == "needs_confirmation"
    assert state["current_step"] == "write_confirmation"
    assert state["config_file"]["requires_confirmation"] is True
    try:
        write_guided_setup_config(setup_input)
    except Exception as exc:  # noqa: BLE001 - assert user-facing error shape
        assert "confirmation" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("write should require overwrite confirmation")


def test_guided_setup_remote_bridge_is_opt_in_and_requires_risk_ack(tmp_path: Path):
    ops_repo = tmp_path / "ops"
    init_git_repo(ops_repo)
    setup_input = GuidedSetupInput(
        config_path=tmp_path / "project.yaml",
        project_root=tmp_path,
        ops_repo=ops_repo,
        remote_bridge_opt_in=True,
        remote_bridge_risk_acknowledged=False,
    )

    state = build_guided_setup_state(setup_input)

    assert state["status"] == "needs_input"
    assert state["current_step"] == "validation"
    assert state["remote_bridge"]["enabled_by_default"] is False
    assert state["remote_bridge"]["requested"] is True
    assert state["remote_bridge"]["can_enable"] is False
    assert state["remote_bridge"]["requires_bearer_token"] is True
    assert state["remote_bridge"]["writes_token"] is False
    assert state["remote_bridge"]["errors"][0]["code"] == "REMOTE_BRIDGE_RISK_ACK_REQUIRED"


def test_guided_setup_remote_bridge_writes_managed_caddy_artifacts(tmp_path: Path, monkeypatch):
    ops_repo = tmp_path / "ops"
    init_git_repo(ops_repo)
    monkeypatch.setattr(
        "project_knowledge_mcp.setup.secrets.token_urlsafe", lambda size: "test-token"
    )
    setup_input = GuidedSetupInput(
        config_path=tmp_path / "project.yaml",
        project_root=tmp_path,
        ops_repo=ops_repo,
        remote_bridge_opt_in=True,
        remote_bridge_risk_acknowledged=True,
        remote_url="https://pkmcp.example.com/mcp",
    )

    state = build_guided_setup_state(setup_input)

    assert state["status"] == "ready"
    assert "remote_bridge" in [step["id"] for step in state["steps"]]
    assert state["remote_bridge"]["can_enable"] is True
    assert state["remote_bridge"]["managed_by_setup"] is True
    assert state["remote_bridge"]["caddy"]["install_mode"] == "docker"
    assert "Caddy" in state["remote_bridge"]["message"]
    assert any(
        path.endswith(".project-knowledge/remote-bridge/Caddyfile")
        for path in state["config_preview"]["would_write"]
    )
    assert state["client_handoff"]["remote_bridge_enabled"] is True
    assert (
        state["client_handoff"]["remote_client_config"]["config"]["headers"]["Authorization"]
        == "Bearer [REDACTED]"
    )

    result = write_guided_setup_config(setup_input)

    bridge = result["written"]["remote_bridge"]
    assert bridge["enabled"] is True
    assert bridge["token"]["redacted"] == "[REDACTED]"
    env_path = Path(bridge["artifacts"]["env"]["path"])
    caddyfile_path = Path(bridge["artifacts"]["caddyfile"]["path"])
    compose_path = Path(bridge["artifacts"]["compose"]["path"])
    assert env_path.exists()
    assert caddyfile_path.exists()
    assert compose_path.exists()
    assert "MCP_AUTH_TOKEN=test-token" in env_path.read_text(encoding="utf-8")
    caddyfile_text = caddyfile_path.read_text(encoding="utf-8")
    assert "{$PKMCP_SITE_ADDRESS:pkmcp.example.com}" in caddyfile_text
    assert "pkmcp.example.com" in caddyfile_text
    assert "caddy:2-alpine" in compose_path.read_text(encoding="utf-8")
    assert "test-token" not in json.dumps(result)


def test_client_handoff_includes_remote_https_when_bridge_enabled(tmp_path: Path):
    result = build_client_handoff(
        tmp_path / "project.yaml",
        clients=["hermes"],
        remote_bridge_enabled=True,
        remote_url="https://pkmcp.example.com/mcp",
    )

    assert result["remote_bridge_enabled"] is True
    assert result["snippets"]["generic-remote-https"]["transport"] == "remote-https"
    assert result["snippets"]["generic-remote-https"]["config"]["url"] == (
        "https://pkmcp.example.com/mcp"
    )
    assert result["snippets"]["generic-remote-https"]["config"]["headers"] == {
        "Authorization": "Bearer [REDACTED]"
    }


def test_setup_ui_exposes_easy_remote_bridge_toggle():
    assert "remote_bridge_opt_in" in _HTML
    assert "remote_bridge_risk_acknowledged" in _HTML
    assert "remote_url" in _HTML
    assert "Start HTTPS bridge" in _HTML


def test_setup_ui_api_returns_state_without_public_binding(tmp_path: Path):
    ops_repo = tmp_path / "ops"
    init_git_repo(ops_repo)
    server = create_setup_ui_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        payload = post_json(
            f"http://{host}:{port}/api/plan",
            {
                "config_path": str(tmp_path / "project.yaml"),
                "project_root": str(tmp_path),
                "ops_repo": str(ops_repo),
                "work_repos": [],
                "clients": ["hermes"],
            },
            token=server.setup_token,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert payload["status"] == "ready"
    assert payload["safety"]["default_host"] == "127.0.0.1"
    assert payload["safety"]["starts_services_by_default"] is False
    assert payload["client_handoff"]["copy_ready"] is True


def test_setup_ui_rejects_cross_site_or_untokened_mutations(tmp_path: Path):
    ops_repo = tmp_path / "ops"
    init_git_repo(ops_repo)
    server = create_setup_ui_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        url = f"http://{host}:{port}/api/write"
        request = urllib.request.Request(
            url,
            data=json.dumps(
                {
                    "config_path": str(tmp_path / "project.yaml"),
                    "project_root": str(tmp_path),
                    "ops_repo": str(ops_repo),
                    "confirm_overwrite": True,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "text/plain", "Sec-Fetch-Site": "cross-site"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=5)  # noqa: S310
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 400
            assert body["error"]["code"] in {
                "JSON_REQUIRED",
                "CROSS_SITE_REQUEST_REJECTED",
                "SETUP_TOKEN_REQUIRED",
            }
        else:  # pragma: no cover
            raise AssertionError("cross-site setup write should be rejected")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert not (tmp_path / "project.yaml").exists()


def test_setup_ui_cli_command_is_documented_and_rejects_public_host():
    help_result = CliRunner().invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "setup-ui" in help_result.output

    bad_host = CliRunner().invoke(app, ["setup-ui", "--host", "0.0.0.0", "--port", "0"])
    assert bad_host.exit_code != 0
    assert "local" in bad_host.output.lower() or "loopback" in bad_host.output.lower()
