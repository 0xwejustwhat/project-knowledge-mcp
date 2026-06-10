---
title: Project Knowledge MCP MVP Implementation Spec
status: current_snapshot
authority: implementation_reference
snapshot_status: mirrored_reference
canonical_status: draft_for_review
author: Hermes Agent
created: 2026-06-09
version: 0.5
reviewed_after_step0: 2026-06-09
snapshot_created: 2026-06-09
canonical_source_repo: 0xwejustwhat/Project-Knowledge-MCP-ops
canonical_source_path: docs/specs/0001-mvp-implementation-spec.md
canonical_source_commit: 26e1e1aa0c3a909a7fc4ec5ec85c38af2efeef7d
canonical_source_pr: https://github.com/0xwejustwhat/Project-Knowledge-MCP-ops/pull/4
source_documents:
  - docs/PRD.md
  - docs/amendments/0001-capture-governance-reconciliation.md
  - docs/discussions/2026-06-09-initial-brainstorm-summary.md
  - docs/discussions/2026-06-09-local-parser-sqlite-codegraph-decision.md
  - docs/decisions/0002-workspace-vs-snapshot-repo-source.md
  - project-knowledge-mcp:docs/decisions/0001-step-0-provider-evaluation.md
---

> **Implementation snapshot:** This file is mirrored from `0xwejustwhat/Project-Knowledge-MCP-ops` for local implementation context. The ops repo remains canonical. See `docs/specs/README.md` for the snapshot policy and refresh rules.

# Project Knowledge MCP MVP Implementation Spec

## 0. Executive Summary

Project Knowledge MCP is a local-first Model Context Protocol server that gives AI assistants, coding agents, and human collaborators current, repo-grounded project context.

The thesis is:

> The assistant is not the memory. The repo is the memory. The MCP server is the access layer.

The MVP must run anywhere Docker can run: a laptop, desktop, workstation, home server, cloud dev box, or Hermes instance. It is not a Hermes-only tool. The security constraint is local-only exposure by default, not local-only deployment on one Hermes machine: the container exposes MCP/HTTP only to the host loopback unless the user deliberately configures a bridge. It must be useful without a local model, without GPU compute, and without any LLM, embedding, or cloud parser API key. The server is deterministic infrastructure: it uses repo-native authority metadata, a local parser registry, SQLite FTS5/BM25 candidate retrieval, typed evidence compilers, and a CodeGraph-style code provider to retrieve evidence, label authority/freshness, compile structured evidence packets, and safely capture low-authority project notes. The connected LLM/assistant performs prose synthesis from the packet.

The MVP should make the following workflow possible:

1. User or agent runs a bootstrap script or local setup UI.
2. Setup helps register/clone/select an ops repo and one or more work/code repos.
3. Setup starts Project Knowledge MCP in Docker with those repos mounted.
4. Setup emits ready-to-use connection instructions/config for Hermes, Claude Desktop, Cursor, Codex/local CLIs, or any MCP-capable client on that host.
5. Browser/mobile assistants such as ChatGPT, Gemini, or iPad workflows are not excluded by architecture; they connect through whatever MCP/connector/bridge the user authorizes. The MVP must not hardcode Hermes as the only client.
6. User asks for a project brief or task context.
7. Client calls `generate_session_brief(task)`.
8. MCP returns a source-cited, authority-ranked, freshness-aware evidence packet.
9. Connected assistant synthesizes the final answer from that packet.
10. User can casually save low-authority notes with `add_project_note(...)`.
11. Canonical doctrine/decision changes are blocked from direct mutation and must be proposed/reviewed via PR or an explicit external governance path.

This spec is intentionally implementation-ready. A competent developer should be able to build the MVP without additional project context.

### 0.1 Step 0 Review Outcome

Step 0 produced the first drift incident this project is meant to prevent: an older LlamaIndex-centered discussion was accidentally treated as current implementation guidance after the later SQLite/local-parser decision superseded it.

The spec therefore treats drift prevention as a first implementation concern, not a later polish item:

- default document retrieval is SQLite FTS5/BM25 plus local parsers, not LlamaIndex/LlamaParse/LlamaCloud;
- FTS5/BM25 is a candidate generator, not the truth layer;
- typed evidence compilers and authority/supersession metadata decide what is current;
- CodeGraphContext remains the first code-context candidate, but Step 0 marked it `PARTIAL` until an adapter hides human/Rich CLI output behind stable result models;
- the code repo should carry a pinned snapshot of this spec with provenance back to the ops repo, so implementation sessions do not depend on chat memory or live cross-repo lookup alone.

The first Step 1 regression must prove that a superseded LlamaIndex artifact cannot outrank the current SQLite/local-parser decision.

---

## 1. Goals and Non-Goals

### 1.1 Goals

The MVP must provide:

1. Docker deployment on any host that can run Docker, not only a Hermes instance.
2. Local-only exposure by default: stdio and/or localhost-bound HTTP/StreamableHTTP, never public network binding unless explicitly configured.
3. One-command bootstrap script and minimal local setup UI or TUI so normal users and agents do not manually assemble Docker, repo mounts, and MCP config.
4. Generated client connection artifacts for Hermes plus generic MCP-capable clients such as Claude Desktop, Cursor, Codex/local CLIs, and future ChatGPT/Gemini connector paths.
5. MCP server exposing project context tools.
6. Project config for one ops repo and one or more work repos.
7. Git freshness/staleness detection for every configured repo.
8. A required pre-implementation evaluation gate for local parser/SQLite retrieval quality and CodeGraph-style code context before the main build starts.
9. Deterministic indexing of ops docs and selected work repo files using a local parser registry and SQLite-backed storage.
10. Local retrieval over SQLite FTS5/BM25 candidate indexes, metadata filters, path scoping, typed evidence compilers, and authority-aware post-ranking.
11. Authority-aware ranking so canonical/current docs outrank raw captures and stale discussions.
12. CodeGraph-style code context as a first-class architecture component when code repos are configured and the reliability spike passes. It is included in the architecture whether or not a specific project uses code; direct file/text retrieval is fallback, not the preferred path.
13. Session brief/evidence packet compilation without any LLM dependency.
14. Safe low-authority note capture via `add_project_note(...)`.
15. Direct mutation prevention for canonical/high-authority files.
16. Structured JSON responses plus human-readable Markdown packet fields.
17. Tests and fixtures proving indexing, retrieval, ranking, staleness, write policy, setup flow, and MCP tool behavior.

### 1.2 Non-Goals

The MVP must not:

1. Require local LLM inference.
2. Require local embeddings.
3. Require cloud LLM or embedding keys.
4. Become an autonomous analyst.
5. Decide project truth using hidden intelligence.
6. Mutate accepted doctrine or ADRs directly.
7. Replace Git, GitHub, Linear, Jira, or documentation systems.
8. Expose project data remotely by default.
9. Run a mandatory file watcher/daemon loop.
10. Implement scheduled reconciliation as a core feature.
11. Build a custom MCP protocol stack, Git parser, frontmatter parser, glob engine, setup orchestration framework, or code graph from scratch when mature open-source libraries exist.
12. Promise semantic search quality in the no-key MVP.
13. Use LlamaIndex, LlamaParse, LlamaCloud, hosted parsers, answer synthesis engines, agents, vector stores, or default LLM-backed query engines in the MVP default path.

### 1.3 Optional Later Features, Explicitly Out of MVP

- Semantic embeddings.
- LLM-based reranking.
- LLM-assisted metadata classification.
- LLM-authored final project briefs inside the MCP server.
- File watching.
- Scheduled reconciliation jobs.
- Automatic doctrine drift adjudication.
- Automatic PR creation.
- Hosted multi-tenant service.

---

## 2. Product Boundary: Evidence Packet vs Synthesis

The most important architectural boundary is:

```text
Project Knowledge MCP server = deterministic evidence packet compiler
Connected assistant/LLM      = reasoning and prose synthesis layer
Human/governance process     = authority layer for canonical truth changes
```

### 2.1 What the MCP Server Does

The server may:

- read local repos;
- inspect Git state;
- index documents and selected files through local parsers into SQLite metadata/chunk/FTS tables;
- parse frontmatter and metadata;
- classify documents using deterministic metadata/path rules;
- retrieve from local no-key indexes using BM25/keyword, metadata filters, path scopes, and related deterministic strategies;
- call the CodeGraph-style provider for symbol/file context when code repos are configured and the provider is healthy;
- rank results by authority, freshness, and relevance;
- compile structured context packets;
- write low-authority capture notes;
- create draft/proposal artifacts in safe folders;
- block direct high-authority mutations.

### 2.2 What the MCP Server Does Not Do in MVP

The server must not:

- call an LLM, embedding provider, or cloud parser by default;
- require a model API key;
- generate uncited final claims as if they are project truth;
- silently promote notes into doctrine;
- mark questions resolved without explicit review;
- infer accepted decisions from raw discussions;
- directly edit canonical doctrine/ADR files through casual tool calls.

### 2.3 Evidence Packet Definition

An evidence packet is a deterministic, structured object containing:

- task/query;
- project identity;
- generated timestamp;
- repo staleness summaries;
- warnings;
- matched canonical doctrine excerpts;
- matched accepted decisions;
- matched open questions;
- matched raw captures/discussions;
- code/file/symbol evidence;
- possible gaps;
- source paths and line ranges where available;
- authority/freshness metadata;
- repository source mode metadata, including whether indexed evidence reflects the active workspace or a pinned snapshot;
- Markdown rendering for humans/LLMs.

