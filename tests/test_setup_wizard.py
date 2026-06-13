from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

from typer.testing import CliRunner

from project_knowledge_mcp.server import app
from project_knowledge_mcp.setup_ui import create_setup_ui_server
from project_knowledge_mcp.setup_wizard import (
    GuidedSetupInput,
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
