---
title: Step 10 CodeGraph, Guided Setup, and Release Gates Runbook
type: runbook
status: draft
snapshot_status: mirrored_reference
authority: working
created: 2026-06-13
snapshot_created: 2026-06-13
canonical_source_repo: 0xwejustwhat/Project-Knowledge-MCP-ops
canonical_source_path: docs/runbooks/2026-06-13-step10-codegraph-guided-setup-release-gates.md
canonical_source_commit: db899d5ed7d7403f1a08a417be063ef3352be301
canonical_source_pr: https://github.com/0xwejustwhat/Project-Knowledge-MCP-ops/pull/7
tags:
  - project-knowledge-mcp
  - step-10
  - codegraph
  - setup
  - release-gates
related:
  - docs/specs/0001-mvp-implementation-spec.md
---

> **Implementation snapshot:** This file is mirrored from `0xwejustwhat/Project-Knowledge-MCP-ops` for local implementation context. The ops repo remains canonical. See provenance fields above for source commit and PR.

# Step 10 CodeGraph, Guided Setup, and Release Gates Runbook

## Executive Summary

Step 10 closes the remaining product-critical MVP gaps after the Step 8/9 infrastructure merge.

Step 8/9 made the server deployable and gave agents/developers deterministic setup primitives. That is not enough for the product promise. The MVP is not complete until:

1. codebase context is backed by a real code-structure provider, not only text fallback;
2. normal users can onboard through guided setup without being expected to understand terminals, Docker, bind mounts, YAML, MCP transports, or Caddy;
3. Docker/HTTP/HTTPS deployment paths are enforced by CI/release checks rather than only documented.

This runbook splits Step 10 into three blocking gates:

- **Step 10A: CodeGraph Integration Gate**
- **Step 10B: Guided Setup / No-Terminal Onboarding Gate**
- **Step 10C: Release Deployment Gate**

## Product Principle

Project Knowledge MCP fails its product goal if a normal user must be a computer wizard to get value.

The CLI is infrastructure for developers, CI, and automation. It is not the primary user experience. The user-facing setup path must be guided, local-first, safe by default, and explicit about network exposure.

For codebases, text search is a degraded fallback. It is not the intended code-intelligence path. A codebase-oriented MVP must retrieve structured code evidence through a CodeGraph-style provider or an explicitly selected replacement that can answer symbol/file/test/schema relationship questions in stable machine-readable form.

## Current Baseline

As of the Step 8/9 merge, the code repo has:

- FastMCP tool surface over stdio and HTTP/StreamableHTTP;
- local parser + SQLite FTS5/BM25 document indexing;
- authority-aware search/ranking;
- staleness reporting;
- evidence packets/session briefs;
- safe write and authority proposal tools;
- Dockerfile, compose example, and Caddy example;
- deterministic CLI setup/status/client-config helpers.

Known gaps:

- CodeGraphContext is behind an adapter boundary but not healthy as the active code provider.
- Text fallback is available, but that only partially satisfies codebase-context value.
- Setup is CLI-first, not guided/no-terminal user onboarding.
- Docker/Caddy checks are not yet first-class release gates in CI.

---

# Step 10A: CodeGraph Integration Gate

## Goal

Make codebase context genuinely valuable by integrating a real code-structure provider as the preferred path for configured code repos, or reject the candidate with recorded evidence and select a replacement.

## Scope

Step 10A owns:

- CodeGraphContext candidate evaluation refresh;
- provider decision record;
- adapter implementation if candidate passes;
- replacement-provider selection if candidate fails;
- graph-backed `search_code`, `get_code_context`, and `get_code_provider_status` behavior;
- evidence-packet use of graph-backed code results;
- Docker/runtime proof for the chosen provider.

## Non-Goals

- Do not add LLM, embedding, GPU, OpenAI, or hosted parser requirements.
- Do not expose provider-specific raw shapes through public MCP responses.
- Do not silently redefine text fallback as the preferred path.
- Do not build a custom code graph from scratch unless all mature candidates fail and a separate design decision approves it.

## Required Decision Record

Create a decision doc under `docs/decisions/` before implementation proceeds beyond the spike.

Recommended path:

```text
docs/decisions/0004-step10-codegraph-provider-decision.md
```

The decision must answer:

1. Can CodeGraphContext install cleanly in the canonical Python/Docker runtime?
2. Can it index fixture and real code repos without credentials?
3. Can it run with vector/embedding modes disabled?
4. Does it expose stable machine-readable query output for files, symbols, and relationships?
5. Can it return enough structure for tests, schemas, call relationships, and file context?
6. What languages are supported well enough for expected users?
7. How does it fail, and can the MCP server recover with text fallback and warnings?
8. If rejected, what named replacement candidate is selected and why?