The connected assistant turns the packet into a human answer.

---

## 3. User Stories

User stories are required for this project because the value depends on workflow behavior, not only API endpoints.

### 3.1 Founder / Project Owner

#### Story 1: Fresh Project Brief Before Brainstorming

As a project owner, I want an AI assistant to request fresh project context before brainstorming, so that the discussion reflects current doctrine, decisions, and implementation rather than stale chat memory.

Acceptance criteria:

- Given a configured project with indexed ops/work repos;
- when the assistant calls `generate_session_brief({ task })`;
- then the server returns current doctrine, accepted decisions, relevant code evidence, open questions, and staleness warnings;
- and every non-warning claim in the packet has source references.

#### Story 2: Ask “What Is True Now?”

As a project owner, I want to ask what is currently true about a topic, so that old conversations do not outrank accepted project truth.

Acceptance criteria:

- `get_current_doctrine({ topic })` returns canonical/current doctrine first.
- Accepted decisions are included when relevant.
- Superseded/rejected documents are excluded by default.
- Results include authority labels and source paths.

#### Story 3: Capture Useful Thoughts Without Bureaucracy

As a project owner, I want to casually save useful context into the repo, so that thoughts from AI conversations are not lost.

Acceptance criteria:

- `add_project_note(...)` writes to a low-authority capture path by default.
- The saved file includes frontmatter with `authority: capture` and `status: captured`.
- The note becomes searchable after the write.
- The tool does not ask for a folder unless the requested target would change authority.

#### Story 4: Avoid Accidental Canon Mutation

As a project owner, I want canonical doctrine and accepted decisions protected from casual writes, so that project truth changes remain deliberate.

Acceptance criteria:

- Direct write tools reject targets under canonical/high-authority paths.
- Rejection explains that a proposal/draft or PR is required.
- A draft/proposal path is suggested when possible.

#### Story 5: Reorient After Time Away

As a project owner, I want a reorientation packet after time away, so that I can understand recent project changes without manually reading every commit and discussion.

Acceptance criteria:

- `generate_session_brief({ task: "reorient me", since })` includes recent indexed docs/code changes when `since` is provided.
- Packet reports local/remote Git freshness.
- Packet separates accepted changes from raw captures.

### 3.2 Developer

#### Story 6: Retrieve Implementation-Relevant Context

As a developer, I want task context that includes both doctrine and code evidence, so that I can implement without violating project intent.

Acceptance criteria:

- `retrieve_ops_code_evidence({ topic })` returns ops and code evidence grouped separately.
- Code results include file paths and line ranges/snippets where available.
- Doctrine and ADR results include authority and freshness.
- Packet includes gaps when code evidence is missing.

#### Story 7: Understand Why Code Is Shaped a Certain Way

As a developer, I want to search accepted decisions from my coding environment, so that I understand design rationale.

Acceptance criteria:

- `search_decisions({ query })` searches accepted and draft decision docs.
- Accepted/current decisions rank above drafts and discussions.
- Superseded decisions show `superseded_by` when metadata exists.

#### Story 8: Know Whether My Local Repos Are Stale

As a developer, I want explicit repo freshness status before relying on context, so that I do not implement against stale docs or code.

Acceptance criteria:

- `check_project_staleness()` reports source mode, host path when known, container path, branch, local HEAD, remote tracking branch, ahead/behind, dirty state, untracked files count, last indexed commit, whether uncommitted changes are included, and whether reindex is needed.
- If remote cannot be checked, response includes a warning rather than failing the whole tool.

#### Story 9: Search Code With CodeGraph-Style Context and Safe Fallback

As a developer, I want code search to use CodeGraph-style symbol/file context as the intended path, so that project briefs can cite real implementation structure instead of only text snippets. I also want the server to remain functional if the provider is unhealthy or a project has no code repo.

Acceptance criteria:

- When a work/code repo is configured and the CodeGraph reliability spike passes, `search_code({ query })` uses the CodeGraph-style provider as the preferred source of code context.
- If no work/code repo is configured, code tools report `not_applicable` rather than an error.
- If the CodeGraph provider is unhealthy/unavailable, the server falls back to indexed text/direct file reads and reports `code_provider: fallback` with warnings.
- The fallback path is for resilience and portability, not a reason to omit the CodeGraph-style architecture.

### 3.3 AI Coding Agent / Assistant

#### Story 10: Request Context Before Acting

As an AI coding agent, I want a task-specific context packet before acting, so that my output is grounded in current project evidence.

Acceptance criteria:

- `generate_session_brief(...)` returns compact, bounded context by default.
- Response includes a `markdown` field optimized for direct assistant consumption.
- Response includes structured sections for programmatic use.

#### Story 11: Avoid Superseded Ideas

As an AI agent, I want results to identify rejected/superseded ideas, so that I do not revive stale assumptions.

Acceptance criteria:

- Documents with `status: superseded` or `status: rejected` are labeled.
- Superseded/rejected material does not appear unless directly relevant or `include_superseded: true` is passed.
- Session brief may include an “Ideas to avoid” section when superseded material strongly matches the task.

#### Story 12: Cite Sources for Architectural Claims

As an AI agent, I want evidence results with source paths and excerpts, so that any architectural answer can be grounded.

Acceptance criteria:

- Every search result has `repo_id`, `path`, `title`, `doc_type`, and `authority` where applicable.
- Text results include snippet/excerpt.
- Code results include file path and line range when available.

### 3.4 Human Collaborator

#### Story 13: Onboard Into a Project

As a human collaborator, I want a project overview packet, so that I can understand current state without reading every file.

Acceptance criteria:

- `generate_session_brief({ task: "project overview" })` includes project summary, canonical docs, key decisions, open questions, configured repos, and warnings.
- Packet excludes low-authority raw discussion unless relevant.

#### Story 14: Find Open Questions

As a collaborator, I want to search open questions, so that I know where discussion is still welcome.

Acceptance criteria:

- `search_open_questions({ query })` searches known open-question paths and docs with `type: open_question`.
- Results include status, owner, related docs, and source path when available.

### 3.5 Ghost Mesh Integration User Stories

These are not special-case implementation requirements, but they validate the architecture.

#### Story 15: Task-Scoped Project Context as a Source Node

As a Ghost Mesh workflow designer, I want project context retrieval to be explicit and task-scoped, so that institutional knowledge is a bounded input rather than ambient memory.

Acceptance criteria:

- A worker can call MCP tools with a task/topic.
- Returned packet includes freshness and source references.
- Worker can record which packet/context was used.

#### Story 16: Doctrine/Implementation Drift Evidence

As a Ghost Mesh operator, I want ops/code evidence for a topic, so that possible doctrine/implementation drift can be reviewed.

Acceptance criteria:

- `retrieve_ops_code_evidence({ topic })` returns doctrine, decisions, code/schema/test evidence, and gaps.
- The server does not claim final alignment/misalignment unless a deterministic missing/stale condition is observed.
- Connected LLM or governance process performs adjudication.

---

## 4. Deployment Model

### 4.1 Runtime Assumptions

The MVP runs as a Docker container on any user-controlled host that can run Docker. It is local-first and locally exposed by default, but it is not tied to the Hermes instance.

Assumptions:

- Any Docker-capable host is a valid target: laptop, desktop, workstation, home server, cloud dev box, or Hermes instance.
- Linux is the first-class implementation target; macOS/Windows Docker Desktop should be supported where practical through the setup script.
- Repos are cloned/selected on the host by the user, setup UI, setup script, or agent.
- Container receives repos as bind mounts.
- Container persists SQLite/index state in the project-local `.project-knowledge/` directory unless explicitly configured otherwise, so host and container paths remain equivalent.
- No GPU is available.
- No local model server is available.
- No LLM/embedding API key is required.
- MCP stdio must be supported for local clients.
- A localhost-bound HTTP/StreamableHTTP mode should also be provided for tools that cannot launch stdio Docker commands directly.
- The server must bind to loopback by default. Public/LAN exposure is out of default MVP behavior and requires explicit user configuration.

### 4.1.1 Repo Source Modes

PKMCP must distinguish the repository being edited from a repository snapshot used for deterministic analysis.

**Workspace mode is the default for local development.** In workspace mode, the canonical repo lives on the host and Docker receives it through a bind mount. Humans and agents edit the host working tree; PKMCP indexes the mounted path inside the container. This ensures uncommitted edits, dirty state, and untracked files are visible to indexing and freshness checks.

**Snapshot mode is opt-in.** In snapshot mode, setup or the container may clone a repo/ref/PR into container-managed storage for CI, demos, PR review, or deterministic remote analysis. Snapshot mode must disclose the pinned ref/commit and must not imply that uncommitted host changes are included.

Required invariant: every repo status, index record, evidence packet, and staleness response must make the source explicit enough to answer: "Did this evidence come from the active workspace I am editing, or from a pinned snapshot?"

At minimum, repo metadata should include:

- `source_mode`: `workspace` or `snapshot`;
- `host_path` when known/applicable;
- `container_path` / configured repo path;
- Git branch/ref and HEAD commit;
- dirty state and untracked file count;
- last indexed commit/time;
- whether the indexed content includes uncommitted changes.

