---
title: Step 10B Guided Setup UX Decision
status: accepted
created: 2026-06-13
runbook: docs/runbooks/2026-06-13-step10-codegraph-guided-setup-release-gates.md
---

# Step 10B Guided Setup UX Decision

## Decision

Use a localhost browser setup wizard as the primary no-terminal onboarding path for Project Knowledge MCP.

The implementation adds `project-knowledge setup-ui`, which starts a loopback-only local web app. The app drives the same deterministic setup helpers used by the CLI (`build_setup_plan`, `write_setup_artifacts`, validation, indexing, and client-config rendering) but presents them as plain-language steps:

1. Welcome
2. Choose project folders
3. Check folders
4. Review generated settings
5. Confirm before writing files
6. Start or inspect the local service
7. Build the local search index
8. Connect the MCP client

This satisfies the no-terminal product principle while preserving the CLI for developers, CI, and automation.

## Rationale

A browser setup page is more normal-user friendly than a terminal-only CLI or developer TUI. It can run locally, require no hosted account, and later be packaged behind a desktop launcher without changing the server-side setup state machine.

The setup wizard remains local-first:

- binds to `127.0.0.1` by default;
- rejects non-loopback bind hosts;
- does not write secrets;
- does not enable remote HTTPS by default;
- does not add shell execution to MCP tools;
- uses explicit config overwrite confirmation;
- starts only the existing policy-enforced MCP server surface when requested.

## Boundaries

The guided setup path owns user onboarding, not authority or remote exposure.

- Existing config files are never overwritten without explicit confirmation.
- Remote HTTPS bridge setup is represented as a separate explicit opt-in state that requires risk acknowledgement and a bearer-token-gated bridge. The wizard does not silently create tokens or expose the service publicly.
- The local service lifecycle starts StreamableHTTP on loopback only. Docker/desktop packaging can build on the same state machine later.
- Initial indexing calls the existing `index_project_from_config` path so indexing behavior remains policy/config driven.
- Client handoff uses copy-ready snippets from the existing client-config renderer.

## Implementation notes

The implementation is split into two layers:

- `project_knowledge_mcp.setup_wizard`: serializable setup state machine, repo-path validation, overwrite safety, remote-bridge boundary checks, config writing, and client handoff.
- `project_knowledge_mcp.setup_ui`: minimal stdlib HTTP setup app, browser/API endpoints protected by a per-run setup token and same-origin checks, loopback service lifecycle support, and indexing trigger.

No new Python package dependency is required.

## Test coverage

Regression tests cover:

- happy-path state-machine flow and valid config writing;
- invalid repo path explanations in non-expert language;
- overwrite confirmation requirement;
- remote bridge opt-in/risk-acknowledgement boundary;
- setup UI API state response with local-only safety metadata;
- CLI discoverability and rejection of public `0.0.0.0` setup UI binding.

## Deferred work

Desktop launcher packaging and screenshots are deferred. The current implementation exposes the local browser app and documentation placeholders so a packaged launcher can wrap the same `setup-ui` command later.
