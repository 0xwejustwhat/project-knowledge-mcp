# 001: local retrieval over Markdown/frontmatter

## Question

Given the ops spec requires no-key local retrieval and the kickoff asked for SQLite FTS5/BM25 + Markdown/text parsing, can Step 0 prove both:

1. spec-preferred LlamaIndex BM25 retrieval can run with LLM/embeddings disabled; and
2. SQLite FTS5 can remain a deterministic metadata/traceability/text-search fallback?

## Approach

`run_spike.py` creates fixture Markdown documents with YAML frontmatter, parses metadata/headings, indexes them into:

- LlamaIndex `BM25Retriever` with `Settings.llm = None` and `Settings.embed_model = None`;
- a local SQLite FTS5 table using `bm25(docs_fts)` and snippet output.

It persists and reloads the LlamaIndex BM25 retriever under `.tmp/llamaindex_bm25`.

## Run

```bash
poetry run python spikes/001-local-retrieval-markdown/run_spike.py
```

## Verdict: VALIDATED

### What worked

- Markdown frontmatter and headings are parsed deterministically.
- LlamaIndex BM25 can retrieve fixture docs with LLM and embeddings explicitly disabled.
- BM25 retriever persistence/reload works for a local directory.
- SQLite FTS5/BM25 works as a fallback exact-text provider and traceable result store.

### What did not work / limitations

- SQLite FTS5 is not the primary retrieval framework under the current ops spec; it is fallback/bookkeeping unless LlamaIndex fails later.
- Metadata filtering is adapter-sensitive; the MVP should wrap retrieval and apply deterministic post-filters/ranking even if provider-native filters are limited.

### Recommendation for the real build

Use LlamaIndex BM25 as the document retrieval provider boundary for MVP Step 1, keep SQLite for metadata, freshness, authority registry, traceability, and fallback text search.