## Acceptance Criteria

Step 10A is complete only when all of the following are true:

1. `get_code_provider_status()` can report `active_provider: "codegraph"` and `codegraph_healthy: true` for a configured code repo when the provider is available.
2. `search_code()` returns stable public `CodeResult` records from the graph provider, including:
   - `repo_id`,
   - `path`,
   - `start_line`,
   - `end_line`,
   - `symbol`,
   - `kind`,
   - `snippet`,
   - `provider`,
   - `score`,
   - `related`.
3. `get_code_context(symbol_or_file)` returns symbol/file context and related tests/schemas/files where available.
4. Evidence packets include graph-backed code evidence for configured code repos.
5. If the provider is missing/unhealthy, tools fail soft to text fallback with explicit warnings.
6. No public MCP response leaks provider-specific raw output.
7. Docker build/run path works with the provider installed or with a documented companion-container/local-service strategy.
8. Tests prove both healthy-provider and fallback-provider behavior.
9. No test requires LLM keys, embedding keys, GPU, or network access.

## Suggested Implementation Tasks

### Task 10A.1: Refresh CodeGraphContext spike

- Re-run install/index/query against a tiny fixture repo and one real repo.
- Capture commands and raw evidence in `docs/spikes/` or in the decision doc appendix.
- Verify Docker/Python runtime compatibility.

### Task 10A.2: Write provider decision doc

- Record pass/fail evidence.
- If CodeGraphContext passes, approve it as the active provider candidate.
- If it fails, name the next replacement candidate and why.

### Task 10A.3: Add provider contract tests

- Test healthy provider status.
- Test graph-backed `search_code` result shape.
- Test `get_code_context` related evidence.
- Test provider-unhealthy fallback and warnings.

### Task 10A.4: Implement adapter

- Keep provider-specific parsing internal.
- Normalize into existing stable `CodeResult` payloads.
- Preserve line ranges and related evidence where available.

### Task 10A.5: Integrate with evidence packets

- Ensure `retrieve_ops_code_evidence` and `generate_session_brief` use graph-backed code evidence when available.
- Preserve explicit gaps when code evidence is missing.

### Task 10A.6: Verify Docker path

- Build image.
- Run provider health command inside container or against approved companion service.
- Prove fallback still works when the provider is absent.

---

# Step 10B: Guided Setup / No-Terminal Onboarding Gate

## Goal

Make Project Knowledge MCP usable by non-expert users through a guided local setup path that does not require terminal fluency.

## Scope

Step 10B owns the user-facing setup experience:

- local setup UI/TUI/browser wizard;
- repository selection/registration;
- config generation and validation;
- Docker/service lifecycle guidance or automation;
- client connection handoff;
- index/status visibility;
- safe remote-bridge enablement only through explicit opt-in.

## Non-Goals

- Do not expose project data remotely by default.
- Do not require users to understand Docker, bind mounts, YAML, MCP transports, environment variables, or Caddy.
- Do not require a hosted SaaS account.
- Do not make shell execution available through MCP tools.

## User Experience Requirement

A normal user should be able to complete the happy path by opening a local app/page or guided installer and following plain-language prompts.

The user should not need to manually:

- write YAML;
- copy Docker volume syntax;
- choose MCP transport modes;
- construct JSON client config;
- edit Caddy config;
- reason about loopback vs public binding.

## Acceptance Criteria

Step 10B is complete only when all of the following are true:

1. A guided setup surface exists and is documented as the primary onboarding path.
2. The user can select or enter an ops/project repo and one or more code repos.
3. The setup surface validates repo paths and explains failures in non-expert language.
4. The setup surface generates or updates project config safely.
5. Existing config files are not overwritten without explicit confirmation.
6. The user can start, stop, and inspect local service status from the guided surface or a packaged local control app.
7. The user can trigger initial indexing and see progress/result state.
8. The user receives copy-ready or one-click client connection guidance for supported clients.
9. Remote HTTPS bridge setup is opt-in, visibly risky, bearer-token gated, and never enabled silently.
10. Tests cover the setup state machine and critical safety boundaries.

## Smallest Viable Guided Setup

The smallest acceptable implementation may be a localhost browser setup app backed by existing deterministic setup helpers.

Recommended path:

- `project-knowledge setup-ui` starts a localhost-only setup server;
- the app opens or prints a local URL;
- the UI drives the existing setup planning/writing functions;
- no project data leaves the machine;
- the app can be packaged later behind a desktop launcher.

A TUI may be useful for developers, but a browser setup app is more aligned with the no-terminal product principle.

