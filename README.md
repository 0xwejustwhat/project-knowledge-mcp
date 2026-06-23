# Project Knowledge MCP

Project Knowledge MCP (PKMCP) is a local-first Model Context Protocol server for repo-grounded project knowledge. The repo is the memory/evidence layer; the MCP server is the deterministic access layer. It returns evidence packets, health metadata, and bounded write/proposal results while the connected assistant performs synthesis.

## Current MVP surface

PKMCP exposes the same policy-enforced FastMCP server through stdio for local clients and StreamableHTTP for local/Docker/remote-bridge clients. No mode adds shell execution, arbitrary filesystem writes, merge authority, or direct canonical write authority.

Registered MCP tools:

- `health`
- `validate_config`
- `index_project`
- `search_ops`
- `search_decisions`
- `get_current_doctrine`
- `search_open_questions`
- `search_code`
- `get_code_context`
- `get_code_provider_status`
- `retrieve_ops_code_evidence`
- `generate_session_brief`
- `add_project_note`
- `create_draft_artifact`
- `propose_authority_change`
- `check_project_staleness`

Default doctrine:

- Local parser registry + SQLite FTS5/BM25 is the default retrieval path.
- No LLM key, embedding key, cloud parser, GPU, or network API is required.
- Low-authority capture writes are allowed only through configured capture paths.
- Draft/proposal artifacts are non-canonical.
- Authority changes are proposed through branch/commit/PR flows; the MCP server cannot approve, merge, or mark proposals accepted.

## Local development

```bash
poetry install --with dev
poetry run pytest
```

If Poetry is unavailable in a local shell, tests can be run from the repo root with the package on `PYTHONPATH`:

```bash
PYTHONPATH=src python3 -m pytest tests -q
```

## Guided setup: recommended onboarding path

Start the local browser setup wizard and follow the prompts:

```bash
poetry run project-knowledge setup-ui
```

The setup page is served on `http://127.0.0.1:8765/` by default. It guides a normal user through choosing an ops/project repo, choosing one or more code repos, validating paths, testing for CodeGraph, installing/configuring/initializing a local CodeGraph CLI when missing, previewing config, confirming before writes, starting/checking the loopback local service, running initial indexing, and copying a client connection snippet.

Safety defaults:

- local-only browser app and MCP service binding;
- per-run setup token and same-origin checks for setup actions;
- local CodeGraph CLI auto-install/configure/initialize for selected code repos when missing;
- no hosted SaaS account;
- no LLM key, embedding key, cloud parser, GPU, or network API requirement;
- no secrets written;
- existing config files are not overwritten without explicit confirmation;
- remote HTTPS bridge remains off unless separately and explicitly opted in behind a bearer token.

Developer/automation alternatives remain available below.

## Working with PKMCP as an AI agent

PKMCP is designed for the **discovery → verify** two-step pattern:

1. **PKMCP for discovery** — use `search_code`, `get_code_context`, `search_ops`, or `retrieve_ops_code_evidence` to find which files, symbols, and docs are relevant to a question. PKMCP returns bounded snippets with provenance metadata (authority, staleness, provider type).

2. **Direct read for verification** — once PKMCP points you at a file and line range, use `read_file` or the equivalent to see the complete logic. PKMCP snippets are bounded and cannot show what's *missing* (control flow gaps, absent error handling, missing else-branches).

**What PKMCP is good for:**
- "Where is this feature implemented?" — broad codebase queries
- "What does the project memory say about this decision?" — docs/decisions discovery
- "Which files touch this concept?" — cross-repo evidence surfacing
- "Is the index stale?" — staleness and provider health checks

**What PKMCP is not for:**
- Full function-level logic tracing — PKMCP shows bounded snippets, not complete functions
- Finding what's *absent* — PKMCP indexes what exists in the repo; it cannot surface missing code paths
- Live state — branch status, uncommitted diffs, test results require direct tool access
- Replacing direct read — PKMCP finds the *where*, direct read confirms the *what*

When CodeGraph reports as unhealthy, check `get_code_provider_status` for the `details` block. If `indexed_repos: []` or `missing_index_repos` is non-empty, the binary exists but needs indexing (`codegraph init && codegraph index`). If the binary path in `project.yaml` doesn't exist on disk, the setup write phase was never completed — do **not** patch the path by hand. Instead, run a fresh setup that correctly installs and initializes CodeGraph.

## Configuration

Copy or adapt the example config:

```bash
cp project.example.yaml project.yaml
poetry run project-knowledge validate-config --config ./project.yaml
poetry run project-knowledge index-project --config ./project.yaml
```

The Docker example expects the config at `/workspace/project.yaml`; the included `project.example.yaml` is therefore written with `/workspace` container paths.

## Searching

CLI commands use hyphens, while MCP tools use underscores: for example, `search-code` on the command line corresponds to the `search_code` MCP tool.

```bash
poetry run project-knowledge search-ops "deployment doctrine" --config ./project.yaml
poetry run project-knowledge search-code "HTTPS Caddy bridge" --config ./project.yaml
poetry run project-knowledge search-decisions "remote bridge" --config ./project.yaml
poetry run project-knowledge search-open-questions "release blocker" --config ./project.yaml
poetry run project-knowledge search-index "raw lexical query" --config ./project.yaml
```

## CLI setup/client bootstrap

