# Decision 0002: Workspace mode is the default repo source for local development

Status: accepted-for-mvp-planning  
Date: 2026-06-10

## Context

Project Knowledge MCP runs inside Docker for portability and dependency isolation. A tempting implementation shortcut is to let the container clone the configured repositories internally and then index those container-owned clones.

That is acceptable for deterministic snapshot analysis, but it is unsafe as the default local development model. If a human or agent edits a host working tree and PKMCP indexes a separate container clone, MCP answers can silently ignore uncommitted edits. That creates false confidence before a PR or commit exists—the exact workflow where local MCP context is supposed to help.

Example failure mode:

1. A developer edits `src/foo.ts` in their host repo.
2. They ask an MCP-capable assistant where a function lives or whether a code path still exists.
3. PKMCP answers from the container clone rather than the active host working tree.
4. The answer omits the uncommitted local change.
5. The mismatch only becomes obvious after commit/PR synchronization.

## Decision

PKMCP has two explicit repository source modes:

1. **Workspace mode** — default for local development and active human/agent work.
   - The canonical repo lives on the host.
   - Docker receives the repo through a bind mount.
   - PKMCP indexes the mounted path inside the container.
   - Uncommitted local edits, dirty state, and untracked files are visible to freshness/staleness checks and indexing.

2. **Snapshot mode** — opt-in for CI, PR review, demos, and deterministic remote analysis.
   - The container or setup process may clone a repo/ref/PR into container-managed storage.
   - The indexed source is a named Git ref/commit snapshot.
   - Uncommitted host changes are not included.
   - Responses must disclose that the source is a snapshot.

Workspace mode is the MVP default whenever the user selects an existing local repo or an agent/human is expected to edit files. Snapshot mode must never be presented as if it reflects the user's active working tree.

## Required behavior

Every configured repo and every evidence packet/staleness response must carry enough source metadata to prevent ambiguity:

- `source_mode`: `workspace` or `snapshot`
- `host_path` when known/applicable
- `container_path`
- `git_ref`/branch
- `head_commit`
- `dirty`
- `untracked_count`
- `last_indexed_commit`
- `last_indexed_at`
- `includes_uncommitted_changes`
- warning when the index was built from a different commit/content hash than the current worktree

For workspace repos, `includes_uncommitted_changes` should be `true` when the index was built from the current mounted filesystem content, even if the repo is dirty. For snapshot repos, it must be `false` unless the snapshot source explicitly includes those changes.

## Consequences

- Setup must generate Docker bind mounts for selected local repos by default.
- Config must make repository source mode explicit rather than relying on path conventions.
- Container-internal clone support remains useful, but only as explicit snapshot mode.
- MCP responses must be honest about whether they reflect the active workspace or a pinned snapshot.
- Tests should cover the stale-clone failure mode: a host-only uncommitted change must be visible in workspace mode and absent-but-disclosed in snapshot mode.

## Follow-ups

- Update the MVP spec and setup docs to name workspace mode as the default.
- Add config validation for `source_mode`, `container_path`, and snapshot provenance fields.
- Extend staleness/index metadata to report whether uncommitted changes are included.
