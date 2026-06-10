# Step 7 Safe Writes and Authority Proposals Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Keep implementation serial for write-policy/authority boundaries; use subagents for independent implementation and review lanes only.

**Goal:** Implement Step 7 MCP/CLI/service support for safe low-authority capture writes, non-canonical draft artifacts, and caller-supplied authority-change branch/PR proposals.

**Architecture:** Add a narrow write-policy service layer in `services.py` backed by existing `ProjectKnowledgeConfig`, then expose it through FastMCP tools and Typer commands in `server.py`. Add a single-document indexing primitive in `index.py` so written notes/drafts become searchable without a full repo rescan. Keep authority-changing content caller-supplied only and route it through isolated Git branch/commit/optional PR plumbing without merge/promotion behavior.

**Tech Stack:** Python 3.12, Pydantic v2 config models, SQLite FTS5 index, Typer, FastMCP, pytest, ruff.

---

## Guardrails

- No direct writes to canonical/high-authority paths through capture or draft tools.
- No arbitrary shell execution; Git/GitHub invocations must use argument lists only.
- `propose_authority_change` accepts caller-supplied file content only; it must not synthesize doctrine/decision content.
- No merge, approval, accepted-status promotion, force-push, or governance bypass.
- All requested paths must normalize under the configured writable ops repo root and reject traversal/symlink escapes.
- Written capture/draft files are indexed with `index_scope: "single_document"` when `auto_reindex_after_note_write` is enabled; never perform a full repo rescan inside write tools.

## Task 1 — Add single-document indexing support

**Objective:** Reuse existing parser/schema logic to upsert one newly written file into the SQLite index.

**Files:**
- Modify: `src/project_knowledge_mcp/index.py`
- Test: `tests/test_write_policy.py`

**Steps:**
1. Add an exported function such as `index_document(repo_path, relative_path, *, state_dir, repo_id, role, max_file_bytes=None) -> IndexSummary`.
2. It must create/open schema, remove any existing document/chunks/FTS rows for `(repo_id, relative_path)`, parse and insert that single file, and record warnings consistently with `index_repo`.
3. It must reject paths outside the repo, unsupported suffixes, symlinks, `.git`, state-dir, secret/token paths, oversized files, and unreadable/binary files.
4. Add a focused test proving a note written after a full index is searchable after single-document indexing, and that existing indexed docs are not cleared.

**Verification:**

```bash
poetry run pytest tests/test_write_policy.py -q
poetry run pytest tests/test_indexing.py -q
```

## Task 2 — Implement write-policy service functions

**Objective:** Add service functions for `add_project_note`, `create_draft_artifact`, and `propose_authority_change` with structured JSON responses.

**Files:**
- Modify: `src/project_knowledge_mcp/services.py`
- Test: `tests/test_write_policy.py`
- Test: `tests/test_authority_proposals.py`

**Steps:**
1. Implement helpers for slug/date-prefixed filenames, YAML frontmatter rendering, safe target normalization, blocked glob matching, configured proposal directory lookup, and structured blocked/error responses.
2. `add_project_note_from_config(title, body, type="note", tags=None, source=None, target=None, config_path=None)`:
   - require writable configured ops/capture repo;
   - default to `write_policy.default_capture_dir`;
   - block high-authority target globs and suggest `create_draft_artifact`/`propose_authority_change`;
   - write markdown with `authority: capture`, `status: captured`, `type`, `tags`, `source`, timestamps;
   - call single-document indexing when enabled and return `indexed`, `index_scope`, and warnings.
3. `create_draft_artifact_from_config(kind, title, body, source=None, tags=None, target=None, config_path=None)`:
   - support `open_question`, `doctrine_delta`, `adr_draft`, `decision_proposal`, `review_packet`, `handover`;
   - resolve default directory from `write_policy.proposal_dirs` with sensible built-in fallbacks;
   - ensure target remains inside proposal/draft dirs and not blocked canonical paths;
   - write non-canonical frontmatter (`authority: working`, proposal/open status) and single-document index.
4. `propose_authority_change_from_config(title, rationale, changes, source=None, tags=None, branch_name=None, config_path=None)`:
   - validate non-empty caller-supplied `changes` with operations `add_file` and `replace_file` only;
   - normalize changed paths under writable ops repo; reject traversal, symlink escapes, directories, and missing replacement targets;
   - create/switch to an isolated branch from current HEAD only when workspace is clean enough; fail closed if dirty;
   - write files, commit with authority-boundary metadata, optionally open a PR via `gh pr create` if `gh auth status` succeeds;
   - return `pr_opened`, `branch_prepared_pr_not_opened`, or structured error/manual next action; never merge or promote.

**Verification:**

```bash
poetry run pytest tests/test_write_policy.py tests/test_authority_proposals.py -q
```

## Task 3 — Expose MCP tools and Typer CLI commands

**Objective:** Make Step 7 functionality callable from MCP clients and CLI with MCP-compatible JSON output.

**Files:**
- Modify: `src/project_knowledge_mcp/server.py`
- Test: `tests/test_mcp_tools.py`
- Test: `tests/test_cli.py`

**Steps:**
1. Register FastMCP tools `add_project_note`, `create_draft_artifact`, and `propose_authority_change` that delegate directly to service functions.
2. Add CLI commands `add-project-note`, `create-draft-artifact`, and `propose-authority-change`.
3. For proposal changes in CLI, accept a JSON file/string argument containing the `changes` array to avoid ad-hoc content generation.
4. Keep JSON output shape aligned with MCP service output.

**Verification:**

```bash
poetry run pytest tests/test_mcp_tools.py tests/test_cli.py -q
```

## Task 4 — Integration, lint, and PR readiness

**Objective:** Prove Step 7 satisfies spec acceptance without regressions.

**Files:**
- Modify/add tests as needed under `tests/`

**Steps:**
1. Add acceptance tests proving:
   - low-authority note writes and single-document indexing;
   - blocked canonical direct writes include suggested draft and PR actions;
   - draft artifacts are constrained to proposal/draft dirs;
   - path traversal/symlink escape attempts fail closed;
   - authority proposals commit caller-supplied changes only and do not merge/promote;
   - dirty workspace proposal path fails closed.
2. Run full targeted Step 7 acceptance and existing suite.
3. Run ruff check.
4. Commit, push branch, open PR, and watch/report CI if checks exist.

**Verification:**

```bash
poetry run pytest tests/test_write_policy.py tests/test_authority_proposals.py -q
poetry run pytest -q
poetry run ruff check .
```