Use `setup` to preview or generate a local/no-LLM `project.yaml`, Docker mount guidance, and client snippets without manually assembling YAML:

```bash
poetry run project-knowledge setup \
  --non-interactive \
  --dry-run \
  --config ./project.yaml \
  --project-root "$PWD" \
  --ops-repo /absolute/path/to/ops-repo \
  --work-repo /absolute/path/to/work-repo \
  --client hermes
```

Remove `--dry-run` to write `project.yaml`. Existing config files are not overwritten unless `--force` is provided. Setup is deliberately non-operational: it does not start Docker, does not open a remote listener, and does not write tokens or credentials.

Print client snippets independently:

```bash
# Local stdio client snippet
poetry run project-knowledge print-client-config --config ./project.yaml --client hermes --transport stdio

# Loopback StreamableHTTP snippet
poetry run project-knowledge print-client-config \
  --config ./project.yaml \
  --client generic \
  --transport streamable-http \
  --http-url http://127.0.0.1:8000/mcp

# Remote HTTPS bridge snippet with redacted auth placeholder
poetry run project-knowledge print-client-config \
  --config ./project.yaml \
  --client generic \
  --transport remote-https \
  --remote-url https://pkmcp.example.com/mcp
```

Check both config validity and repo/index freshness in one JSON packet:

```bash
poetry run project-knowledge status --config ./project.yaml
```

`print-client-config` and `status` point to the same policy-enforced server surface as stdio, local HTTP, Docker, and the optional HTTPS bridge.

## Local MCP clients: stdio

Use stdio for local desktop/coding-agent clients that spawn MCP servers directly:

```bash
poetry run project-knowledge start --transport stdio
```

Generic MCP client shape:

```json
{
  "mcpServers": {
    "project-knowledge": {
      "command": "poetry",
      "args": ["run", "project-knowledge", "start", "--transport", "stdio"],
      "cwd": "/absolute/path/to/project-knowledge-mcp",
      "env": {
        "PROJECT_KNOWLEDGE_CONFIG": "/absolute/path/to/project.yaml"
      }
    }
  }
}
```

Hermes native MCP config follows the same command/args/cwd/env structure under the configured MCP server entry.

## Local HTTP / StreamableHTTP

Use StreamableHTTP for local loopback smoke tests or clients that connect to an HTTP MCP endpoint:

```bash
poetry run project-knowledge start \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8000
```

The MCP endpoint is:

```text
http://127.0.0.1:8000/mcp
```

HTTP mode serves the same `create_mcp()` tool registry as stdio. It is loopback-bound by default.

## Docker

Build the image:

```bash
docker build -t project-knowledge-mcp:test .
```

Show CLI help through the container entrypoint:

```bash
docker run --rm -i \
  -v "$PWD:/workspace:rw" \
  -e PROJECT_KNOWLEDGE_CONFIG=/workspace/project.example.yaml \
  project-knowledge-mcp:test --help
```

Run StreamableHTTP inside Docker while publishing only to host loopback:

```bash
docker run --rm \
  -p 127.0.0.1:8000:8000 \
  -v "$PWD:/workspace:rw" \
  -e PROJECT_KNOWLEDGE_CONFIG=/workspace/project.example.yaml \
  project-knowledge-mcp:test \
  start --transport streamable-http --host 0.0.0.0 --port 8000
```

Inside the container, `0.0.0.0` is used only so Docker can forward traffic. The host-side publish remains `127.0.0.1:8000:8000` by default.

Compose example:

```bash
docker compose -f docker-compose.example.yaml up --build project-knowledge-mcp
```

## Authorized HTTPS/Caddy bridge for browser/mobile assistants

Some browser-hosted assistants require an HTTPS-reachable MCP endpoint. PKMCP supports that as an explicit remote bridge:

1. Run the normal PKMCP StreamableHTTP server.
2. Put Caddy in front of it for HTTPS termination.
3. Require an authorization bearer header before proxying to PKMCP.
4. Proxy to the same `/mcp` endpoint and same policy-enforced tools.

Validate the example Caddyfile when Caddy is installed:

```bash
MCP_AUTH_TOKEN=[REDACTED] \
PKMCP_SITE_ADDRESS=http://127.0.0.1:18080 \
PKMCP_UPSTREAM=127.0.0.1:8000 \
caddy validate --config deploy/Caddyfile.example --adapter caddyfile
```

Enable the compose Caddy bridge only when you intentionally want remote exposure. The compose file does not require `MCP_AUTH_TOKEN` for local-only `docker compose ... up project-knowledge-mcp`, but the remote-bridge service exits before Caddy starts unless you set a non-empty token:

```bash
MCP_AUTH_TOKEN=[REDACTED] \
PKMCP_SITE_ADDRESS='pkmcp.example.com' \
docker compose -f docker-compose.example.yaml --profile remote-bridge up --build
```

Remote clients must send:

```http
Authorization: Bearer [REDACTED]
```

Expected bridge behavior:

- Missing token: `401` before MCP.
- Wrong token: `401` before MCP.
- Correct token: reaches the same FastMCP `/mcp` endpoint.
- Visible tools match local stdio/HTTP tools exactly.

Caddy is not an authority boundary for repo writes. It is only transport/auth wrapping. The Python server still enforces all read/write/proposal policies.
