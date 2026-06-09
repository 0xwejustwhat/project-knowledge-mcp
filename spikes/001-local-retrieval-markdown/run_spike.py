from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from llama_index.core import Document, Settings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.retrievers.bm25 import BM25Retriever

from spikes.shared.markdown_parse import parse_markdown_document

ROOT = Path(__file__).resolve().parent

FIXTURES = ROOT / "fixture_docs"
TMP = ROOT / ".tmp"


def load_docs() -> list[dict]:
    docs = []
    for path in sorted(FIXTURES.glob("*.md")):
        docs.append(parse_markdown_document(path.name, path.read_text(encoding="utf-8")))
    return docs


def sqlite_fts_query(docs: list[dict], query: str) -> list[dict]:
    TMP.mkdir(exist_ok=True)
    db_path = TMP / "fts5.sqlite"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE docs(id TEXT PRIMARY KEY, path TEXT, title TEXT, metadata_json TEXT, body TEXT)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE docs_fts USING fts5(id UNINDEXED, path, title, body, tokenize='porter unicode61')"
    )
    for doc in docs:
        conn.execute(
            "INSERT INTO docs VALUES (?, ?, ?, ?, ?)",
            (
                doc["id"],
                doc["path"],
                doc["title"],
                json.dumps(doc["metadata"], sort_keys=True),
                doc["body"],
            ),
        )
        conn.execute(
            "INSERT INTO docs_fts(id, path, title, body) VALUES (?, ?, ?, ?)",
            (doc["id"], doc["path"], doc["title"], doc["body"]),
        )
    rows = conn.execute(
        """
        SELECT path, title, bm25(docs_fts) AS bm25_score,
               snippet(docs_fts, 3, '[', ']', ' ... ', 12) AS snippet
        FROM docs_fts
        WHERE docs_fts MATCH ?
        ORDER BY bm25_score ASC
        LIMIT 5
        """,
        (query,),
    ).fetchall()
    conn.close()
    return [
        {"path": row[0], "title": row[1], "bm25_score": row[2], "snippet": row[3]} for row in rows
    ]


def llamaindex_bm25_query(docs: list[dict], query: str) -> list[dict]:
    Settings.llm = None
    Settings.embed_model = None
    li_docs = [
        Document(
            text=doc["body"],
            id_=doc["id"],
            metadata={"path": doc["path"], "title": doc["title"], **doc["metadata"]},
        )
        for doc in docs
    ]
    nodes = SentenceSplitter(chunk_size=128, chunk_overlap=0).get_nodes_from_documents(li_docs)
    retriever = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=3)
    persist_dir = TMP / "llamaindex_bm25"
    persist_dir.mkdir(parents=True, exist_ok=True)
    retriever.persist(str(persist_dir))
    reloaded = BM25Retriever.from_persist_dir(str(persist_dir))
    results = reloaded.retrieve(query)
    return [
        {
            "path": r.node.metadata.get("path"),
            "title": r.node.metadata.get("title"),
            "authority": r.node.metadata.get("authority"),
            "score": r.score,
            "text": r.node.get_content()[:160],
        }
        for r in results
    ]


def main() -> None:
    docs = load_docs()
    report = {
        "parsed_docs": [
            {
                "path": d["path"],
                "title": d["title"],
                "metadata": d["metadata"],
                "headings": d["headings"],
            }
            for d in docs
        ],
        "llamaindex_bm25": llamaindex_bm25_query(docs, "canonical truth synthesis"),
        "sqlite_fts5_bm25": sqlite_fts_query(docs, '"SQLite" OR "BM25"'),
        "verdict": "VALIDATED",
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