The setup flow may offer clone-from-URL convenience, but for local developer workflows the result should still be a host clone bind-mounted into the container unless the user explicitly selects snapshot mode.

### 4.2 Example Docker Compose

```yaml
services:
  project-knowledge-mcp:
    build: .
    image: project-knowledge-mcp:dev
    command: ["project-knowledge", "start", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
    environment:
      PROJECT_KNOWLEDGE_CONFIG: /workspace/project.yaml
      PROJECT_KNOWLEDGE_ROOT: /workspace
      PROJECT_KNOWLEDGE_STATE_DIR: /workspace/.project-knowledge
      PROJECT_KNOWLEDGE_LOG_LEVEL: info
    volumes:
      # Mount the ops/project root read-write because low-authority capture writes target it.
      - /root/work/Project-Knowledge-MCP-ops:/workspace:rw
      # Work repos default to read-only.
      - /root/work/ghostmesh:/repos/ghostmesh:ro
    ports:
      # Host-loopback only. The container may bind 0.0.0.0 internally, but Docker must expose it only on localhost by default.
      - "127.0.0.1:8000:8000"
```

### 4.3 Setup Script and Local UI Requirements

This is not only a developer tool. Normal users and agents need a guided path. The MVP must include:

1. `project-knowledge setup` CLI command or equivalent bootstrap script.
2. A minimal local setup UI/TUI served on `localhost` or rendered in terminal.
3. Repo registration flow: choose existing local repos or clone from provided Git URLs.
4. Docker volume/mount generation.
5. Config generation and validation.
6. One-command start/stop/status.
7. Generated client snippets for Hermes, Claude Desktop-style MCP config, Cursor/local coding CLIs where applicable, and a generic stdio/HTTP MCP connection description.
8. Clear explanation that browser/mobile assistants need an authorized connector/bridge, but the Project Knowledge MCP server itself is not Hermes-specific.

Example commands:

```bash
project-knowledge setup
project-knowledge start
project-knowledge status
project-knowledge print-client-config --client hermes
project-knowledge print-client-config --client generic-mcp
```

### 4.4 Hermes MCP Client Configuration Example

```yaml
mcp_servers:
  project_knowledge:
    command: "docker"
    args:
      - "run"
      - "--rm"
      - "-i"
      - "-v"
      - "$PWD:/workspace:rw"
      - "-v"
      - "/root/work/ghostmesh:/repos/ghostmesh:ro"
      - "-e"
      - "PROJECT_KNOWLEDGE_CONFIG=/workspace/project.yaml"
      - "project-knowledge-mcp:latest"
      - "project-knowledge"
      - "start"
      - "--transport"
      - "stdio"
    timeout: 120
    connect_timeout: 60
```

The exact image name can change, but the server must support stdio MCP from inside Docker and a localhost-bound HTTP/StreamableHTTP mode for clients that cannot spawn Docker directly.

Canonical runtime entrypoints:

```bash
project-knowledge start --transport stdio
project-knowledge start --transport http --host 127.0.0.1 --port 8000
```

When running HTTP mode inside Docker, the container may bind `0.0.0.0` internally only if Docker publishes the port to host loopback (`127.0.0.1`) by default.

---

## 5. Project Configuration

### 5.1 Config File Location

The server reads config from:

1. `PROJECT_KNOWLEDGE_CONFIG` environment variable, if set;
2. otherwise `/workspace/project.yaml` inside the canonical Docker container;
3. otherwise `./project.yaml` when running locally outside Docker.

State defaults to `<project-root>/.project-knowledge/`. Docker and host CLI execution should use project-relative state paths so indexing commands do not behave differently inside and outside the container.

### 5.2 Config Schema

```yaml
schema_version: 1

project:
  id: project-knowledge-mcp
  name: Project Knowledge MCP
  description: Local-first MCP server for repo-grounded project context.
  timezone: UTC

storage:
  project_root: /workspace
  state_dir: /workspace/.project-knowledge
  sqlite_path: /workspace/.project-knowledge/project_knowledge.sqlite3
  cache_dir: /workspace/.project-knowledge/cache
  lock_dir: /workspace/.project-knowledge/locks

repos:
  - id: ops
    role: ops
    name: Project Knowledge MCP Ops
    source_mode: workspace
    host_path: /root/work/Project-Knowledge-MCP-ops
    path: /workspace
    writable: true
    canonical_priority: high
    include_globs:
      - "README.md"
      - "docs/**/*.md"
      - "*.md"
    exclude_globs:
      - ".git/**"
      - "node_modules/**"
      - ".venv/**"

  - id: ghostmesh
    role: work
    name: Ghost Mesh Runtime
    source_mode: workspace
    host_path: /root/work/ghostmesh
    path: /repos/ghostmesh
    writable: false
    include_globs:
      - "README.md"
      - "docs/**/*.md"
      - "src/**/*.py"
      - "tests/**/*.py"
      - "schemas/**/*"
      - "*.toml"
      - "*.yaml"
      - "*.yml"
      - "*.json"
    exclude_globs:
      - ".git/**"
      - "node_modules/**"
      - ".venv/**"
      - "dist/**"
      - "build/**"

indexing:
  provider: local_parser_registry
  max_file_bytes: 1000000
  chunk_target_chars: 1800
  chunk_overlap_chars: 200
  file_watch: false
  auto_reindex_after_note_write: true
  initial_parsers:
    - markdown
    - text
  future_parser_adapters:
    - pdf
    - docx
    - pptx
    - xlsx
    - html

retrieval:
  provider: sqlite_fts5
  mode: local_no_llm
  llm_enabled: false
  embeddings_enabled: false
  cloud_parsers_enabled: false
  strategies:
    - typed_evidence_compiler
    - metadata_filter
    - sqlite_fts5
    - bm25
    - path_scope
    - authority_rank
    - linked_evidence_expansion
  default_limit: 10
  brief_max_results_per_section: 8
  include_superseded_by_default: false

code_context:
  provider: codegraph
  fallback_provider: text
  required_for_code_repos: true
  fallback_on_unhealthy: true
  codegraph:
    enabled: true
    # Adapter-specific values. The MVP may run with defaults, but the architecture includes this provider.
    command: null
    url: null
    index_dir: /workspace/.project-knowledge/codegraph
    vector_resolve_enabled: false
    embedding_model: null

write_policy:
  default_capture_repo: ops
  default_capture_dir: docs/notes
  allow_direct_capture: true
  blocked_direct_write_globs:
    - "docs/doctrine/**"
    - "doctrine/**"
    - "docs/decisions/accepted/**"
    - "decisions/accepted/**"
    - "docs/PRD.md"
    - "project-brief.md"
  proposal_dirs:
    doctrine_delta: docs/proposals/doctrine-deltas
    adr_draft: docs/proposals/adr-drafts
    review_packet: docs/reviews
```

### 5.3 Validation Rules

On startup or `validate_config()`:

- `schema_version` must be `1`.
- `project.id` must be non-empty and URL/path safe.
- Every repo path must exist.
- Every repo path must be a Git worktree unless `allow_non_git: true` is explicitly added in future.
- Every repo must declare `source_mode: workspace` or `source_mode: snapshot`; omitted values may be normalized to `workspace` only for backward-compatible MVP configs, with a warning.
- Workspace-mode repos should include `host_path` when running under Docker so status output can explain which host working tree is mounted.
- Snapshot-mode repos must include enough provenance to identify the cloned source/ref/commit and must report `includes_uncommitted_changes: false` unless the snapshot was explicitly built from those changes.
- Exactly one repo should have `role: ops` for MVP.
- At least one repo must be configured.
- If any repo has `writable: true`, the container filesystem must actually allow writes.
- `default_capture_repo` must refer to a writable ops repo.
- Include/exclude globs must be relative to repo root.
- `storage.state_dir` must resolve under the configured project/ops root by default. External state directories require explicit configuration.
- `retrieval.provider` must be `sqlite_fts5` for the MVP default path. Optional adapters must not be required for default operation.

---

## 6. Repository and Document Model

### 6.1 Repo Roles

| Role | Meaning | Write policy |
|---|---|---|
| `ops` | Project knowledge, doctrine, decisions, notes, handovers, open questions | Low-authority capture writes allowed if repo writable |
| `work` | Implementation truth: code, tests, schemas, runtime docs | Read-only in MVP |
| `artifact` | Product/site/generated artifacts | Read-only in MVP |

### 6.2 Authority Levels

The server must distinguish relevance from authority.

Authority levels, highest to lowest:

1. `implementation_truth` — code, schemas, tests, runtime config in work repos.
2. `canonical` — current doctrine, canonical terminology, PRD/project brief.
3. `accepted_decision` — accepted ADRs/decisions.
4. `working` — open questions, drafts, handovers, plans.
5. `capture` — raw notes, discussions, imported chat summaries, evidence packets.
6. `historical` — archived material.
7. `superseded` — explicitly superseded material.
8. `rejected` — explicitly rejected models/ideas.

### 6.3 Deterministic Authority Inference

Authority is inferred in this order:

1. Frontmatter `authority`, if present and valid.
2. Frontmatter `status`, if it maps to authority (`accepted`, `current`, `superseded`, `rejected`, `captured`, `draft`).
3. Path rules.
4. Repo role fallback.

