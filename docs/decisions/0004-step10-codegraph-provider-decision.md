---
title: Step 10 CodeGraph Provider Decision
status: accepted
created: 2026-06-13
runbook: docs/runbooks/2026-06-13-step10-codegraph-guided-setup-release-gates.md
provider_candidate: colbymchenry/codegraph
provider_repo: https://github.com/colbymchenry/codegraph
provider_version_validated: 1.0.0
---

# Step 10 CodeGraph Provider Decision

## Decision

Use `colbymchenry/codegraph` as the Step 10A code-structure provider for Project Knowledge MCP.

Project Knowledge MCP shells out to an operator-installed `codegraph` CLI. It does not vendor CodeGraph internals and does not expose provider-specific raw output through MCP responses. Public tools normalize provider output into stable `CodeResult` records and fail soft to text fallback with explicit warnings when the provider is missing or unhealthy.

## Validation questions

### 1. Can CodeGraph install cleanly as an external CLI / MCP sidecar in the canonical runtime path?

Yes for the validated local Linux path.

Validated commands:

```bash
node --version
npm --version
npm view @colbymchenry/codegraph version dist.tarball bin engines dependencies --json
npm view @colbymchenry/codegraph-linux-x64@1.0.0 version dist.unpackedSize dist.fileCount os cpu --json
mkdir -p /tmp/codegraph-viability
cd /tmp/codegraph-viability
DO_NOT_TRACK=1 CODEGRAPH_INSTALL_DIR=/tmp/codegraph-install npm_config_cache=/tmp/codegraph-npm-cache npm install @colbymchenry/codegraph@1.0.0
```

Observed evidence:

- Node: `v22.22.2`
- npm: `10.9.7`
- package: `@colbymchenry/codegraph@1.0.0`
- bin: `codegraph -> npm-shim.js`
- platform optional dependency: `@colbymchenry/codegraph-linux-x64@1.0.0`
- linux-x64 unpacked size: about 197 MB
- install result: `added 2 packages in 14s`

Runtime strategy:

- Local/dev: install CodeGraph as an external CLI and point `code_context.codegraph.command` at it if it is not on `PATH`.
- Container: either install the same CLI in the image/runtime environment or run Project Knowledge MCP against host-mounted repos with a companion/operator-installed CodeGraph binary available in the container path. Fallback remains available when no CodeGraph CLI is installed.

### 2. Can `codegraph init`, `status --json`, `explore`, and `node` operate on fixture and real code repos without credentials?

Yes for a tiny local JavaScript fixture repo.

Validated command existence:

```bash
DO_NOT_TRACK=1 ./node_modules/.bin/codegraph --version
DO_NOT_TRACK=1 ./node_modules/.bin/codegraph init --help
DO_NOT_TRACK=1 ./node_modules/.bin/codegraph status --help
DO_NOT_TRACK=1 ./node_modules/.bin/codegraph explore --help
DO_NOT_TRACK=1 ./node_modules/.bin/codegraph node --help
```

Observed version: `1.0.0`.

Fixture commands:

```bash
DO_NOT_TRACK=1 ./node_modules/.bin/codegraph status /tmp/codegraph-tiny --json
DO_NOT_TRACK=1 ./node_modules/.bin/codegraph init /tmp/codegraph-tiny --verbose
DO_NOT_TRACK=1 ./node_modules/.bin/codegraph status /tmp/codegraph-tiny --json
DO_NOT_TRACK=1 ./node_modules/.bin/codegraph explore -p /tmp/codegraph-tiny "how does run compute result"
DO_NOT_TRACK=1 ./node_modules/.bin/codegraph node -p /tmp/codegraph-tiny add
DO_NOT_TRACK=1 ./node_modules/.bin/codegraph node -p /tmp/codegraph-tiny src/index.js --limit 80
```

Observed post-init status included:

```json
{
  "initialized": true,
  "version": "1.0.0",
  "fileCount": 2,
  "nodeCount": 8,
  "edgeCount": 14,
  "backend": "node-sqlite",
  "languages": ["javascript"],
  "pendingChanges": {"added": 0, "modified": 0, "removed": 0}
}
```

