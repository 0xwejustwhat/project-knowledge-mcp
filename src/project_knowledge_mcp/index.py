from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

STATE_DIR_NAME = ".project-knowledge"
INDEX_DB_NAME = "index.sqlite3"
SUPPORTED_SUFFIXES = {".md", ".mdx", ".txt"}

VALID_TYPES = {
    "doctrine",
    "decision",
    "discussion",
    "note",
    "open_question",
    "handover",
    "proposal",
    "evidence",
    "code_doc",
    "project_brief",
    "text",
    "code",
    "test",
    "schema",
}
VALID_STATUSES = {
    "current",
    "accepted",
    "draft",
    "captured",
    "open",
    "closed",
    "superseded",
    "rejected",
}
VALID_AUTHORITIES = {
    "implementation_truth",
    "canonical",
    "accepted_decision",
    "working",
    "capture",
    "historical",
    "superseded",
    "rejected",
}
STATUS_AUTHORITY = {
    "current": "canonical",
    "accepted": "accepted_decision",
    "draft": "working",
    "open": "working",
    "closed": "historical",
    "captured": "capture",
    "superseded": "superseded",
    "rejected": "rejected",
}
AUTHORITY_BOOST = {
    "implementation_truth": 0.35,
    "canonical": 0.30,
    "accepted_decision": 0.25,
    "working": 0.10,
    "capture": 0.00,
    "historical": -0.10,
    "superseded": -0.50,
    "rejected": -0.60,
}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class IndexWarning:
    event_type: str
    path: str
    message: str


@dataclass(frozen=True)
class ParsedChunk:
    id: str
    text: str
    heading_path: list[str]
    line_start: int
    line_end: int
    ordinal: int


@dataclass(frozen=True)
class ParsedDocument:
    id: str
    repo_id: str
    path: str
    parser: str
    title: str
    body: str
    doc_type: str
    status: str
    authority: str
    tags: list[str]
    raw_frontmatter: dict[str, Any]
    superseded_by: list[str]
    content_hash: str
    size_bytes: int
    chunks: list[ParsedChunk]
    warnings: list[IndexWarning]


@dataclass(frozen=True)
class IndexSummary:
    db_path: Path
    repo_id: str
    indexed_documents: int
    indexed_chunks: int
    warning_count: int
    skipped_documents: int = 0


@dataclass(frozen=True)
class SearchResult:
    repo_id: str
    source_mode: str
    includes_uncommitted_changes: bool
    snapshot_ref: str | None
    snapshot_commit: str | None
    path: str
    title: str
    doc_type: str
    status: str
    authority: str
    tags: list[str]
    superseded_by: list[str]
    chunk_id: str
    heading_path: list[str]
    line_start: int | None
    line_end: int | None
    snippet: str
    bm25_score: float
    relevance_score: float
    final_score: float


@dataclass(frozen=True)
class StoredIndexEvent:
    event_type: str
    repo_id: str | None
    path: str | None
    status: str
    message: str
    created_at: str


