from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import shlex
import shutil
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
_CODEGRAPH_RESULT_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".mdx",
    ".py",
    ".rs",
    ".ts",
    ".tsx",
    ".txt",
}


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


class CodeGraphProvider:
    """Shell-out adapter for the external `colbymchenry/codegraph` provider.

    Project Knowledge MCP does not vendor or import provider internals. The active
    provider path is an operator-installed `codegraph` CLI / MCP sidecar. Provider
    output is normalized into the stable CodeResult public contract; raw provider
    markdown/JSON is never returned through MCP payloads.
    """

    def __init__(self, config: ProjectKnowledgeConfig):
        self.config = config

    def health(self) -> CodeProviderHealth:
        codegraph = self.config.code_context.codegraph
        warnings: list[str] = []
        try:
            command = self._command_argv()
        except RuntimeError:
            command = None
            warnings.append("CodeGraph command configuration is invalid; using text fallback.")
        work_repos = [repo for repo in self.config.repos if repo.role == "work"]
        indexed_repos: list[RepoConfig] = []
        repo_statuses: list[dict[str, Any]] = []

        if not codegraph.enabled:
            warnings.append("CodeGraph is disabled by config; using text fallback.")
        elif command is None:
            warnings.append(
                "CodeGraph CLI is not configured; rerun guided setup so it can install/configure "
                "the local CodeGraph CLI, or use text fallback."
            )
        elif not work_repos:
            warnings.append("No work repos are configured for CodeGraph context.")
        else:
            for repo in work_repos:
                try:
                    status = self._status_for_repo(repo)
                except RuntimeError:
                    warnings.append(
                        f"CodeGraph status failed for repo {repo.id}; using text fallback."
                    )
                    continue
                status_matches_repo = self._status_matches_repo(status, repo)
                initialized = status.get("initialized") is True
                index_present = _status_index_present(status)
                repo_statuses.append(
                    {
                        "repo_id": repo.id,
                        "initialized": initialized,
                        "version": _safe_provider_scalar(status.get("version")),
                        "index_present": index_present,
                        "project_matches_config": status_matches_repo,
                        "file_count": _safe_provider_count(status.get("fileCount")),
                        "node_count": _safe_provider_count(status.get("nodeCount")),
                        "edge_count": _safe_provider_count(status.get("edgeCount")),
                        "languages": _safe_provider_string_list(status.get("languages")),
                        "pending_changes": _safe_pending_changes(status.get("pendingChanges")),
                    }
                )
                if initialized and index_present and status_matches_repo:
                    indexed_repos.append(repo)
                else:
                    warnings.append(
                        f"CodeGraph index is not ready for repo {repo.id}; run `codegraph init` for that repo."
                    )

        healthy = bool(work_repos) and len(indexed_repos) == len(work_repos)
        return CodeProviderHealth(
            configured_provider=self.config.code_context.provider,
            active_provider=(
                "codegraph"
                if healthy
                else self.config.code_context.fallback_provider
                if self.config.code_context.fallback_on_unhealthy
                else "unavailable"
            ),
            codegraph_enabled=codegraph.enabled,
            codegraph_healthy=healthy,
            fallback_available=self.config.code_context.fallback_on_unhealthy,
            warnings=warnings,
            details={
                "provider_repo": "https://github.com/colbymchenry/codegraph",
                "cli_path": _safe_executable_identifier(command[0]) if command else None,
                "command_configured": bool(codegraph.command),
                "indexed_repos": [repo.id for repo in indexed_repos],
                "missing_index_repos": [
                    repo.id for repo in work_repos if repo not in indexed_repos
                ],
                "repo_statuses": repo_statuses,
                "index_dir_configured": codegraph.index_dir is not None,
                "telemetry_required_off": True,
            },
        )

    def search_code(self, query: str, repo_id: str | None, limit: int) -> list[CodeResult]:
        results: list[CodeResult] = []
        for repo in self._selected_work_repos(repo_id):
            output = self._run("explore", "-p", str(repo.path), query)
            parsed = _parse_codegraph_explore(output, repo_id=repo.id)
            sanitized = self._sanitize_results(parsed, repo)
            if not sanitized:
                raise RuntimeError(
                    "CodeGraph explore output did not include recognized code results"
                )
            results.extend(sanitized)
        return results[:limit]

    def get_code_context(
        self, symbol_or_file: str, repo_id: str | None, limit: int
    ) -> list[CodeResult]:
        results: list[CodeResult] = []
        for repo in self._selected_work_repos(repo_id):
            output = self._run("node", "-p", str(repo.path), symbol_or_file)
            parsed = _parse_codegraph_node(output, repo_id=repo.id, query=symbol_or_file)
            sanitized = self._sanitize_results(parsed, repo)
            if not sanitized:
                raise RuntimeError("CodeGraph node output did not include recognized code results")
            results.extend(sanitized)
        return results[:limit]

    def _sanitize_results(self, results: list[CodeResult], repo: RepoConfig) -> list[CodeResult]:
        assert self.config.storage.state_dir is not None
        sanitized: list[CodeResult] = []
        for result in results:
            relative = _normalize_provider_relative_path(result.path)
            if relative is None:
                continue
            if not self._provider_path_allowed(repo, relative):
                continue
            snippet = self._source_snippet_from_repo(
                repo, relative, start_line=result.start_line, end_line=result.end_line
            )
            if snippet is None:
                continue
            sanitized.append(
                CodeResult(
                    repo_id=repo.id,
                    path=relative,
                    start_line=result.start_line,
                    end_line=result.end_line,
                    symbol=_safe_provider_label(result.symbol),
                    kind=_stable_codegraph_kind(result.kind, path=relative),
                    snippet=snippet,
                    provider=result.provider,
                    score=result.score,
                    related=self._sanitize_related(result.related, repo),
                )
            )
        return sanitized

    def _sanitize_related(
        self, related: list[dict[str, str]], repo: RepoConfig
    ) -> list[dict[str, str]]:
        assert self.config.storage.state_dir is not None
        safe: list[dict[str, str]] = []
        for item in related:
            path = item.get("path")
            if not path:
                continue
            relative = _normalize_provider_relative_path(path)
            if relative is None:
                continue
            if not self._provider_path_allowed(repo, relative):
                continue
            kind = _safe_provider_related_kind(item.get("kind"))
            if kind is None:
                continue
            safe_item = {"kind": kind, "path": relative}
            symbol = _safe_provider_label(item.get("symbol"))
            if symbol is not None:
                safe_item["symbol"] = symbol
            line = item.get("line")
            if isinstance(line, str) and line.isdigit():
                safe_item["line"] = line
            safe.append(safe_item)
        return safe

    def _source_snippet_from_repo(
        self, repo: RepoConfig, relative: str, *, start_line: int | None, end_line: int | None
    ) -> str | None:
        path = repo.path / relative
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        if start_line is not None or end_line is not None:
            if (
                start_line is None
                or end_line is None
                or start_line < 1
                or end_line < start_line
                or start_line > len(lines)
            ):
                return None
            selected = lines[start_line - 1 : min(end_line, len(lines))]
        else:
            selected = lines[:40]
        snippet = _trim_snippet("\n".join(selected))
        return snippet or None

    def _provider_path_allowed(self, repo: RepoConfig, relative: str) -> bool:
        assert self.config.storage.state_dir is not None
        relative_path = Path(relative)
        original_candidate = repo.path / relative_path
        if self._path_has_symlink_component(repo.path, relative_path):
            return False
        if (
            not original_candidate.is_file()
            or original_candidate.suffix.lower() not in _CODEGRAPH_RESULT_SUFFIXES
        ):
            return False
        if not self._relative_path_allowed_by_repo_scope(relative_path, relative, repo):
            return False
        try:
            resolved_root = repo.path.resolve()
            resolved_path = original_candidate.resolve()
            canonical_relative_path = resolved_path.relative_to(resolved_root)
        except (OSError, ValueError):
            return False
        canonical_relative = canonical_relative_path.as_posix()
        if not self._relative_path_allowed_by_repo_scope(
            canonical_relative_path, canonical_relative, repo
        ):
            return False
        try:
            resolved_path.relative_to(self.config.storage.state_dir.resolve())
        except ValueError:
            pass
        else:
            return False
        if self.config.indexing.max_file_bytes is not None:
            try:
                if resolved_path.stat().st_size > self.config.indexing.max_file_bytes:
                    return False
            except OSError:
                return False
        return True

    @staticmethod
    def _path_has_symlink_component(repo_path: Path, relative_path: Path) -> bool:
        candidate = repo_path
        for part in relative_path.parts:
            candidate = candidate / part
            if candidate.is_symlink():
                return True
        return False

    @staticmethod
    def _relative_path_allowed_by_repo_scope(
        relative_path: Path, relative: str, repo: RepoConfig
    ) -> bool:
        if ".git" in relative_path.parts or ".project-knowledge" in relative_path.parts:
            return False
        if any(
            part == ".env" or part == ".envrc" or part.startswith(".env.")
            for part in relative_path.parts
        ):
            return False
        if any(
            "secret" in part.casefold() or "token" in part.casefold()
            for part in relative_path.parts
        ):
            return False
        if repo.include_globs and not _provider_matches_any(relative, repo.include_globs):
            return False
        if repo.exclude_globs and _provider_matches_any(relative, repo.exclude_globs):
            return False
        return True

    def _status_matches_repo(self, status: dict[str, Any], repo: RepoConfig) -> bool:
        project_path = status.get("projectPath")
        if project_path is None:
            return False
        try:
            return Path(str(project_path)).expanduser().resolve() == repo.path.resolve()
        except OSError:
            return False

    def _selected_work_repos(self, repo_id: str | None) -> list[RepoConfig]:
        repos = [repo for repo in self.config.repos if repo.role == "work"]
        if repo_id is None:
            return repos
        return [repo for repo in repos if repo.id == repo_id]

    def _status_for_repo(self, repo: RepoConfig) -> dict[str, Any]:
        output = self._run("status", str(repo.path), "--json")
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("status output was not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("status output was not a JSON object")
        return parsed

    def _command_argv(self) -> list[str] | None:
        configured = self.config.code_context.codegraph.command
        if configured:
            try:
                command = shlex.split(configured)
            except ValueError as exc:
                raise RuntimeError("CodeGraph command configuration is invalid") from exc
            if not command or command[0].startswith("-"):
                raise RuntimeError("CodeGraph command configuration is invalid")
            return command
        binary = shutil.which("codegraph")
        return [binary] if binary else None

    def _run(self, *args: str) -> str:
        command = self._command_argv()
        if command is None:
            raise RuntimeError("CodeGraph CLI is not installed")
        try:
            result = subprocess.run(
                [*command, *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                env=_codegraph_env(),
            )
        except OSError as exc:
            raise RuntimeError("CodeGraph command could not be executed") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("CodeGraph command timed out") from exc
        if result.returncode != 0:
            raise RuntimeError(f"CodeGraph command failed with exit {result.returncode}")
        return result.stdout


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
        return self.search_repos(
            query,
            repos=repos,
            limit=limit,
            provider="fts5",
            include_all_doc_types=False,
        )

    def search_repos(
        self,
        query: str,
        *,
        repos: list[RepoConfig],
        limit: int,
        provider: str,
        include_all_doc_types: bool,
    ) -> list[CodeResult]:
        self._ensure_repos_match_index(repos)
        index = self.index
        collected: list[CodeResult] = []
        per_repo_limit = max(limit * 5, 25)
        for repo in repos:
            results = index.search(query, filters={"repo_id": repo.id}, limit=per_repo_limit)
            for result in results:
                if not include_all_doc_types and result.doc_type not in _CODE_DOC_TYPES:
                    continue
                collected.append(
                    self._from_search_result(result, query=query, repo=repo, provider=provider)
                )
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
        self, result: SearchResult, *, query: str, repo: RepoConfig, provider: str
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
            provider=provider,
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


_CODEGRAPH_SECTION_RE = re.compile(r"^####\s+(?P<path>.*?)\s+—\s+(?P<symbols>.+)$", re.MULTILINE)
_CODEGRAPH_NODE_LOCATION_RE = re.compile(
    r"^\*\*Location:\*\*\s+(?P<path>.+?):(?P<line>\d+)\s*$", re.MULTILINE
)
_CODEGRAPH_NODE_HEADING_RE = re.compile(
    r"^##\s+(?P<symbol>.+?)\s+\((?P<kind>[^)]+)\)", re.MULTILINE
)
_CODEGRAPH_FILE_HEADING_RE = re.compile(r"^\*\*(?P<path>[^*]+)\*\*\s+—", re.MULTILINE)
_CODEGRAPH_SYMBOL_RE = re.compile(r"(?P<symbol>[^,()]+?)\((?P<kind>[^)]+)\)")
_CODEGRAPH_CALLER_RE = re.compile(
    r"^\*\*Called by ←\*\*\s+(?P<symbol>.+?)\s+\((?P<path>.+?):(?P<line>\d+)\)",
    re.MULTILINE,
)
_CODEGRAPH_RELATED_FILES_RE = re.compile(r"^\*\*Related files:\*\*\s+(?P<paths>.+)$", re.MULTILINE)


def _parse_codegraph_explore(output: str, *, repo_id: str) -> list[CodeResult]:
    matches = list(_CODEGRAPH_SECTION_RE.finditer(output))
    results: list[CodeResult] = []
    for index, match in enumerate(matches):
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(output)
        body = output[match.end() : section_end]
        symbols = _parse_codegraph_symbols(match.group("symbols"))
        symbol, kind = (
            symbols[0] if symbols else (None, _kind_from_codegraph_path(match.group("path")))
        )
        start_line, end_line, snippet, has_fence = _codegraph_snippet(body)
        if not has_fence or not snippet or start_line is None or end_line is None:
            raise RuntimeError("CodeGraph explore output did not include a source snippet")
        results.append(
            CodeResult(
                repo_id=repo_id,
                path=match.group("path").strip(),
                start_line=start_line,
                end_line=end_line,
                symbol=symbol,
                kind=_stable_codegraph_kind(kind, path=match.group("path")),
                snippet=snippet,
                provider="codegraph",
                score=max(0.0, 1.0 - (index * 0.05)),
                related=[],
            )
        )
    return results


def _parse_codegraph_node(output: str, *, repo_id: str, query: str) -> list[CodeResult]:
    location = _CODEGRAPH_NODE_LOCATION_RE.search(output)
    heading = _CODEGRAPH_NODE_HEADING_RE.search(output)
    file_heading = _CODEGRAPH_FILE_HEADING_RE.search(output)
    if location is None and file_heading is None:
        raise RuntimeError("CodeGraph node output did not include a recognized code location")

    if location:
        path = location.group("path").strip()
        start_line = int(location.group("line"))
    elif file_heading:
        path = file_heading.group("path").strip()
        start_line = None
    else:
        path = query.strip().lstrip("/")
        start_line = None
    symbol = (
        heading.group("symbol").strip() if heading else (None if _looks_like_path(query) else query)
    )
    kind = heading.group("kind").strip() if heading else _kind_from_codegraph_path(path)
    block_start, block_end, snippet, has_fence = _codegraph_snippet(output)
    if not has_fence or not snippet or block_start is None or block_end is None:
        raise RuntimeError("CodeGraph node output did not include a source snippet")
    if start_line is None:
        start_line = block_start
    end_line = block_end
    return [
        CodeResult(
            repo_id=repo_id,
            path=path,
            start_line=start_line,
            end_line=end_line,
            symbol=symbol,
            kind=_stable_codegraph_kind(kind, path=path),
            snippet=snippet,
            provider="codegraph",
            score=1.0,
            related=_parse_codegraph_related(output),
        )
    ]


def _parse_codegraph_symbols(raw: str) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    for match in _CODEGRAPH_SYMBOL_RE.finditer(raw):
        symbol = match.group("symbol").strip()
        kind = match.group("kind").strip()
        if symbol and kind != "file":
            parsed.append((symbol, kind))
    if parsed:
        return parsed
    return [
        (match.group("symbol").strip(), match.group("kind").strip())
        for match in _CODEGRAPH_SYMBOL_RE.finditer(raw)
        if match.group("symbol").strip()
    ]


def _codegraph_snippet(text: str) -> tuple[int | None, int | None, str, bool]:
    fence = re.search(r"```[^\n]*\n(?P<code>.*?)\n```", text, re.DOTALL)
    raw = fence.group("code") if fence else ""
    lines: list[str] = []
    line_numbers: list[int] = []
    for raw_line in raw.splitlines():
        numbered = re.match(r"^\s*(?P<line>\d+)\t(?P<text>.*)$", raw_line)
        if numbered:
            line_numbers.append(int(numbered.group("line")))
            lines.append(numbered.group("text"))
        else:
            stripped = raw_line.rstrip()
            if stripped:
                lines.append(stripped)
    snippet = _trim_snippet("\n".join(lines))
    return (
        min(line_numbers) if line_numbers else None,
        max(line_numbers) if line_numbers else None,
        snippet,
        fence is not None,
    )


def _parse_codegraph_related(output: str) -> list[dict[str, str]]:
    related: list[dict[str, str]] = []
    for match in _CODEGRAPH_CALLER_RE.finditer(output):
        related.append(
            {
                "kind": "caller",
                "symbol": match.group("symbol").strip(),
                "path": match.group("path").strip(),
                "line": match.group("line").strip(),
            }
        )
    files = _CODEGRAPH_RELATED_FILES_RE.search(output)
    if files:
        for path in files.group("paths").split(","):
            clean = path.strip()
            if clean:
                related.append({"kind": "related_file", "path": clean})
    return related


def _redact_sensitive_text(text: str) -> str:
    redacted = re.sub(
        r"(?i)(--?(?:api[-_]?key|token|password|secret|passwd)(?:=|\s+))([^\s,'\"]+)",
        r"\1[REDACTED]",
        text,
    )
    return re.sub(
        r"(?i)((?:api[-_]?key|secret|password|token|passwd)\s*=\s*['\"]?)([^'\"\s,]+)",
        r"\1[REDACTED]",
        redacted,
    )


def _codegraph_env() -> dict[str, str]:
    allowed = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(
        {
            "DO_NOT_TRACK": "1",
            "CODEGRAPH_TELEMETRY": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


def _safe_provider_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or _redact_sensitive_text(stripped) != stripped:
        return None
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.$:# -]{0,119}", stripped):
        return stripped
    return None


def _safe_provider_related_kind(value: Any) -> str | None:
    if value in {"caller", "related_file"}:
        return str(value)
    return None


def _safe_executable_identifier(executable: str) -> str:
    return Path(executable).name if Path(executable).is_absolute() else executable


def _safe_provider_scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._+:-]{1,40}", value):
        return value
    return None


def _safe_provider_count(value: Any) -> int | None:
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _status_index_present(status: dict[str, Any]) -> bool:
    index_path = status.get("indexPath")
    if not isinstance(index_path, str):
        return False
    stripped = index_path.strip()
    if not stripped or _redact_sensitive_text(stripped) != stripped:
        return False
    return True


def _safe_provider_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    safe: list[str] = []
    for item in value[:20]:
        if isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_+#.-]{1,30}", item):
            safe.append(item)
    return safe


def _safe_pending_changes(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, int] = {}
    for key in ("added", "modified", "removed"):
        count = value.get(key)
        if isinstance(count, int) and count >= 0:
            safe[key] = count
    return safe


def _normalize_provider_relative_path(path: str) -> str | None:
    candidate = path.strip().replace("\\", "/")
    if not candidate or candidate.startswith(("/", "~")):
        return None
    while candidate.startswith("./"):
        candidate = candidate[2:]
    parts = [part for part in candidate.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _provider_matches_any(relative_path: str, patterns: list[str]) -> bool:
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


def _stable_codegraph_kind(kind: str, *, path: str) -> str:
    normalized = kind.strip().casefold().replace(" ", "_")
    if normalized in {"class", "function", "method", "schema", "file", "test", "code"}:
        return normalized
    return _kind_from_codegraph_path(path)


def _kind_from_codegraph_path(path: str) -> str:
    normalized = path.casefold()
    if normalized.startswith("tests/") or "/tests/" in normalized:
        return "test"
    if normalized.startswith("schemas/") or normalized.endswith(".schema.json"):
        return "schema"
    return "file" if _looks_like_path(path) else "code"


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
