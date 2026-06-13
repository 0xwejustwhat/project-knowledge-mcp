from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from project_knowledge_mcp.codegraph_installer import ensure_codegraph_configured
from project_knowledge_mcp.config import CONFIG_ENV_VAR

ClientName = Literal["hermes", "claude-desktop", "cursor", "generic"]
TransportName = Literal["stdio", "streamable-http", "remote-https"]


DEFAULT_HTTP_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_REMOTE_URL = "https://example.invalid/mcp"
VALID_CLIENTS = {"hermes", "claude-desktop", "cursor", "generic"}
VALID_TRANSPORTS = {"stdio", "streamable-http", "remote-https"}


def _resolved(path: Path | str | None) -> Path | None:
    if path is None:
        return None
    return Path(path).expanduser().resolve()


def _repo_config(
    *,
    repo_id: str,
    role: str,
    path: Path,
    writable: bool,
    include_globs: list[str],
) -> dict[str, Any]:
    return {
        "id": repo_id,
        "role": role,
        "path": path.as_posix(),
        "writable": writable,
        "source_mode": "workspace",
        "include_globs": include_globs,
        "exclude_globs": [".git/**", ".project-knowledge/**"],
    }


def _load_config_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a YAML mapping: {path}")
    return data


def _validate_client(client: str) -> ClientName:
    if client not in VALID_CLIENTS:
        raise ValueError(f"client must be one of: {', '.join(sorted(VALID_CLIENTS))}")
    return cast(ClientName, client)


def _validate_transport(transport: str) -> TransportName:
    if transport not in VALID_TRANSPORTS:
        raise ValueError(f"transport must be one of: {', '.join(sorted(VALID_TRANSPORTS))}")
    return cast(TransportName, transport)


