from __future__ import annotations

import ast
import importlib.util
import subprocess
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
    """Soft-failing adapter boundary for CodeGraphContext.

    Step 0 accepted CodeGraphContext as the first provider candidate with a known
    limitation: the tested CLI output is human/Rich oriented rather than a stable
    machine-readable response. Until a future adapter can prove a stable query
    shape, this provider reports actionable health and lets services use text
    fallback without leaking raw provider output.
    """

    def __init__(self, config: ProjectKnowledgeConfig):
        self.config = config

    def health(self) -> CodeProviderHealth:
        codegraph = self.config.code_context.codegraph
        installed = importlib.util.find_spec("codegraphcontext") is not None
        warnings: list[str] = []
        if not codegraph.enabled:
            warnings.append("CodeGraph is disabled by config; using text fallback.")
        elif not installed:
            warnings.append("CodeGraphContext package is not installed; using text fallback.")
        else:
            warnings.append(
                "CodeGraphContext adapter has no stable machine-readable query surface yet; "
                "using text fallback until provider health is proven."
            )
        return CodeProviderHealth(
            configured_provider=self.config.code_context.provider,
            active_provider=self.config.code_context.fallback_provider,
            codegraph_enabled=codegraph.enabled,
            codegraph_healthy=False,
            fallback_available=self.config.code_context.fallback_on_unhealthy,
            warnings=warnings,
            details={
                "package_installed": installed,
                "index_dir": str(codegraph.index_dir) if codegraph.index_dir is not None else None,
                "vector_resolve_enabled": codegraph.vector_resolve_enabled,
            },
        )


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
        if allow_unborn and "rev-parse HEAD" in " ".join(args) and "unknown revision" in message:
            return None
        raise ValueError(f"git command failed for {repo_path}: {message}")
    output = result.stdout.strip()
    if not output:
        raise ValueError(f"git command returned no output for {repo_path}: {' '.join(args)}")
    return output


def _kind_sort_rank(kind: str) -> int:
    return {"code": 0, "schema": 1, "test": 2, "file": 3}.get(kind, 4)


def _kind_from_doc_type(doc_type: str) -> str:
    if doc_type in _CODE_DOC_TYPES:
        return doc_type
    return "unknown"


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value or Path(value).suffix != ""


def _trim_snippet(text: str, *, max_chars: int = 500) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 1].rstrip() + "…"


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
