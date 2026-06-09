from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from spikes.shared.markdown_parse import parse_markdown_document
from spikes.shared.text_parse import parse_text_document

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixture_docs"
TMP = ROOT / ".tmp"


def load_docs() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for path in sorted(FIXTURES.iterdir()):
        if path.suffix == ".md":
            docs.append(parse_markdown_document(path.name, path.read_text(encoding="utf-8")))
        elif path.suffix == ".txt":
            docs.append(parse_text_document(path.name, path.read_text(encoding="utf-8")))
    return docs


def build_sqlite_index(docs: list[dict[str, Any]]) -> Path:
    TMP.mkdir(exist_ok=True)
    db_path = TMP / "fts5.sqlite"
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE docs(
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT,
            authority TEXT,
            metadata_json TEXT NOT NULL,
            body TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE docs_fts USING fts5(
            id UNINDEXED,
            path,
            title,
            body,
            tokenize='porter unicode61'
        )
        """
    )

    for doc in docs:
        metadata = doc["metadata"]
        conn.execute(
            "INSERT INTO docs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                doc["id"],
                doc["path"],
                doc["title"],
                metadata.get("status"),
                metadata.get("authority"),
                json.dumps(metadata, sort_keys=True),
                doc["body"],
            ),
        )
        conn.execute(
            "INSERT INTO docs_fts(id, path, title, body) VALUES (?, ?, ?, ?)",
            (doc["id"], doc["path"], doc["title"], doc["body"]),
        )

    conn.commit()
    conn.close()
    return db_path


def sqlite_fts_query(
    db_path: Path, query: str, *, status: str | None = None
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    params: list[Any] = [query]
    status_clause = ""
    if status is not None:
        status_clause = "AND docs.status = ?"
        params.append(status)

    rows = conn.execute(
        f"""
        SELECT docs.path,
               docs.title,
               docs.status,
               docs.authority,
               bm25(docs_fts) AS bm25_score,
               snippet(docs_fts, 3, '[', ']', ' ... ', 12) AS snippet
        FROM docs_fts
        JOIN docs ON docs.id = docs_fts.id
        WHERE docs_fts MATCH ?
        {status_clause}
        ORDER BY bm25_score ASC, docs.path ASC
        LIMIT 5
        """,
        params,
    ).fetchall()
    conn.close()
    return [
        {
            "path": row[0],
            "title": row[1],
            "status": row[2],
            "authority": row[3],
            "bm25_score": row[4],
            "snippet": row[5],
        }
        for row in rows
    ]


def main() -> None:
    docs = load_docs()
    db_path = build_sqlite_index(docs)
    retrieval_results = sqlite_fts_query(db_path, '"SQLite" OR "BM25"')
    accepted_boundary_results = sqlite_fts_query(db_path, "synthesis", status="accepted")

    assert any(result["path"] == "spec.md" for result in retrieval_results)
    assert any(result["path"] == "plain.txt" for result in retrieval_results)
    assert accepted_boundary_results and accepted_boundary_results[0]["path"] == "canonical.md"

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
        "sqlite_fts5_bm25": retrieval_results,
        "metadata_filtered_query": accepted_boundary_results,
        "index_path": str(db_path),
        "verdict": "VALIDATED",
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