Path rule examples:

| Path pattern | Default type | Default authority |
|---|---|---|
| `docs/doctrine/**`, `doctrine/**` | doctrine | canonical |
| `docs/decisions/accepted/**`, `decisions/accepted/**` | decision | accepted_decision |
| `docs/PRD.md`, `project-brief.md` | project_brief | canonical |
| `docs/open-questions/**`, `open-questions/**` | open_question | working |
| `docs/proposals/**`, `proposals/**` | proposal | working |
| `docs/handovers/**`, `handovers/**` | handover | working |
| `docs/discussions/**`, `discussions/**` | discussion | capture |
| `docs/notes/**`, `notes/**`, `inbox/**` | note | capture |
| `docs/rejected/**`, `rejected-models/**` | rejected_model | rejected |
| work repo `src/**` | code | implementation_truth |
| work repo `tests/**` | test | implementation_truth |
| work repo `schemas/**` | schema | implementation_truth |

### 6.4 Frontmatter Fields

Markdown docs may include YAML frontmatter. Supported fields:

```yaml
title: Human-readable title
type: doctrine | decision | discussion | note | open_question | handover | proposal | evidence | code_doc | project_brief
status: current | accepted | draft | captured | open | closed | superseded | rejected
authority: canonical | accepted_decision | working | capture | historical | superseded | rejected
created: 2026-06-09
updated: 2026-06-09
date: 2026-06-09
tags:
  - project-knowledge-mcp
owner: optional-human-or-team
supersedes:
  - path/or/id
superseded_by: path/or/id
related:
  - path/or/id
source: optional external source descriptor
needs_review: false
```

Missing frontmatter must not break indexing.

### 6.5 Frontmatter Normalization and Warnings

The indexing pipeline must validate and normalize frontmatter through Pydantic models before authority ranking uses it. Unknown values must not crash indexing and must not silently enter the authority model.

Required behavior:

1. Preserve raw frontmatter in `documents.frontmatter_json`.
2. Normalize known `type`, `status`, and `authority` values into enums.
3. If a value is unknown, fall back deterministically to path rules and repo-role defaults.
4. Record an `index_events` warning with the raw value, normalized fallback, repo, and path.
5. Surface normalization warnings in relevant search/brief responses when the affected document is included.

Example warning record:

```json
{
  "event_type": "frontmatter_normalization_warning",
  "repo_id": "ops",
  "path": "docs/decisions/example.md",
  "status": "warning",
  "message": "Unknown status 'depreciated'; normalized using path rules as status='draft', authority='working'."
}
```

---

## 7. Indexing and Retrieval Substrate

### 7.0 Pre-Implementation Evaluation Gate

Before the main implementation starts, run a short evaluation of the deterministic retrieval path and the CodeGraph-style code context path:

1. **Local parser + SQLite FTS5/BM25 retrieval quality spike** for project docs, notes, decisions, and non-code files.
2. **CodeGraph-style code context spike** for symbol/file/code relationship evidence.
3. **Transport/framework sanity check** proving FastMCP can expose the same tool functions through stdio and localhost-bound HTTP/StreamableHTTP without forcing cloud/model dependencies.
4. **Decision checkpoint** that records whether each candidate is accepted for MVP, accepted behind an adapter with limitations, or rejected in favor of a named alternate.

This gate prevents two bad outcomes:

- building a naive Control-F filing machine and assuming it will retrieve project truth;
- assuming a RAG/library framework solves the product problem without testing local/no-key behavior, Docker behavior, persistence, and response shape.

The decision record must be committed before feature implementation begins. It should include commands run, fixture repos used, observed outputs, failure modes, retrieval-quality fixtures, and the final decision.

### 7.1 Parser Registry and Indexed File Types

The MVP uses a local parser registry. Parsing is file-format handling, not a RAG-platform dependency.

Initial required parsers:

- `.md`, `.mdx` -> `MarkdownParser` using `python-frontmatter` plus heading-aware text extraction.
- `.txt` -> `PlainTextParser` using direct local text reads.

Optional/later parser adapters, added as needed without changing the index/retrieval contract:

- `.pdf` -> local PDF parser such as PyMuPDF; scanned/OCR PDFs may be marked `requires_ocr` rather than parsed in MVP.
- `.docx` -> `python-docx` or equivalent local parser.
- `.pptx` -> `python-pptx` or equivalent local parser.
- `.xlsx`, `.csv` -> `openpyxl`/stdlib CSV adapters.
- `.html` -> BeautifulSoup/trafilatura-style local parser.
- universal parser adapters such as Tika/Pandoc/Docling only if a later spike proves they are worth their dependency surface.

Common source files from work repos may be indexed as text fallback at minimum: `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.go`, `.rs`, `.java`, `.cs`, `.rb`, `.php`, `.sh`, `.sql`. For code repos, CodeGraph-style evidence is the preferred path once its spike passes.

Binary files are skipped. Files larger than `indexing.max_file_bytes` are skipped with a warning record.

### 7.2 Parsed Document Contract

Every parser must return a provider-neutral object such as:

```python
class ParsedDocument(BaseModel):
    repo_id: str
    source_path: str
    source_type: str
    title: str | None
    raw_frontmatter: dict[str, Any]
    normalized_metadata: NormalizedMetadata
    chunks: list[ParsedChunk]
    parse_warnings: list[IndexWarning]
```

```python
class ParsedChunk(BaseModel):
    chunk_id: str
    text: str
    heading_path: list[str] = []
    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    ordinal: int
```

The parser layer must preserve enough metadata for source-cited evidence packets: repo ID, path, title, heading path, line range when available, raw frontmatter, normalized authority fields, parse warnings, and content hash.

### 7.3 SQLite FTS5/BM25 Retrieval Provider

The default document retrieval provider is SQLite FTS5 with BM25 candidate ranking.

SQLite FTS5 is not the authority model and not the whole product. It is the local, inspectable candidate generator. Project Knowledge MCP supplies:

- typed evidence compilers;
- metadata/path/status filters;
- frontmatter normalization;
- authority and supersession rules;
- linked-evidence expansion;
- code provider augmentation;
- structured packet assembly.

Required provider behavior:

1. Persist canonical document/chunk metadata in SQLite tables.
2. Persist searchable chunk text in an FTS5 virtual table.
3. Use SQLite `bm25()` or equivalent FTS5 scoring as the initial lexical relevance signal.
4. Support filters by repo, path, type, status, authority, tags, date, and supersession state.
5. Return stable chunk IDs and source metadata for every hit.
6. Keep response shapes provider-neutral so optional future retrieval providers can be swapped behind the same contract.
7. Never require network access, hosted parsers, embeddings, LLMs, or API keys in the default path.

### 7.4 Chunking Rules

Markdown/text chunking should use simple, testable local logic and mature parser libraries rather than a hidden RAG framework.

Required behavior:

1. Prefer heading-aware chunks for Markdown.
2. Keep normalized frontmatter metadata on every chunk from the document.
3. Target `chunk_target_chars` characters, with `chunk_overlap_chars` overlap for long sections.
4. Preserve source path and best-effort source line ranges when possible.
5. Preserve enough heading/path context that evidence packets can cite why a chunk was included.

Code chunking rules:

1. If CodeGraph provider supplies symbol ranges, index or reference symbol chunks.
2. Otherwise use the text fallback to chunk by file sections with line ranges.
3. Include function/class names from simple regex extraction only as fallback metadata; do not build a custom code parser as MVP core.

### 7.5 Local Storage and Index Registry

Use local disk only. Default state lives under `<project-root>/.project-knowledge/` so Docker and host CLI execution use equivalent project-relative paths.

Suggested metadata/FTS schema:

```sql
CREATE TABLE repos (
  id TEXT PRIMARY KEY,
  role TEXT NOT NULL,
  name TEXT NOT NULL,
  source_mode TEXT NOT NULL DEFAULT 'workspace',
  host_path TEXT,
  path TEXT NOT NULL,
  writable INTEGER NOT NULL DEFAULT 0,
  current_branch TEXT,
  head_commit TEXT,
  remote_name TEXT,
  remote_branch TEXT,
  remote_head_commit TEXT,
  ahead_count INTEGER,
  behind_count INTEGER,
  dirty INTEGER,
  untracked_count INTEGER,
  includes_uncommitted_changes INTEGER NOT NULL DEFAULT 0,
  snapshot_ref TEXT,
  snapshot_commit TEXT,
  last_status_checked_at TEXT,
  last_indexed_at TEXT,
  last_indexed_commit TEXT
);

CREATE TABLE documents (
  id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  path TEXT NOT NULL,
  parser TEXT NOT NULL,
  title TEXT,
  doc_type TEXT,
  status TEXT,
  authority TEXT,
  tags_json TEXT,
  frontmatter_json TEXT,
  git_commit TEXT,
  mtime REAL,
  size_bytes INTEGER,
  content_hash TEXT,
  skipped INTEGER NOT NULL DEFAULT 0,
  skip_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(repo_id, path)
);

CREATE TABLE chunks (
  rowid INTEGER PRIMARY KEY,
  id TEXT UNIQUE NOT NULL,
  document_id TEXT NOT NULL,
  repo_id TEXT NOT NULL,
  path TEXT NOT NULL,
  heading_path_json TEXT,
  chunk_index INTEGER NOT NULL,
  start_line INTEGER,
  end_line INTEGER,
  page INTEGER,
  text TEXT NOT NULL,
  authority TEXT,
  doc_type TEXT,
  status TEXT,
  content_hash TEXT
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
  text,
  content='chunks',
  content_rowid='rowid'
);

CREATE TABLE retrieval_events (
  id TEXT PRIMARY KEY,
  query TEXT NOT NULL,
  provider TEXT NOT NULL,
  strategy TEXT,
  result_count INTEGER NOT NULL,
  warnings_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE index_events (
  id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  repo_id TEXT,
  path TEXT,
  status TEXT NOT NULL,
  message TEXT,
  created_at TEXT NOT NULL
);
```

