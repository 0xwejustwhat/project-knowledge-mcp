---
name: pkmcp-codebase-questions
description: Use PKMCP for authority-aware project and code questions.
version: 1.1.0
author: Amin El-Gazzar (0xwejustwhat), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [pkmcp, mcp, project-knowledge, authority, evidence]
    related_skills: []
---

# PKMCP Codebase Questions

Use a configured Project Knowledge MCP (PKMCP) server to retrieve project
knowledge and code evidence without confusing relevance with authority. PKMCP
finds and classifies evidence; the assistant still owns query planning,
synthesis, direct verification, and the final answer.

Tool names may be prefixed by the client, for example
`mcp_project_knowledge_get_project_state`. Map those names to the unprefixed
names used here while preserving the same arguments and semantics.

## When to Use

Use this skill when the user asks:

- What the project currently says about a topic.
- Where a feature is implemented or tested.
- Which doctrine, decisions, proposals, discussions, or open questions apply.
- What context should be loaded before planning or implementation.
- Whether indexed project evidence is current and complete enough to trust.

Do not use for:

- Reading an exact named file when no discovery is needed.
- Reporting live git, service, process, test, or deployment state without direct
  inspection.
- Maintaining PKMCP itself; that requires a separate maintainer workflow,
  repository conventions, implementation tests, and release procedures.
- Writing project knowledge unless the user explicitly asks to capture, draft,
  or propose a change.

## Installation

Copy this complete skill directory into the skills directory used by the agent
harness. Preserve the directory name and `SKILL.md` filename.

For Hermes Agent, from a checkout of the PKMCP repository, use `terminal` to run:

```bash
mkdir -p "$HOME/.hermes/skills"
cp -R samples/skills/pkmcp-codebase-questions "$HOME/.hermes/skills/"
```

Start a new agent session after installation so the harness reloads its skill
catalog. Other clients should use their documented Agent Skills directory while
keeping this file unchanged.

## Prerequisites

- A reachable PKMCP MCP server.
- A valid project configuration with at least one configured repository.
- An index for searches and project-state retrieval.
- Direct file, git, test, and runtime tools when precise live verification is
  required.

Confirm setup with `health` and `validate_config`. Do not infer a valid project
from the presence of MCP tools alone.

## Authority and Retrieval Model

PKMCP separates **what matched** from **whether retrieval ran successfully** and
**whether its source is fresh**.

For every project-state section, inspect:

- `retrieval_state`: whether retrieval was `complete`, `partial`, `unavailable`,
  or `not_run`.
- `match_state`: whether it returned `with_results`, `no_match`, or
  `indeterminate`.
- `freshness_state`: whether indexed source evidence is current enough for the
  claim.
- `warnings` and `errors`: named limits that must remain visible in synthesis.

Only `complete` retrieval can support a meaningful `no_match`. A `partial`,
`unavailable`, or `not_run` section cannot prove absence; its match should be
treated as `indeterminate`.

Authority is explicit. Newer material does not silently supersede older accepted
material. Current doctrine and accepted decisions govern unless explicit
supersession or scope says otherwise.

## Required Project-State View

Every `get_project_state` and `generate_session_brief` call must explicitly
choose one view; there is no default:

- `doctrine`: authoritative doctrine, accepted decisions, and separately labeled
  implementation evidence. Informal sections are not searched.
- `doctrine_and_discussion`: the same authoritative baseline plus separately
  labeled open questions, proposals/drafts, discussions, and captures.

Use `doctrine` when the user asks what currently governs. Use
`doctrine_and_discussion` when unresolved or proposed context could help, but
never let informal material override doctrine.

Stable project-state sections include:

- `doctrine`
- `accepted_decisions`
- `implementation_evidence`
- `open_questions`
- `proposals_and_drafts`
- `discussions_and_captures`
- `superseded_and_rejected` only when explicitly requested

`generate_session_brief` uses the same authority-separated compiler and may also
include `recent_changes`. If no `since` value was supplied, recent changes should
be `not_run` and `indeterminate`, not a false complete no-match.

## First-Move Workflow

1. **Check availability.** Call `health`, then `validate_config` when configuration
   correctness is relevant. Completion criterion: the server and project config
   are usable, or the limitation is explicit.

2. **Check staleness.** Call `check_project_staleness` before freshness-sensitive
   retrieval. Completion criterion: each relevant repository is current or its
   stale/unavailable state is carried into the answer.

3. **Select the epistemic view.** Call `get_project_state` with `view: doctrine`
   for the governing baseline or `view: doctrine_and_discussion` for separately
   labeled informal context. Completion criterion: no authority class is blended
   with another.

4. **Retrieve code evidence when needed.** Check `get_code_provider_status`, then
   use `search_code` and `get_code_context`. Set `repo_id` whenever the target
   repository is known. Completion criterion: likely files or symbols are located
   and provider limitations are recorded.

5. **Verify precise claims directly.** Read the complete file/function, inspect
   live git state, or run tests as the task requires. Completion criterion: exact
   behavior is supported by current source or execution, not only a bounded
   snippet.

6. **Synthesize.** State the answer, evidence, authority class, and caveats.
   Completion criterion: incomplete retrieval is never described as absence of
   project knowledge.

## Tool Guide

