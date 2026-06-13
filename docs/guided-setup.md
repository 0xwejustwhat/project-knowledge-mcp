---
title: Guided Setup
status: current
created: 2026-06-13
---

# Guided Setup

Project Knowledge MCP's primary onboarding path is the local browser setup wizard. The page is loopback-only and every setup action uses a per-run setup token plus same-origin checks:

```bash
poetry run project-knowledge setup-ui
```

Open the printed local URL, normally:

```text
http://127.0.0.1:8765/
```

## What the wizard does

1. Explains that setup is local-first and no secrets are written.
2. Lets you choose the ops/project repo folder.
3. Lets you choose one or more code repo folders.
4. Validates folders and explains missing/non-Git paths in plain language.
5. Shows a YAML preview before writing `project.yaml`.
6. Requires explicit confirmation before overwriting an existing config.
7. Tests for CodeGraph when code repos are selected:
   - uses an existing healthy `codegraph` CLI if one is already configured or on `PATH`;
   - otherwise installs `@colbymchenry/codegraph@1.0.0` locally under `.project-knowledge/tools/codegraph-cli` via npm;
   - initializes CodeGraph for the selected code repos;
   - writes the verified local `codegraph` command into `project.yaml`.
8. Can start/inspect/stop the local loopback StreamableHTTP service.
9. Can trigger initial indexing through the same policy-enforced config path as the CLI.
10. Shows copy-ready client connection snippets.

## Screenshot placeholders

Desktop packaging and final screenshots are deferred. Until then, docs and release notes should use these placeholders:

- Welcome/local-first safety screen: `docs/assets/setup-ui-welcome-placeholder.svg`
- Repo selection and validation screen: `docs/assets/setup-ui-repos-placeholder.svg`
- Client handoff screen: `docs/assets/setup-ui-client-handoff-placeholder.svg`

## Remote bridge safety

Remote HTTPS bridge setup is not part of the default happy path. It remains off unless an operator explicitly opts in, acknowledges the risk, and configures a bearer-token-gated bridge.