The implementation may add migration tables and adapter-specific tables, but these concepts must exist.

### 7.6 Retrieval Quality Evaluation Requirements

The retrieval spike must answer whether the default retrieval path is good enough for real project-memory questions. It must not merely prove that FTS returns rows.

Required evaluation fixture shape:

```yaml
- question: "Who compiles a project brief?"
  expected_evidence:
    - docs/decisions/accepted/0001-context-compiler.md
  must_include_concepts:
    - MCP returns evidence packet
    - assistant synthesizes prose

- question: "What is the current default document retrieval architecture?"
  expected_answer_class: "sqlite_fts5_bm25_with_local_parsers"
  expected_current_evidence:
    - docs/discussions/2026-06-09-local-parser-sqlite-codegraph-decision.md
    - project-knowledge-mcp:docs/decisions/0001-step-0-provider-evaluation.md
  must_label_as_superseded_or_background:
    - docs/discussions/2026-06-09-llamaindex-codegraph-retrieval-decision.md
  must_include_concepts:
    - SQLite FTS5/BM25 is the candidate generator
    - local Markdown/text parsers are default
    - LlamaIndex/LlamaParse/LlamaCloud are not default MVP dependencies
    - typed evidence compilers and authority ranking determine current truth

- question: "Is a cloud parser allowed in the no-key MVP?"
  expected_answer_class: "no"
  must_exclude:
    - superseded LlamaIndex/LlamaParse-as-core draft

- question: "Where is this behavior implemented?"
  expected_evidence_type:
    - code_graph_or_text_fallback
    - source_file
```

The spike must record:

1. Which local parser libraries are used for Markdown/text.
2. SQLite schema and FTS5 setup commands.
3. Fixture indexing command.
4. Fixture query command and raw results.
5. Whether expected evidence appears in top 5/top 10.
6. Whether stale/superseded documents are excluded or visibly labeled.
7. Whether authority ranking beats lexical score when equally relevant raw captures exist.
8. Failure modes and next named alternate if SQLite FTS5/BM25 plus evidence compilation is insufficient.

If the default path fails the spike, record the failed evidence and test the next named retrieval candidate behind the same provider boundary. Do not silently ship a filing machine.

### 7.7 Index Commands

The implementation must support both CLI and MCP-triggered indexing.

CLI examples:

```bash
project-knowledge validate-config --config /workspace/project.yaml
project-knowledge index-project --config /workspace/project.yaml
project-knowledge check-staleness --config /workspace/project.yaml
```

MCP tools:

- `validate_config()`
- `index_project({ repo_id?, force? })`
- `check_project_staleness()`

### 7.8 No Required File Watching

MVP must not require file watching. Explicit indexing is enough.

Required behavior:

- `index_project` rebuilds or incrementally updates the local parser/SQLite metadata and FTS registry.
- `add_project_note` indexes only the newly written note in-process if `auto_reindex_after_note_write` is true; it must not trigger a full repository rescan.
- The write response must report `index_scope: "single_document"` when immediate note indexing succeeds.
- Search/brief tools include warnings when repo HEAD, file mtimes, or dirty state suggest the index is stale.

---

## 8. Code Context Provider

### 8.1 Design Intent

Most target projects involve code. CodeGraph-style context is part of the MVP architecture, not a nice-to-have. The working assumption is that a reliable open-source CodeGraph-style tool exists and should be used rather than reinvented. If the reliability spike fails, implementation must keep the provider boundary and document the failed tool, but the architecture still treats symbol/file context as a first-class component.

Direct file reads and SQLite text search are fallback paths. They keep the MCP server functional when there is no code repo, when CodeGraph is unhealthy, or while the provider is being repaired. They are not the intended replacement for CodeGraph-style context in coding projects.

### 8.2 Provider Interface

Define an internal interface such as:

```python
class CodeContextProvider(Protocol):
    def health(self) -> CodeProviderHealth: ...
    def index_repo(self, repo: RepoConfig) -> CodeIndexResult: ...
    def search_code(self, query: str, repo_id: str | None, limit: int) -> list[CodeResult]: ...
    def get_code_context(self, symbol_or_file: str, repo_id: str | None, limit: int) -> list[CodeResult]: ...
```

`CodeResult` fields:

```json
{
  "repo_id": "ghostmesh",
  "path": "src/example.py",
  "start_line": 10,
  "end_line": 42,
  "symbol": "ExampleClass.method",
  "kind": "class|function|file|schema|test|unknown",
  "snippet": "...",
  "provider": "codegraph|text",
  "score": 0.84,
  "related": [
    { "path": "tests/test_example.py", "relationship": "test" }
  ]
}
```

### 8.3 CodeGraph Provider Requirements

The CodeGraph adapter must:

- be included in the codebase and architecture if the reliability spike passes;
- be the preferred provider for configured work/code repos;
- run inside Docker or connect to a configured local process/API;
- index configured work repos when requested;
- return symbol/file context where available;
- be bypassed only when no code repo is configured or when health checks fail;
- never be required for ops docs retrieval;
- fail soft to direct file/SQLite fallback with actionable warnings;
- not require an LLM key;
- not require GPU/local model inference;
- run with vector/embedding resolution disabled in the default MVP path;
- never use OpenAI or hosted embedding modes unless a future explicit configuration enables them outside MVP defaults.

Before implementation, run a short CodeGraph reliability spike and record the selected package/tool, install command, Docker behavior, indexing command, query command/API, and failure modes in the repo. If the tool passes, wire it into the MVP. If it fails, document why and keep the adapter interface plus fallback path so a replacement CodeGraph-style provider can be swapped in without redesign. Do not leak provider-specific shapes into MCP tool responses.

### 8.4 Text Fallback Provider

Fallback provider must:

- search indexed code chunks using SQLite FTS5/text fallback and/or direct file reads;
- optionally call `git grep`/ripgrep equivalent internally if available, but tests should not require ripgrep;
- return file snippets and line ranges;
- identify likely tests/schemas by path conventions;
- mark `provider: text` or `provider: sqlite_fts5_text` as appropriate.

### 8.5 Health Semantics

`get_code_provider_status()` should return:

```json
{
  "configured_provider": "codegraph",
  "active_provider": "codegraph|text",
  "codegraph_enabled": true,
  "codegraph_healthy": false,
  "fallback_available": true,
  "warnings": ["CodeGraph health check failed; using direct file/SQLite fallback until provider is repaired"]
}
```

### 8.6 Initial CodeGraph Candidate

Initial candidate to spike before implementation:

- Repository: `CodeGraphContext/CodeGraphContext`
- Package: `codegraphcontext`
- Current observed PyPI version during spec update: `0.4.17`
- Summary: MCP server and CLI that indexes local code into a graph database for AI context.
- Relevant capabilities to verify: CLI mode, MCP mode, Docker behavior, Python 3.12 compatibility, multi-language indexing, repository ignore rules, graph DB backend defaults, query API shape, vector/embedding modes disabled by default, and failure behavior.

The spike must answer:

1. Can it install cleanly in the project Docker image or companion container?
2. Can it index a small fixture repo and a real repo without special credentials?
3. Can it return file/symbol/call relationship context through CLI or API in a stable machine-readable shape?
4. Can it run without an LLM key, GPU, local model server, OpenAI embedding mode, or local embedding/vector resolution?
5. Can it run cleanly on the canonical Python 3.12 Docker runtime?
6. Can it tolerate repos without full language build metadata?
7. What fallback behavior occurs when its graph DB backend is unavailable?
8. Does it support enough languages for the expected project set?

Known pre-spike observation: `codegraphcontext==0.4.17` appears no-key compatible when vector resolution is disabled, but the published package contains Python syntax that fails on Python 3.11. The canonical PKMCP runtime therefore targets Python 3.12+, and the spike must still prove install/index/query behavior inside Docker.

If `CodeGraphContext` fails the spike, record the failed evidence and test the next named candidate. Do not silently downgrade the architecture to plain text search.

---

## 9. Retrieval and Ranking

### 9.1 Retrieval Stages

For `search_ops`, `search_code`, and brief generation:

1. Validate config and index availability.
2. Check repo/index freshness enough to produce warnings.
3. Query the configured no-LLM retrieval provider, normally SQLite FTS5/BM25 retrieval with metadata filters and typed evidence-compiler scopes.
4. Apply or verify filters by repo, type, status, authority, tags, path, and date.
5. Add code provider results for code tools/briefs when relevant.
6. Score and post-rank results using authority/freshness/relevance rules.
7. Group into sections.
8. Render structured JSON and Markdown.

