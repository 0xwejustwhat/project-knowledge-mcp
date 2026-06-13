from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from project_knowledge_mcp.config import ProjectKnowledgeConfig, RepoConfig
from project_knowledge_mcp.index import (
    ProjectIndex,
    SearchResult,
    _git_status_porcelain_strict,
    _is_indexable_file,
    worktree_fingerprint,
)

_CODE_DOC_TYPES = {"code", "test", "schema"}
_CODEGRAPH_NODE_LABELS = ("Function", "Class", "Variable")
_CODEGRAPH_DB_NAME = "kuzudb"
_CODEGRAPH_PROVENANCE_NAME = "project-knowledge-provenance.json"


@dataclass(frozen=True)
class CodeProviderHealth:
    configured_provider: str
    active_provider: str
    codegraph_enabled: bool
    codegraph_healthy: bool
    fallback_available: bool
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CodeResult:
    repo_id: str
    path: str
    start_line: int | None
    end_line: int | None
    symbol: str | None
    kind: str
    snippet: str
    provider: str
    score: float
    related: list[dict[str, str]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "symbol": self.symbol,
            "kind": self.kind,
            "snippet": self.snippet,
            "provider": self.provider,
            "score": self.score,
            "related": self.related,
        }


class CodeContextProvider(Protocol):
    def health(self) -> CodeProviderHealth: ...

    def search_code(self, query: str, repo_id: str | None, limit: int) -> list[CodeResult]: ...

    def get_code_context(
        self, symbol_or_file: str, repo_id: str | None, limit: int
    ) -> list[CodeResult]: ...