class ProjectIndex:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    @classmethod
    def open(cls, state_dir: Path) -> "ProjectIndex":
        db_path = _db_path(state_dir)
        if not db_path.exists():
            raise FileNotFoundError(f"Project index does not exist: {db_path}")
        conn = _connect(db_path)
        _ensure_repo_source_columns(conn)
        conn.commit()
        conn.close()
        return cls(db_path)

    def search(
        self,
        query: str,
        *,
        filters: dict[str, str] | None = None,
        include_superseded: bool = False,
        limit: int = 10,
    ) -> list[SearchResult]:
        filters = filters or {}
        fts_query = _quote_fts_query(query)
        where_clauses = ["chunks_fts MATCH ?"]
        params: list[Any] = [fts_query]

        if not include_superseded:
            where_clauses.append("documents.authority NOT IN ('superseded', 'rejected')")
            where_clauses.append("documents.status NOT IN ('superseded', 'rejected')")

        filter_columns = {
            "repo_id": "documents.repo_id",
            "type": "documents.doc_type",
            "doc_type": "documents.doc_type",
            "status": "documents.status",
            "authority": "documents.authority",
            "path": "documents.path",
        }
        for key, value in filters.items():
            if key not in filter_columns:
                raise ValueError(f"unsupported search filter: {key}")
            where_clauses.append(f"{filter_columns[key]} = ?")
            params.append(value)

        where_sql = " AND ".join(where_clauses)
        conn = _connect(self.db_path)
        rows = conn.execute(
            f"""
            SELECT documents.repo_id,
                   repos.source_mode,
                   repos.includes_uncommitted_changes,
                   repos.snapshot_ref,
                   repos.snapshot_commit,
                   documents.path,
                   documents.title,
                   documents.doc_type,
                   documents.status,
                   documents.authority,
                   documents.tags_json,
                   documents.superseded_by_json,
                   chunks.id,
                   chunks.heading_path_json,
                   chunks.start_line,
                   chunks.end_line,
                   snippet(chunks_fts, 0, '[', ']', ' ... ', 20) AS snippet,
                   bm25(chunks_fts) AS bm25_score
            FROM chunks_fts
            JOIN chunks ON chunks.rowid = chunks_fts.rowid
            JOIN documents ON documents.id = chunks.document_id
            JOIN repos ON repos.id = documents.repo_id
            WHERE {where_sql}
            LIMIT ?
            """,
            [*params, max(limit * 4, limit)],
        ).fetchall()
        conn.close()

        results = [_row_to_search_result(row, query) for row in rows]
        results.sort(key=lambda result: (-result.final_score, result.path, result.chunk_id))
        return results[:limit]

    def index_events(self, *, event_type: str | None = None) -> list[StoredIndexEvent]:
        conn = _connect(self.db_path)
        if event_type is None:
            rows = conn.execute(
                """
                SELECT event_type, repo_id, path, status, message, created_at
                FROM index_events
                ORDER BY created_at ASC, path ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT event_type, repo_id, path, status, message, created_at
                FROM index_events
                WHERE event_type = ?
                ORDER BY created_at ASC, path ASC
                """,
                (event_type,),
            ).fetchall()
        conn.close()
        return [StoredIndexEvent(*row) for row in rows]


def index_repo(
    repo_path: Path,
    *,
    state_dir: Path | None = None,
    repo_id: str,
    role: str,
    writable: bool = False,
    source_mode: str = "workspace",
    host_path: Path | None = None,
    includes_uncommitted_changes: bool | None = None,
    snapshot_ref: str | None = None,
    snapshot_commit: str | None = None,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    max_file_bytes: int | None = None,
) -> IndexSummary:
    repo_path = repo_path.resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise FileNotFoundError(f"repo path does not exist or is not a directory: {repo_path}")

    state_dir = state_dir or repo_path / STATE_DIR_NAME
    db_path = _db_path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    conn = _connect(db_path)
    _create_schema(conn)
    _clear_repo(conn, repo_id)

    now = _now()
    conn.execute(
        """
        INSERT INTO repos(
          id, role, name, source_mode, host_path, path, writable,
          includes_uncommitted_changes, snapshot_ref, snapshot_commit, last_indexed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          role = excluded.role,
          name = excluded.name,
          source_mode = excluded.source_mode,
          host_path = excluded.host_path,
          path = excluded.path,
          writable = excluded.writable,
          includes_uncommitted_changes = excluded.includes_uncommitted_changes,
          snapshot_ref = excluded.snapshot_ref,
          snapshot_commit = excluded.snapshot_commit,
          last_indexed_at = excluded.last_indexed_at
        """,
        (
            repo_id,
            role,
            repo_path.name,
            source_mode,
            str(host_path) if host_path is not None else None,
            str(repo_path),
            int(writable),
            int(
                includes_uncommitted_changes
                if includes_uncommitted_changes is not None
                else source_mode == "workspace"
            ),
            snapshot_ref,
            snapshot_commit,
            now,
        ),
    )

    indexed_docs = 0
    indexed_chunks = 0
    warning_count = 0
    skipped_docs = 0
    for file_path in _iter_indexable_files(
        repo_path,
        state_dir=state_dir,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        max_file_bytes=max_file_bytes,
    ):
        relative_path = file_path.relative_to(repo_path).as_posix()
        try:
            stat = file_path.stat()
        except OSError:
            skipped_docs += 1
            continue
        if max_file_bytes is not None and stat.st_size > max_file_bytes:
            skipped_docs += 1
            warning_count += 1
            _insert_index_event(
                conn,
                event_type="file_skipped",
                repo_id=repo_id,
                path=relative_path,
                status="warning",
                message=f"Skipped file larger than max_file_bytes ({stat.st_size} > {max_file_bytes})",
                created_at=now,
            )
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            skipped_docs += 1
            continue
        parsed = parse_document(relative_path, text, repo_id=repo_id, repo_role=role)
        _insert_document(conn, parsed, mtime=stat.st_mtime)
        indexed_docs += 1
        indexed_chunks += len(parsed.chunks)
        warning_count += len(parsed.warnings)

    conn.commit()
    conn.close()
    return IndexSummary(
        db_path=db_path,
        repo_id=repo_id,
        indexed_documents=indexed_docs,
        indexed_chunks=indexed_chunks,
        warning_count=warning_count,
        skipped_documents=skipped_docs,
    )