## Suggested Implementation Tasks

### Task 10B.1: Write setup UX decision

- Choose localhost browser UI, TUI, desktop wrapper, or staged combination.
- Explain why it satisfies no-terminal onboarding.

### Task 10B.2: Extract setup state machine

- Model steps explicitly: welcome, repo selection, validation, config preview, write confirmation, service lifecycle, indexing, client handoff.
- Keep state serializable and testable.

### Task 10B.3: Add guided setup tests

- Test happy path.
- Test invalid repo explanation.
- Test overwrite confirmation requirement.
- Test remote bridge opt-in boundary.

### Task 10B.4: Implement local setup surface

- Use the existing `build_setup_plan`, `write_setup_artifacts`, and client config renderers.
- Keep localhost binding by default.
- Do not add cloud dependency.

### Task 10B.5: Add service lifecycle support

- Provide safe start/stop/status for local service.
- If Docker is required but unavailable, explain the missing prerequisite plainly.
- Avoid hidden public network exposure.

### Task 10B.6: Add docs and screenshots/placeholders

- README should start with guided setup, not CLI-only setup.
- CLI commands remain documented for developers and automation.

---

# Step 10C: Release Deployment Gate

## Goal

Turn Step 8/9 deployment documentation into enforced release checks.

## Scope

Step 10C owns CI/release validation for:

- Docker build;
- container CLI smoke;
- stdio start smoke where feasible;
- localhost HTTP/StreamableHTTP smoke;
- Caddy config validation;
- bearer-token remote bridge behavior;
- setup wizard smoke once Step 10B exists.

## Acceptance Criteria

Step 10C is complete only when all of the following are true:

1. CI builds the Docker image.
2. CI runs at least one containerized CLI smoke test.
3. CI verifies the server can start in containerized stdio or loopback HTTP mode.
4. CI validates the Caddy example, either with a real Caddy binary/container or a clearly justified equivalent.
5. CI or integration tests prove missing/wrong bearer token returns 401 before MCP reaches the upstream app.
6. CI proves correct-token path reaches the same policy-enforced MCP tool registry as local mode.
7. CI does not require secrets, public network exposure, GPU, model keys, or hosted parser credentials.
8. Release docs name the exact checks required before tagging/shipping.

## Suggested Implementation Tasks

### Task 10C.1: Mirror actual CI locally

- Inspect `.github/workflows`.
- Add Docker and Caddy checks without weakening existing lint/test gates.

### Task 10C.2: Add Docker build job

- Build image using canonical Python runtime.
- Cache dependencies only if it does not hide missing lockfile/package issues.

### Task 10C.3: Add container smoke tests

- Run `project-knowledge --help`.
- Run `project-knowledge start --help`.
- Run a minimal loopback HTTP smoke if CI permits background process orchestration.

### Task 10C.4: Add Caddy validation

- Prefer real `caddy validate`.
- If Caddy installation is unavailable, use official Caddy container validation.

### Task 10C.5: Add release checklist

- Document required local and CI evidence before a release is tagged.

---

# Ordering and Dependencies

Recommended order:

1. **10A decision first.** CodeGraph value determines whether the current provider candidate remains viable.
2. **10B setup surface second, in parallel only after setup state boundaries are clear.** A guided setup UX should expose real provider status, not hide CodeGraph uncertainty.
3. **10C hardening continuously.** Add CI checks as soon as each behavior exists.

The work can run in parallel only if each lane preserves the product gates:

- 10A owns code intelligence truth.
- 10B owns normal-user onboarding.
- 10C owns release proof.

# Definition of Done for Step 10

Step 10 is complete when:

1. CodeGraph-style provider path is either healthy and integrated, or formally rejected with a selected replacement and implementation plan.
2. Text fallback is clearly marked degraded mode, not the primary codebase value path.
3. Guided setup exists for normal users and does not require terminal fluency for the happy path.
4. Setup includes safe local lifecycle/status/indexing/client-handoff behavior.
5. Docker/Caddy release checks are enforced in CI or an equivalent release gate.
6. Documentation leads with guided setup and accurately describes CLI as developer/automation infrastructure.
7. The code repo carries a mirrored snapshot of this runbook with provenance back to the ops repo.

# Guardrails

- Local-first by default.
- No remote exposure without explicit opt-in.
- No LLM, embedding, GPU, hosted parser, or cloud requirement.
- No shell-execution MCP tool.
- No direct canonical mutation through casual tools.
- No provider-specific response leakage.
- No claim of codebase intelligence while only text fallback is active.
- No user-facing setup flow that assumes terminal or Docker expertise.
