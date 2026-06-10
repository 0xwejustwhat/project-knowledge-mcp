# Spec Snapshot Policy

The Project Knowledge MCP ops repo is the canonical source for product/spec/governance documents.

This code repo carries a pinned implementation snapshot so implementation work has local, auditable context without depending on chat memory or ad hoc cross-repo lookup.

## Canonical source

```text
repo: 0xwejustwhat/Project-Knowledge-MCP-ops
path: docs/specs/0001-mvp-implementation-spec.md
```

## Snapshot rules

- `docs/specs/0001-mvp-implementation-spec.md` is not independent canon.
- The snapshot must include provenance frontmatter:
  - `canonical_source_repo`
  - `canonical_source_path`
  - `canonical_source_commit`
  - `canonical_source_pr` when applicable
  - `snapshot_status: mirrored_reference`
- If the ops spec changes, refresh this snapshot before making implementation claims that depend on the spec.
- Later PKMCP staleness checks should compare the snapshot provenance with the configured ops repo and warn when the code snapshot is behind.

## Why this exists

Step 0 reproduced the exact drift PKMCP is meant to prevent: a stale LlamaIndex-centered artifact was treated as current guidance after the SQLite/local-parser decision superseded it.

The snapshot gives implementation agents a local starting point, while the provenance fields preserve the authority chain back to ops.