def parse_document(path: str, text: str, *, repo_id: str, repo_role: str) -> ParsedDocument:
    suffix = Path(path).suffix.lower()
    if suffix in {".md", ".mdx"}:
        parser = "markdown"
        raw_frontmatter, body = _split_frontmatter(path, text)
        headings = [match.group(2).strip() for match in _HEADING_RE.finditer(body)]
        title = str(raw_frontmatter.get("title") or (headings[0] if headings else Path(path).name))
    elif suffix == ".txt":
        parser = "text"
        raw_frontmatter = {}
        body = text
        headings = []
        title = next((line.strip() for line in text.splitlines() if line.strip()), Path(path).name)[
            :120
        ]
    else:
        raise ValueError(f"unsupported file type for indexing: {path}")

    body = body.strip()
    normalized, warnings = normalize_frontmatter(raw_frontmatter, path=path, repo_role=repo_role)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    document_id = hashlib.sha256(f"{repo_id}\n{path}".encode("utf-8")).hexdigest()[:24]
    chunk_id = hashlib.sha256(f"{document_id}\n0\n{content_hash}".encode("utf-8")).hexdigest()[:24]
    line_count = max(1, len(text.splitlines()))

    chunk = ParsedChunk(
        id=chunk_id,
        text=body,
        heading_path=headings[:1],
        line_start=1,
        line_end=line_count,
        ordinal=0,
    )
    return ParsedDocument(
        id=document_id,
        repo_id=repo_id,
        path=path,
        parser=parser,
        title=title,
        body=body,
        doc_type=normalized["type"],
        status=normalized["status"],
        authority=normalized["authority"],
        tags=normalized["tags"],
        raw_frontmatter=raw_frontmatter,
        superseded_by=normalized["superseded_by"],
        content_hash=content_hash,
        size_bytes=len(text.encode("utf-8")),
        chunks=[chunk],
        warnings=warnings,
    )


def normalize_frontmatter(
    frontmatter: dict[str, Any], *, path: str, repo_role: str
) -> tuple[dict[str, Any], list[IndexWarning]]:
    inferred_type, inferred_status, inferred_authority = _infer_from_path(path, repo_role)
    warnings: list[IndexWarning] = []

    raw_type = frontmatter.get("type")
    raw_status = frontmatter.get("status")
    raw_authority = frontmatter.get("authority")

    doc_type = str(raw_type) if raw_type in VALID_TYPES else inferred_type
    if raw_type is not None and raw_type not in VALID_TYPES:
        warnings.append(
            IndexWarning(
                event_type="frontmatter_normalization_warning",
                path=path,
                message=(
                    f"Unknown type {raw_type!r}; normalized using path rules as "
                    f"type='{doc_type}', status='{inferred_status}', authority='{inferred_authority}'."
                ),
            )
        )

    status = str(raw_status) if raw_status in VALID_STATUSES else inferred_status
    if raw_status is not None and raw_status not in VALID_STATUSES:
        warnings.append(
            IndexWarning(
                event_type="frontmatter_normalization_warning",
                path=path,
                message=(
                    f"Unknown status {raw_status!r}; normalized using path rules as "
                    f"status='{status}', authority='{inferred_authority}'."
                ),
            )
        )

    if raw_authority in VALID_AUTHORITIES:
        authority = str(raw_authority)
    elif raw_status in VALID_STATUSES:
        authority = STATUS_AUTHORITY[str(raw_status)]
    else:
        authority = inferred_authority

    if raw_authority is not None and raw_authority not in VALID_AUTHORITIES:
        warnings.append(
            IndexWarning(
                event_type="frontmatter_normalization_warning",
                path=path,
                message=(
                    f"Unknown authority {raw_authority!r}; normalized using path rules as "
                    f"status='{status}', authority='{authority}'."
                ),
            )
        )

    return (
        {
            "type": doc_type,
            "status": status,
            "authority": authority,
            "tags": _normalize_string_list(frontmatter.get("tags")),
            "superseded_by": _normalize_string_list(frontmatter.get("superseded_by")),
        },
        warnings,
    )


