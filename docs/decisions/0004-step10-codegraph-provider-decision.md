# Decision 0004: Step 10 CodeGraph provider decision

Status: accepted-for-step-10a  
Date: 2026-06-13

## Context

Step 10A requires codebase context to prefer a real code-structure provider rather than silently treating text search as the intended path. The provider must remain local-first, no-key, Docker-compatible, and shielded behind the stable Project Knowledge MCP `CodeResult` contract.

Decision 0001 accepted CodeGraphContext as the first candidate with a caveat: its CLI output was human/Rich-oriented and needed adapter shielding before public MCP responses could use it. This decision records the refreshed Step 10A evaluation and implementation boundary.

## Decision

Accept `codegraphcontext==0.4.17` as the Step 10A graph provider candidate behind the internal `CodeGraphContextProvider` adapter.

The MCP server will:

1. keep `code_context.provider: codegraph` as the preferred code provider;
2. index configured `work` repos into a local KuzuDB graph under the configured state directory;
3. write a Project Knowledge provenance record beside the graph index;
4. report `active_provider: "codegraph"` only when package availability, graph repository paths, text-index readiness, and worktree provenance all validate;
5. normalize graph rows into stable public `CodeResult` records;
6. keep provider-specific Kuzu/CodeGraph labels, rows, and raw payloads internal;
7. fail soft to text fallback with explicit warnings when the provider is absent or unhealthy, unless fallback is disabled;
8. fail closed when the Project Knowledge text index is missing, corrupt, stale, or scope/provenance mismatched, even if a graph database exists.

## Evidence

| Question | Finding | Evidence |
|---|---|---|
| Can CodeGraphContext install cleanly in canonical Python runtime? | Yes for local Python 3.12. | `uv pip install 'codegraphcontext==0.4.17'` succeeded during the Step 10A spike; the package is also present in `poetry.lock` as optional extra `codegraph`. |
| Can it index fixture repos without credentials? | Yes. | Test fixture indexing completed with no API keys, using local KuzuDB and `CGC_RUNTIME_DB_TYPE=kuzudb`. |
| Can vector/embedding modes be disabled? | Yes for this adapter path. | Config uses `code_context.codegraph.vector_resolve_enabled: false`; graph queries use deterministic Cypher over KuzuDB rather than embedding/vector resolution. |
| Does it expose machine-readable query output? | Yes through the package database driver/Kuzu session, not through CLI text output. | Adapter queries Kuzu node/relationship tables (`Repository`, `Function`, `Class`, `Variable`, `CALLS`) and normalizes rows internally. |
| Can it return files, symbols, relationships, tests, and schemas? | Partially sufficient for MVP. | Function/class/variable rows provide path, line range, source/docstring, and function context; `CALLS` relationships are used where present; schema/test relationships are supplemented from the Project Knowledge text index. |
| What languages are supported well enough? | Python is sufficient for the current MVP fixture and expected near-term repo use. | Fixture indexing returns Python class/function/test symbols including class context such as `ExampleService.compile_context`. Broader language coverage remains provider-dependent and should be tested before claiming support. |
| How does it fail? | Missing package, missing graph DB, Kuzu query/schema errors, stale repo path/fingerprint, and corrupt text index are recoverable or fail-closed depending on config. | Tests cover healthy provider, provider query failure fallback, missing package fallback, disabled fallback, invalid query, stale index, and corrupt-index fail-closed behavior. |
| If rejected, what replacement is selected? | Not rejected for Step 10A. | Replacement selection is deferred unless Docker or real-repo evidence later reveals a blocker. |

## Implementation notes

- `CodeGraphContextProvider` uses CodeGraphContext only as an internal graph/indexing engine.
- The public response shape remains `repo_id`, `path`, `start_line`, `end_line`, `symbol`, `kind`, `snippet`, `provider`, `score`, and `related`.
- `Variable` graph nodes in CodeGraphContext 0.4.17 do not expose `end_line`; the adapter maps their end line to `line_number`.
- `Function` graph nodes expose class context separately; the adapter qualifies method symbols as `ClassName.method_name` where available.
- Graph health is deliberately tied to Project Knowledge text-index readiness because related schema/test evidence and freshness gates depend on the text index.
- `.cgcignore` and configured state directories are excluded from worktree fingerprinting so provider-generated state does not invalidate a just-built index.

## Verification

Local targeted verification:

```text
uv run pytest tests/test_code_context.py -q
24 passed
```

Docker verification is part of the Step 10A release gate. The image installs the `codegraph` extra by default (`python -m pip install '.[codegraph]'`) so containerized runtime includes CodeGraphContext rather than silently shipping fallback-only behavior.

## Risks and follow-ups

- CodeGraphContext schema is provider-owned. Future provider versions may rename labels/properties; adapter tests should pin expected behavior and fail clearly.
- Relationship coverage is still shallow. Tests/schemas are supplemented from the text index today; deeper graph-native relationships can be added after more real-repo evidence.
- Docker build/run evidence should be refreshed before merging any release candidate.
- Broader language support should be documented from measured fixture evidence rather than assumed from provider marketing claims.
