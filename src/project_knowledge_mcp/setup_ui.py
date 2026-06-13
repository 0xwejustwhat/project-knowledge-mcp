from __future__ import annotations

import hmac
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from project_knowledge_mcp.config import CONFIG_ENV_VAR, validate_project_config
from project_knowledge_mcp.services import index_project_from_config
from project_knowledge_mcp.setup_wizard import (
    GuidedSetupError,
    build_client_handoff,
    build_guided_setup_state,
    ensure_loopback_host,
    normalize_guided_setup_input,
    write_guided_setup_config,
)


class LocalServiceManager:
    """Manage a single loopback-only Project Knowledge service process for setup UI."""

    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.command: list[str] | None = None
        self.config_path: str | None = None
        self.host = "127.0.0.1"
        self.port = 8000

    def status(self) -> dict[str, Any]:
        running = self.process is not None and self.process.poll() is None
        return {
            "status": "running" if running else "stopped",
            "running": running,
            "pid": self.process.pid if running and self.process is not None else None,
            "host": self.host,
            "port": self.port,
            "url": f"http://{self.host}:{self.port}/mcp",
            "network_exposure": "loopback_only",
            "command": self.command,
            "config_path": self.config_path,
            "message": (
                "The local MCP service is running on this computer only."
                if running
                else "The local MCP service is not running from this setup page."
            ),
        }

    def start(
        self, *, config_path: Path, host: str = "127.0.0.1", port: int = 8000
    ) -> dict[str, Any]:
        ensure_loopback_host(host)
        validation = validate_project_config(config_path)
        if not validation["valid"]:
            raise GuidedSetupError(
                "CONFIG_INVALID",
                "Fix the setup config before starting the local service.",
                details={"validation": validation},
            )
        if self.process is not None and self.process.poll() is None:
            return self.status()
        env = os.environ.copy()
        env[CONFIG_ENV_VAR] = str(config_path.expanduser().resolve())
        self.host = host
        self.port = port
        self.config_path = env[CONFIG_ENV_VAR]
        self.command = [
            sys.executable,
            "-m",
            "project_knowledge_mcp.server",
            "start",
            "--transport",
            "streamable-http",
            "--host",
            host,
            "--port",
            str(port),
        ]
        self.process = subprocess.Popen(
            self.command,
            env=env,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(0.2)
        if self.process.poll() is not None:
            raise GuidedSetupError(
                "SERVICE_START_FAILED",
                "The local service could not start. Check that the config is valid and the port is free.",
                details={"returncode": self.process.returncode, "port": port},
            )
        return self.status()

    def stop(self) -> dict[str, Any]:
        if self.process is None or self.process.poll() is not None:
            return self.status()
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except ProcessLookupError:
            return self.status()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
            self.process.wait(timeout=5)
        return self.status()


class SetupUIServer(ThreadingHTTPServer):
    def __init__(
        self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler]
    ):
        self.setup_token = secrets.token_urlsafe(32)
        super().__init__(server_address, handler_class)
        self.service_manager = LocalServiceManager()


