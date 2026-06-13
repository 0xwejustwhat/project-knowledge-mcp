from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from typing import Any, Literal

from project_knowledge_mcp.config import validate_project_config
from project_knowledge_mcp.setup import build_client_config, build_setup_plan, write_setup_artifacts

SetupStep = Literal[
    "welcome",
    "repo_selection",
    "validation",
    "config_preview",
    "write_confirmation",
    "service_lifecycle",
    "indexing",
    "client_handoff",
]

SETUP_STEPS: list[SetupStep] = [
    "welcome",
    "repo_selection",
    "validation",
    "config_preview",
    "write_confirmation",
    "service_lifecycle",
    "indexing",
    "client_handoff",
]

_STEP_LABELS: dict[SetupStep, str] = {
    "welcome": "Welcome",
    "repo_selection": "Choose project folders",
    "validation": "Check folders",
    "config_preview": "Review generated settings",
    "write_confirmation": "Confirm before writing files",
    "service_lifecycle": "Start or check the local service",
    "indexing": "Build the local search index",
    "client_handoff": "Connect your MCP client",
}

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class GuidedSetupInput:
    config_path: Path
    project_root: Path
    ops_repo: Path | None = None
    work_repos: list[Path] = field(default_factory=list)
    clients: list[str] = field(default_factory=lambda: ["hermes"])
    project_id: str | None = None
    confirm_overwrite: bool = False
    remote_bridge_opt_in: bool = False
    remote_bridge_risk_acknowledged: bool = False
    remote_url: str | None = None


