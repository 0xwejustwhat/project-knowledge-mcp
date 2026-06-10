from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

STATE_DIR_NAME = ".project-knowledge"
INDEX_DB_NAME = "index.sqlite3"
SUPPORTED_SUFFIXES = {".md", ".mdx", ".txt", ".py", ".json"}

VALID_TYPES = {
    "doctrine",
    "decision",
    "discussion",
    "note",
    "open_question",
    "handover",
    "proposal",
    "doctrine_delta",
    "adr_draft",
    "decision_proposal",
    "review_packet",
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
    "proposal",
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
    "proposal": 0.10,
    "working": 0.10,
    "capture": 0.00,
    "historical": -0.10,
    "superseded": -0.50,
    "rejected": -0.60,
}
AUTHORITY_RANK_BASE = {
    "implementation_truth": 4.0,
    "canonical": 3.0,
    "accepted_decision": 2.0,
    "proposal": 1.0,
    "working": 1.0,
    "capture": 0.0,
    "historical": -1.0,
    "superseded": -2.0,
    "rejected": -3.0,
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
    frontmatter: dict[str, Any]
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
                   documents.frontmatter_json,
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
            ORDER BY bm25(chunks_fts) ASC, documents.path ASC, chunks.id ASC
            LIMIT ?
            """,
            [*params, min(max(limit * 20, 100), 1000)],
        ).fetchall()
        conn.close()

        relevance_scores = _normalized_relevance_scores([float(row[18]) for row in rows])
        results = [
            _row_to_search_result(row, query, relevance_score=relevance_scores[index])
            for index, row in enumerate(rows)
        ]
        results.sort(key=lambda result: (-result.final_score, result.path, result.chunk_id))
        return results[:limit]

    def repo_metadata(self, repo_id: str) -> dict[str, Any] | None:
        conn = _connect(self.db_path)
        row = conn.execute(
            """
            SELECT path, role, source_mode, host_path, includes_uncommitted_changes,
                   snapshot_ref, snapshot_commit, head_commit, dirty, untracked_count,
                   last_indexed_commit, last_indexed_worktree_fingerprint,
                   index_scope_fingerprint
            FROM repos
            WHERE id = ?
            """,
            (repo_id,),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return {
            "path": row[0],
            "role": row[1],
            "source_mode": row[2],
            "host_path": row[3],
            "includes_uncommitted_changes": bool(row[4]),
            "snapshot_ref": row[5],
            "snapshot_commit": row[6],
            "head_commit": row[7],
            "dirty": bool(row[8]),
            "untracked_count": int(row[9] or 0),
            "last_indexed_commit": row[10],
            "last_indexed_worktree_fingerprint": row[11],
            "index_scope_fingerprint": row[12],
        }

    def repo_path(self, repo_id: str) -> Path | None:
        metadata = self.repo_metadata(repo_id)
        if metadata is None or metadata["path"] is None:
            return None
        return Path(str(metadata["path"]))

    def repo_matches_provenance(
        self,
        *,
        repo_id: str,
        role: str,
        path: Path,
        source_mode: str,
        host_path: Path | None,
        includes_uncommitted_changes: bool | None,
        snapshot_ref: str | None,
        snapshot_commit: str | None,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
        max_file_bytes: int | None,
    ) -> bool:
        metadata = self.repo_metadata(repo_id)
        if metadata is None or metadata["path"] is None:
            return False
        try:
            path_matches = Path(str(metadata["path"])).resolve() == path.resolve()
        except OSError:
            return False
        if not path_matches:
            return False
        indexed_host_path = metadata["host_path"]
        try:
            host_path_matches = (
                indexed_host_path is None
                if host_path is None
                else indexed_host_path is not None
                and Path(str(indexed_host_path)).resolve() == host_path.resolve()
            )
        except OSError:
            return False
        metadata_matches = (
            host_path_matches
            and metadata["role"] == role
            and metadata["source_mode"] == source_mode
            and metadata["includes_uncommitted_changes"] == bool(includes_uncommitted_changes)
            and metadata["snapshot_ref"] == snapshot_ref
            and metadata["snapshot_commit"] == snapshot_commit
            and metadata["index_scope_fingerprint"]
            == index_scope_fingerprint(
                role=role,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                max_file_bytes=max_file_bytes,
            )
        )
        if not metadata_matches:
            return False
        if source_mode == "snapshot":
            return (
                metadata["head_commit"] == snapshot_commit
                and not metadata["dirty"]
                and metadata["untracked_count"] == 0
            )
        return True

    def repo_matches_path(self, *, repo_id: str, path: Path) -> bool:
        indexed_path = self.repo_path(repo_id)
        if indexed_path is None:
            return False
        try:
            return indexed_path.resolve() == path.resolve()
        except OSError:
            return False

    def document_chunks(self, *, repo_id: str, path: str) -> list[dict[str, Any]]:
        conn = _connect(self.db_path)
        rows = conn.execute(
            """
            SELECT documents.doc_type,
                   documents.status,
                   documents.authority,
                   chunks.start_line,
                   chunks.end_line,
                   chunks.text
            FROM documents
            JOIN chunks ON chunks.document_id = documents.id
            WHERE documents.repo_id = ?
              AND documents.path = ?
              AND documents.authority NOT IN ('superseded', 'rejected')
              AND documents.status NOT IN ('superseded', 'rejected')
            ORDER BY chunks.chunk_index ASC
            """,
            (repo_id, path),
        ).fetchall()
        conn.close()
        return [
            {
                "doc_type": row[0],
                "status": row[1],
                "authority": row[2],
                "start_line": row[3],
                "end_line": row[4],
                "text": row[5],
            }
            for row in rows
        ]

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
    head_commit = _git_output(repo_path, "rev-parse", "HEAD")
    worktree_status = (
        _git_status_porcelain_strict(repo_path, state_dir=state_dir)
        if source_mode == "snapshot"
        else _git_status_porcelain(repo_path, state_dir=state_dir)
    )
    indexed_worktree_fingerprint = worktree_fingerprint(repo_path, worktree_status)
    scope_fingerprint = index_scope_fingerprint(
        role=role,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        max_file_bytes=max_file_bytes,
    )
    status_lines = [line for line in worktree_status.splitlines() if line]
    untracked_count = sum(1 for line in status_lines if line.startswith("??"))
    dirty = any(not line.startswith("??") for line in status_lines)
    effective_includes_uncommitted_changes = bool(
        includes_uncommitted_changes
        if includes_uncommitted_changes is not None
        else source_mode == "workspace"
    )
    if (
        source_mode != "snapshot"
        and not effective_includes_uncommitted_changes
        and (dirty or untracked_count)
    ):
        raise ValueError(
            f"repo {repo_id} has uncommitted changes but includes_uncommitted_changes is false"
        )
    if source_mode == "snapshot":
        if snapshot_commit and head_commit != snapshot_commit:
            raise ValueError(
                f"snapshot repo {repo_id} HEAD does not match snapshot_commit "
                f"({head_commit} != {snapshot_commit})"
            )
        if dirty or untracked_count:
            raise ValueError(f"snapshot repo {repo_id} must have a clean worktree before indexing")
    conn.execute(
        """
        INSERT INTO repos(
          id, role, name, source_mode, host_path, path, writable,
          current_branch, head_commit, dirty, untracked_count, includes_uncommitted_changes,
          snapshot_ref, snapshot_commit, last_indexed_at, last_indexed_commit,
          last_indexed_worktree_fingerprint, index_scope_fingerprint
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          role = excluded.role,
          name = excluded.name,
          source_mode = excluded.source_mode,
          host_path = excluded.host_path,
          path = excluded.path,
          writable = excluded.writable,
          current_branch = excluded.current_branch,
          head_commit = excluded.head_commit,
          dirty = excluded.dirty,
          untracked_count = excluded.untracked_count,
          includes_uncommitted_changes = excluded.includes_uncommitted_changes,
          snapshot_ref = excluded.snapshot_ref,
          snapshot_commit = excluded.snapshot_commit,
          last_indexed_at = excluded.last_indexed_at,
          last_indexed_commit = excluded.last_indexed_commit,
          last_indexed_worktree_fingerprint = excluded.last_indexed_worktree_fingerprint,
          index_scope_fingerprint = excluded.index_scope_fingerprint
        """,
        (
            repo_id,
            role,
            repo_path.name,
            source_mode,
            str(host_path) if host_path is not None else None,
            str(repo_path),
            int(writable),
            _git_output(repo_path, "rev-parse", "--abbrev-ref", "HEAD"),
            head_commit,
            int(dirty),
            untracked_count,
            int(effective_includes_uncommitted_changes),
            snapshot_ref,
            snapshot_commit,
            now,
            head_commit,
            indexed_worktree_fingerprint,
            scope_fingerprint,
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
        # Keep oversized candidates in the indexing loop so they produce skip
        # counters and warning events; direct file context passes max_file_bytes
        # to _is_indexable_file as a hard leak-prevention gate.
        max_file_bytes=None,
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


def index_document(
    repo_path: Path,
    relative_path: str | Path,
    *,
    state_dir: Path,
    repo_id: str,
    role: str,
    max_file_bytes: int | None = None,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
) -> IndexSummary:
    """Upsert one safe, indexable file into the SQLite index without clearing the repo."""
    repo_path = repo_path.resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise FileNotFoundError(f"repo path does not exist or is not a directory: {repo_path}")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"relative_path must stay under repo root: {relative_path}")
    candidate = repo_path / relative
    if candidate.is_symlink():
        raise ValueError(f"cannot index symlink: {relative_path}")
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"document path does not exist or is not a file: {relative_path}")

    state_dir = state_dir.resolve()
    relative_posix = relative.as_posix().strip("/")
    if not _is_indexable_file(
        repo_path,
        candidate,
        state_dir=state_dir,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        max_file_bytes=max_file_bytes,
    ):
        raise ValueError(f"document is not safe or supported for indexing: {relative_posix}")

    stat = candidate.stat()
    if max_file_bytes is not None and stat.st_size > max_file_bytes:
        raise ValueError(
            f"document exceeds max_file_bytes ({stat.st_size} > {max_file_bytes}): {relative_posix}"
        )
    try:
        text = candidate.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"document is not valid UTF-8 text: {relative_posix}") from exc
    except OSError as exc:
        raise ValueError(f"document is not readable: {relative_posix}") from exc

    db_path = _db_path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    _create_schema(conn)

    now = _now()
    head_commit = _git_output(repo_path, "rev-parse", "HEAD")
    worktree_status = _git_status_porcelain(repo_path, state_dir=state_dir)
    status_lines = [line for line in worktree_status.splitlines() if line]
    untracked_count = sum(1 for line in status_lines if line.startswith("??"))
    dirty = any(not line.startswith("??") for line in status_lines)
    conn.execute(
        """
        INSERT INTO repos(
          id, role, name, source_mode, path, writable,
          current_branch, head_commit, dirty, untracked_count,
          includes_uncommitted_changes, last_indexed_at, last_indexed_commit,
          last_indexed_worktree_fingerprint, index_scope_fingerprint
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          role = excluded.role,
          name = excluded.name,
          source_mode = excluded.source_mode,
          path = excluded.path,
          writable = excluded.writable,
          current_branch = excluded.current_branch,
          head_commit = excluded.head_commit,
          dirty = excluded.dirty,
          untracked_count = excluded.untracked_count,
          includes_uncommitted_changes = excluded.includes_uncommitted_changes,
          last_indexed_at = excluded.last_indexed_at,
          last_indexed_commit = excluded.last_indexed_commit,
          last_indexed_worktree_fingerprint = excluded.last_indexed_worktree_fingerprint,
          index_scope_fingerprint = excluded.index_scope_fingerprint
        """,
        (
            repo_id,
            role,
            repo_path.name,
            "workspace",
            str(repo_path),
            1,
            _git_output(repo_path, "rev-parse", "--abbrev-ref", "HEAD"),
            head_commit,
            int(dirty),
            untracked_count,
            1,
            now,
            head_commit,
            worktree_fingerprint(repo_path, worktree_status),
            index_scope_fingerprint(
                role=role,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                max_file_bytes=max_file_bytes,
            ),
        ),
    )
    _delete_document(conn, repo_id=repo_id, path=relative_posix)
    parsed = parse_document(relative_posix, text, repo_id=repo_id, repo_role=role)
    _insert_document(conn, parsed, mtime=stat.st_mtime)
    conn.commit()
    conn.close()
    return IndexSummary(
        db_path=db_path,
        repo_id=repo_id,
        indexed_documents=1,
        indexed_chunks=len(parsed.chunks),
        warning_count=len(parsed.warnings),
        skipped_documents=0,
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
    elif suffix in {".py", ".json"}:
        parser = "code" if suffix == ".py" else "json"
        raw_frontmatter = {}
        body = text
        headings = []
        title = Path(path).name
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
          last_indexed_commit TEXT,
          last_indexed_worktree_fingerprint TEXT,
          index_scope_fingerprint TEXT);


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
        "current_branch": "ALTER TABLE repos ADD COLUMN current_branch TEXT",
        "head_commit": "ALTER TABLE repos ADD COLUMN head_commit TEXT",
        "remote_name": "ALTER TABLE repos ADD COLUMN remote_name TEXT",
        "remote_branch": "ALTER TABLE repos ADD COLUMN remote_branch TEXT",
        "remote_head_commit": "ALTER TABLE repos ADD COLUMN remote_head_commit TEXT",
        "ahead_count": "ALTER TABLE repos ADD COLUMN ahead_count INTEGER",
        "behind_count": "ALTER TABLE repos ADD COLUMN behind_count INTEGER",
        "dirty": "ALTER TABLE repos ADD COLUMN dirty INTEGER",
        "untracked_count": "ALTER TABLE repos ADD COLUMN untracked_count INTEGER",
        "includes_uncommitted_changes": (
            "ALTER TABLE repos ADD COLUMN includes_uncommitted_changes INTEGER NOT NULL DEFAULT 0"
        ),
        "snapshot_ref": "ALTER TABLE repos ADD COLUMN snapshot_ref TEXT",
        "snapshot_commit": "ALTER TABLE repos ADD COLUMN snapshot_commit TEXT",
        "last_status_checked_at": "ALTER TABLE repos ADD COLUMN last_status_checked_at TEXT",
        "last_indexed_at": "ALTER TABLE repos ADD COLUMN last_indexed_at TEXT",
        "last_indexed_commit": "ALTER TABLE repos ADD COLUMN last_indexed_commit TEXT",
        "last_indexed_worktree_fingerprint": (
            "ALTER TABLE repos ADD COLUMN last_indexed_worktree_fingerprint TEXT"
        ),
        "index_scope_fingerprint": "ALTER TABLE repos ADD COLUMN index_scope_fingerprint TEXT",
    }
    for column, sql in column_sql.items():
        if column not in existing_columns:
            conn.execute(sql)


def _git_output(repo_path: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _git_status_porcelain_strict(repo_path: Path, *, state_dir: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain=v1", "--untracked-files=all"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        raise ValueError(f"git status failed for snapshot repo {repo_path}: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ValueError(f"git status failed for snapshot repo {repo_path}: {message}")
    return _filter_state_dir_status(repo_path, result.stdout, state_dir=state_dir)


def _git_status_porcelain(repo_path: Path, *, state_dir: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain=v1", "--untracked-files=all"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return _filter_state_dir_status(repo_path, result.stdout, state_dir=state_dir)


def index_scope_fingerprint(
    *,
    role: str,
    include_globs: list[str] | None,
    exclude_globs: list[str] | None,
    max_file_bytes: int | None,
) -> str:
    payload = {
        "role": role,
        "include_globs": sorted(include_globs or []),
        "exclude_globs": sorted(exclude_globs or []),
        "max_file_bytes": max_file_bytes,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def worktree_fingerprint(repo_path: Path, porcelain: str) -> str:
    digest = hashlib.sha256()
    digest.update(porcelain.encode("utf-8", errors="replace"))
    for line in porcelain.splitlines():
        relative = _status_relative_path(line)
        if not relative:
            continue
        path = repo_path / relative
        digest.update(relative.encode("utf-8", errors="replace"))
        if path.is_file() and not path.is_symlink():
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"<unreadable>")
    return digest.hexdigest()


def _filter_state_dir_status(repo_path: Path, porcelain: str, *, state_dir: Path | None) -> str:
    if state_dir is None:
        return porcelain
    try:
        state_relative = state_dir.resolve().relative_to(repo_path.resolve()).as_posix()
    except ValueError:
        return porcelain
    state_prefix = state_relative.rstrip("/") + "/"
    kept_lines: list[str] = []
    for line in porcelain.splitlines():
        relative = _status_relative_path(line)
        if relative == state_relative or (relative and relative.startswith(state_prefix)):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines) + ("\n" if kept_lines else "")


def _status_relative_path(line: str) -> str | None:
    if len(line) < 4:
        return None
    relative = line[3:]
    if " -> " in relative:
        relative = relative.rsplit(" -> ", 1)[1]
    return relative.strip('"') or None


def _clear_repo(conn: sqlite3.Connection, repo_id: str) -> None:
    rowids = [
        row[0] for row in conn.execute("SELECT rowid FROM chunks WHERE repo_id = ?", (repo_id,))
    ]
    for rowid in rowids:
        conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (rowid,))
    conn.execute("DELETE FROM documents WHERE repo_id = ?", (repo_id,))
    conn.execute("DELETE FROM index_events WHERE repo_id = ?", (repo_id,))


def _delete_document(conn: sqlite3.Connection, *, repo_id: str, path: str) -> None:
    rowids = [
        row[0]
        for row in conn.execute(
            "SELECT rowid FROM chunks WHERE repo_id = ? AND path = ?", (repo_id, path)
        )
    ]
    for rowid in rowids:
        conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (rowid,))
    conn.execute("DELETE FROM documents WHERE repo_id = ? AND path = ?", (repo_id, path))
    conn.execute("DELETE FROM index_events WHERE repo_id = ? AND path = ?", (repo_id, path))


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
    if max_file_bytes is not None:
        try:
            if path.stat().st_size > max_file_bytes:
                return False
        except OSError:
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


def _normalized_relevance_scores(bm25_scores: list[float]) -> list[float]:
    if not bm25_scores:
        return []
    best = min(bm25_scores)
    weakest = max(bm25_scores)
    if best == weakest:
        return [1.0 for _ in bm25_scores]
    span = weakest - best
    return [(weakest - score) / span for score in bm25_scores]


def _row_to_search_result(
    row: sqlite3.Row | tuple[Any, ...], original_query: str, *, relevance_score: float
) -> SearchResult:
    bm25_score = float(row[18])
    authority = str(row[9] or "working")
    final_score = (
        AUTHORITY_RANK_BASE.get(authority, AUTHORITY_RANK_BASE["working"])
        + (relevance_score * 0.10)
        + AUTHORITY_BOOST.get(authority, 0.0)
    )
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
        frontmatter=json.loads(row[12] or "{}"),
        chunk_id=str(row[13]),
        heading_path=json.loads(row[14] or "[]"),
        line_start=row[15],
        line_end=row[16],
        snippet=str(row[17] or ""),
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
