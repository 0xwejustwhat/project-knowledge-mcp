# Step 3 Git Staleness and Source Status Implementation Plan

> **For Hermes:** Keep this as a narrow serial slice. Use TDD and verify the CLI/MCP surfaces directly before PR.

**Goal:** Add deterministic Git freshness/source-status reporting for configured repos via a shared service exposed as MCP `check_project_staleness` and CLI `check-project-staleness`.

**Why this step:** Step 2 persisted source-mode metadata in config/index/search. The next durable boundary is answering whether configured evidence is fresh enough to rely on before broader evidence-packet work.

**Spec coverage:** Story 8 and spec §4.1.1: report source mode, host/container paths, branch, HEAD, remote tracking, ahead/behind, dirty state, untracked count, last indexed commit, whether uncommitted changes are included, and whether reindex is needed. Remote lookup failure should warn, not fail the whole tool.

## Acceptance Criteria

1. `check_project_staleness(config_path?)` validates config and returns structured JSON for every configured repo.
2. Each repo status includes `repo_id`, `role`, `source_mode`, `host_path`, `path`, `branch`, `head_commit`, `remote_tracking_branch`, `remote_head_commit`, `ahead_count`, `behind_count`, `dirty`, `untracked_count`, `last_indexed_commit`, `last_indexed_at`, `includes_uncommitted_changes`, and `reindex_needed`.
3. Remote/fetch problems are represented as repo-level warnings and do not make the whole tool fail.
4. Snapshot-mode repos report `includes_uncommitted_changes: false` and carry `snapshot_ref`/`snapshot_commit` provenance.
5. CLI and MCP call the same service-layer function.
6. Existing Step 0/1/2 tests and smoke paths remain compatible.

## Non-Goals

- Do not implement file watchers or scheduled reconciliation.
- Do not auto-fetch remotes as a side effect; inspect local Git tracking refs only.
- Do not build full session briefs or evidence packets.
- Do not implement note capture/write-policy tools.

## TDD Tasks

### Task 1: MCP staleness shape test

Create a fixture Git repo with a committed baseline, run `index_project`, then modify a tracked file and add an untracked file. Assert `check_project_staleness` reports dirty/untracked counts, last indexed commit, `includes_uncommitted_changes: true`, and `reindex_needed: true`.

### Task 2: CLI staleness shape test

Exercise `project-knowledge check-project-staleness --config ...` and assert the CLI JSON matches the same service shape.

### Task 3: Snapshot/source-mode regression

Create a snapshot-mode config with `snapshot_ref` and `snapshot_commit`; assert status carries those fields and `includes_uncommitted_changes: false`.

### Task 4: Implementation

Add a Git-status helper in `services.py` using subprocess with short timeouts. Read last indexed commit/time from `ProjectIndex` metadata when the SQLite index exists. Expose through MCP/CLI in `server.py`.

## Verification

Run:

```bash
poetry run pytest tests/test_mcp_tools.py tests/test_cli.py -q
poetry run pytest -q
poetry run ruff format --check .
poetry run ruff check .
poetry run project-knowledge check-project-staleness --config <fixture-project.yaml>
```

Open a PR only after these pass or report exact blockers.
