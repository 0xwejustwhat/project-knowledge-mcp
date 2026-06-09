# 001: local retrieval over Markdown/text with SQLite FTS5

## Question

Given the Step 0 direction is local-only/no-key retrieval, can PKMCP prove the default MVP path using:

1. deterministic Markdown/frontmatter parsing;
2. deterministic plain-text parsing; and
3. SQLite FTS5 with BM25 scoring as the baseline retrieval primitive?

## Approach

`run_spike.py` loads Markdown and `.txt` fixtures, parses metadata/headings locally, writes them to a normal SQLite metadata table plus an FTS5 virtual table, and queries with `bm25(docs_fts)` and snippet output.

No LlamaIndex, LlamaParse, embeddings, local model, hosted model, or API key is used.

## Run

```bash
poetry run python spikes/001-local-retrieval-markdown/run_spike.py
```

## Verdict: VALIDATED

### What worked

- Markdown frontmatter and headings are parsed deterministically.
- Plain text files are parsed deterministically.
- SQLite FTS5/BM25 retrieves Markdown and text fixtures without keys.
- SQLite joins retrieval hits back to metadata, allowing deterministic status/authority filters outside provider scoring.

### What did not work / limitations

- FTS5/BM25 is only a baseline retrieval primitive. It is not the product truth layer by itself.
- Important MVP tools still need typed evidence compilers, authority-aware ranking, supersession handling, stale-index warnings, and retrieval regression tests.

### Recommendation for the real build

Use SQLite FTS5/BM25 as the default no-key document retrieval primitive for Step 1. Keep provider boundaries so LlamaIndex or other retrieval providers can be evaluated later, but do not include them in the default MVP dependency path.