def build_project_config(
    *,
    project_root: Path | str,
    ops_repo: Path | str,
    work_repos: list[Path | str] | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Build a minimal local/no-LLM Project Knowledge config."""
    root = _resolved(project_root)
    ops_path = _resolved(ops_repo)
    assert root is not None
    assert ops_path is not None
    work_paths = [_resolved(path) for path in work_repos or []]
    safe_project_id = project_id or root.name or "project-knowledge"

    repos: list[dict[str, Any]] = [
        _repo_config(
            repo_id="ops",
            role="ops",
            path=ops_path,
            writable=True,
            include_globs=["README.md", "docs/**/*.md", "*.md"],
        )
    ]
    for index, work_path in enumerate(work_paths, start=1):
        assert work_path is not None
        repo_id = "work" if len(work_paths) == 1 else f"work-{index}"
        repos.append(
            _repo_config(
                repo_id=repo_id,
                role="work",
                path=work_path,
                writable=False,
                include_globs=[
                    "src/**/*.py",
                    "tests/**/*.py",
                    "docs/**/*.md",
                    "*.md",
                    "pyproject.toml",
                ],
            )
        )

    return {
        "schema_version": 1,
        "project": {
            "id": safe_project_id,
            "name": safe_project_id.replace("-", " ").replace("_", " ").title(),
            "timezone": "UTC",
        },
        "storage": {
            "project_root": root.as_posix(),
            "state_dir": (root / ".project-knowledge").as_posix(),
        },
        "repos": repos,
        "retrieval": {
            "provider": "sqlite_fts5",
            "mode": "local_no_llm",
            "llm_enabled": False,
            "embeddings_enabled": False,
            "cloud_parsers_enabled": False,
            "default_limit": 10,
            "include_superseded_by_default": False,
        },
        "code_context": {
            "provider": "codegraph",
            "fallback_provider": "text",
            "required_for_code_repos": True,
            "fallback_on_unhealthy": True,
            "codegraph": {"enabled": True, "vector_resolve_enabled": False},
        },
        "write_policy": {
            "default_capture_repo": "ops",
            "default_capture_dir": "docs/notes",
            "allow_direct_capture": True,
        },
    }


def dump_config_yaml(config: dict[str, Any]) -> str:
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def build_client_config(
    *,
    config_path: Path | str,
    client: str = "hermes",
    transport: str = "stdio",
    http_url: str | None = None,
    remote_url: str | None = None,
) -> dict[str, Any]:
    """Return a redacted, policy-enforced MCP client snippet."""
    selected_client = _validate_client(client)
    selected_transport = _validate_transport(transport)
    resolved_config = _resolved(config_path)
    assert resolved_config is not None
    url = remote_url if selected_transport == "remote-https" else http_url
    if not url:
        url = DEFAULT_REMOTE_URL if selected_transport == "remote-https" else DEFAULT_HTTP_URL
    if selected_transport == "remote-https" and not url.startswith("https://"):
        raise ValueError("remote-https transport requires an https:// --remote-url")

    if selected_transport == "stdio":
        config: dict[str, Any] = {
            "command": "project-knowledge",
            "args": ["serve"],
            "env": {CONFIG_ENV_VAR: str(resolved_config)},
        }
        instructions = "Use stdio for local clients; no network listener is required."
    elif selected_transport == "streamable-http":
        config = {"url": url, "headers": {}}
        instructions = "Start the server explicitly on loopback before using this snippet."
    else:
        config = {
            "url": url,
            "headers": {"Authorization": "Bearer [REDACTED]"},
        }
        instructions = (
            "Remote HTTPS requires an explicitly configured bridge/reverse proxy; "
            "replace [REDACTED] outside this generated snippet."
        )

    snippet: dict[str, Any] = {
        "client": selected_client,
        "transport": selected_transport,
        "server_name": "project-knowledge-mcp",
        "config_path": str(resolved_config),
        "config": config,
        "instructions": instructions,
        "safety": {
            "uses_policy_enforced_server": True,
            "starts_services": False,
            "remote_requires_explicit_bridge": selected_transport == "remote-https",
        },
    }

    if selected_client in {"claude-desktop", "cursor"} and selected_transport == "stdio":
        snippet["mcpServers"] = {
            "project-knowledge-mcp": {
                "command": config["command"],
                "args": config["args"],
                "env": config["env"],
            }
        }
    return snippet


def _project_root_from_config(config: dict[str, Any], fallback: Path) -> Path:
    storage = config.get("storage") if isinstance(config.get("storage"), dict) else {}
    configured_root = storage.get("project_root") if isinstance(storage, dict) else None
    return _resolved(configured_root) or fallback


def _repo_mounts(config: dict[str, Any]) -> list[dict[str, Any]]:
    mounts_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    raw_repos = config.get("repos")
    repos = raw_repos if isinstance(raw_repos, list) else []
    for repo in repos:
        if not isinstance(repo, dict) or not repo.get("path"):
            continue
        host_path = _resolved(repo["path"])
        if host_path is None:
            continue
        read_only = not bool(repo.get("writable", False))
        mount = {
            "repo_id": repo.get("id"),
            "host_path": str(host_path),
            "container_path": str(host_path),
            "read_only": read_only,
        }
        mounts_by_key[(mount["host_path"], mount["container_path"])] = mount
    return list(mounts_by_key.values())


def _volume_arg(host_path: str, container_path: str, *, read_only: bool) -> str:
    mode = "ro" if read_only else "rw"
    return f"-v {shlex.quote(f'{host_path}:{container_path}:{mode}')}"


def build_docker_guidance(
    *, config_path: Path | str, project_root: Path | str, config: dict[str, Any]
) -> dict[str, Any]:
    resolved_config = _resolved(config_path)
    root = _resolved(project_root)
    assert resolved_config is not None
    assert root is not None
    container_config = "/workspace/project.yaml"
    repo_mounts = _repo_mounts(config)
    volume_args = [
        _volume_arg(str(resolved_config), container_config, read_only=True),
        _volume_arg(str(root), "/workspace", read_only=False),
        _volume_arg(str(root), str(root), read_only=False),
    ]
    for mount in repo_mounts:
        volume_args.append(
            _volume_arg(mount["host_path"], mount["container_path"], read_only=mount["read_only"])
        )
    run_command = " ".join(
        [
            "docker run --rm",
            "-p 127.0.0.1:8000:8000",
            *volume_args,
            "project-knowledge-mcp",
            "start --transport streamable-http --host 0.0.0.0 --port 8000",
        ]
    )
    return {
        "network_exposure": "loopback_only",
        "config_mount": {
            "host_path": str(resolved_config),
            "container_path": container_config,
            "read_only": True,
        },
        "workspace_mount": {
            "host_path": str(root),
            "container_path": "/workspace",
            "read_only": False,
        },
        "repo_mounts": repo_mounts,
        "run_command": run_command,
        "compose_command": "docker compose -f docker-compose.example.yaml up --build project-knowledge-mcp",
        "note": "Commands are guidance only; setup does not start Docker.",
    }


def build_setup_plan(
    *,
    config_path: Path | str,
    project_root: Path | str | None = None,
    ops_repo: Path | str | None = None,
    work_repos: list[Path | str] | None = None,
    clients: list[str] | None = None,
    dry_run: bool = True,
    project_id: str | None = None,
) -> dict[str, Any]:
    resolved_config = _resolved(config_path)
    assert resolved_config is not None
    loaded_existing = resolved_config.exists()
    if loaded_existing and ops_repo is None:
        config = _load_config_yaml(resolved_config)
        root = _project_root_from_config(config, resolved_config.parent)
    else:
        if ops_repo is None:
            raise ValueError("--ops-repo is required for generated setup")
        root = _resolved(project_root) or resolved_config.parent
        config = build_project_config(
            project_root=root,
            ops_repo=ops_repo,
            work_repos=work_repos,
            project_id=project_id,
        )

    selected_clients = clients or ["hermes"]
    codegraph_setup = ensure_codegraph_configured(config, install=False)
    config_yaml = dump_config_yaml(config)
    client_configs = {
        client: build_client_config(config_path=resolved_config, client=client, transport="stdio")
        for client in selected_clients
    }
    return {
        "status": "ok",
        "dry_run": dry_run,
        "would_write": not dry_run,
        "artifacts": {
            "config": {
                "path": str(resolved_config),
                "written": False,
                "exists": resolved_config.exists(),
                "loaded_existing": loaded_existing,
            }
        },
        "config": config,
        "config_yaml": config_yaml,
        "codegraph_setup": codegraph_setup,
        "docker": build_docker_guidance(
            config_path=resolved_config, project_root=root, config=config
        ),
        "remote_bridge": {
            "enabled_by_default": False,
            "requires_https": True,
            "authorization_header": "Authorization: Bearer [REDACTED]",
            "caddy_example": "deploy/Caddyfile.example",
            "compose_profile": "remote-bridge",
            "instructions": (
                "Enable the HTTPS bridge explicitly with the remote-bridge compose profile; "
                "setup does not start or expose remote services."
            ),
        },
        "client_configs": client_configs,
        "safety": {
            "starts_services": False,
            "network_exposure": "loopback_or_stdio_only",
            "remote_enabled": False,
            "secrets_written": False,
        },
    }


def write_setup_artifacts(plan: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    config_path = Path(plan["artifacts"]["config"]["path"])
    if config_path.exists() and not force:
        raise FileExistsError(f"Config already exists: {config_path}")
    config = dict(plan["config"])
    codegraph_setup = ensure_codegraph_configured(config, install=True)
    config_yaml = dump_config_yaml(config)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_yaml, encoding="utf-8")
    updated = dict(plan)
    updated["config"] = config
    updated["config_yaml"] = config_yaml
    updated["codegraph_setup"] = codegraph_setup
    updated["artifacts"] = dict(plan["artifacts"])
    updated["artifacts"]["config"] = dict(plan["artifacts"]["config"])
    updated["artifacts"]["config"]["written"] = True
    updated["artifacts"]["config"]["exists"] = True
    return updated
