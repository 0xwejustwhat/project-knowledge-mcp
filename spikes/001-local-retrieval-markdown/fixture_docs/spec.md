---
title: Retrieval Spec
authority: implementation_truth
status: draft_for_review
type: spec
tags: [retrieval, sqlite, fts5, bm25]
---
# Retrieval Spec

The MVP default retrieval primitive is SQLite FTS5 with BM25 scoring, deterministic metadata, path scoping, and authority-aware ranking outside the search provider.
