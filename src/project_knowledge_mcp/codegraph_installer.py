from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

CODEGRAPH_PACKAGE = "@colbymchenry/codegraph@1.0.0"


def ensure_codegraph_configured(config: dict[str, Any], *, install: bool) -> dict[str, Any]:
    """Detect CodeGraph and optionally install a local CLI, mutating config with command."""

    work_repos = [
        repo
        for repo in config.get("repos", []) or []
        if isinstance(repo, dict) and repo.get("role") == "work"
    ]
    code_context = _dict_child(config, "code_context")
    codegraph = _dict_child(code_context, "codegraph")
    enabled = bool(codegraph.get("enabled", True))
    if not enabled:
        return {
            "status": "disabled",
            "required": False,
            "will_install_on_write": False,
            "package": CODEGRAPH_PACKAGE,
            "message": "CodeGraph is disabled in this config.",
        }
    if not work_repos:
        return {
            "status": "skipped",
            "required": False,
            "will_install_on_write": False,
            "package": CODEGRAPH_PACKAGE,
            "message": "No code repos were selected, so CodeGraph setup was skipped.",
        }

    configured = codegraph.get("command")
    if isinstance(configured, str) and configured.strip():
        command = _split_configured_command(configured)
        if _command_healthy(command):
            codegraph["command"] = configured
            initialized_repos = _initialize_code_repos(command, work_repos) if install else []
            return _result(
                "configured",
                command[0],
                "CodeGraph is already configured and healthy.",
                initialized_repos=initialized_repos,
                will_initialize=not install,
            )

    discovered = shutil.which("codegraph")
    if discovered and _command_healthy([discovered]):
        codegraph["command"] = shlex.quote(discovered)
        initialized_repos = _initialize_code_repos([discovered], work_repos) if install else []
        return _result(
            "configured",
            discovered,
            "CodeGraph was found on PATH and configured.",
            initialized_repos=initialized_repos,
            will_initialize=not install,
        )

    state_dir = _state_dir(config)
    install_dir = state_dir / "tools" / "codegraph-cli"
    command_path = install_dir / "node_modules" / ".bin" / "codegraph"
    if command_path.exists() and _command_healthy([str(command_path)]):
        codegraph["command"] = shlex.quote(str(command_path))
        initialized_repos = (
            _initialize_code_repos([str(command_path)], work_repos) if install else []
        )
        return _result(
            "configured",
            str(command_path),
            "A local CodeGraph install was found and configured.",
            initialized_repos=initialized_repos,
            will_initialize=not install,
        )

    if not install:
        return {
            "status": "missing",
            "required": True,
            "will_install_on_write": True,
            "will_initialize_on_write": True,
            "package": CODEGRAPH_PACKAGE,
            "install_dir": str(install_dir),
            "message": "CodeGraph is not installed; setup will install a local CodeGraph CLI and write its command into project.yaml.",
        }

    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError(
            "CodeGraph is required for selected code repos, but neither codegraph nor npm was found. "
            "Install Node/npm or put codegraph on PATH, then run setup again."
        )

    install_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            npm,
            "install",
            "--prefix",
            str(install_dir),
            "--no-audit",
            "--no-fund",
            CODEGRAPH_PACKAGE,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=_codegraph_install_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"CodeGraph install failed with exit {result.returncode}")
    if not command_path.exists() or not _command_healthy([str(command_path)]):
        raise RuntimeError("CodeGraph installed, but the codegraph CLI could not be verified")
    codegraph["command"] = shlex.quote(str(command_path))
    initialized_repos = _initialize_code_repos([str(command_path)], work_repos)
    return {
        "status": "installed",
        "required": True,
        "will_install_on_write": False,
        "will_initialize_on_write": False,
        "package": CODEGRAPH_PACKAGE,
        "command": str(command_path),
        "install_dir": str(install_dir),
        "initialized_repos": initialized_repos,
        "message": "CodeGraph was installed locally, initialized for selected code repos, and configured in project.yaml.",
    }


def _dict_child(parent: dict[str, Any], key: str) -> dict[str, Any]:
    child = parent.get(key)
    if not isinstance(child, dict):
        child = {}
        parent[key] = child
    return child


def _split_configured_command(command: str) -> list[str]:
    try:
        parsed = shlex.split(command)
    except ValueError:
        return []
    return parsed if parsed and not parsed[0].startswith("-") else []


def _command_healthy(command: list[str]) -> bool:
    if not command:
        return False
    try:
        result = subprocess.run(
            [*command, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=_codegraph_install_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _initialize_code_repos(command: list[str], work_repos: list[dict[str, Any]]) -> list[str]:
    initialized: list[str] = []
    for repo in work_repos:
        repo_path = repo.get("path")
        repo_id = str(repo.get("id") or repo_path or "work")
        if not repo_path:
            continue
        path = Path(str(repo_path)).expanduser().resolve()
        if _repo_already_initialized(command, path):
            continue
        try:
            result = subprocess.run(
                [*command, "init", str(path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
                env=_codegraph_install_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"CodeGraph init failed for repo {repo_id}") from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"CodeGraph init failed for repo {repo_id} with exit {result.returncode}"
            )
        initialized.append(repo_id)
    return initialized


def _repo_already_initialized(command: list[str], path: Path) -> bool:
    try:
        result = subprocess.run(
            [*command, "status", str(path), "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=_codegraph_install_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    return isinstance(status, dict) and status.get("initialized") is True


def _state_dir(config: dict[str, Any]) -> Path:
    storage = config.get("storage") if isinstance(config.get("storage"), dict) else {}
    raw_state_dir = storage.get("state_dir") if isinstance(storage, dict) else None
    if raw_state_dir:
        return Path(str(raw_state_dir)).expanduser().resolve()
    raw_root = storage.get("project_root") if isinstance(storage, dict) else None
    root = Path(str(raw_root)).expanduser().resolve() if raw_root else Path.cwd().resolve()
    return root / ".project-knowledge"


def _codegraph_install_env() -> dict[str, str]:
    env = os.environ.copy()
    env["DO_NOT_TRACK"] = "1"
    env["CODEGRAPH_TELEMETRY"] = "0"
    return env


def _result(
    status: str,
    command: str,
    message: str,
    *,
    initialized_repos: list[str] | None = None,
    will_initialize: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "required": True,
        "will_install_on_write": False,
        "will_initialize_on_write": will_initialize,
        "package": CODEGRAPH_PACKAGE,
        "command": command,
        "initialized_repos": initialized_repos or [],
        "message": message,
    }
