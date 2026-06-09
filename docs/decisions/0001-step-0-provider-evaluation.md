# Decision 0001: Step 0 provider evaluation

Status: accepted-for-mvp-planning  
Date: 2026-06-09

## Context

Step 0 validates the implementation substrate for Project Knowledge MCP before main feature work. The corrected source-of-truth direction is:

- Docker-anywhere, local-only/no-key default.
- MCP server is deterministic evidence access infrastructure, not a narrative analyst.
- SQLite FTS5/BM25 is the default document retrieval primitive.
- SQLite also stores metadata, authority registry inputs, freshness, traceability, and fallback/search evidence.
- Markdown and plain text parsing are local deterministic parser helpers for the MVP.
- CodeGraph-style context is first-class for code repositories if the reliability spike passes.
- FastMCP is the protocol substrate candidate for stdio/http.
- LlamaIndex/LlamaParse are not default MVP dependencies. LlamaIndex can be revisited later only behind an optional provider boundary; LlamaParse/LlamaCloud would be key/cloud-required and therefore not default.

## Evidence

| Area | Spike | Verdict | Evidence |
|---|---|---|---|
| Python/Poetry scaffold | root project files | Validated | `pyproject.toml`, package under `src/`, tests under `tests/` |
| Markdown/text + local retrieval | `spikes/001-local-retrieval-markdown` | Validated | Parses Markdown frontmatter/headings and plain text, indexes into SQLite metadata + FTS5 tables, queries with `bm25(docs_fts)`, and applies metadata filters |
| FastMCP transports | `spikes/002-fastmcp-transports` | Validated | Calls `health` over stdio subprocess and `validate_config` over loopback HTTP |
| CodeGraphContext | `spikes/003-codegraphcontext-no-key` | Partial | Installs and indexes/searches fixture repo with local KuzuDB and no API-key env; output shape is human-oriented and needs adapter shielding |

## Decision

Proceed to Step 1 with these boundaries:

1. Use FastMCP as the MCP server framework.
2. Use SQLite FTS5/BM25 as the default no-key document retrieval primitive.
3. Keep SQLite as project metadata/authority/freshness/traceability store.
4. Add local deterministic parser helpers. MVP default is Markdown/frontmatter plus plain text; richer local parsers can be added later by file type.
5. Include a `CodeContextProvider` boundary. Start with CodeGraphContext as the first code-repo provider, but hide provider-specific output behind stable internal result models.
6. Build fallback text code search for unhealthy/no-code cases, with explicit warnings rather than silent downgrades.
7. Do not implement final answer synthesis in the MCP server.
8. Do not add LlamaIndex, LlamaParse, LlamaCloud, embeddings, local models, or hosted-model dependencies to the default MVP path.

## Risks and follow-ups

- FTS5/BM25 is not the truth layer by itself. Step 1 must add typed evidence compilers, authority-aware ranking, supersession handling, stale-index warnings, and retrieval regression tests.
- Build a Docker image and prove all selected packages install cleanly there.
- Convert CodeGraphContext CLI/Rich output into stable adapter results or identify a better API surface.
- Keep provider boundaries narrow so optional retrieval providers can be evaluated later without contaminating the default local/no-key path.
