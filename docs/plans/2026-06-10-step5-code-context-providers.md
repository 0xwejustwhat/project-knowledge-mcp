# Step 5 Plan — Code Context Providers

## Objective

Implement Phase 5 minimally and durably on top of the Step 2–4 config/index/service boundary:

- define a stable internal `CodeContextProvider` shape;
- make indexed work repos searchable through a no-key text fallback;
- keep CodeGraphContext as the preferred configured provider boundary, but soft-fail when unavailable or unhealthy;
- expose `search_code`, `get_code_context`, and `get_code_provider_status` through service, MCP, and CLI surfaces;
- prove code/test/schema evidence and provider-health fallback behavior with regression tests.

## Guardrails

- No LLM keys, embeddings, GPU, local model server, or hosted retrieval APIs are required.
- Code tools must search configured `work` repos only unless a specific `repo_id` names a configured work repo.
- Invalid `repo_id`, invalid `limit`, and no work repos must return structured errors/warnings rather than crashes.
- CodeGraphContext output must not leak raw Rich/human CLI output into MCP responses; stable result objects only.
- Text fallback is resilience, not a replacement for the CodeGraph provider boundary.
- Existing ops search, decision/doctrine/open-question tools, and legacy spike compatibility must stay intact.

## Implementation Tasks

### Task 1 — RED tests for Step 5 surfaces and fallback behavior

Files:

- `tests/test_code_context.py`
- `tests/test_cli.py`
- `tests/test_mcp_tools.py`

Add tests proving:

1. A configured work repo with `src/`, `tests/`, and `schemas/` files is indexed and searchable.
2. `search_code_from_config` returns stable code-result fields: `repo_id`, `path`, `start_line`, `end_line`, `symbol`, `kind`, `snippet`, `provider`, `score`, and `related`.
3. `get_code_context_from_config` resolves both symbol-like queries and file-path queries through fallback.
4. `get_code_provider_status_from_config` reports configured CodeGraph, inactive/unhealthy CodeGraph, active text fallback, fallback availability, work repo count, and actionable warnings.
5. `search_code` rejects non-work `repo_id` widening and invalid limits with structured errors.
6. CLI/MCP surfaces expose `search-code`, `get-code-context`, and `get-code-provider-status` / `search_code`, `get_code_context`, and `get_code_provider_status`.

### Task 2 — Extend config and indexing for code repos

Files:

- `src/project_knowledge_mcp/config.py`
- `src/project_knowledge_mcp/index.py`

Implement:

- `CodeContextConfig` and nested `CodeGraphConfig` with defaults from the spec.
- Validation that `code_context.provider` is `codegraph`, fallback is `text`, and default CodeGraph path disables vector/embedding resolution.
- Indexing support for text-like code/test/schema files needed by the MVP fixture (`.py`, `.json`) while preserving hard excludes for `.git`, state dirs, env/secret/token paths, symlinks, and configured globs.
- Parser/type inference that marks work repo `src/**` as `code`, `tests/**` as `test`, and `schemas/**` as `schema`.

### Task 3 — Add provider boundary and text fallback service functions

Files:

- Create `src/project_knowledge_mcp/code_context.py`
- Modify `src/project_knowledge_mcp/services.py`

Implement:

- Stable dataclasses for `CodeResult` and `CodeProviderHealth` plus a provider protocol.
- `TextFallbackCodeContextProvider` backed by `ProjectIndex.search`, scoped to configured work repos.
- `CodeGraphContextProvider` health adapter that detects optional dependency/CLI availability and reports soft-fail warnings without requiring CodeGraph in default tests.
- Service functions:
  - `search_code_from_config(query, config_path=None, repo_id=None, limit=None)`
  - `get_code_context_from_config(symbol_or_file, config_path=None, repo_id=None, limit=None)`
  - `get_code_provider_status_from_config(config_path=None)`
- Markdown renderers and structured error handling aligned with existing search tools.

### Task 4 — Expose MCP and CLI surfaces

File: `src/project_knowledge_mcp/server.py`

Add MCP tools and Typer commands:

- `search_code` / `search-code`
- `get_code_context` / `get-code-context`
- `get_code_provider_status` / `get-code-provider-status`

CLI output must be MCP-compatible JSON and must accept `--config`, optional `--repo-id`, and optional `--limit` where applicable.

### Task 5 — Verification, review, commit, and PR

Run:

```bash
VENV=/root/.cache/pypoetry/virtualenvs/project-knowledge-mcp-GZ03qJTl-py3.12
$VENV/bin/python -m pytest tests/test_code_context.py -q
$VENV/bin/python -m pytest tests/test_mcp_tools.py tests/test_cli.py -q
$VENV/bin/ruff format --check .
$VENV/bin/ruff check .
$VENV/bin/python -m pytest -q
git diff --check
```

Then run delegated spec/quality reviews, patch blockers, commit, push, open PR, and verify CI. If GitHub checks report no checks, say so explicitly; otherwise watch the check run to completion.
