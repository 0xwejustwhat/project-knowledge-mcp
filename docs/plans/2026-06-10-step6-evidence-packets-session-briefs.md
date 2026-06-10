# Step 6 Plan — Evidence Packets and Session Briefs

## Objective

Implement Phase 6 minimally and durably on top of the Step 2–5 config/index/service boundary:

- add `retrieve_ops_code_evidence` to gather ops doctrine/decisions/open questions and code evidence for a topic;
- add `generate_session_brief` to compile a compact, source-cited, freshness-aware evidence packet for an assistant task;
- include deterministic recent indexed doc/code changes from scoped Git history when `since` is provided;
- include structured repo staleness summaries and warnings in packets;
- render deterministic Markdown optimized for client-assistant consumption;
- expose both capabilities through service, MCP, and CLI surfaces.

## Guardrails

- No LLM calls, embeddings, vector stores, network fetches, or synthesis inside the MCP server.
- Evidence packets compile and group existing deterministic search/status outputs; the connected assistant writes prose synthesis.
- Every non-warning evidence item must preserve source fields (`repo_id`, `path`, authority/status where applicable, snippet/excerpt or code snippet, and line ranges where available).
- Missing code evidence is represented as an explicit gap, not hidden success.
- Underlying retrieval errors are preserved in structured `errors` with section/tool source labels.
- `brief_max_results_per_section` bounds packet sections when callers omit `--limit`.
- `since`-driven recent changes are scoped by configured repo include/exclude globs and sourced from Git history; staleness warnings still indicate whether search-index evidence may lag committed or uncommitted repo state.
- Staleness warnings are included without mutating repos or fetching remotes.
- Existing Step 2–5 tools and output shapes remain compatible.

## Implementation Tasks

### Task 1 — RED tests for Step 6 services

Files:

- Create `tests/test_brief_packet.py`

Add tests proving:

1. `retrieve_ops_code_evidence_from_config(topic=...)` returns grouped `doctrine`, `decisions`, `open_questions`, `code`, `gaps`, `warnings`, and `markdown`.
2. It preserves citations/source metadata from existing search/code outputs.
3. It reports a code gap when no work repo/code result is available.
4. `generate_session_brief_from_config(task=...)` returns an `evidence_packet` with `task`, `project_id`, `generated_at`, `repo_staleness`, `sections`, `warnings`, `gaps`, and `markdown`.
5. If a repo becomes dirty after indexing, the brief includes staleness warnings/reindex information.

### Task 2 — RED tests for CLI and MCP surfaces

Files:

- Modify `tests/test_cli.py`
- Modify `tests/test_mcp_tools.py`

Add tests proving:

1. CLI help lists `retrieve-ops-code-evidence` and `generate-session-brief`.
2. CLI commands return JSON with the same core shape as the service.
3. MCP tools `retrieve_ops_code_evidence` and `generate_session_brief` are registered and callable through `FastMCP` client tests.

### Task 3 — Implement service packet compiler

File:

- Modify `src/project_knowledge_mcp/services.py`

Implement:

- `retrieve_ops_code_evidence_from_config(topic, config_path=None, limit=None)`.
- `generate_session_brief_from_config(task, config_path=None, since=None, limit=None)`.
- Small helpers to normalize warnings, staleness summaries, packet sections, gaps, and Markdown rendering.

Service behavior:

- Reuse `get_current_doctrine_from_config`, `search_open_questions_from_config`, `search_code_from_config`, and `check_project_staleness_from_config`.
- Keep section names stable: `doctrine`, `decisions`, `open_questions`, `code`.
- Include `repo_staleness` in briefs and `staleness` in ops/code evidence packets.
- Add a `CODE_EVIDENCE_MISSING` gap if no code results are found or code search is not applicable.
- Return structured errors from underlying validation/index failures without crashing.

### Task 4 — Expose MCP and CLI surfaces

File:

- Modify `src/project_knowledge_mcp/server.py`

Add MCP tools and Typer commands:

- `retrieve_ops_code_evidence` / `retrieve-ops-code-evidence`
- `generate_session_brief` / `generate-session-brief`

CLI output must be MCP-compatible JSON and accept `--config`, optional `--limit`, and optional `--since` for session briefs.

### Task 5 — Verification, review, commit, and PR

Run:

```bash
VENV=/root/.cache/pypoetry/virtualenvs/project-knowledge-mcp-GZ03qJTl-py3.12
$VENV/bin/python -m pytest tests/test_brief_packet.py -q
$VENV/bin/python -m pytest tests/test_mcp_tools.py tests/test_cli.py -q
$VENV/bin/ruff format --check .
$VENV/bin/ruff check .
$VENV/bin/python -m pytest -q
git diff --check
```

Then run an independent spec/quality review, patch blockers, commit, push, open PR, and verify CI. If GitHub checks report no checks, say so explicitly; otherwise watch checks to completion.
