# Project Knowledge MCP

Local-first Model Context Protocol server spike for repo-grounded project knowledge.

The Step 0 branch is intentionally a provider-evaluation gate, not the full MVP. It is aligned to the current ops spec in `0xwejustwhat/Project-Knowledge-MCP-ops`:

- Python 3.12 + Poetry project scaffold.
- No-key local document retrieval spike over Markdown/frontmatter.
- SQLite FTS5/BM25 fallback spike for metadata/traceability and direct text search.
- FastMCP stdio/http transport smoke spike.
- CodeGraphContext no-key reliability spike.
- Decision record before main feature implementation.

## Quick start

```bash
poetry install --with dev
poetry run pytest
poetry run python spikes/001-local-retrieval-markdown/run_spike.py
poetry run python spikes/002-fastmcp-transports/run_spike.py
poetry run python spikes/003-codegraphcontext-no-key/run_spike.py
```

## Minimal server smoke

```bash
poetry run project-knowledge --transport stdio
poetry run project-knowledge --transport http --host 127.0.0.1 --port 8765
```

MVP doctrine: the repo is memory; this MCP server is the deterministic access layer. It returns evidence packets and metadata. The connected assistant does synthesis.
