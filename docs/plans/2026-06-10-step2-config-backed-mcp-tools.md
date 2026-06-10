# Step 2 Config-Backed MCP Tools Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Wire the Step 1 authority-aware SQLite index behind real project configuration and MCP tool surfaces.

**Architecture:** Add a typed config boundary that loads `PROJECT_KNOWLEDGE_CONFIG` or `project.yaml`, validates repo/storage/retrieval rules, and exposes shared service functions. The Typer CLI and FastMCP tools should call those services so CLI and MCP output shapes stay aligned. Keep this slice narrow: config validation, configured indexing, and authority-ranked ops search only.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, FastMCP, Typer, SQLite FTS5, pytest, ruff.

---

## Acceptance Criteria

1. `validate_config` loads real config from explicit path, env var, or local `project.yaml` and returns structured JSON.
2. Config validation enforces Step 2 MVP rules: `schema_version == 1`, URL/path-safe `project.id`, at least one repo, exactly one ops repo, existing Git repo paths, writable flag reality check, default capture repo points to writable ops repo, and `retrieval.provider == sqlite_fts5`.
3. `index_project(repo_id?, force?)` indexes configured repos through the Step 1 `ProjectIndex` path and returns Step 10.3-style JSON.
4. `search_ops(query, filters?, limit?)` searches the configured persisted index and returns authority-ranked JSON plus Markdown.
5. CLI commands support config path and keep Step 1 direct `--repo-path --state-dir` compatibility.
6. Step 0 FastMCP stdio/http smoke remains compatible.
7. No LLM/embedding/cloud parser dependency is added.

## Non-Goals

- Do not implement full session brief/evidence packet compilation yet.
- Do not implement staleness checking beyond warnings needed for index availability/config validation.
- Do not implement write policy tools or note capture yet.
- Do not expose CodeGraphContext output through MCP.
- Do not migrate Poetry metadata in this PR unless required by tests.

---

### Task 1: Add failing config validation tests

**Objective:** Prove config loading and validation are real, not the current placeholder.

**Files:**
- Create: `tests/test_config.py`
- Create/modify: `src/project_knowledge_mcp/config.py`

**Step 1: Write failing tests**

Tests should create temporary Git repos with `git init`, write `project.yaml`, and assert:

- `load_project_config(config_path)` returns project id/name, state dir, ops repo config.
- missing repo path makes `validate_project_config(config_path)` return `valid: false` with `CONFIG_INVALID` error details.
- non-`sqlite_fts5` retrieval provider is invalid.
- `PROJECT_KNOWLEDGE_CONFIG` env var is honored when no explicit path is provided.

**Step 2: Run test to verify RED**

Run:

```bash
VENV=/root/.cache/pypoetry/virtualenvs/project-knowledge-mcp-GZ03qJTl-py3.12
$VENV/bin/python -m pytest tests/test_config.py -q
```

Expected: FAIL because `project_knowledge_mcp.config` does not exist.

**Step 3: Implement minimal config module**

Add Pydantic models and helpers:

- `RepoConfig`
- `ProjectMetadata`
- `StorageConfig`
- `RetrievalConfig`
- `WritePolicyConfig`
- `ProjectKnowledgeConfig`
- `resolve_config_path(explicit_path: Path | None = None) -> Path`
- `load_project_config(config_path: Path | None = None) -> ProjectKnowledgeConfig`
- `validate_project_config(config_path: Path | None = None) -> dict`

**Step 4: Verify GREEN**

Run targeted test and then all existing tests.

---

### Task 2: Add failing configured indexing tests

