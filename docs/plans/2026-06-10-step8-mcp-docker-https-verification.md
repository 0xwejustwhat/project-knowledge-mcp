# Step 8 MCP, Docker, and HTTPS/Caddy Verification Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Keep controller ownership of transport/auth boundary decisions; use subagents for discovery/review lanes and serial implementation only.

**Goal:** Complete Phase 8 by proving the full Phase 7 MCP tool surface works through MCP clients, local HTTP/StreamableHTTP, Docker packaging, and an explicit Caddy bearer-token HTTPS bridge.

**Architecture:** Keep one policy-enforced FastMCP server (`create_mcp()`) and expose it through stdio or StreamableHTTP without adding a special remote variant. Docker packages the same CLI/server. Caddy is an external opt-in transport/auth wrapper that rejects missing/wrong bearer tokens before proxying to the same `/mcp` endpoint.

**Tech Stack:** Python 3.12, FastMCP, Typer, pytest, Docker, Caddyfile example, YAML deployment artifacts.

---

## Guardrails / Non-goals

- Do not add shell execution, arbitrary filesystem writes, merge/approve actions, or canonical direct-write authority.
- Do not create a second remote-only MCP implementation.
- Do not require LLM keys, embedding keys, network APIs, GPU, or cloud services for tests.
- Docker may bind `0.0.0.0` inside the container only when host publishing is loopback-bound by default.
- Caddy validates/guards transport only; Python services remain the authority policy boundary.

## Task 1: Assert complete MCP registry and exercise Step 7 write/proposal tools via FastMCP client

**Objective:** Prove the full registered MCP surface is complete and that the Step 7 tools are callable through an MCP client, not just service tests.

**Files:**
- Modify: `tests/test_mcp_tools.py`

**Steps:**
1. Add an `EXPECTED_TOOL_NAMES` set with all 16 current policy-enforced tools.
2. Add `test_mcp_tool_registry_matches_phase7_surface` using `Client(create_mcp()).list_tools()` and `health`.
3. Add MCP-client tests for `add_project_note`, `create_draft_artifact`, and `propose_authority_change` using the existing temp git/config helpers.
4. Run targeted tests and confirm failures before implementation when possible, then green.

**Verification:**
```bash
PYTHONPATH=src python3 -m pytest tests/test_mcp_tools.py -q
```

## Task 2: Add local HTTP/StreamableHTTP integration harness

**Objective:** Prove the same tool registry is reachable over loopback StreamableHTTP at `/mcp`.

**Files:**
- Create: `tests/test_http_transport.py`
- Modify: `src/project_knowledge_mcp/server.py` if needed for default port alignment

**Steps:**
1. Add a subprocess test that starts `python -m project_knowledge_mcp.server start --transport streamable-http --host 127.0.0.1 --port <free>`.
2. Wait for `/mcp` to become reachable via `fastmcp.Client`.
3. Assert `health.status == "ok"` and remote HTTP tool names equal `EXPECTED_TOOL_NAMES` / local registry.
4. Align CLI default HTTP port with FastMCP/spec default `8000` if tests/docs require it.

**Verification:**
```bash
PYTHONPATH=src python3 -m pytest tests/test_http_transport.py -q
```

## Task 3: Add remote HTTPS/Caddy bearer-gate test harness

**Objective:** Prove missing/wrong bearer credentials fail before MCP and correct credentials reach the same policy-enforced tool registry, without requiring a Caddy binary in unit tests.

**Files:**
- Create: `tests/test_remote_https_bridge.py`

**Steps:**
1. Use `create_mcp().http_app(path="/mcp", transport="streamable-http")` with a tiny ASGI bearer middleware that mirrors the Caddy policy.
2. Drive the wrapped ASGI app with an in-process uvicorn server and `fastmcp.Client(..., auth="token")`.
3. Assert no `Authorization` header returns `401`.
4. Assert wrong bearer token returns `401`.
5. Assert correct bearer token can call `health` and list the exact same policy-enforced tools as local mode.

**Verification:**
```bash
PYTHONPATH=src python3 -m pytest tests/test_remote_https_bridge.py -q
```

## Task 4: Add deployment artifacts

**Objective:** Provide the concrete Docker, Compose, project config, and Caddy examples required by Phase 8 acceptance.

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.example.yaml`
- Create: `project.example.yaml`
- Create: `deploy/Caddyfile.example`
- Create/Modify: deployment docs in `README.md`

**Steps:**
1. Add a Python 3.12 Dockerfile that installs the package and exposes port 8000.
2. Add compose example that publishes `127.0.0.1:8000:8000`, mounts `/workspace:rw`, and uses StreamableHTTP on `0.0.0.0:8000` inside the container.
3. Add a minimal `project.example.yaml` whose default ops repo is `/workspace` and state dir is `/workspace/.project-knowledge`.
4. Add `deploy/Caddyfile.example` that checks `Authorization: Bearer {$MCP_AUTH_TOKEN}`, returns `401`, then reverse-proxies to `project-knowledge-mcp:8000`.
5. Update README with local stdio, local HTTP, Docker build/run, generic MCP client, Hermes config, and browser/mobile/Caddy bridge boundaries.

**Verification:**
```bash
PYTHONPATH=src python3 -m pytest tests/test_deployment_artifacts.py -q
```

## Task 5: Add static deployment artifact tests and final quality gates

**Objective:** Make deployment artifact expectations executable and run full CI-shaped validation.

**Files:**
- Create: `tests/test_deployment_artifacts.py`

**Steps:**
1. Assert Dockerfile exists, uses Python 3.12, exposes 8000, and defaults to the packaged CLI.
2. Assert compose binds host loopback only and uses StreamableHTTP inside the container.
3. Assert Caddyfile has the bearer matcher, `respond ... 401`, and `reverse_proxy`.
4. If `caddy` is installed, run `caddy validate`; otherwise skip real binary validation with an explicit pytest skip.
5. Run targeted Step 8 tests, then full pytest, then ruff.
6. Attempt Docker build/run acceptance if base images/network allow; if blocked by missing network/cache, report the exact blocker and verified static/runtime harness coverage.

**Verification:**
```bash
PYTHONPATH=src python3 -m pytest tests/test_mcp_tools.py tests/test_http_transport.py tests/test_remote_https_bridge.py tests/test_deployment_artifacts.py -q
PYTHONPATH=src python3 -m pytest tests -q
python3 -m ruff check .
docker build -t project-knowledge-mcp:test .
docker run --rm -i -v "$PWD:/workspace:rw" -e PROJECT_KNOWLEDGE_CONFIG=/workspace/project.example.yaml project-knowledge-mcp:test --help
```