class GuidedSetupError(ValueError):
    """Plain-language setup error for browser UI and CLI surfaces."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": self.details,
            "recoverable": True,
        }


def normalize_guided_setup_input(payload: dict[str, Any]) -> GuidedSetupInput:
    """Parse browser JSON into the serializable setup state-machine input."""

    config_path = Path(str(payload.get("config_path") or "project.yaml"))
    project_root = Path(str(payload.get("project_root") or config_path.parent or "."))
    ops_repo = payload.get("ops_repo")
    work_repos = payload.get("work_repos") or []
    clients = payload.get("clients") or ["hermes"]
    if isinstance(work_repos, str):
        work_repos = [item.strip() for item in work_repos.splitlines() if item.strip()]
    if isinstance(clients, str):
        clients = [clients]
    return GuidedSetupInput(
        config_path=config_path,
        project_root=project_root,
        ops_repo=Path(str(ops_repo)) if ops_repo else None,
        work_repos=[Path(str(path)) for path in work_repos],
        clients=[str(client) for client in clients],
        project_id=str(payload["project_id"]) if payload.get("project_id") else None,
        confirm_overwrite=bool(payload.get("confirm_overwrite", False)),
        remote_bridge_opt_in=bool(payload.get("remote_bridge_opt_in", False)),
        remote_bridge_risk_acknowledged=bool(payload.get("remote_bridge_risk_acknowledged", False)),
        remote_url=str(payload["remote_url"]) if payload.get("remote_url") else None,
    )


def step_overview() -> list[dict[str, Any]]:
    return [
        {"id": step, "label": _STEP_LABELS[step], "order": index + 1}
        for index, step in enumerate(SETUP_STEPS)
    ]


def validate_repo_path(path: Path | None, *, role: str) -> dict[str, Any]:
    if path is None:
        return _repo_error(
            "REPO_PATH_REQUIRED",
            f"Choose the {role} repository folder before continuing.",
            role=role,
            path=None,
        )
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return _repo_error(
            "REPO_PATH_MISSING",
            f"We could not find this {role} repository folder. Choose an existing folder.",
            role=role,
            path=resolved,
        )
    if not resolved.is_dir():
        return _repo_error(
            "REPO_PATH_NOT_DIRECTORY",
            f"This {role} path is a file, not a folder. Choose the repository folder.",
            role=role,
            path=resolved,
        )
    if not _is_git_worktree(resolved):
        return _repo_error(
            "REPO_PATH_NOT_GIT",
            f"This {role} folder is not a Git repository yet. Choose a cloned repo folder.",
            role=role,
            path=resolved,
        )
    return {
        "ok": True,
        "role": role,
        "path": str(resolved),
        "message": f"Found the {role} repository folder.",
    }


def build_guided_setup_state(input_data: GuidedSetupInput) -> dict[str, Any]:
    """Return a serializable guided setup state without writing files or starting services."""

    config_path = input_data.config_path.expanduser().resolve()
    project_root = input_data.project_root.expanduser().resolve()
    repo_checks = [validate_repo_path(input_data.ops_repo, role="ops")]
    repo_checks.extend(validate_repo_path(path, role="code") for path in input_data.work_repos)
    remote_bridge = _remote_bridge_state(input_data)
    errors = [check for check in repo_checks if not check["ok"]]
    errors.extend(remote_bridge["errors"])
    config_exists = config_path.exists()
    state: dict[str, Any] = {
        "status": "needs_input" if errors else "ready",
        "surface": "localhost_browser_setup",
        "current_step": "validation" if errors else "config_preview",
        "steps": step_overview(),
        "paths": {
            "config_path": str(config_path),
            "project_root": str(project_root),
            "ops_repo": str(input_data.ops_repo.expanduser().resolve())
            if input_data.ops_repo is not None
            else None,
            "work_repos": [str(path.expanduser().resolve()) for path in input_data.work_repos],
        },
        "repo_checks": repo_checks,
        "errors": errors,
        "config_file": {
            "exists": config_exists,
            "requires_confirmation": config_exists and not input_data.confirm_overwrite,
            "overwrite_confirmed": input_data.confirm_overwrite,
            "message": _config_file_message(config_exists, input_data.confirm_overwrite),
        },
        "remote_bridge": remote_bridge,
        "safety": {
            "local_first": True,
            "default_host": "127.0.0.1",
            "starts_services_by_default": False,
            "remote_enabled_by_default": False,
            "secrets_written": False,
            "shell_execution_tool_added": False,
        },
    }
    if errors:
        return state

    if config_exists and not input_data.confirm_overwrite:
        state["status"] = "needs_confirmation"
        state["current_step"] = "write_confirmation"

    try:
        plan = build_setup_plan(
            config_path=config_path,
            project_root=project_root,
            ops_repo=input_data.ops_repo,
            work_repos=input_data.work_repos,
            clients=input_data.clients,
            dry_run=True,
            project_id=input_data.project_id,
        )
    except ValueError as exc:
        error = GuidedSetupError("SETUP_PLAN_INVALID", _plain_setup_error(str(exc))).as_dict()
        state["status"] = "needs_input"
        state["current_step"] = "validation"
        state["errors"] = [*errors, error]
        return state
    state["plan"] = plan
    state["config_preview"] = {
        "summary": "Project Knowledge will use local files only and keep remote access off.",
        "config_yaml": plan["config_yaml"],
        "would_write": [plan["artifacts"]["config"]["path"]],
    }
    state["client_handoff"] = {
        "clients": plan["client_configs"],
        "copy_ready": True,
        "message": "Copy the generated client snippet into your local MCP client.",
    }
    return state


def write_guided_setup_config(input_data: GuidedSetupInput) -> dict[str, Any]:
    state = build_guided_setup_state(input_data)
    if state["errors"]:
        raise GuidedSetupError(
            "SETUP_INPUT_INVALID",
            "Fix the highlighted folder choices before writing setup files.",
            details={"errors": state["errors"]},
        )
    if state["config_file"]["requires_confirmation"]:
        raise GuidedSetupError(
            "OVERWRITE_CONFIRMATION_REQUIRED",
            "A config file already exists. Review it and check the overwrite confirmation box first.",
            details={"config_path": state["paths"]["config_path"]},
        )
    plan = dict(state["plan"])
    plan["dry_run"] = False
    plan["would_write"] = True
    try:
        written = write_setup_artifacts(plan, force=input_data.confirm_overwrite)
    except FileExistsError as exc:
        raise GuidedSetupError(
            "OVERWRITE_CONFIRMATION_REQUIRED",
            "A config file already exists. Review it and check the overwrite confirmation box first.",
        ) from exc
    validation = validate_project_config(written["artifacts"]["config"]["path"])
    return {
        "status": "ok" if validation["valid"] else "error",
        "current_step": "service_lifecycle" if validation["valid"] else "validation",
        "written": written,
        "validation": validation,
        "next": {
            "service_lifecycle": "Start or inspect the local loopback service.",
            "indexing": "Run initial indexing after the config is valid.",
            "client_handoff": "Copy the generated client snippet into your MCP client.",
        },
    }


def build_client_handoff(config_path: Path, *, clients: list[str] | None = None) -> dict[str, Any]:
    selected_clients = clients or ["hermes"]
    snippets = {
        client: build_client_config(config_path=config_path, client=client, transport="stdio")
        for client in selected_clients
    }
    snippets["generic-streamable-http"] = build_client_config(
        config_path=config_path,
        client="generic",
        transport="streamable-http",
        http_url="http://127.0.0.1:8000/mcp",
    )
    return {
        "status": "ok",
        "copy_ready": True,
        "remote_bridge_enabled": False,
        "snippets": snippets,
        "message": "Use local stdio or loopback HTTP first. Remote HTTPS is a separate opt-in step.",
    }


def ensure_loopback_host(host: str) -> None:
    if host not in _LOOPBACK_HOSTS:
        raise GuidedSetupError(
            "NON_LOOPBACK_HOST_REJECTED",
            "Guided setup only starts local services on this computer. Use 127.0.0.1.",
            details={"host": host},
        )


def _remote_bridge_state(input_data: GuidedSetupInput) -> dict[str, Any]:
    requested = input_data.remote_bridge_opt_in
    acknowledged = input_data.remote_bridge_risk_acknowledged
    errors: list[dict[str, Any]] = []
    if requested and not acknowledged:
        errors.append(
            GuidedSetupError(
                "REMOTE_BRIDGE_RISK_ACK_REQUIRED",
                "Remote HTTPS can expose project metadata. Acknowledge the risk before enabling it.",
            ).as_dict()
        )
    if requested and input_data.remote_url and not input_data.remote_url.startswith("https://"):
        errors.append(
            GuidedSetupError(
                "REMOTE_BRIDGE_HTTPS_REQUIRED",
                "Remote bridge URLs must start with https://.",
                details={"remote_url": input_data.remote_url},
            ).as_dict()
        )
    return {
        "enabled_by_default": False,
        "requested": requested,
        "risk_acknowledged": acknowledged,
        "can_enable": requested and acknowledged and not errors,
        "requires_bearer_token": True,
        "writes_token": False,
        "message": (
            "Remote HTTPS stays off unless you explicitly opt in, acknowledge the risk, "
            "and configure a bearer-token-gated bridge."
        ),
        "errors": errors,
    }


def _repo_error(code: str, message: str, *, role: str, path: Path | None) -> dict[str, Any]:
    return {
        "ok": False,
        "code": code,
        "role": role,
        "path": str(path) if path is not None else None,
        "message": message,
        "recoverable": True,
    }


def _is_git_worktree(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _config_file_message(config_exists: bool, confirm_overwrite: bool) -> str:
    if not config_exists:
        return "No existing config file was found; setup can create one."
    if confirm_overwrite:
        return "An existing config file was found and overwrite was explicitly confirmed."
    return "An existing config file was found; setup will not overwrite it without confirmation."


def _plain_setup_error(message: str) -> str:
    if "--ops-repo" in message:
        return "Choose the ops/project repository folder before continuing."
    return message