`explore` returned structured Markdown grouped by file and symbols. `node add` returned location, signature, source snippet, and caller relationship.

### 3. Can it run local-only with telemetry disabled and without LLM, embedding, GPU, OpenAI, or hosted parser requirements?

Yes, with telemetry explicitly disabled.

Validated telemetry command:

```bash
DO_NOT_TRACK=1 ./node_modules/.bin/codegraph telemetry status
```

Observed output:

```text
Telemetry: disabled (DO_NOT_TRACK environment variable)
```

Project Knowledge MCP sets both `DO_NOT_TRACK=1` and `CODEGRAPH_TELEMETRY=0` for all CodeGraph shell-outs.

No API key, LLM, embedding model, GPU, hosted parser, or cloud parser was needed for `init`, `status`, `explore`, or `node` in the fixture smoke.

### 4. Does it expose stable output that the adapter can normalize for files, symbols, and relationships without leaking provider-specific raw shapes?

Yes, with a conservative adapter boundary.

Public MCP responses expose only `CodeResult` fields:

- `repo_id`
- `path`
- `start_line`
- `end_line`
- `symbol`
- `kind`
- `snippet`
- `provider`
- `score`
- `related`

Provider status exposes sanitized details such as initialized repos, version, counts, languages, pending changes, and CLI path. Raw provider Markdown and raw JSON are not included in public search/context results.

### 5. Can it return enough structure for tests, schemas, call relationships, and file context?

Yes for the validated shape.

Observed `explore` grouped symbols by file. Observed `node` returned symbol location, source snippet, and caller relationship. The adapter also normalizes related file lines when provided by the provider output.

### 6. What languages are supported well enough for expected users?

Validated directly: JavaScript.

CodeGraph reported `languages: ["javascript"]` for the smoke repo. Project Knowledge MCP adapter tests cover Python and JSON/schema-shaped normalized results through a fake CLI contract fixture. Full language support remains delegated to the external CodeGraph provider and should be documented from the provider's own release notes.

### 7. How does it fail, and can the MCP server recover with text fallback and warnings?

Observed/handled failure modes:

- CLI missing: `get_code_provider_status` reports `active_provider: "text"`, `codegraph_healthy: false`, and an explicit installation/index warning when fallback is enabled.
- Repo not initialized: status is unhealthy and names missing initialized repos.
- Command failure during search/context: the service falls back to text search when enabled and adds an explicit warning such as `CodeGraph search failed; using text fallback: ...`.
- Fallback disabled: tools return recoverable `PROVIDER_UNAVAILABLE` instead of silently claiming code intelligence.

### 8. If rejected, what replacement candidate is selected?

Not applicable. `colbymchenry/codegraph` is accepted for Step 10A with the conditions below.

## Conditions

1. All Project Knowledge MCP shell-outs must disable telemetry with `DO_NOT_TRACK=1` and `CODEGRAPH_TELEMETRY=0`.
2. Public MCP payloads must keep the stable `CodeResult` contract and must not leak provider-specific raw Markdown or JSON.
3. Text fallback must remain explicitly marked as degraded fallback and may not be described as the preferred code-intelligence path.
4. Locked-down/offline Docker/runtime paths should pin the npm/release artifact and may set `CODEGRAPH_NO_DOWNLOAD=1` to prevent the npm shim from downloading fallback release bundles.
5. The large platform package footprint (~197 MB unpacked for linux-x64) should be considered in release packaging.

## Implementation evidence in this repo

Step 10A implementation adds:

- CodeGraph CLI status checks via `codegraph status <repo> --json`.
- CodeGraph search via `codegraph explore -p <repo> <query>`.
- CodeGraph context lookup via `codegraph node -p <repo> <symbol-or-file>`.
- Stable `CodeResult` normalization for graph-backed search/context.
- Graph-backed evidence-packet code sections.
- Fallback warnings and provider-unavailable behavior.
- Regression tests for healthy provider, graph result shape, related evidence, evidence packets, and fallback.