def _db_path(state_dir: Path) -> Path:
    return state_dir / INDEX_DB_NAME


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS repos (
          id TEXT PRIMARY KEY,
          role TEXT NOT NULL,
          name TEXT NOT NULL,
          source_mode TEXT NOT NULL DEFAULT 'workspace',
          host_path TEXT,
          path TEXT NOT NULL,
          writable INTEGER NOT NULL DEFAULT 0,
          current_branch TEXT,
          head_commit TEXT,
          remote_name TEXT,
          remote_branch TEXT,
          remote_head_commit TEXT,
          ahead_count INTEGER,
          behind_count INTEGER,
          dirty INTEGER,
          untracked_count INTEGER,
          includes_uncommitted_changes INTEGER NOT NULL DEFAULT 0,
          snapshot_ref TEXT,
          snapshot_commit TEXT,
          last_status_checked_at TEXT,
          last_indexed_at TEXT,
          last_indexed_commit TEXT
        );

        CREATE TABLE IF NOT EXISTS documents (
          id TEXT PRIMARY KEY,
          repo_id TEXT NOT NULL,
          path TEXT NOT NULL,
          parser TEXT NOT NULL,
          title TEXT,
          doc_type TEXT,
          status TEXT,
          authority TEXT,
          tags_json TEXT,
          frontmatter_json TEXT,
          superseded_by_json TEXT,
          git_commit TEXT,
          mtime REAL,
          size_bytes INTEGER,
          content_hash TEXT,
          skipped INTEGER NOT NULL DEFAULT 0,
          skip_reason TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(repo_id, path)
        );

        CREATE TABLE IF NOT EXISTS chunks (
          rowid INTEGER PRIMARY KEY,
          id TEXT UNIQUE NOT NULL,
          document_id TEXT NOT NULL,
          repo_id TEXT NOT NULL,
          path TEXT NOT NULL,
          heading_path_json TEXT,
          chunk_index INTEGER NOT NULL,
          start_line INTEGER,
          end_line INTEGER,
          page INTEGER,
          text TEXT NOT NULL,
          authority TEXT,
          doc_type TEXT,
          status TEXT,
          content_hash TEXT,
          FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
          text,
          content='chunks',
          content_rowid='rowid',
          tokenize='porter unicode61'
        );

        CREATE TABLE IF NOT EXISTS retrieval_events (
          id TEXT PRIMARY KEY,
          query TEXT NOT NULL,
          provider TEXT NOT NULL,
          strategy TEXT,
          result_count INTEGER NOT NULL,
          warnings_json TEXT,
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS index_events (
          id TEXT PRIMARY KEY,
          event_type TEXT NOT NULL,
          repo_id TEXT,
          path TEXT,
          status TEXT NOT NULL,
          message TEXT,
          created_at TEXT NOT NULL
        );
        """
    )
    _ensure_repo_source_columns(conn)


def _ensure_repo_source_columns(conn: sqlite3.Connection) -> None:
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(repos)").fetchall()}
    column_sql = {
        "source_mode": "ALTER TABLE repos ADD COLUMN source_mode TEXT NOT NULL DEFAULT 'workspace'",
        "host_path": "ALTER TABLE repos ADD COLUMN host_path TEXT",
        "includes_uncommitted_changes": (
            "ALTER TABLE repos ADD COLUMN includes_uncommitted_changes INTEGER NOT NULL DEFAULT 0"
        ),
        "snapshot_ref": "ALTER TABLE repos ADD COLUMN snapshot_ref TEXT",
        "snapshot_commit": "ALTER TABLE repos ADD COLUMN snapshot_commit TEXT",
    }
    for column, sql in column_sql.items():
        if column not in existing_columns:
            conn.execute(sql)


def _clear_repo(conn: sqlite3.Connection, repo_id: str) -> None:
    rowids = [
        row[0] for row in conn.execute("SELECT rowid FROM chunks WHERE repo_id = ?", (repo_id,))
    ]
    for rowid in rowids:
        conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (rowid,))
    conn.execute("DELETE FROM documents WHERE repo_id = ?", (repo_id,))
    conn.execute("DELETE FROM index_events WHERE repo_id = ?", (repo_id,))


def _insert_index_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    repo_id: str | None,
    path: str | None,
    status: str,
    message: str,
    created_at: str,
) -> None:
    event_id = hashlib.sha256(
        f"{repo_id}\n{path}\n{event_type}\n{message}".encode("utf-8")
    ).hexdigest()[:24]
    conn.execute(
        """
        INSERT INTO index_events(id, event_type, repo_id, path, status, message, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (event_id, event_type, repo_id, path, status, message, created_at),
    )


def _insert_document(conn: sqlite3.Connection, doc: ParsedDocument, *, mtime: float) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO documents(
          id, repo_id, path, parser, title, doc_type, status, authority,
          tags_json, frontmatter_json, superseded_by_json, mtime, size_bytes,
          content_hash, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc.id,
            doc.repo_id,
            doc.path,
            doc.parser,
            doc.title,
            doc.doc_type,
            doc.status,
            doc.authority,
            _json_dumps(doc.tags),
            _json_dumps(doc.raw_frontmatter),
            _json_dumps(doc.superseded_by),
            mtime,
            doc.size_bytes,
            doc.content_hash,
            now,
            now,
        ),
    )
    for chunk in doc.chunks:
        cursor = conn.execute(
            """
            INSERT INTO chunks(
              id, document_id, repo_id, path, heading_path_json, chunk_index,
              start_line, end_line, text, authority, doc_type, status, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk.id,
                doc.id,
                doc.repo_id,
                doc.path,
                json.dumps(chunk.heading_path),
                chunk.ordinal,
                chunk.line_start,
                chunk.line_end,
                chunk.text,
                doc.authority,
                doc.doc_type,
                doc.status,
                doc.content_hash,
            ),
        )
        rowid = cursor.lastrowid
        conn.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)", (rowid, chunk.text))

    for warning in doc.warnings:
        event_id = hashlib.sha256(
            f"{doc.repo_id}\n{doc.path}\n{warning.event_type}\n{warning.message}".encode("utf-8")
        ).hexdigest()[:24]
        conn.execute(
            """
            INSERT INTO index_events(id, event_type, repo_id, path, status, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                warning.event_type,
                doc.repo_id,
                warning.path,
                "warning",
                warning.message,
                now,
            ),
        )


