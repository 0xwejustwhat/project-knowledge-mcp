# Step 4 Plan — Search and Ranking Tools

## Objective

Implement Phase 4 minimally on top of the existing Step 2/3 service layer:

- keep existing `search_ops` behavior;
- add `search_decisions`;
- add `get_current_doctrine`;
- add `search_open_questions`;
- normalize SQLite FTS5/BM25 provider relevance across candidate results;
- prove authority ordering with dedicated tests.

## Guardrails

- All Step 4 tools remain ops-repo scoped; caller filters must not widen retrieval into work/artifact repos.
- Specialized tools must reject conflicting `type` / `doc_type` filters rather than silently broadening scope.
- Superseded/rejected content is excluded by default and only included with explicit `include_superseded`.
- Invalid queries, unsupported filters, invalid limits, missing indexes, and invalid configs return structured JSON errors.
- MCP and CLI surfaces call the same service-layer functions.

## Implementation Tasks

### Task 1 — RED tests for ranking and missing Step 4 surfaces

Files:

- `tests/test_search_ranking.py`
- `tests/test_mcp_tools.py`
- `tests/test_cli.py`

Add tests proving:

1. BM25 scores are normalized into `0.0..1.0` relevance scores and final scores sort descending.
2. Canonical/current docs outrank lower-authority captures even when captures have more keyword density.
3. `search_decisions` returns decision docs only, accepted/current before draft, and preserves `superseded_by` when superseded is explicitly included.
4. `get_current_doctrine` returns canonical/current doctrine first and relevant accepted decisions.
5. `search_open_questions` returns open-question docs with owner/related-doc metadata.
6. Specialized tools preserve ops scope and structured filter validation.
7. CLI commands exist and return JSON.

### Task 2 — Index search result metadata and relevance normalization

File: `src/project_knowledge_mcp/index.py`

- Add `frontmatter` to `SearchResult`.
- Select and parse `documents.frontmatter_json` in search results.
- Normalize SQLite BM25 across fetched candidates before applying authority boosts:
  - lower BM25 is better;
  - best candidate maps to `1.0`;
  - weakest candidate maps to `0.0`;
  - all-equal candidates map to `1.0`.

### Task 3 — Service-layer specialized search wrappers

File: `src/project_knowledge_mcp/services.py`

Add:

- `search_decisions_from_config(...)`
- `get_current_doctrine_from_config(...)`
- `search_open_questions_from_config(...)`

Reuse `search_ops_from_config` for config validation, index availability, ops scoping, query validation, scalar filter validation, tag handling, and Markdown rendering.

Add helpers to:

- force `doc_type` without allowing conflicts;
- normalize owner / related-doc frontmatter for open questions;
- render doctrine packet Markdown.

### Task 4 — MCP and CLI surfaces

File: `src/project_knowledge_mcp/server.py`

Add MCP tools and Typer commands:

- `search_decisions` / `search-decisions`
- `get_current_doctrine` / `get-current-doctrine`
- `search_open_questions` / `search-open-questions`

CLI options should mirror `search-ops`: `--config`, `--limit`, `--include-superseded`, plus safe scalar filters where relevant.

### Task 5 — Verification and review

Run:

```bash
VENV=/root/.cache/pypoetry/virtualenvs/project-knowledge-mcp-GZ03qJTl-py3.12
$VENV/bin/python -m pytest tests/test_search_ranking.py -q
$VENV/bin/python -m pytest tests/test_mcp_tools.py tests/test_cli.py -q
$VENV/bin/ruff format --check .
$VENV/bin/ruff check .
$VENV/bin/python -m pytest -q
```

Then run delegated spec and quality reviews before commit/PR.
