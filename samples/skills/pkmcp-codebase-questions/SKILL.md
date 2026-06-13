---
name: pkmcp-codebase-questions
description: Use when answering codebase, repository, project-memory, architecture, implementation-location, or evidence-gathering questions through a configured Project Knowledge MCP server. Guides when to call PKMCP tools, how to interpret evidence packets and CodeResult records, and when to verify with live file/git/test inspection.
version: 1.0.0
author: Project Knowledge MCP
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [pkmcp, mcp, codebase-questions, project-memory, evidence, codegraph]
    related_skills: [codegraph, native-mcp]
---

# PKMCP Codebase Questions

## Overview

Use this skill when an assistant has access to a configured Project Knowledge MCP
(PKMCP) server and needs to answer questions from repo-grounded evidence. PKMCP is
an MCP evidence layer: its tools return search results, code context, provider
health, staleness metadata, and grouped evidence packets. The assistant still owns
query planning, synthesis, verification, and the final user-facing answer.

This skill assumes the harness exposes PKMCP's MCP tools directly. Some harnesses
prefix tool names with the server name, for example `mcp_project_knowledge_search_code`.
When that happens, map the tool names below to the harness-specific names and keep
the same arguments and interpretation rules.

If your harness also has a generic CodeGraph/codebase-question skill, use that
skill's code-question heuristics for query planning and use this PKMCP skill for
the actual MCP tool contract and result interpretation.

## When to Use

Use PKMCP for broad or evidence-seeking questions such as:

- "Where is this feature implemented?"
- "How does this repo handle authentication/config/indexing/etc.?"
- "Which files or symbols should I inspect before changing this?"
- "Find related tests, schema, docs, decisions, or open questions."
- "Give me the project context for this task."
- "What does the project memory say about this decision?"
- "Compile evidence before planning or implementing this change."

Use PKMCP before direct file spelunking when the user asks a broad codebase,
architecture, or project-memory question and has not already named the precise file
to inspect.

Do **not** use PKMCP as the only source of truth when the task requires live state:

- The user asks for the current contents of a specific file.
- You need exact git history, branch status, or uncommitted diff state.
- You need to run tests, linters, type checks, builds, or the application.
- You need to verify behavior that depends on generated files, runtime services, or
  environment state.

In those cases, PKMCP can still help locate the relevant area, but you must verify
with live file/git/test tools before making final claims.

## Available PKMCP Tools

Current PKMCP exposes these MCP tools:

| Tool | Use it for |
| --- | --- |
| `health` | Confirm the server is reachable. |
| `validate_config` | Check that the project config is usable. |
| `index_project` | Build/refresh the configured repo index. |
| `check_project_staleness` | Check whether indexed evidence may be stale relative to repo state. |
| `search_ops` | Search indexed ops/docs/project-memory content. |
| `search_decisions` | Search accepted and draft decision docs. |
| `get_current_doctrine` | Retrieve current doctrine plus accepted decisions for a topic. |
| `search_open_questions` | Search open questions with source/owner metadata. |
| `get_code_provider_status` | Check CodeGraph/text fallback provider health for code context. |
| `search_code` | Search configured work repo code/test/schema evidence. |
| `get_code_context` | Retrieve context for a symbol or repo-relative file path. |
| `retrieve_ops_code_evidence` | Retrieve grouped doctrine, decisions, open questions, code, and staleness for a topic. |
| `generate_session_brief` | Compile a task-oriented evidence packet, optionally including recent indexed changes. |
| `add_project_note` | Write a low-authority capture note. Do not use unless the user asked to record/capture something. |
| `create_draft_artifact` | Create a non-canonical draft/proposal artifact. Do not use unless the user asked to draft/write. |
| `propose_authority_change` | Prepare an authority-change branch/commit/PR flow. Do not use for normal question answering. |

For codebase questions, the usual tools are `get_code_provider_status`,
`search_code`, `get_code_context`, `retrieve_ops_code_evidence`, and
`generate_session_brief`.

## First-Move Workflow

For a broad codebase/project-memory question:

1. **Check freshness when reliability matters.**
   - Call `check_project_staleness` if the answer depends on current repo state.
   - If any returned repo has `reindex_needed: true`, treat indexed evidence as a
     lead and verify with live repo tools. If the harness permits it and the user
     asked for current indexed evidence, call `index_project` before searching.