- `health`: confirm server reachability.
- `validate_config`: validate project and repository configuration.
- `index_project`: build or refresh configured indexes; use `repo_id` for a
  targeted repository when supported by the client schema.
- `check_project_staleness`: compare repository state with index metadata.
- `get_project_state`: retrieve an explicit authority-separated project view.
- `generate_session_brief`: compile task-oriented project state and optional
  recent changes using the same required view.
- `search_ops`: search indexed operational/project-memory material.
- `search_decisions`: search decision records by status and authority.
- `search_open_questions`: retrieve unresolved questions with source metadata.
- `get_code_provider_status`: inspect CodeGraph and text-fallback readiness.
- `search_code`: find conceptual code, test, and schema evidence.
- `get_code_context`: retrieve context for a likely symbol or file.
- `add_project_note`: write a low-authority capture.
- `create_draft_artifact`: create a non-canonical proposal or draft.
- `propose_authority_change`: prepare a review-gated authority change.

## Reindexing

When staleness reports `reindex_needed: true`, call `index_project` before
relying on indexed absence if indexing is in scope.

- Use the configured project mode so include/exclude globs, source mode, worktree
  policy, and scope fingerprints remain consistent.
- Pass `repo_id` to refresh only the affected repository when the exposed schema
  supports targeted indexing.
- Use a full configured reindex when several repositories or indexing settings
  changed.
- Run `check_project_staleness` again afterward. Successful indexing is not fully
  verified until the relevant repository reports current with no scope mismatch.

Index warnings do not automatically mean failure. Preserve warnings about
unknown document types, normalization, skipped documents, or provider readiness
and judge whether they affect the requested claim.

## Result Interpretation

For document evidence, retain:

- `repo_id`, `path`, and line range.
- `type`, `authority`, and `status`.
- `source_mode`, snapshot/worktree metadata, and warnings.
- Ranking scores as relevance signals only, never authority.

For code evidence, retain:

- `path`, line range, symbol, and kind.
- `provider`: `codegraph` or `text`.
- Related symbols/files as leads, not a proven complete call graph.

A snippet shows what is present in a bounded range. It cannot prove the absence
of an else branch, validation step, caller, error path, or test elsewhere. Read
the complete live source before editing or making detailed behavioral claims.

## Provider Recovery

If `get_code_provider_status` reports:

- `active_provider: codegraph`: use graph-backed results normally, then verify
  exact source claims.
- `active_provider: text`: treat results as search leads and verify more
  aggressively with direct inspection.
- `active_provider: unavailable`: do not claim code intelligence from PKMCP;
  inspect the repository directly or repair the configured provider.

If the configured CodeGraph executable is missing, rerun the supported PKMCP
setup workflow rather than silently patching generated paths. If it exists but is
unhealthy, inspect its status/index using the configured command and report the
failure before falling back.

## Write Boundaries

Question-answering should normally remain read-only.

Use `add_project_note` only for an explicitly requested low-authority capture.
Use `create_draft_artifact` only for an explicitly requested proposal, question,
handover, or other non-canonical draft. Use `propose_authority_change` only when
the user has approved preparing a review-gated change to authoritative material.

Never bypass `WRITE_POLICY_DENIED` with arbitrary filesystem writes. A draft or
proposal remains non-authoritative until the configured review and promotion
workflow accepts it.

## Error Recovery

- `INDEX_NOT_READY`: index the relevant configured repository if in scope;
  otherwise use direct inspection and disclose the limitation.
- `QUERY_INVALID`: correct the query, view, `repo_id`, filter, or limit. Do not
  widen a restricted search silently.
- `CONFIG_INVALID`: run `validate_config` and repair or report the named problem.
- `PROVIDER_UNAVAILABLE`: use direct repository tools or repair the provider; do
  not fabricate code context.
- `WRITE_POLICY_DENIED`: stop the write and preserve the authority boundary.

Keep all packet-level and section-level warnings attached to the reasoning even
when results are non-empty.

## Answer Format

Prefer:

1. **Answer:** the direct synthesis.
2. **Authoritative basis:** doctrine and accepted decisions with citations.
3. **Additional context:** separately labeled proposals, discussions, captures,
   open questions, or implementation evidence.
4. **Retrieval caveats:** stale, partial, unavailable, excluded, or not-run
   sections and provider limitations.
5. **Verification:** direct files, git state, tests, or runtime evidence used for
   precise claims.

Do not dump the packet as the final answer. Explain what governs, what merely
informs, and what could not be established.

## Verification Checklist

Before finalizing:

- [ ] The server and configuration were usable, or failure was stated.
- [ ] Relevant repository staleness was checked.
- [ ] `doctrine` or `doctrine_and_discussion` was chosen explicitly.
- [ ] Each section's `retrieval_state`, `match_state`, and `freshness_state` was
      interpreted independently.
- [ ] Informal material was kept separate from doctrine and accepted decisions.
- [ ] `repo_id` constrained retrieval or indexing when the repository was known.
- [ ] CodeGraph and text-fallback evidence were distinguished.
- [ ] Precise code, git, test, and runtime claims were verified directly.
- [ ] Warnings, errors, omissions, and unavailable sections remained visible.
- [ ] Write tools were used only because the user explicitly asked for a write.