**Objective:** Prove configured indexing uses repo IDs/roles/state dir from config.

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/project_knowledge_mcp/server.py`
- Modify: `src/project_knowledge_mcp/index.py` only if needed for summary/output fields

**Step 1: Write failing tests**

Add CLI tests that call:

```bash
project-knowledge index-project --config /tmp/project.yaml
project-knowledge index-project --config /tmp/project.yaml --repo-id ops
```

Expected JSON fields:

- `status: ok`
- `repos[0].repo_id == "ops"`
- `repos[0].documents_indexed > 0`
- `repos[0].chunks_indexed > 0`
- `state_dir` or db path resolves to configured storage state.

Also test unknown `--repo-id` returns non-zero structured error JSON or Typer error.

**Step 2: Run RED**

Run:

```bash
$VENV/bin/python -m pytest tests/test_cli.py -q
```

Expected: FAIL because CLI lacks config indexing mode.

**Step 3: Implement service + CLI**

Add service helper, likely in `src/project_knowledge_mcp/services.py`:

- `index_project_from_config(config_path: Path | None = None, repo_id: str | None = None, force: bool = False) -> dict`

Update CLI `index-project` to accept `--config` and use configured mode when present, while preserving old direct mode.

**Step 4: Verify GREEN**

Run targeted tests.

---

### Task 3: Add failing MCP tool shape tests for `index_project` and `search_ops`

**Objective:** Prove MCP clients can call configured indexing/search and receive stable JSON/Markdown output.

**Files:**
- Create: `tests/test_mcp_tools.py`
- Modify: `src/project_knowledge_mcp/server.py`
- Modify: `spikes/002-fastmcp-transports/run_spike.py` if needed for new `validate_config` shape compatibility

**Step 1: Write failing tests**

Using `fastmcp.Client(create_mcp())` or the in-memory transport, test:

- `validate_config({"config_path": "/tmp/project.yaml"})` returns `valid: true`, `project_id`, `repos`.
- `index_project({"config_path": "/tmp/project.yaml"})` indexes fixture docs and returns `status: ok`.
- `search_ops({"query": "evidence packets", "config_path": "/tmp/project.yaml", "limit": 3})` returns:
  - `query`
  - `results[]` with `repo_id`, `path`, `title`, `doc_type`, `status`, `authority`, `start_line`, `end_line`, `excerpt`, `score`, `warnings`
  - `warnings`
  - `markdown`

**Step 2: Run RED**

Run:

```bash
$VENV/bin/python -m pytest tests/test_mcp_tools.py -q
```

Expected: FAIL because MCP tools do not exist or are placeholders.

**Step 3: Implement shared search service and MCP tools**

Add service helper:

- `search_ops_from_config(config_path: Path | None, query: str, filters: dict | None, limit: int | None) -> dict`

Update `create_mcp()` to expose:

- `validate_config(config_path: str | None = None, config: dict | None = None)`
- `index_project(config_path: str | None = None, repo_id: str | None = None, force: bool = False)`
- `search_ops(query: str, config_path: str | None = None, filters: dict | None = None, limit: int | None = None)`

Errors should be JSON-serializable and structured; avoid raw tracebacks in normal invalid-config cases.

**Step 4: Verify GREEN**

Run MCP tool tests.

---

### Task 4: Add CLI `search-ops` and final compatibility validation

**Objective:** Exercise the same service through CLI and keep prior spike compatibility.

**Files:**
- Modify: `src/project_knowledge_mcp/server.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md` if command docs become misleading

**Step 1: Write failing test**

Add CLI test for:

```bash
project-knowledge search-ops "evidence packets" --config /tmp/project.yaml --limit 3
```

Assert it returns the same shape as MCP `search_ops`, including `markdown`.

**Step 2: Run RED**

Expected: FAIL because `search-ops` command does not exist.

**Step 3: Implement command**

Add `search-ops` CLI command. Preserve `search-index` direct-state command from Step 1.

**Step 4: Verify all surfaces**

Run:

```bash
$VENV/bin/python -m pytest tests/test_config.py tests/test_cli.py tests/test_mcp_tools.py -q
$VENV/bin/python -m pytest -q
$VENV/bin/ruff format --check .
$VENV/bin/ruff check .
$VENV/bin/project-knowledge --help
$VENV/bin/project-knowledge validate-config --config /tmp/pkmcp-step2/project.yaml
$VENV/bin/project-knowledge index-project --config /tmp/pkmcp-step2/project.yaml
$VENV/bin/project-knowledge search-ops "SQLite FTS5" --config /tmp/pkmcp-step2/project.yaml --limit 3
$VENV/bin/python spikes/001-local-retrieval-markdown/run_spike.py
$VENV/bin/python spikes/002-fastmcp-transports/run_spike.py
```

---

## Review Gates

After implementation and before commit:

1. Spec compliance review checks this plan plus spec sections 5.1-5.3, 7.8, 9.1, 10.1, 10.3, and 10.4.
2. Code quality review checks config validation, path safety, structured error handling, CLI/MCP shape consistency, and no accidental cloud/LLM dependency.
3. Controller verifies subagent claims with direct commands before commit.

## Commit / PR

Commit message:

```text
feat: add config-backed MCP indexing and search tools
```

PR body must include:

- Summary of Step 2 capability.
- Exact verification commands and results.
- Note that full session briefs, staleness checks, write tools, and CodeGraph adapter remain out of scope.
- CI status: checks passed or no checks configured.