2. **Start with an evidence-packet tool for multi-source questions.**
   - Call `retrieve_ops_code_evidence({"topic": "..."})` when the question mixes
     docs, decisions, open questions, and code.
   - Call `generate_session_brief({"task": "..."})` when beginning a coding task
     or needing session context.

3. **Use code-specific tools for implementation questions.**
   - Call `search_code({"query": "..."})` for conceptual code search.
   - Call `get_code_context({"symbol_or_file": "..."})` after you have a likely
     symbol, function, class, or file path.
   - Use `repo_id` when the project has multiple work repos and the target repo is
     known.

4. **Inspect directly before precise claims or edits.**
   - PKMCP snippets establish relevance, not complete semantics.
   - Before editing, citing exact behavior, or saying a test covers something,
     inspect the returned files directly and/or run the relevant checks.

5. **Synthesize with citations and uncertainty.**
   - Cite paths, line ranges, symbols, doc titles, decision status, and warnings.
   - Explicitly mention stale index warnings, fallback provider warnings, and gaps.

## Query Patterns

Use natural-language queries first, then narrow:

```json
{"query": "how authentication middleware validates bearer tokens", "limit": 5}
```

If results are too broad, add specific vocabulary:

```json
{"query": "BearerAuth validate_authorization_header token expiry", "repo_id": "api", "limit": 5}
```

If a result names a symbol or file, pivot from search to context:

```json
{"symbol_or_file": "AuthMiddleware", "repo_id": "api", "limit": 5}
```

```json
{"symbol_or_file": "src/auth/middleware.py", "repo_id": "api", "limit": 5}
```

For task kickoff, use a task-shaped query:

```json
{"task": "Implement bearer-token expiry validation", "since": "2026-06-01", "limit": 5}
```

## Result Interpretation

### Search/docs results

`search_ops`, `search_decisions`, `get_current_doctrine`, and
`search_open_questions` return project-memory evidence. Important fields include:

- `path`, `start_line`, `end_line`: where the indexed evidence came from.
- `title`, `heading_path`, `excerpt`: human-readable context.
- `repo_id`, `source_mode`, `snapshot_ref`, `snapshot_commit`,
  `includes_uncommitted_changes`: provenance and freshness context.
- `doc_type` / `type`: evidence class such as doctrine, decision, note, or open question.
- `authority` and `status`: whether the result should be treated as current,
  accepted, draft, capture, working, superseded, or rejected.
- `warnings`: per-result cautions such as superseded/rejected content.
- `score` and `bm25_score`: ranking signals, not proof.

Interpretation rules:

- Current doctrine and accepted decisions are stronger project-memory evidence than
  notes, drafts, captures, or open questions.
- Open questions indicate uncertainty or unresolved work, not settled truth.
- Superseded/rejected results can explain history but must not be presented as
  current state.
- Docs/decisions explain intent; live code and tests still decide current behavior.

### Code results

`search_code` and `get_code_context` return normalized `CodeResult` records. Public
result fields are:

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

Interpretation rules:

- `provider: "codegraph"` means PKMCP used graph-backed code context.
- `provider: "text"` means PKMCP used fallback indexed text search. Treat this as
  useful but less structurally precise than graph-backed context.
- `symbol` and `related` are leads to inspect; they are not a full call graph proof.
- `snippet` is bounded evidence. Read the full file/function before editing or
  making detailed behavioral claims.
- `kind` distinguishes code/test/schema-style evidence when available.
- `score` ranks likely relevance only.

### Evidence packets

`retrieve_ops_code_evidence` returns:

- `sections.doctrine`
- `sections.decisions`
- `sections.open_questions`
- `sections.code`
- `staleness`
- `warnings`
- `gaps`
- `errors`
- `markdown`

`generate_session_brief` returns the same kind of packet with task framing, plus:

- `evidence_topic`
- `repo_staleness`
- `sections.recent_changes` when `since` is provided

Interpretation rules:

- Treat packet sections as an evidence bundle to synthesize, not as a final answer.
- If `gaps` includes `CODE_EVIDENCE_MISSING`, say no code evidence was found and
  try a narrower query or direct repository search.
- If `errors` includes `INDEX_NOT_READY`, call `index_project` if appropriate or
  fall back to direct live repo inspection.
- If `warnings` mention reindexing, stale repos, fallback provider, or config
  issues, surface the caveat before making claims.

## Provider Health and Fallback

Before relying on code intelligence, especially for architecture or symbol-level
questions, call:

```json
{}
```

against `get_code_provider_status`.

Read these fields:

