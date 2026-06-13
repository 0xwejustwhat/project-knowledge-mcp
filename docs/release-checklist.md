# Release Checklist

Project Knowledge MCP releases must be tagged only after the local and CI release gates below pass. These gates are local-first and must not require secrets, public network exposure, GPU, model keys, hosted parser credentials, LLM keys, or embedding keys.

## Required pre-tag checks

Run the same quality gates used by CI:

```bash
poetry install --no-interaction --no-ansi
poetry run ruff format --check .
poetry run ruff check .
poetry run pytest -q
docker build -t project-knowledge-mcp:ci .
poetry run bash scripts/ci/release_deployment_gates.sh
```

## Deployment gates enforced by `scripts/ci/release_deployment_gates.sh`

The release deployment gate must prove all of the following before shipping:

1. Docker image builds from the repository `Dockerfile` using the canonical Python runtime.
2. Containerized CLI smoke passes:
   - `docker run --rm project-knowledge-mcp:ci --help`
   - `docker run --rm project-knowledge-mcp:ci start --help`
   - `docker run --rm project-knowledge-mcp:ci setup-ui --help`
3. Containerized StreamableHTTP starts on a loopback-published port and exposes the same policy-enforced MCP registry as local mode.
4. The Caddy example validates with the official `caddy:2-alpine` container and a non-empty `MCP_AUTH_TOKEN`.
5. A real Caddy remote-bridge smoke proves bearer-token behavior before release:
   - Missing token returns `401` before MCP.
   - Wrong token returns `401` before MCP.
   - Correct token reaches the same policy-enforced MCP tool registry.
6. The setup wizard remains a local onboarding surface; remote HTTPS bridge setup stays explicit opt-in.
7. Guided setup detects CodeGraph for selected code repos and, when missing, installs/configures/initializes the pinned local CodeGraph CLI instead of requiring a user-managed install.
8. No secrets, public network exposure, GPU, model keys, or hosted parser credentials are required.

## Release evidence to include with a tag or release PR

- Link to the passing CI run containing `lint-and-test` and `release-deployment-gates`.
- Paste local command evidence if a release manager ran the gates manually.
- Note any intentionally skipped checks and why. Do not mark a release ready if Docker, Caddy validation, bearer-token behavior, or registry-parity checks were skipped without an approved replacement gate.