def _iter_indexable_files(
    root: Path,
    *,
    state_dir: Path,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    max_file_bytes: int | None = None,
) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*"))
        if _is_indexable_file(
            root,
            path,
            state_dir=state_dir,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            max_file_bytes=max_file_bytes,
        )
    ]


def _is_indexable_file(
    root: Path,
    path: Path,
    *,
    state_dir: Path,
    include_globs: list[str] | None,
    exclude_globs: list[str] | None,
    max_file_bytes: int | None,
) -> bool:
    if path.is_symlink() or not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return False
    try:
        resolved_root = root.resolve()
        resolved_path = path.resolve()
        resolved_state_dir = state_dir.resolve()
        relative_path = resolved_path.relative_to(resolved_root)
    except (OSError, ValueError):
        return False
    try:
        resolved_path.relative_to(resolved_state_dir)
    except ValueError:
        pass
    else:
        return False
    relative = relative_path.as_posix()
    if ".git" in relative_path.parts or STATE_DIR_NAME in relative_path.parts:
        return False
    if _matches_any(relative, [".git/**", f"{STATE_DIR_NAME}/**", ".env", ".env.*"]):
        return False
    if any(
        "secret" in part.casefold() or "token" in part.casefold() for part in Path(relative).parts
    ):
        return False
    if include_globs and not _matches_any(relative, include_globs):
        return False
    if exclude_globs and _matches_any(relative, exclude_globs):
        return False
    return True