- `status`: should be `ok` for a normal health packet.
- `configured_provider`: usually `codegraph`.
- `active_provider`: `codegraph`, `text`, or `unavailable`.
- `codegraph_enabled` and `codegraph_healthy`: whether graph-backed context is available.
- `fallback_available`: whether text fallback can be used when graph context is unhealthy.
- `work_repos`: configured code repo IDs.
- `warnings`: setup/index/provider caveats.
- `details`: sanitized provider/status details.

Rules:

- If `active_provider` is `codegraph`, proceed normally.
- If `active_provider` is `text`, use results as retrieval leads and verify more
  aggressively with direct file inspection.
- If `active_provider` is `unavailable` or a tool returns `PROVIDER_UNAVAILABLE`,
  do not claim code intelligence from PKMCP. Fall back to direct repo tools or ask
  the user/operator to repair provider setup.

## Error and Recovery Rules

PKMCP errors are usually structured as `error: {code, message, details,
recoverable}` and may also appear in packet-level `errors`.

Common codes and actions:

| Code | Meaning | Agent action |
| --- | --- | --- |
| `INDEX_NOT_READY` | The configured index does not exist or cannot be opened. | Call `index_project` if indexing is in scope; otherwise use live repo tools and report the limitation. |
| `QUERY_INVALID` | Bad filter, invalid `repo_id`, invalid limit, or widened query outside the tool boundary. | Fix the argument and retry; do not widen restricted searches. |
| `CONFIG_INVALID` | Project config cannot be loaded/validated. | Call `validate_config`; report the config problem if not repairable in this task. |
| `PROVIDER_UNAVAILABLE` | CodeGraph failed and fallback is disabled/unavailable. | Use direct repo inspection or ask for provider repair; do not fabricate code context. |
| `WRITE_POLICY_DENIED` | A requested write crosses configured policy. | Do not bypass with arbitrary filesystem writes unless the user explicitly changes scope and you have authority. |

If a tool returns `warnings`, keep them attached to your reasoning. Do not hide
warnings just because results are non-empty.

## Answer Format

For codebase answers, prefer this structure:

1. **Short answer**: the direct answer in 1-3 sentences.
2. **Evidence**: bullets with `path:start-end`, `symbol`, decision/doc status, and
   provider where useful.
3. **Caveats**: stale index, fallback provider, missing code evidence, or open questions.
4. **Next action**: direct file inspection, run tests, reindex, or ask for decision.

Example:

```markdown
Short answer: Token validation appears to live in `src/auth/middleware.py`, centered
on `AuthMiddleware.validate_request`.

Evidence:
- `src/auth/middleware.py:42-88` — `AuthMiddleware.validate_request`, provider `codegraph`.
- `tests/test_auth.py:15-67` — expiry and missing-header coverage, provider `codegraph`.
- `docs/decisions/0007-auth.md:12-28` — accepted decision for bearer-token auth.

Caveat: PKMCP reported repo `api` may need reindexing, so I verified the file directly
before making this claim.
```

## Write Tools Boundary

Question-answering tasks should normally avoid write tools. Only use the following
when the user explicitly asks to capture, draft, or propose project-memory changes:

- `add_project_note`: low-authority capture note; returns `status`, `repo_id`,
  `path`, `authority`, `indexed`, `index_scope`, and warnings.
- `create_draft_artifact`: non-canonical draft/proposal artifact; returns an
  authority boundary and suggested next actions.
- `propose_authority_change`: prepares a review-required authority-change flow;
  can be blocked by uncommitted workspace changes.

Do not use write tools to silently mutate doctrine, accepted decisions, project
briefs, or canonical docs. If a write result says `authority_boundary:
review_required_before_promotion`, treat it as not yet accepted.

## Verification Checklist

Before finalizing a PKMCP-grounded answer:

- [ ] I used PKMCP for broad repo/project-memory discovery when appropriate.
- [ ] I checked staleness or disclosed that I did not when freshness matters.
- [ ] I interpreted `authority`, `status`, `warnings`, `gaps`, and `errors`.
- [ ] I distinguished graph-backed `codegraph` evidence from `text` fallback.
- [ ] I inspected live files or ran checks before precise behavioral claims or edits.
- [ ] I cited concrete paths, line ranges, symbols, or decision/doc statuses.
- [ ] I did not present PKMCP evidence packets as final truth without synthesis.
- [ ] I did not use write tools unless the user explicitly requested a capture,
      draft, or proposal.