### 9.2 Ranking Formula

A simple deterministic score is enough:

```text
final_score = provider_relevance_score
            + authority_boost
            + freshness_boost
            + type_intent_boost
            + exact_title_or_path_boost
            - superseded_penalty
            - stale_repo_penalty
```

Suggested boosts:

| Condition | Score adjustment |
|---|---:|
| Provider relevance score, e.g. BM25/keyword score normalized by adapter | 0.0 to 1.0 |
| authority `implementation_truth` | +0.35 |
| authority `canonical` | +0.30 |
| authority `accepted_decision` | +0.25 |
| authority `working` | +0.10 |
| authority `capture` | +0.00 |
| authority `historical` | -0.10 |
| authority `superseded` | -0.50 |
| authority `rejected` | -0.60 |
| title/path exact match | +0.20 |
| type matches tool intent | +0.20 |
| repo dirty/stale | warning, optional -0.05 |

The exact numbers can change, but tests must prove canonical/accepted docs outrank equally relevant raw captures.

### 9.3 Superseded/Rejected Handling

Default behavior:

- Exclude superseded/rejected content from normal search unless:
  - query explicitly targets it;
  - `include_superseded: true`;
  - session brief includes “ideas to avoid” because stale material is strongly relevant.

Superseded/rejected content must never be silently mixed with current doctrine.

---

## 10. MCP Tool Surface

All tools must return JSON-serializable objects. Errors should be structured and actionable.

### 10.1 `validate_config()`

Input:

```json
{}
```

Output:

```json
{
  "valid": true,
  "project_id": "project-knowledge-mcp",
  "repos": [
    { "id": "ops", "path": "/repos/project-knowledge-mcp-ops", "role": "ops", "exists": true, "is_git_repo": true }
  ],
  "warnings": []
}
```

### 10.2 `check_project_staleness()`

Input:

```json
{}
```

Output:

```json
{
  "project_id": "project-knowledge-mcp",
  "checked_at": "2026-06-09T00:00:00Z",
  "repos": [
    {
      "repo_id": "ops",
      "role": "ops",
      "path": "/repos/project-knowledge-mcp-ops",
      "branch": "main",
      "head_commit": "abc123",
      "remote": "origin/main",
      "remote_head_commit": "def456",
      "ahead_count": 0,
      "behind_count": 2,
      "dirty": false,
      "untracked_count": 0,
      "last_indexed_commit": "abc123",
      "last_indexed_at": "2026-06-09T00:00:00Z",
      "needs_reindex": false,
      "warnings": ["Local branch is behind remote by 2 commits"]
    }
  ],
  "warnings": []
}
```

Remote lookup failure should produce a warning, not a full failure.

### 10.3 `index_project(repo_id?, force?)`

Input:

```json
{
  "repo_id": null,
  "force": false
}
```

Output:

```json
{
  "status": "ok",
  "started_at": "2026-06-09T00:00:00Z",
  "finished_at": "2026-06-09T00:00:05Z",
  "repos": [
    {
      "repo_id": "ops",
      "documents_indexed": 12,
      "chunks_indexed": 47,
      "documents_skipped": 0,
      "warnings": []
    }
  ]
}
```

### 10.4 `search_ops(query, filters?, limit?)`

Input:

```json
{
  "query": "authority promotion",
  "filters": {
    "doc_type": null,
    "authority": null,
    "tags": ["project-knowledge-mcp"],
    "include_superseded": false
  },
  "limit": 10
}
```

Output:

```json
{
  "query": "authority promotion",
  "results": [
    {
      "repo_id": "ops",
      "path": "docs/amendments/0001-capture-governance-reconciliation.md",
      "title": "Amendment 0001: Capture, Governance, and Reconciliation",
      "doc_type": "decision",
      "status": "draft",
      "authority": "working",
      "start_line": 116,
      "end_line": 124,
      "excerpt": "The recommended workflow is: Capture, Classify, Retrieve, Promote, Canonize...",
      "score": 1.12,
      "warnings": []
    }
  ],
  "warnings": [],
  "markdown": "## Search Results..."
}
```

### 10.5 `search_decisions(query, status?, limit?)`

Searches docs with decision/ADR path/type/status.

### 10.6 `get_current_doctrine(topic?, limit?)`

Returns canonical/current doctrine and accepted decisions relevant to `topic`.

### 10.7 `search_open_questions(query?, limit?)`

Searches open questions and unresolved working docs.

### 10.8 `search_code(query, repo_id?, limit?)`

Returns code/text/provider results.

### 10.9 `get_code_context(symbol_or_file, repo_id?, limit?)`

Returns symbol/file context via CodeGraph if healthy, else text fallback.

### 10.10 `get_code_provider_status()`

Returns provider health as described in section 8.5.

### 10.11 `retrieve_ops_code_evidence(topic, limit?)`

Input:

```json
{
  "topic": "workers are pipe-aware",
  "limit": 8
}
```

Output sections:

- `doctrine_evidence`
- `decision_evidence`
- `open_questions`
- `capture_context`
- `code_evidence`
- `test_evidence`
- `schema_evidence`
- `gaps`
- `warnings`
- `markdown`

This tool retrieves and organizes evidence. It does not adjudicate final truth beyond deterministic warnings such as missing code evidence, stale repo, or superseded docs.

### 10.12 `get_project_brief_evidence(task, since?, limit?)` / `generate_session_brief(...)`

Input:

```json
{
  "task": "Draft a project brief for Project Knowledge MCP MVP",
  "since": null,
  "limit": 8
}
```

Output:

```json
{
  "brief_type": "evidence_packet",
  "synthesis_required": true,
  "project": { "id": "project-knowledge-mcp", "name": "Project Knowledge MCP" },
  "task": "Draft a project brief for Project Knowledge MCP MVP",
  "generated_at": "2026-06-09T00:00:00Z",
  "staleness": { "repos": [], "warnings": [] },
  "sections": [
    { "id": "canonical_context", "title": "Canonical Context", "items": [] },
    { "id": "accepted_decisions", "title": "Accepted Decisions", "items": [] },
    { "id": "implementation_evidence", "title": "Implementation Evidence", "items": [] },
    { "id": "open_questions", "title": "Open Questions", "items": [] },
    { "id": "lower_authority_context", "title": "Lower-Authority Context", "items": [] },
    { "id": "warnings", "title": "Warnings", "items": [] }
  ],
  "warnings": [],
  "markdown": "# Session Context Packet..."
}
```

`generate_session_brief` may remain as a friendly alias, but the canonical semantics are evidence compilation, not prose generation.

### 10.13 `add_project_note(title, body, type?, tags?, source?, target?)`

Writes a low-authority capture artifact.

Input:

```json
{
  "title": "CodeGraph MVP Boundary",
  "body": "CodeGraph-style context is part of the architecture for code repos; direct file/SQLite reads are fallback for health or no-code cases.",
  "type": "note",
  "tags": ["codegraph", "mvp"],
  "source": "discord discussion 2026-06-09",
  "target": null
}
```

Output:

```json
{
  "status": "written",
  "repo_id": "ops",
  "path": "docs/notes/2026-06-09-codegraph-mvp-boundary.md",
  "authority": "capture",
  "indexed": true,
  "index_scope": "single_document",
  "full_reindex_required": false,
  "warnings": []
}
```

Rules:

- Default target is `write_policy.default_capture_dir`.
- File name is date-prefixed and slugified.
- Frontmatter is always added.
- Authority is `capture` unless a safe working type like `open_question` is selected.
- If target matches blocked high-authority globs, reject and suggest a proposal path.
- If `auto_reindex_after_note_write` is true, index only the newly written document before returning; never perform a full repo rescan inside this tool call.

### 10.14 `create_draft_artifact(kind, title, body, source?, tags?)`

Optional but recommended for MVP if time permits. Creates proposal/draft artifacts only.

Supported `kind`:

- `open_question`
- `doctrine_delta`
- `adr_draft`
- `review_packet`

No accepted/canonical writes.

---

## 11. Markdown Rendering Requirements

Every major retrieval tool should include a `markdown` field because connected LLMs and humans consume Markdown well.

Session brief Markdown template:

```md
# Session Context Packet: {project.name}

Task: {task}
Generated: {generated_at}
Synthesis required: yes

## Repo Freshness

- ops: branch main @ abc123, indexed at ..., warnings: none
- ghostmesh: branch main @ def456, warning: behind remote by 2 commits

## Canonical Context

### {title}
Source: `{path}:{start_line}-{end_line}`
Authority: canonical

> {excerpt}

## Accepted Decisions
...

## Implementation Evidence
...

## Open Questions
...

## Lower-Authority Context
...

## Warnings and Gaps
...
```

Markdown must not hide authority warnings. Superseded/rejected material must be visibly labeled.

---

## 12. Write Policy and Authority Protection

### 12.1 Direct Write Allowed

Direct writes are allowed only for low-authority artifacts:

- notes;
- discussions;
- handovers;
- evidence packets;
- raw imported summaries;
- open-question drafts;
- review packets;
- implementation observations.

### 12.2 Direct Write Blocked

Direct writes are blocked for:

- doctrine/current files;
- accepted ADRs/decisions;
- canonical terminology;
- canonical project brief/PRD;
- rejected/superseded markings;
- acceptance criteria;
- any configured `blocked_direct_write_globs`.

