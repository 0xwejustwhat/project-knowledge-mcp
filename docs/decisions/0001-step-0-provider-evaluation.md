# Decision 0001: Step 0 provider evaluation

Status: accepted-for-mvp-planning  
Date: 2026-06-09

## Context

The current ops spec for Project Knowledge MCP requires a pre-implementation evaluation gate before main feature implementation. The relevant source of truth is `0xwejustwhat/Project-Knowledge-MCP-ops`, especially:

- `docs/specs/0001-mvp-implementation-spec.md`
- `docs/discussions/2026-06-09-llamaindex-codegraph-retrieval-decision.md`

Key constraints:

- Docker-anywhere, local-only/no-key default.
- MCP server is deterministic evidence access infrastructure, not a narrative analyst.
- LlamaIndex BM25/keyword retrieval is preferred for documents if no-key behavior holds.
- SQLite remains required for metadata, authority registry, freshness, traceability, and fallback text search.
- CodeGraph-style context is first-class for code repos if the reliability spike passes.
- FastMCP is the protocol substrate candidate for stdio/http.

## Evidence

| Area | Spike | Verdict | Evidence |
|---|---|---|---|
| Python/Poetry scaffold | root project files | Validated | `pyproject.toml`, package under `src/`, tests under `tests/` |
| Markdown + local retrieval | `spikes/001-local-retrieval-markdown` | Validated | Parses frontmatter/headings, queries LlamaIndex BM25 with LLM/embeddings disabled, persists/reloads retriever, queries SQLite FTS5/BM25 fallback |
| FastMCP transports | `spikes/002-fastmcp-transports` | Validated | Calls `health` over stdio subprocess and `validate_config` over loopback HTTP |
| CodeGraphContext | `spikes/003-codegraphcontext-no-key` | Partial | Installs and indexes/searches fixture repo with local KuzuDB and no API-key env; output shape is human-oriented and needs adapter shielding |

## Decision

Proceed to Step 1 with these boundaries:

1. Use FastMCP as the MCP server framework.
2. Use LlamaIndex BM25/keyword retrieval as the preferred document retrieval provider for the no-key MVP.
3. Keep SQLite as project metadata/authority/freshness/traceability store and fallback FTS5 text-search provider.
4. Include a `CodeContextProvider` boundary. Start with CodeGraphContext as the first provider, but hide provider-specific output behind stable internal result models.
5. Build fallback text code search for unhealthy/no-code cases, with explicit warnings rather than silent downgrades.
6. Do not implement final answer synthesis in the MCP server.

## Risks and follow-ups

- Build a Docker image and prove all selected packages install cleanly there.
- Validate LlamaIndex metadata filtering behavior on real ops docs, not only fixtures.
- Convert CodeGraphContext CLI/Rich output into stable adapter results or identify a better API surface.
- Add authority-aware ranking and stale-index warnings around provider scores.