class CodeGraphContextProvider:
    """CodeGraphContext adapter with a stable public Project Knowledge shape.

    The CodeGraphContext package remains optional. When it is installed and the
    configured work repos have a fresh graph index, this adapter queries the
    local KuzuDB graph and normalizes provider output into the public
    ``CodeResult`` contract. Provider-specific rows, database labels, and raw
    query output never leave this module. If package/runtime/provenance health is
    not proven, callers can fail soft to the existing text fallback.
    """

    def __init__(self, config: ProjectKnowledgeConfig):
        self.config = config
        assert config.storage.state_dir is not None
        self.state_dir = config.storage.state_dir
        self.index_dir = config.code_context.codegraph.index_dir or self.state_dir / "codegraph"
        self.db_path = self.index_dir / _CODEGRAPH_DB_NAME
        self.provenance_path = self.index_dir / _CODEGRAPH_PROVENANCE_NAME

    def health(self) -> CodeProviderHealth:
        codegraph = self.config.code_context.codegraph
        work_repos = self._work_repos()
        installed = self._package_installed()
        warnings: list[str] = []
        indexed_repos: dict[str, str] = {}
        stale_repos: list[str] = []
        provenance = self._load_provenance()

        if not codegraph.enabled:
            warnings.append("CodeGraph is disabled by config; using text fallback.")
        elif not work_repos:
            warnings.append("No work repos are configured for code context.")
        elif not installed:
            warnings.append("CodeGraphContext package is not installed; using text fallback.")
        else:
            indexed_repos = self._indexed_repo_paths()
            for repo in work_repos:
                if indexed_repos.get(repo.id) != str(repo.path.resolve()):
                    stale_repos.append(repo.id)
                    continue
                if not self._repo_matches_provenance(repo, provenance.get(repo.id, {})):
                    stale_repos.append(repo.id)
            if stale_repos:
                warnings.append(
                    "CodeGraph index is missing or stale for work repo(s): "
                    + ", ".join(stale_repos)
                    + "; using text fallback."
                )

        text_index_ready = self._text_index_ready(work_repos)
        if not text_index_ready:
            stale_repos.extend(repo.id for repo in work_repos if repo.id not in stale_repos)
            warnings.append(
                "Project text index is missing or corrupt; code context is unavailable until reindexed."
            )

        healthy = bool(
            codegraph.enabled and installed and work_repos and not stale_repos and text_index_ready
        )
        return CodeProviderHealth(
            configured_provider=self.config.code_context.provider,
            active_provider="codegraph"
            if healthy
            else (
                self.config.code_context.fallback_provider
                if self.config.code_context.fallback_on_unhealthy
                else "unavailable"
            ),
            codegraph_enabled=codegraph.enabled,
            codegraph_healthy=healthy,
            fallback_available=self.config.code_context.fallback_on_unhealthy and bool(work_repos),
            warnings=warnings,
            details={
                "package_installed": installed,
                "index_dir": str(self.index_dir),
                "database": _CODEGRAPH_DB_NAME,
                "db_path": str(self.db_path),
                "vector_resolve_enabled": codegraph.vector_resolve_enabled,
                "indexed_repos": indexed_repos,
                "stale_repos": stale_repos,
            },
        )

    def index_repos(self, repos: list[RepoConfig], *, force: bool = False) -> list[str]:
        """Index configured work repos into CodeGraphContext when available.

        Returns warning strings instead of raising so the MCP server can preserve
        soft-fallback behavior when the optional provider is absent or unhealthy.
        """

        work_repos = [repo for repo in repos if repo.role == "work"]
        codegraph = self.config.code_context.codegraph
        if not codegraph.enabled or not work_repos:
            return []
        if not self._package_installed():
            return ["CodeGraphContext package is not installed; code graph indexing skipped."]

        self.index_dir.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        indexed: list[RepoConfig] = []
        for repo in work_repos:
            command = [
                sys.executable,
                "-m",
                "codegraphcontext",
                "--database",
                _CODEGRAPH_DB_NAME,
                "--path",
                str(self.db_path),
                "index",
                str(repo.path),
            ]
            if force:
                command.append("--force")
            env = {
                **os.environ,
                "CGC_RUNTIME_DB_TYPE": _CODEGRAPH_DB_NAME,
                "CGC_ALLOWED_ROOTS": str(repo.path.resolve()),
            }
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    env=env,
                )
            except Exception as exc:
                warnings.append(f"CodeGraph indexing failed for {repo.id}: {exc}")
                continue
            if result.returncode != 0:
                stderr = (result.stderr or result.stdout).strip().splitlines()
                message = stderr[-1] if stderr else f"exit {result.returncode}"
                warnings.append(f"CodeGraph indexing failed for {repo.id}: {message}")
                continue
            indexed.append(repo)

        if indexed:
            self._write_provenance(indexed)
        return warnings

    def search_code(self, query: str, repo_id: str | None, limit: int) -> list[CodeResult]:
        repos = self._selected_work_repos(repo_id)
        self._ensure_graph_ready(repos)
        query_text = query.strip()
        collected: list[CodeResult] = []
        for repo in repos:
            rows = self._query_code_elements(repo, query_text, per_label_limit=max(limit * 4, 20))
            for row in rows:
                result = self._row_to_result(repo, row, query=query_text)
                if result is not None:
                    collected.append(result)
        return self._rank_and_limit(collected, query=query_text, limit=limit)

    def get_code_context(
        self, symbol_or_file: str, repo_id: str | None, limit: int
    ) -> list[CodeResult]:
        repos = self._selected_work_repos(repo_id)
        self._ensure_graph_ready(repos)
        normalized = symbol_or_file.strip().lstrip("/")
        collected: list[CodeResult] = []
        if _looks_like_path(normalized):
            for repo in repos:
                collected.extend(self._file_context(repo, normalized, limit=limit))
            if collected:
                return self._rank_and_limit(collected, query=symbol_or_file, limit=limit)
        return self.search_code(symbol_or_file, repo_id=repo_id, limit=limit)

    def _package_installed(self) -> bool:
        return importlib.util.find_spec("codegraphcontext") is not None

    def _work_repos(self) -> list[RepoConfig]:
        return [repo for repo in self.config.repos if repo.role == "work"]

    def _text_index_ready(self, repos: list[RepoConfig]) -> bool:
        try:
            TextFallbackCodeContextProvider(self.config)._ensure_repos_match_index(repos)
        except Exception:
            return False
        return True

    def _selected_work_repos(self, repo_id: str | None) -> list[RepoConfig]:
        repos = self._work_repos()
        if repo_id is None:
            return repos
        return [repo for repo in repos if repo.id == repo_id]

    def _ensure_graph_ready(self, repos: list[RepoConfig]) -> None:
        health = self.health()
        indexed = health.details.get("indexed_repos", {})
        stale = set(health.details.get("stale_repos", []))
        missing = [
            repo.id
            for repo in repos
            if repo.id in stale or indexed.get(repo.id) != str(repo.path.resolve())
        ]
        if not health.codegraph_healthy or missing:
            raise FileNotFoundError(
                "CodeGraph index is not ready for configured work repo(s): "
                + ", ".join(missing or [repo.id for repo in repos])
            )

    def _driver(self) -> Any:
        os.environ["CGC_RUNTIME_DB_TYPE"] = _CODEGRAPH_DB_NAME
        from codegraphcontext.server import get_database_manager  # type: ignore[import-not-found]

        manager = get_database_manager(db_path=str(self.db_path))
        return manager.get_driver()

    def _indexed_repo_paths(self) -> dict[str, str]:
        if not self.db_path.exists():
            return {}
        try:
            rows = self._query(
                "MATCH (r:Repository) RETURN r.path as path, r.name as name, r.commit_hash as commit_hash"
            )
        except Exception:
            return {}
        by_path = {str(repo.path.resolve()): repo.id for repo in self._work_repos()}
        indexed: dict[str, str] = {}
        for row in rows:
            path = str(row.get("path") or "")
            repo_id = by_path.get(path)
            if repo_id:
                indexed[repo_id] = path
        return indexed

    def _query(self, statement: str, **params: Any) -> list[dict[str, Any]]:
        driver = self._driver()
        with driver.session() as session:
            return list(session.run(statement, **params).data())

    def _query_code_elements(
        self, repo: RepoConfig, query: str, *, per_label_limit: int
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        terms = [term.casefold() for term in query.replace("_", " ").split() if term.strip()]
        if not terms:
            terms = [query.casefold()]
        for label in _CODEGRAPH_NODE_LABELS:
            end_line_projection = (
                "n.line_number as end_line" if label == "Variable" else "n.end_line as end_line"
            )
            context_predicate = (
                ""
                if label == "Class"
                else "\n                    OR (n.context IS NOT NULL AND toLower(n.context) CONTAINS $search_query)"
            )
            context_projection = "'' as context" if label == "Class" else "n.context as context"
            statement = f"""
                MATCH (n:{label})
                WHERE n.path STARTS WITH $repo_path
                  AND (
                    toLower(n.name) CONTAINS $search_query
                    OR (n.source IS NOT NULL AND toLower(n.source) CONTAINS $search_query)
                    OR (n.docstring IS NOT NULL AND toLower(n.docstring) CONTAINS $search_query){context_predicate}
                  )
                RETURN '{label}' as label,
                       n.name as name,
                       n.path as path,
                       n.line_number as line_number,
                       {end_line_projection},
                       n.source as source,
                       n.docstring as docstring,
                       {context_projection}
                LIMIT {per_label_limit}
            """
            for term in terms:
                rows.extend(
                    self._query(
                        statement,
                        repo_path=str(repo.path.resolve()),
                        search_query=term,
                    )
                )
        return rows

    def _file_context(
        self, repo: RepoConfig, relative_path: str, *, limit: int
    ) -> list[CodeResult]:
        candidate = (repo.path / relative_path).resolve()
        try:
            candidate.relative_to(repo.path.resolve())
        except ValueError:
            return []
        if not self._direct_file_allowed(repo, candidate):
            return []
        rows: list[dict[str, Any]] = []
        for label in _CODEGRAPH_NODE_LABELS:
            end_line_projection = (
                "n.line_number as end_line" if label == "Variable" else "n.end_line as end_line"
            )
            context_projection = "'' as context" if label == "Class" else "n.context as context"
            statement = f"""
                MATCH (n:{label})
                WHERE n.path = $path
                RETURN '{label}' as label,
                       n.name as name,
                       n.path as path,
                       n.line_number as line_number,
                       {end_line_projection},
                       n.source as source,
                       n.docstring as docstring,
                       {context_projection}
                ORDER BY n.line_number
                LIMIT {max(limit * 4, 20)}
            """
            rows.extend(self._query(statement, path=str(candidate)))
        results = [self._row_to_result(repo, row, query=relative_path) for row in rows]
        return [result for result in results if result is not None]

    def _row_to_result(
        self, repo: RepoConfig, row: dict[str, Any], *, query: str
    ) -> CodeResult | None:
        raw_path = row.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return None
        path = Path(raw_path).resolve()
        if not self._direct_file_allowed(repo, path):
            return None
        try:
            relative = path.relative_to(repo.path.resolve()).as_posix()
        except ValueError:
            return None
        name = str(row.get("name") or "") or None
        context = str(row.get("context") or "") or None
        label = str(row.get("label") or "")
        symbol = _qualified_graph_symbol(label=label, name=name, context=context)
        start_line = _safe_int(row.get("line_number"))
        end_line = _safe_int(row.get("end_line")) or start_line
        snippet = _trim_snippet(
            str(row.get("source") or row.get("docstring") or symbol or name or relative)
        )
        kind = _kind_from_graph_label(label, relative)
        return CodeResult(
            repo_id=repo.id,
            path=relative,
            start_line=start_line,
            end_line=end_line,
            symbol=symbol,
            kind=kind,
            snippet=snippet,
            provider="codegraph",
            score=_graph_score(name=symbol or name, snippet=snippet, query=query),
            related=self._related_evidence(repo, path=path, symbol=name, limit=8),
        )

    def _related_evidence(
        self, repo: RepoConfig, *, path: Path, symbol: str | None, limit: int
    ) -> list[dict[str, str]]:
        related: list[dict[str, str]] = []
        if symbol:
            related.extend(self._graph_call_related(repo, path=path, symbol=symbol, limit=limit))
            related.extend(
                self._indexed_schema_related(
                    repo, symbol=symbol, limit=max(0, limit - len(related))
                )
            )
        deduped: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in related:
            key = (item.get("relation", ""), item.get("repo_id", ""), item.get("path", ""))
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        return deduped[:limit]

    def _graph_call_related(
        self, repo: RepoConfig, *, path: Path, symbol: str, limit: int
    ) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        related: list[dict[str, str]] = []
        queries = [
            (
                "called_by",
                """
                MATCH (caller:Function)-[:CALLS]->(target:Function)
                WHERE target.path = $path AND target.name = $symbol
                RETURN caller.name as symbol, caller.path as path
                LIMIT $limit
                """,
            ),
            (
                "calls",
                """
                MATCH (source:Function)-[:CALLS]->(callee:Function)
                WHERE source.path = $path AND source.name = $symbol
                RETURN callee.name as symbol, callee.path as path
                LIMIT $limit
                """,
            ),
        ]
        for relation, statement in queries:
            try:
                rows = self._query(statement, path=str(path), symbol=symbol, limit=limit)
            except Exception:
                continue
            for row in rows:
                raw_path = row.get("path")
                if not isinstance(raw_path, str):
                    continue
                related_path = Path(raw_path).resolve()
                if not self._direct_file_allowed(repo, related_path):
                    continue
                try:
                    relative = related_path.relative_to(repo.path.resolve()).as_posix()
                except ValueError:
                    continue
                related.append(
                    {
                        "repo_id": repo.id,
                        "path": relative,
                        "symbol": str(row.get("symbol") or ""),
                        "kind": _kind_from_relative_path(relative),
                        "relation": relation,
                        "provider": "codegraph",
                    }
                )
        return related[:limit]

    def _indexed_schema_related(
        self, repo: RepoConfig, *, symbol: str, limit: int
    ) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        try:
            index = ProjectIndex.open(self.state_dir)
            results = index.search(symbol, filters={"repo_id": repo.id}, limit=25)
        except Exception:
            return []
        related: list[dict[str, str]] = []
        for result in results:
            if result.doc_type != "schema":
                continue
            related.append(
                {
                    "repo_id": repo.id,
                    "path": result.path,
                    "symbol": "",
                    "kind": "schema",
                    "relation": "matching_schema",
                    "provider": "text_index",
                }
            )
            if len(related) >= limit:
                break
        return related

    def _rank_and_limit(
        self, results: list[CodeResult], *, query: str, limit: int
    ) -> list[CodeResult]:
        deduped: dict[tuple[str, str, str | None, int | None], CodeResult] = {}
        for result in results:
            key = (result.repo_id, result.path, result.symbol, result.start_line)
            existing = deduped.get(key)
            if existing is None or result.score > existing.score:
                deduped[key] = result
        ranked = list(deduped.values())
        ranked.sort(
            key=lambda result: (
                _kind_sort_rank(result.kind),
                -result.score,
                result.path,
                result.symbol or "",
            )
        )
        return ranked[:limit]

    def _direct_file_allowed(self, repo: RepoConfig, candidate: Path) -> bool:
        return _is_indexable_file(
            repo.path,
            candidate,
            state_dir=self.state_dir,
            include_globs=repo.include_globs,
            exclude_globs=repo.exclude_globs,
            max_file_bytes=self.config.indexing.max_file_bytes,
        )

    def _load_provenance(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.provenance_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        repos = data.get("repos", {})
        return repos if isinstance(repos, dict) else {}

    def _write_provenance(self, repos: list[RepoConfig]) -> None:
        existing = self._load_provenance()
        for repo in repos:
            existing[repo.id] = self._current_repo_signature(repo)
        self.provenance_path.parent.mkdir(parents=True, exist_ok=True)
        self.provenance_path.write_text(
            json.dumps({"version": 1, "repos": existing}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _repo_matches_provenance(self, repo: RepoConfig, provenance: dict[str, Any]) -> bool:
        if not provenance:
            return False
        try:
            current = self._current_repo_signature(repo)
        except ValueError:
            return False
        return provenance == current

    def _current_repo_signature(self, repo: RepoConfig) -> dict[str, Any]:
        current_head = _git_output_strict(
            repo.path, "rev-parse", "HEAD", allow_unborn=repo.source_mode == "workspace"
        )
        current_status = _git_status_porcelain_strict(repo.path, state_dir=self.state_dir)
        return {
            "role": repo.role,
            "path": str(repo.path.resolve()),
            "source_mode": repo.source_mode,
            "host_path": str(repo.host_path) if repo.host_path is not None else None,
            "includes_uncommitted_changes": repo.includes_uncommitted_changes,
            "snapshot_ref": repo.snapshot_ref,
            "snapshot_commit": repo.snapshot_commit,
            "include_globs": repo.include_globs,
            "exclude_globs": repo.exclude_globs,
            "max_file_bytes": self.config.indexing.max_file_bytes,
            "commit": repo.snapshot_commit if repo.source_mode == "snapshot" else current_head,
            "worktree_fingerprint": worktree_fingerprint(repo.path, current_status),
            "codegraph_ignore_fingerprint": _file_sha256(repo.path / ".cgcignore"),
        }


class TextFallbackCodeContextProvider:
    def __init__(self, config: ProjectKnowledgeConfig):
        self.config = config
        assert config.storage.state_dir is not None
        self.state_dir = config.storage.state_dir
        self.index = ProjectIndex.open(self.state_dir)

    def health(self) -> CodeProviderHealth:
        work_repos = self._work_repos()
        warnings = [] if work_repos else ["No work repos are configured for code context."]
        return CodeProviderHealth(
            configured_provider=self.config.code_context.provider,
            active_provider="text",
            codegraph_enabled=self.config.code_context.codegraph.enabled,
            codegraph_healthy=False,
            fallback_available=bool(work_repos),
            warnings=warnings,
            details={"work_repos": [repo.id for repo in work_repos]},
        )

    def search_code(self, query: str, repo_id: str | None, limit: int) -> list[CodeResult]:
        repos = self._selected_work_repos(repo_id)
        self._ensure_repos_match_index(repos)
        index = self.index
        collected: list[CodeResult] = []
        per_repo_limit = max(limit * 5, 25)
        for repo in repos:
            results = index.search(query, filters={"repo_id": repo.id}, limit=per_repo_limit)
            for result in results:
                if result.doc_type not in _CODE_DOC_TYPES:
                    continue
                collected.append(self._from_search_result(result, query=query, repo=repo))
        collected.sort(
            key=lambda result: (
                _kind_sort_rank(result.kind),
                -result.score,
                result.path,
                result.symbol or "",
            )
        )
        return collected[:limit]

    def get_code_context(
        self, symbol_or_file: str, repo_id: str | None, limit: int
    ) -> list[CodeResult]:
        direct = self._direct_file_context(symbol_or_file, repo_id=repo_id)
        if direct:
            return direct[:limit]
        return self.search_code(symbol_or_file, repo_id=repo_id, limit=limit)

    def _work_repos(self) -> list[RepoConfig]:
        return [repo for repo in self.config.repos if repo.role == "work"]

    def _selected_work_repos(self, repo_id: str | None) -> list[RepoConfig]:
        repos = self._work_repos()
        if repo_id is None:
            return repos
        return [repo for repo in repos if repo.id == repo_id]

    def _ensure_repos_match_index(self, repos: list[RepoConfig]) -> None:
        stale: list[str] = []
        for repo in repos:
            matches_index_config = self.index.repo_matches_provenance(
                repo_id=repo.id,
                role=repo.role,
                path=repo.path,
                source_mode=repo.source_mode,
                host_path=repo.host_path,
                includes_uncommitted_changes=repo.includes_uncommitted_changes,
                snapshot_ref=repo.snapshot_ref,
                snapshot_commit=repo.snapshot_commit,
                include_globs=repo.include_globs,
                exclude_globs=repo.exclude_globs,
                max_file_bytes=self.config.indexing.max_file_bytes,
            )
            metadata = self.index.repo_metadata(repo.id)
            if (
                not matches_index_config
                or metadata is None
                or not self._repo_current_matches_index(repo, metadata)
            ):
                stale.append(repo.id)
        if stale:
            raise FileNotFoundError(
                "Project index is not ready for configured work repo(s): " + ", ".join(stale)
            )

    def _repo_current_matches_index(self, repo: RepoConfig, metadata: dict[str, Any]) -> bool:
        try:
            current_head = _git_output_strict(
                repo.path, "rev-parse", "HEAD", allow_unborn=repo.source_mode == "workspace"
            )
            current_status = _git_status_porcelain_strict(
                repo.path, state_dir=self.config.storage.state_dir
            )
        except ValueError:
            return False
        status_lines = [line for line in current_status.splitlines() if line]
        dirty = any(not line.startswith("??") for line in status_lines)
        untracked_count = sum(1 for line in status_lines if line.startswith("??"))
        current_fingerprint = worktree_fingerprint(repo.path, current_status)
        expected_commit = repo.snapshot_commit if repo.source_mode == "snapshot" else current_head
        if metadata["last_indexed_commit"] != expected_commit:
            return False
        if repo.source_mode == "snapshot":
            return (
                repo.snapshot_commit is not None
                and current_head == repo.snapshot_commit
                and not dirty
                and untracked_count == 0
                and metadata["last_indexed_worktree_fingerprint"] == current_fingerprint
            )
        if not bool(repo.includes_uncommitted_changes) and (dirty or untracked_count):
            return False
        if bool(repo.includes_uncommitted_changes):
            return metadata["last_indexed_worktree_fingerprint"] == current_fingerprint
        return True

    def _from_search_result(
        self, result: SearchResult, *, query: str, repo: RepoConfig
    ) -> CodeResult:
        repo_path = repo.path / result.path
        snippet = result.snippet.replace("[", "").replace("]", "")
        symbol = _infer_symbol_from_text(repo_path, query, snippet)
        return CodeResult(
            repo_id=result.repo_id,
            path=result.path,
            start_line=result.line_start,
            end_line=result.line_end,
            symbol=symbol,
            kind=_kind_from_doc_type(result.doc_type),
            snippet=snippet,
            provider="text",
            score=float(result.final_score),
            related=[],
        )

    def _direct_file_context(self, symbol_or_file: str, *, repo_id: str | None) -> list[CodeResult]:
        normalized = symbol_or_file.strip().lstrip("/")
        if not _looks_like_path(normalized):
            return []
        matches: list[CodeResult] = []
        repos = self._selected_work_repos(repo_id)
        self._ensure_repos_match_index(repos)
        for repo in repos:
            candidate = (repo.path / normalized).resolve()
            try:
                candidate.relative_to(repo.path.resolve())
            except ValueError:
                continue
            if not self._direct_file_allowed(repo, candidate):
                continue
            relative = candidate.relative_to(repo.path).as_posix()
            chunks = self.index.document_chunks(repo_id=repo.id, path=relative)
            if not chunks or chunks[0]["doc_type"] not in _CODE_DOC_TYPES:
                continue
            indexed_text = "\n\n".join(str(chunk["text"]) for chunk in chunks)
            start_lines = [
                chunk["start_line"] for chunk in chunks if chunk["start_line"] is not None
            ]
            end_lines = [chunk["end_line"] for chunk in chunks if chunk["end_line"] is not None]
            kind = _kind_from_doc_type(str(chunks[0]["doc_type"]))
            matches.append(
                CodeResult(
                    repo_id=repo.id,
                    path=relative,
                    start_line=min(start_lines) if start_lines else 1,
                    end_line=max(end_lines)
                    if end_lines
                    else max(1, len(indexed_text.splitlines())),
                    symbol=_infer_symbol_from_text(candidate, symbol_or_file, indexed_text),
                    kind=kind,
                    snippet=_trim_snippet(indexed_text),
                    provider="text",
                    score=1.0,
                    related=[],
                )
            )
        return matches

    def _direct_file_allowed(self, repo: RepoConfig, candidate: Path) -> bool:
        assert self.config.storage.state_dir is not None
        return _is_indexable_file(
            repo.path,
            candidate,
            state_dir=self.config.storage.state_dir,
            include_globs=repo.include_globs,
            exclude_globs=repo.exclude_globs,
            max_file_bytes=self.config.indexing.max_file_bytes,
        )


def _git_output_strict(repo_path: Path, *args: str, allow_unborn: bool = False) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        raise ValueError(f"git command failed for {repo_path}: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        if (
            allow_unborn
            and "rev-parse HEAD" in " ".join(args)
            and ("unknown revision" in message or "ambiguous argument" in message)
        ):
            return None
        raise ValueError(f"git command failed for {repo_path}: {message}")
    output = result.stdout.strip()
    if not output:
        raise ValueError(f"git command returned no output for {repo_path}: {' '.join(args)}")
    return output


def _file_sha256(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _kind_sort_rank(kind: str) -> int:
    return {"code": 0, "schema": 1, "test": 2, "file": 3}.get(kind, 4)


def _kind_from_doc_type(doc_type: str) -> str:
    if doc_type in _CODE_DOC_TYPES:
        return doc_type
    return "unknown"


def _kind_from_graph_label(label: str, relative_path: str) -> str:
    path_kind = _kind_from_relative_path(relative_path)
    if path_kind in {"test", "schema"}:
        return path_kind
    if label in _CODEGRAPH_NODE_LABELS:
        return "code"
    return "unknown"


def _kind_from_relative_path(relative_path: str) -> str:
    path = relative_path.replace("\\", "/")
    name = Path(path).name.lower()
    if path.startswith("tests/") or name.startswith("test_") or name.endswith("_test.py"):
        return "test"
    if name.endswith((".schema.json", ".schema.yaml", ".schema.yml")) or "/schemas/" in f"/{path}":
        return "schema"
    return "code"


def _qualified_graph_symbol(*, label: str, name: str | None, context: str | None) -> str | None:
    if not name:
        return None
    if label == "Function" and context and not name.startswith(f"{context}."):
        return f"{context}.{name}"
    return name


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value or Path(value).suffix != ""


def _trim_snippet(text: str, *, max_chars: int = 500) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 1].rstrip() + "…"


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _graph_score(*, name: str | None, snippet: str, query: str) -> float:
    terms = [term.casefold() for term in query.replace(".", " ").replace("_", " ").split()]
    folded_name = (name or "").casefold()
    folded_snippet = snippet.casefold()
    if name and query.casefold() == folded_name:
        return 1.0
    if name and all(term in folded_name for term in terms):
        return 0.9
    if all(term in folded_snippet for term in terms):
        return 0.75
    return 0.5


def _infer_symbol_from_text(path: Path, query: str, text: str) -> str | None:
    if path.suffix.lower() != ".py":
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    query_terms = {term.casefold() for term in query.replace(".", " ").split() if term.strip()}
    symbols: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    symbols.append(f"{node.name}.{child.name}")
            symbols.append(node.name)
        elif isinstance(node, ast.FunctionDef):
            symbols.append(node.name)
    if not symbols:
        return None
    for symbol in symbols:
        folded = symbol.casefold()
        if any(term in folded for term in query_terms):
            return symbol
    return symbols[0]