### 12.3 Blocked Write Response

When blocked:

```json
{
  "status": "blocked",
  "reason": "Target path is canonical/high-authority. Direct MCP writes are not allowed.",
  "target": "docs/doctrine/current.md",
  "suggested_action": "create_draft_artifact",
  "suggested_target": "docs/proposals/doctrine-deltas/2026-06-09-example.md"
}
```

### 12.4 Promotion

Promotion from capture/working to canonical is not done by the MCP server in MVP. It happens via:

- human editing;
- PR workflow;
- external governance process;
- future explicit promotion tool requiring approval.

---

## 13. Error Handling

Errors must be structured:

```json
{
  "error": {
    "code": "CONFIG_INVALID",
    "message": "Repo path does not exist: /repos/foo",
    "details": { "repo_id": "foo" },
    "recoverable": true
  }
}
```

Error codes:

- `CONFIG_INVALID`
- `REPO_NOT_FOUND`
- `REPO_NOT_GIT`
- `INDEX_NOT_READY`
- `INDEX_FAILED`
- `QUERY_INVALID`
- `WRITE_BLOCKED_AUTHORITY`
- `WRITE_FAILED`
- `CODE_PROVIDER_UNAVAILABLE`
- `GIT_STATUS_FAILED`

Search/brief tools should degrade where possible. A stale/remote failure warning should not block local evidence retrieval.

---

## 14. Security and Privacy

Required MVP protections:

1. No network exposure by default.
2. No cloud model/API calls by default.
3. No secret environment variables required.
4. Work repos mounted read-only by default.
5. Ops repo write access limited by write policy.
6. Path traversal prevention on all write targets.
7. All target paths normalized and verified under configured repo root.
8. Binary files skipped.
9. `.git` internals never indexed as content.
10. Generated Markdown must include source paths but not secret redaction guarantees beyond skipped files/globs.

Recommended default excludes:

```yaml
exclude_globs:
  - ".git/**"
  - ".env"
  - ".env.*"
  - "**/*secret*"
  - "**/*token*"
  - "node_modules/**"
  - ".venv/**"
  - "dist/**"
  - "build/**"
```

---

## 15. Implementation Stack and Library Policy

Use Python 3.12+ for the MVP canonical Docker/runtime implementation unless there is a strong reason not to. Use mature open-source libraries for parsing, indexing, CLI/UI, Git, packaging, and MCP plumbing. Do not spend MVP time hand-rolling chunkers, Markdown parsers, glob engines, Git wrappers, setup orchestration, or code graph functionality when maintained libraries exist.

### 15.1 Package and Runtime Tooling

Required tooling:

- Python 3.12+ for the canonical Docker/runtime path. Host execution is best-effort unless the host has Python 3.12+ and required system dependencies installed.
- Poetry for dependency management and lockfile generation (`pyproject.toml` + `poetry.lock`).
- Dockerfile and `docker-compose.example.yaml`.
- `ruff` for lint/format.
- `pytest` for tests.
- `pytest-cov` for coverage.
- `mypy` or `pyright` for static type checks if it does not slow MVP delivery.
- `pre-commit` optional but recommended.

Required commands in README:

```bash
poetry install
poetry run pytest
poetry run ruff check .
poetry run project-knowledge setup
poetry run project-knowledge start
```

### 15.2 Concrete Python Libraries

Use these libraries unless implementation finds a clear incompatibility and documents the replacement:

| Concern | Library/tool | Notes |
|---|---|---|
| MCP server | `fastmcp` on top of the official MCP Python SDK | Use shared service functions exposed through stdio and localhost HTTP/StreamableHTTP entrypoints; do not implement MCP protocol manually. |
| Config/schema | `pydantic` v2 + `pydantic-settings` | Typed config and tool inputs/outputs. |
| CLI | `typer` + `rich` | Human-friendly setup/status commands. |
| Local setup UI/TUI | `textual` or `rich` prompts; optional minimal `fastapi` localhost UI if faster | Must avoid manual Docker/config assembly for normal users. |
| YAML | `ruamel.yaml` or `PyYAML` | Prefer round-trip preservation if editing config. |
| Markdown frontmatter | `python-frontmatter` | Do not hand-parse frontmatter. |
| Local parser registry | Project-owned thin registry using format-specific local parser libraries | Parser selection by file extension/content type; no cloud parser in default path. |
| Markdown/frontmatter parsing | `python-frontmatter` plus `markdown-it-py` or `mistune` if heading parsing needs help | Do not hand-parse YAML frontmatter; keep chunking simple and testable. |
| Text retrieval | stdlib `sqlite3` with SQLite FTS5/BM25 | Default candidate generator; authority/ranking logic remains project-owned and deterministic. |
| Git | `GitPython` plus subprocess fallback for exact porcelain commands | Do not parse `.git` internals. |
| Globs/gitignore | `pathspec` | Respect include/exclude/gitignore-style patterns. |
| SQLite | stdlib `sqlite3`; optional `sqlite-utils` for migrations/dev ergonomics | Keep metadata, FTS5/BM25 candidate retrieval, authority registry, freshness, and traceability boring/local. |
| File watching, later only | `watchfiles` | Not required in MVP runtime. |
| Code search fallback | SQLite FTS5 text retrieval + direct file reads; optionally `ripgrep` if present | Fallback only, not replacement for CodeGraph-style path. |
| CodeGraph-style context | `codegraphcontext` / `CodeGraphContext` as initial spike candidate; next named candidate only if spike fails | Must be tested before coding adapter; do not leave provider vague. |
| Hashing | stdlib `hashlib` | Content hashes for index freshness. |
| Time | stdlib `datetime` with timezone-aware UTC | No naive timestamps. |
| Testing | `pytest`, `pytest-cov`, `pytest-mock`, `freezegun` | Fixtures for repos and deterministic time. |

### 15.3 Do-Not-Reinvent Policy

Implementation must prefer open-source libraries over bespoke helpers. The developer should not implement custom versions of:

- MCP transport/protocol;
- Markdown/frontmatter parsing;
- Git object/status parsing;
- glob/gitignore matching;
- terminal UI prompts;
- code graph/symbol extraction beyond simple fallback snippets;
- Docker/client config orchestration beyond thin wrappers around standard commands;
- parser libraries, SQLite FTS primitives, or migration helpers already available in mature open-source packages;
- database migration helpers if an existing lightweight migration pattern is used.

Custom code is appropriate for project-specific concepts:

- authority inference;
- source ranking;
- evidence packet compilation;
- write-policy enforcement;
- provider abstraction;
- Markdown packet rendering.

### 15.4 Suggested Repository Layout

```text
project-knowledge-mcp/
├── pyproject.toml
├── poetry.lock
├── README.md
├── Dockerfile
├── docker-compose.example.yaml
├── project.example.yaml
├── src/
│   └── project_knowledge_mcp/
│       ├── __init__.py
│       ├── server.py              # FastMCP stdio and localhost HTTP entrypoints
│       ├── cli.py                 # setup/start/status/validate/index/check commands
│       ├── setup_wizard.py        # guided setup/UI/TUI flow
│       ├── client_configs.py      # generated MCP client snippets
│       ├── config.py              # Pydantic config models
│       ├── errors.py
│       ├── git_state.py
│       ├── indexing/
│       │   ├── indexer.py
│       │   ├── parsers.py
│       │   ├── markdown_parser.py
│       │   ├── text_parser.py
│       │   ├── frontmatter.py
│       │   └── storage.py
│       ├── retrieval/
│       │   ├── sqlite_fts.py
│       │   ├── search.py
│       │   ├── evidence_compilers.py
│       │   ├── ranking.py
│       │   └── packets.py
│       ├── providers/
│       │   ├── base.py
│       │   ├── text.py
│       │   └── codegraph.py
│       ├── write_policy.py
│       ├── render_markdown.py
│       └── tools.py               # MCP tool functions
└── tests/
    ├── fixtures/
    │   ├── ops_repo/
    │   └── work_repo/
    ├── test_config.py
    ├── test_git_state.py
    ├── test_indexing.py
    ├── test_search_ranking.py
    ├── test_brief_packet.py
    ├── test_write_policy.py
    └── test_mcp_tools.py
```

---

## 16. Implementation Phases

### Phase 0: Retrieval, CodeGraph, and Transport Decision Gate

Deliver:

- Local parser + SQLite FTS5/BM25 retrieval-quality spike recorded in `docs/spikes/` or equivalent.
- CodeGraphContext/codegraph-style context spike recorded in `docs/spikes/` or equivalent.
- FastMCP stdio + localhost HTTP/StreamableHTTP sanity check recorded in `docs/spikes/` or equivalent.
- Decision checkpoint documenting accepted provider, accepted limitations, or rejected candidate plus named alternate.
- Minimal commands and fixture outputs proving install/index/query/persist/failure behavior and retrieval quality against expected evidence.

Acceptance:

```bash
# exact commands depend on the spike, but the committed evidence must include:
# - package install commands
# - fixture indexing command
# - fixture query command
# - persistence/reload check
# - no-key/no-network assertion
```

Implementation must not proceed to Phase 1 until the decision record exists. If either preferred candidate fails, choose and document an alternate behind the same provider boundary.

### Phase 1: Skeleton and Config