def _matches_any(relative_path: str, patterns: list[str]) -> bool:
    path = Path(relative_path)
    for pattern in patterns:
        if fnmatch.fnmatchcase(relative_path, pattern) or path.match(pattern):
            return True
        if "/**/" in pattern:
            direct_child_pattern = pattern.replace("/**/", "/")
            if fnmatch.fnmatchcase(relative_path, direct_child_pattern) or path.match(
                direct_child_pattern
            ):
                return True
    return False


def _split_frontmatter(path: str, text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    loaded = yaml.safe_load(match.group(1)) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"frontmatter in {path} must be a mapping")
    return loaded, text[match.end() :]


def _infer_from_path(path: str, repo_role: str) -> tuple[str, str, str]:
    path = path.strip("/")
    if repo_role == "work":
        if path.startswith("src/"):
            return "code", "current", "implementation_truth"
        if path.startswith("tests/"):
            return "test", "current", "implementation_truth"
        if path.startswith("schemas/"):
            return "schema", "current", "implementation_truth"
    if path.startswith(("docs/doctrine/", "doctrine/")):
        return "doctrine", "current", "canonical"
    if path.startswith(("docs/decisions/accepted/", "decisions/accepted/")):
        return "decision", "accepted", "accepted_decision"
    if path in {"docs/PRD.md", "project-brief.md"}:
        return "project_brief", "current", "canonical"
    if path.startswith(("docs/open-questions/", "open-questions/")):
        return "open_question", "open", "working"
    if path.startswith(("docs/proposals/", "proposals/")):
        return "proposal", "draft", "working"
    if path.startswith(("docs/handovers/", "handovers/")):
        return "handover", "draft", "working"
    if path.startswith(("docs/discussions/", "discussions/")):
        return "discussion", "captured", "capture"
    if path.startswith(("docs/notes/", "notes/", "inbox/")):
        return "note", "captured", "capture"
    if path.startswith(("docs/rejected/", "rejected-models/")):
        return "rejected_model", "rejected", "rejected"
    return "note" if repo_role == "ops" else "text", "draft", "working"


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _quote_fts_query(query: str) -> str:
    terms = re.findall(r"[\w.-]+", query)
    if not terms:
        raise ValueError("search query must contain at least one searchable term")
    return " ".join(f'"{term}"' for term in terms)


def _row_to_search_result(row: sqlite3.Row | tuple[Any, ...], original_query: str) -> SearchResult:
    bm25_score = float(row[17])
    relevance_score = 1.0 / (1.0 + abs(bm25_score))
    authority = str(row[9] or "working")
    final_score = relevance_score + AUTHORITY_BOOST.get(authority, 0.0)
    if _path_or_title_exact_match(original_query, path=str(row[5]), title=str(row[6])):
        final_score += 0.20
    if authority in {"superseded", "rejected"}:
        final_score -= 0.50

    return SearchResult(
        repo_id=str(row[0]),
        source_mode=str(row[1] or "workspace"),
        includes_uncommitted_changes=bool(row[2]),
        snapshot_ref=str(row[3]) if row[3] is not None else None,
        snapshot_commit=str(row[4]) if row[4] is not None else None,
        path=str(row[5]),
        title=str(row[6] or row[5]),
        doc_type=str(row[7] or "text"),
        status=str(row[8] or "draft"),
        authority=authority,
        tags=json.loads(row[10] or "[]"),
        superseded_by=json.loads(row[11] or "[]"),
        chunk_id=str(row[12]),
        heading_path=json.loads(row[13] or "[]"),
        line_start=row[14],
        line_end=row[15],
        snippet=str(row[16] or ""),
        bm25_score=bm25_score,
        relevance_score=relevance_score,
        final_score=final_score,
    )


def _path_or_title_exact_match(query: str, *, path: str, title: str) -> bool:
    query_folded = query.casefold().strip()
    return query_folded in path.casefold() or query_folded in title.casefold()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