class SetupUIRequestHandler(BaseHTTPRequestHandler):
    server: SetupUIServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(_HTML.replace("__SETUP_TOKEN__", self.server.setup_token))
            return
        if path == "/api/service/status":
            self._send_json(self.server.service_manager.status())
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            self._validate_mutating_request()
            payload = self._read_json()
            if path == "/api/plan":
                state = build_guided_setup_state(normalize_guided_setup_input(payload))
                self._send_json(state)
                return
            if path == "/api/write":
                result = write_guided_setup_config(normalize_guided_setup_input(payload))
                self._send_json(result)
                return
            if path == "/api/index":
                config_path = Path(str(payload.get("config_path") or "project.yaml"))
                repo_id = str(payload["repo_id"]) if payload.get("repo_id") else None
                result = index_project_from_config(
                    config_path=config_path, repo_id=repo_id, force=True
                )
                self._send_json(result)
                return
            if path == "/api/client-handoff":
                config_path = Path(str(payload.get("config_path") or "project.yaml"))
                clients = payload.get("clients") or ["hermes"]
                if isinstance(clients, str):
                    clients = [clients]
                self._send_json(build_client_handoff(config_path, clients=list(clients)))
                return
            if path == "/api/service/start":
                config_path = Path(str(payload.get("config_path") or "project.yaml"))
                host = str(payload.get("host") or "127.0.0.1")
                port = int(payload.get("port") or 8000)
                self._send_json(
                    self.server.service_manager.start(
                        config_path=config_path,
                        host=host,
                        port=port,
                    )
                )
                return
            if path == "/api/service/stop":
                self._send_json(self.server.service_manager.stop())
                return
        except GuidedSetupError as exc:
            self._send_json(
                {"status": "error", "error": exc.as_dict()}, status=HTTPStatus.BAD_REQUEST
            )
            return
        except ValueError as exc:
            self._send_json(
                {
                    "status": "error",
                    "error": {
                        "code": "BAD_REQUEST",
                        "message": str(exc),
                        "recoverable": True,
                    },
                },
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def _validate_mutating_request(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            raise GuidedSetupError(
                "JSON_REQUIRED",
                "Setup actions only accept application/json requests from the local setup page.",
            )
        fetch_site = self.headers.get("Sec-Fetch-Site", "")
        if fetch_site.lower() == "cross-site":
            raise GuidedSetupError(
                "CROSS_SITE_REQUEST_REJECTED",
                "Setup actions must come from this local setup page, not another website.",
            )
        origin = self.headers.get("Origin")
        if origin and not self._is_allowed_origin(origin):
            raise GuidedSetupError(
                "ORIGIN_REJECTED",
                "Setup actions must come from the local setup page origin.",
                details={"origin": origin},
            )
        token = self.headers.get("X-Setup-Token", "")
        if not hmac.compare_digest(token, self.server.setup_token):
            raise GuidedSetupError(
                "SETUP_TOKEN_REQUIRED",
                "Refresh the local setup page and try again.",
            )

    def _is_allowed_origin(self, origin: str) -> bool:
        parsed = urlparse(origin)
        host = parsed.hostname or ""
        return host in {"127.0.0.1", "localhost", "::1"} and parsed.port == self.server.server_port

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_setup_ui_server(host: str = "127.0.0.1", port: int = 8765) -> SetupUIServer:
    ensure_loopback_host(host)
    return SetupUIServer((host, port), SetupUIRequestHandler)


def run_setup_ui(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = create_setup_ui_server(host=host, port=port)
    try:
        print(f"Project Knowledge setup is available at http://{host}:{server.server_port}/")
        server.serve_forever()
    finally:
        server.service_manager.stop()
        server.server_close()


_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Project Knowledge MCP Setup</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; }
    label { display: block; margin-top: 1rem; font-weight: 600; }
    input, textarea, select, button { width: 100%; padding: .7rem; margin-top: .3rem; box-sizing: border-box; }
    button { cursor: pointer; font-weight: 700; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
    pre { background: #111827; color: #e5e7eb; padding: 1rem; overflow: auto; border-radius: .5rem; }
    .safe { background: #ecfdf5; border: 1px solid #10b981; padding: 1rem; border-radius: .5rem; }
    .warn { background: #fffbeb; border: 1px solid #f59e0b; padding: 1rem; border-radius: .5rem; }
  </style>
</head>
<body>
  <h1>Project Knowledge MCP guided setup</h1>
  <p class="safe">This setup page is local-only. For selected code repos it tests CodeGraph and, if missing, installs, initializes, and configures a local pinned CodeGraph CLI before writing config. It writes no secrets and keeps remote HTTPS off unless you explicitly opt in later.</p>
  <div class="row">
    <div><label>Config file<input id="config_path" value="project.yaml" /></label></div>
    <div><label>Project folder<input id="project_root" value="." /></label></div>
  </div>
  <label>Ops/project repo folder<input id="ops_repo" placeholder="/path/to/ops-repo" /></label>
  <label>Code repo folders (one per line)<textarea id="work_repos" rows="4"></textarea></label>
  <label>Client<select id="client"><option>hermes</option><option>claude-desktop</option><option>cursor</option><option>generic</option></select></label>
  <label><input id="confirm_overwrite" type="checkbox" style="width:auto" /> Overwrite existing config if one is already there</label>
  <p class="warn">Remote HTTPS bridge is intentionally not part of the happy path. Use local stdio or loopback HTTP first.</p>
  <button onclick="plan()">Preview setup</button>
  <button onclick="writeConfig()">Write config</button>
  <button onclick="startService()">Start local service</button>
  <button onclick="indexProject()">Run initial indexing</button>
  <button onclick="clientHandoff()">Show client connection snippet</button>
  <button onclick="stopService()">Stop local service</button>
  <pre id="out">Fill in folders, then preview setup.</pre>
<script>
const SETUP_TOKEN = '__SETUP_TOKEN__';
function payload() {
  return {
    config_path: document.getElementById('config_path').value,
    project_root: document.getElementById('project_root').value,
    ops_repo: document.getElementById('ops_repo').value,
    work_repos: document.getElementById('work_repos').value.split('\n').filter(Boolean),
    clients: [document.getElementById('client').value],
    confirm_overwrite: document.getElementById('confirm_overwrite').checked
  };
}
async function post(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Setup-Token': SETUP_TOKEN},
    body: JSON.stringify(body)
  });
  document.getElementById('out').textContent = JSON.stringify(await response.json(), null, 2);
}
function plan() { post('/api/plan', payload()); }
function writeConfig() { post('/api/write', payload()); }
function startService() { post('/api/service/start', {config_path: document.getElementById('config_path').value}); }
function stopService() { post('/api/service/stop', {}); }
function indexProject() { post('/api/index', {config_path: document.getElementById('config_path').value}); }
function clientHandoff() { post('/api/client-handoff', {config_path: document.getElementById('config_path').value, clients: [document.getElementById('client').value]}); }
</script>
</body>
</html>
"""