Deliver:

- Python package managed by Poetry.
- CLI entrypoint with `project-knowledge setup/start/status/print-client-config` commands.
- Minimal local setup UI/TUI or guided prompt flow.
- MCP stdio server starts.
- Localhost-bound HTTP/StreamableHTTP mode starts if selected.
- Config loading and validation.
- Dockerfile and example compose.
- Generated client config snippets for Hermes and generic MCP clients.

Acceptance:

```bash
poetry run pytest tests/test_config.py -q
poetry run project-knowledge validate-config --config project.example.yaml
poetry run project-knowledge setup --non-interactive --config project.example.yaml --dry-run
```

### Phase 2: Git Staleness

Deliver:

- Repo inspection.
- Branch/head/dirty/untracked.
- Remote ahead/behind when available.
- Structured warnings on remote failure.

Acceptance:

```bash
poetry run pytest tests/test_git_state.py -q
poetry run project-knowledge check-staleness --config project.example.yaml
```

### Phase 3: Local Parser Registry and SQLite Indexing

Deliver:

- Markdown parser.
- Plain text parser.
- Parser registry interface for later document formats.
- Frontmatter parser and Pydantic metadata normalization.
- Authority inference.
- SQLite metadata/chunk/FTS5 registry under the project-local state directory.
- Index CLI/MCP tool.

Acceptance:

```bash
poetry run pytest tests/test_indexing.py -q
poetry run project-knowledge index-project --config project.example.yaml
```

### Phase 4: Search and Ranking

Deliver:

- `search_ops`.
- `search_decisions`.
- `get_current_doctrine`.
- `search_open_questions`.
- Provider relevance normalization for SQLite FTS5/BM25 results.
- Ranking tests proving authority ordering.

Acceptance:

```bash
poetry run pytest tests/test_search_ranking.py -q
```

### Phase 5: Code Context Providers

Deliver:

- Provider interface.
- Text fallback provider using SQLite FTS5 text retrieval/direct file reads.
- CodeGraphContext reliability spike recorded with install/index/query/failure evidence.
- CodeGraph provider adapter with soft-fail behavior.
- `search_code`, `get_code_context`, `get_code_provider_status`.

Acceptance:

```bash
poetry run pytest tests/test_code_context.py -q
```

If the selected CodeGraph-style tool cannot be installed in CI, tests must mock the provider, prove the adapter contract, and separately prove fallback behavior. The implementation plan must still name the chosen tool or record the failed reliability spike; do not leave CodeGraph vague or repeatedly hedged.

### Phase 6: Evidence Packets and Session Briefs

Deliver:

- `retrieve_ops_code_evidence`.
- `generate_session_brief`.
- Markdown renderer.
- Staleness warnings included in packets.

Acceptance:

```bash
poetry run pytest tests/test_brief_packet.py -q
```

### Phase 7: Safe Capture Writes

Deliver:

- `add_project_note`.
- Optional `create_draft_artifact`.
- Write policy enforcement.
- Immediate indexing of written note.

Acceptance:

```bash
poetry run pytest tests/test_write_policy.py -q
```

### Phase 8: End-to-End MCP Verification

Deliver:

- MCP server tool registration.
- Tool calls exercised through an MCP client test or integration harness.
- Docker run verified.

Acceptance:

```bash
poetry run pytest tests/test_mcp_tools.py -q
docker build -t project-knowledge-mcp:test .
docker run --rm -i -v "$PWD:/workspace:rw" -e PROJECT_KNOWLEDGE_CONFIG=/workspace/project.example.yaml project-knowledge-mcp:test --help
```

---

## 17. Test Fixture Requirements

Create tiny fixture repos under `tests/fixtures`.

### 17.1 Ops Fixture

Files:

```text
tests/fixtures/ops_repo/
├── README.md
├── docs/
│   ├── doctrine/current.md
│   ├── decisions/accepted/0001-context-compiler.md
│   ├── discussions/2026-06-09-old-brainstorm.md
│   ├── notes/2026-06-09-capture.md
│   ├── open-questions/0001-codegraph-boundary.md
│   └── rejected/old-local-llm-required.md
```

Required content patterns:

- Canonical doc says server is deterministic context compiler.
- Raw discussion says an old or contradictory idea.
- Rejected doc says local LLM is required, marked rejected.
- Open question mentions CodeGraph provider boundary.

Tests must prove canonical/rejected handling works.

### 17.2 Work Fixture

Files:

```text
tests/fixtures/work_repo/
├── README.md
├── src/example.py
├── tests/test_example.py
└── schemas/example.schema.json
```

Tests must prove code search returns source/test/schema evidence.

---

## 18. Definition of Done

MVP is complete when:

1. Docker image builds.
2. Container runs on a generic Docker-capable host, not only a Hermes instance.
3. MCP stdio server starts from Docker.
4. Localhost-bound HTTP/StreamableHTTP mode starts or is explicitly deferred with a documented connector limitation.
5. Setup script/UI can generate config, mount definitions, and client snippets without manual Docker/YAML assembly.
6. Config validation works for mounted repos.
7. Local parser + SQLite FTS5/BM25 retrieval-quality spike is recorded before implementation and the selected retrieval decision is documented.
8. Code repo contains a pinned implementation snapshot of this spec with provenance fields pointing back to the ops repo source path and commit.
9. Indexing works without network, model keys, embedding keys, or cloud parser keys.
10. SQLite FTS5/BM25 candidate retrieval works for ops docs with metadata preserved and authority ranking tested.
11. Code search works through text fallback when no code graph provider is healthy.
12. CodeGraphContext is spiked and either integrated as the preferred code context path or rejected with recorded evidence and replacement criteria; fallback works with status reporting in either case.
13. Staleness checks report Git state.
14. Session brief returns structured evidence packet plus Markdown.
15. `add_project_note` writes low-authority capture docs and indexes only the newly written document synchronously.
16. Direct writes to canonical paths are blocked.
17. All tests pass locally.
18. README documents setup, config, Docker, generic MCP clients, Hermes MCP connection, and browser/mobile connector boundaries.
19. No test requires an LLM key, embedding key, GPU, or network access.

---

## 19. Open Questions for Review

These are explicitly non-blocking for the MVP draft unless reviewers decide otherwise.

1. Is SQLite FTS5/BM25 plus typed evidence compilation sufficient after the retrieval-quality spike, or must the implementation move to the next named retrieval candidate?
2. Is `CodeGraphContext` reliable enough on Python 3.12 after the spike, or must the implementation move to the next named CodeGraph-style candidate?
3. Should the default capture directory be `docs/notes/` or `inbox/`?
4. Should `create_draft_artifact` be MVP or immediate post-MVP?
5. Should project config support multiple ops repos in v1, or exactly one ops repo?
6. Should MCP expose `generate_session_brief` or rename it to `compile_session_context` while preserving an alias?
7. Should line ranges be best-effort in MVP or strict for Markdown docs?
8. Should a Git remote fetch be attempted during staleness checks, or should remote comparison use existing refs only by default?
9. Which browser/mobile connector path should be documented first for ChatGPT/Gemini/iPad workflows, given local-only exposure by default?

Recommended defaults for MVP:

- Local parser registry + SQLite FTS5/BM25 is the default document retrieval/indexing path; if retrieval-quality tests fail, evaluate the next named local/no-key candidate behind the same provider boundary.
- FastMCP is the default MCP framework on top of the official MCP Python SDK; expose shared service functions through stdio and localhost HTTP/StreamableHTTP entrypoints.
- CodeGraphContext is the first code context candidate; if reliable on Python 3.12, it is the preferred code context provider behind the adapter boundary; text fallback required for no-code/unhealthy cases.
- Default capture directory: `docs/notes/`.
- Include `create_draft_artifact` only if it does not delay core tools.
- Exactly one ops repo in MVP.
- Keep `generate_session_brief` as the friendly tool name.
- Line ranges best-effort.
- Do not fetch by default; report against existing remote refs unless `fetch_remote: true` is configured later.

---

## 20. Implementation Guardrails

Do:

- Keep server deterministic.
- Keep core no-key/no-model.
- Prefer mature local parser, SQLite, MCP, Git, and code-graph libraries over bespoke infrastructure.
- Include source paths everywhere.
- Make authority visible.
- Make staleness visible.
- Treat CodeGraph-style context as the intended code path and fail soft to direct reads only for resilience.
- Block canonical direct writes.
- Write tests around authority behavior.

Do not:

- Hide LLM calls inside MCP tools.
- Require local embeddings.
- Require LlamaIndex, LlamaParse, LlamaCloud, cloud parsers, vector stores, or hosted retrieval APIs in the default MVP path.
- Build a custom semantic search system in MVP.
- Treat raw captures as truth.
- Let old/rejected docs appear as current doctrine.
- Make file watching required.
- Let provider-specific CodeGraph output leak into public MCP responses.
- Hedge CodeGraph into non-existence; either integrate the selected tool or document why the reliability spike failed and what replacement criteria are required.
- Ask the user clarifying questions during normal note capture unless authority changes.

---

## 21. Canonical One-Sentence Product Description

Project Knowledge MCP is a local-first MCP server that compiles source-cited, authority-ranked, freshness-aware project context from local ops and code repos so any connected AI assistant can reason from current project truth without becoming the memory itself.
