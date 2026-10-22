from pathlib import Path
import subprocess

import pytest
import yaml

from project_knowledge_mcp import services
from project_knowledge_mcp.config import load_project_config
from project_knowledge_mcp.index import ProjectIndex


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def remote_project(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    git(origin, "config", "user.email", "test@example.com")
    git(origin, "config", "user.name", "Test")
    advance(origin, "baseline")
    path = tmp_path / "project.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "project": {"id": "sync"},
                "storage": {"project_root": str(tmp_path)},
                "repos": [
                    {
                        "id": "ops",
                        "role": "ops",
                        "source_mode": "remote",
                        "remote_url": str(origin),
                        "canonical_branch": "main",
                        "writable": True,
                    }
                ],
            }
        )
    )
    return load_project_config(path), origin


def advance(origin, marker):
    (origin / "knowledge.md").write_text("# Knowledge\n" + marker)
    git(origin, "add", ".")
    git(origin, "commit", "-m", marker)
    return git(origin, "rev-parse", "HEAD")


def test_sync_clones_indexes_advances_and_skips_unchanged_index(tmp_path, monkeypatch):
    config, origin = remote_project(tmp_path)
    first = services.sync_project(config)
    assert first["repos"][0]["reindex_needed"] is False
    assert ProjectIndex.open(config.storage.state_dir).search("baseline")
    commit = advance(origin, "latermarker")
    second = services.sync_project(config)
    assert second["repos"][0]["last_indexed_commit"] == commit
    assert ProjectIndex.open(config.storage.state_dir).search("latermarker")
    monkeypatch.setattr(
        services,
        "index_project_from_config",
        lambda **kw: (_ for _ in ()).throw(AssertionError("unnecessary index")),
    )
    assert services.sync_project(config)["repos"][0]["reindex_needed"] is False


def test_fetch_failure_preserves_safe_index_and_never_current(tmp_path):
    config, origin = remote_project(tmp_path)
    services.sync_project(config)
    origin.rename(origin.with_name("offline"))
    result = services.sync_project(config)
    repo = result["repos"][0]
    assert repo["reindex_needed"] is True
    assert repo["last_sync_error"]
    assert ProjectIndex.open(config.storage.state_dir).search("baseline")


def test_failed_index_restores_checkout_and_blocks_false_absence(tmp_path, monkeypatch):
    config, origin = remote_project(tmp_path)
    services.sync_project(config)
    baseline = git(config.ops_repo.path, "rev-parse", "HEAD")
    advance(origin, "newmarker")

    def fail(**kwargs):
        raise RuntimeError("injected index failure")

    monkeypatch.setattr(services, "index_project_from_config", fail)
    result = services.sync_project(config)
    assert git(config.ops_repo.path, "rev-parse", "HEAD") == baseline
    assert result["repos"][0]["last_sync_error"]
    packet = services.search_ops_from_config(config_path=config.config_path, query="absentmarker")
    assert packet["match_state"] == "indeterminate"
    assert packet["freshness_state"] == "stale"
    assert services.search_ops_from_config(config_path=config.config_path, query="baseline")[
        "results"
    ]


def test_first_sync_failure_makes_retrieval_unavailable(tmp_path, monkeypatch):
    config, origin = remote_project(tmp_path)
    origin.rename(origin.with_name("offline"))
    services.sync_project(config)
    packet = services.search_ops_from_config(config_path=config.config_path, query="absent")
    assert packet["retrieval_state"] == "unavailable"
    assert packet["match_state"] == "indeterminate"


def test_required_codegraph_is_refreshed_and_verified(tmp_path, monkeypatch):
    config, origin = remote_project(tmp_path)
    data = yaml.safe_load(config.config_path.read_text())
    data["repos"].append({**data["repos"][0], "id": "work", "role": "work", "writable": False})
    config.config_path.write_text(yaml.safe_dump(data))
    config = load_project_config(config.config_path)
    calls = []
    initialized = False
    pending = False

    def status(self, repo):
        return {
            "initialized": initialized,
            "projectPath": str(repo.path),
            "indexPath": str(repo.path / ".git" / "codegraph"),
            "pendingChanges": {"added": int(pending), "modified": 0, "removed": 0},
            "worktreeMismatch": False,
        }

    def run(self, *args):
        nonlocal initialized
        calls.append(args)
        initialized = True
        return ""

    monkeypatch.setattr(services.CodeGraphProvider, "_status_for_repo", status)
    monkeypatch.setattr(services.CodeGraphProvider, "_run", run)
    result = services.sync_project(config)
    assert calls == [("init", str(config.repo_by_id("work").path))]
    assert not result["repos"][1]["reindex_needed"]
    advance(origin, "newcode")
    services.sync_project(config)
    assert calls[-1][0] == "sync"
    pending = True
    packet = services.check_project_staleness_from_config(config_path=config.config_path)
    assert packet["repos"][1]["reindex_needed"]


def test_optional_disabled_codegraph_is_not_invoked(tmp_path, monkeypatch):
    config, _ = remote_project(tmp_path)
    config.ops_repo.role = "work"
    config.code_context.required_for_code_repos = False
    config.code_context.codegraph.enabled = False

    def fail(*args, **kwargs):
        raise AssertionError("optional disabled CodeGraph must not be invoked")

    monkeypatch.setattr(services.CodeGraphProvider, "_status_for_repo", fail)
    monkeypatch.setattr(services.CodeGraphProvider, "_run", fail)

    result = services.sync_project(config)

    assert result["repos"][0]["reindex_needed"] is False


def test_running_server_observes_repeated_remote_advances(tmp_path, monkeypatch):
    import asyncio
    import json
    from fastmcp import Client
    from project_knowledge_mcp.server import create_mcp

    config, origin = remote_project(tmp_path)
    data = yaml.safe_load(config.config_path.read_text())
    data["synchronization"] = {"interval_seconds": 0.1}
    config.config_path.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("PROJECT_KNOWLEDGE_CONFIG", str(config.config_path))

    async def exercise():
        async with Client(create_mcp()) as client:
            assert config.ops_repo.path.exists(), "startup must clone before readiness"
            for marker in ["periodicfirst", "periodicsecond"]:
                commit = advance(origin, marker)
                for _ in range(100):
                    response = await client.call_tool("search_ops", {"query": marker})
                    packet = json.loads(response.content[0].text)
                    if packet.get("results"):
                        assert packet["staleness"][0]["last_indexed_commit"] == commit
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError("periodic synchronization did not converge")

    asyncio.run(exercise())


def test_busy_sync_skips_and_reads_wait(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    config, origin = remote_project(tmp_path)
    services.sync_project(config)
    advance(origin, "concurrentmarker")
    entered = threading.Event()
    release = threading.Event()
    original = services.index_project_from_config

    def paused(**kwargs):
        entered.set()
        assert release.wait(10)
        return original(**kwargs)

    monkeypatch.setattr(services, "index_project_from_config", paused)
    with ThreadPoolExecutor(max_workers=2) as executor:
        refresh = executor.submit(services.sync_project, config)
        assert entered.wait(10)
        assert services.sync_project(config, skip_if_busy=True)["status"] == "skipped"
        read = executor.submit(
            services.search_ops_from_config,
            config_path=config.config_path,
            query="concurrentmarker",
        )
        assert not read.done()
        release.set()
        refresh.result(timeout=10)
        assert read.result(timeout=10)["results"]


def test_unowned_clone_is_never_reset(tmp_path):
    config, origin = remote_project(tmp_path)
    config.ops_repo.path.parent.mkdir(parents=True)
    git(config.ops_repo.path.parent, "clone", str(origin), str(config.ops_repo.path))
    (config.ops_repo.path / "unpublished.md").write_text("preserve me")
    result = services.sync_project(config)
    assert "ownership" in result["repos"][0]["last_sync_error"]
    assert (config.ops_repo.path / "unpublished.md").read_text() == "preserve me"


def test_owned_clone_repairs_dirty_and_ignored_content(tmp_path):
    config, origin = remote_project(tmp_path)
    (origin / ".gitignore").write_text("ignored.md\n")
    git(origin, "add", ".gitignore")
    git(origin, "commit", "-m", "ignore")
    services.sync_project(config)
    root = config.ops_repo.path
    (root / "knowledge.md").write_text("dirtymarker")
    (root / "ignored.md").write_text("ignoredmarker")
    advance(origin, "canonicalmarker")
    services.sync_project(config)
    index = ProjectIndex.open(config.storage.state_dir)
    assert index.search("canonicalmarker")
    assert not index.search("dirtymarker")
    assert not index.search("ignoredmarker")


def test_restoration_failure_blocks_reads_and_next_sync_recovers(tmp_path, monkeypatch):
    config, origin = remote_project(tmp_path)
    services.sync_project(config)
    advance(origin, "recovermarker")
    original_git = services._sync_git
    resets = 0

    def fail_restore(path, *args):
        nonlocal resets
        if args[0] == "reset":
            resets += 1
            if resets == 2:
                raise RuntimeError("injected restoration failure")
        return original_git(path, *args)

    original_index = services.index_project_from_config

    def fail_index(**kw):
        raise RuntimeError("injected index failure")

    monkeypatch.setattr(services, "_sync_git", fail_restore)
    monkeypatch.setattr(services, "index_project_from_config", fail_index)
    result = services.sync_project(config)
    assert "restoration failed" in result["repos"][0]["last_sync_error"]
    assert (
        services.search_ops_from_config(config_path=config.config_path, query="baseline")[
            "retrieval_state"
        ]
        == "unavailable"
    )
    monkeypatch.setattr(services, "_sync_git", original_git)
    monkeypatch.setattr(services, "index_project_from_config", original_index)
    assert not services.sync_project(config)["repos"][0]["reindex_needed"]
    assert services.search_ops_from_config(config_path=config.config_path, query="recovermarker")[
        "results"
    ]


def test_index_failure_releases_transaction_for_restoration(tmp_path, monkeypatch):
    import sqlite3
    import pytest
    from project_knowledge_mcp import index

    config, origin = remote_project(tmp_path)
    services.sync_project(config)

    def fail(*args, **kwargs):
        raise RuntimeError("parser insert failed")

    monkeypatch.setattr(index, "_insert_document", fail)
    with pytest.raises(RuntimeError) as failure:
        services.index_project_from_config(config_path=config.config_path)
    assert failure.value
    with sqlite3.connect(config.storage.state_dir / "index.sqlite3", timeout=0.1) as database:
        database.execute("UPDATE repos SET writable = writable")
    assert ProjectIndex.open(config.storage.state_dir).search("baseline")


def test_remote_configuration_change_cannot_reuse_current_claim(tmp_path):
    config, origin = remote_project(tmp_path)
    services.sync_project(config)
    data = yaml.safe_load(config.config_path.read_text())
    data["repos"][0]["canonical_branch"] = "release"
    config.config_path.write_text(yaml.safe_dump(data))
    packet = services.check_project_staleness_from_config(config_path=config.config_path)
    assert packet["repos"][0]["reindex_needed"]
    git(origin, "branch", "release")
    config = load_project_config(config.config_path)
    result = services.sync_project(config)
    assert not result["repos"][0]["reindex_needed"]
    assert result["repos"][0]["branch"] == "release"


def test_symlink_git_directory_is_not_owned(tmp_path):
    config, _ = remote_project(tmp_path)
    services.sync_project(config)
    git_dir = config.ops_repo.path / ".git"
    moved = tmp_path / "displaced-git"
    git_dir.rename(moved)
    git_dir.symlink_to(moved, target_is_directory=True)
    result = services.sync_project(config)
    assert result["repos"][0]["last_sync_error"]
    assert not result["repos"][0]["retrieval_available"]


def test_restart_with_failed_fetch_serves_prior_aligned_index_as_stale(tmp_path):
    config, origin = remote_project(tmp_path)
    services.sync_project(config)
    services._sync_results.pop((str(config.storage.state_dir), "ops"))
    origin.rename(origin.with_name("offline"))
    services.sync_project(config)
    packet = services.search_ops_from_config(config_path=config.config_path, query="baseline")
    assert packet["results"]
    assert packet["freshness_state"] == "stale"


def test_graph_index_missing_prevents_current(tmp_path, monkeypatch):
    config, _ = remote_project(tmp_path)
    config.ops_repo.role = "work"
    monkeypatch.setattr(
        services.CodeGraphProvider,
        "_status_for_repo",
        lambda self, repo: {
            "initialized": True,
            "projectPath": str(repo.path),
            "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
        },
    )
    assert not services._required_graph_ready(config, config.repos[0])


def test_server_health_reports_unavailable_remote(tmp_path, monkeypatch):
    import asyncio
    import json
    from fastmcp import Client
    from project_knowledge_mcp.server import create_mcp

    config, origin = remote_project(tmp_path)
    origin.rename(origin.with_name("offline"))
    monkeypatch.setenv("PROJECT_KNOWLEDGE_CONFIG", str(config.config_path))

    async def exercise():
        async with Client(create_mcp()) as client:
            response = await client.call_tool("health")
            packet = json.loads(response.content[0].text)
            assert packet["status"] == "error"
            assert packet["ready"] is False

    asyncio.run(exercise())


def test_sync_does_not_mutate_workspace_or_snapshot(tmp_path):
    config, origin = remote_project(tmp_path)
    data = yaml.safe_load(config.config_path.read_text())
    for mode in ["workspace", "snapshot"]:
        root = tmp_path / mode
        git(tmp_path, "clone", str(origin), str(root))
        repo = {"id": mode, "role": "artifact", "path": str(root), "source_mode": mode}
        if mode == "snapshot":
            repo.update(snapshot_ref="main", snapshot_commit=git(root, "rev-parse", "HEAD"))
        else:
            (root / "unpublished.md").write_text("workspaceonlymarker")
        data["repos"].append(repo)
    config.config_path.write_text(yaml.safe_dump(data))
    config = load_project_config(config.config_path)
    services.index_project_from_config(config_path=config.config_path, repo_id="workspace")
    services.index_project_from_config(config_path=config.config_path, repo_id="snapshot")
    baseline = git(origin, "rev-parse", "HEAD")
    advance(origin, "remoteonlymarker")
    services.sync_project(config)
    for mode in ["workspace", "snapshot"]:
        assert git(tmp_path / mode, "rev-parse", "HEAD") == baseline
    results = ProjectIndex.open(config.storage.state_dir).search("workspaceonlymarker")
    assert [result.repo_id for result in results] == ["workspace"]
    assert results[0].includes_uncommitted_changes


def test_failure_after_sqlite_commit_restores_previous_complete_index(tmp_path, monkeypatch):
    config, origin = remote_project(tmp_path)
    services.sync_project(config)
    baseline = git(config.ops_repo.path, "rev-parse", "HEAD")
    advance(origin, "unpublishedgeneration")
    original = services.index_project_from_config

    def fail_after_commit(**kwargs):
        original(**kwargs)
        raise RuntimeError("injected post-commit failure")

    monkeypatch.setattr(services, "index_project_from_config", fail_after_commit)
    services.sync_project(config)
    assert git(config.ops_repo.path, "rev-parse", "HEAD") == baseline
    index = ProjectIndex.open(config.storage.state_dir)
    assert index.search("baseline")
    assert not index.search("unpublishedgeneration")


def test_codegraph_failure_restores_required_graph_and_checkout(tmp_path, monkeypatch):
    config, origin = remote_project(tmp_path)
    data = yaml.safe_load(config.config_path.read_text())
    data["repos"].append({**data["repos"][0], "id": "work", "role": "work", "writable": False})
    config.config_path.write_text(yaml.safe_dump(data))
    config = load_project_config(config.config_path)
    graph_commit = None
    fail_next = False

    def status(self, repo):
        return {
            "initialized": graph_commit is not None,
            "projectPath": str(repo.path),
            "indexPath": str(tmp_path / "graph"),
            "worktreeMismatch": False,
            "pendingChanges": {
                "added": 0,
                "removed": 0,
                "modified": int(graph_commit != git(repo.path, "rev-parse", "HEAD")),
            },
        }

    def run(self, *args):
        nonlocal graph_commit, fail_next
        if fail_next:
            fail_next = False
            raise RuntimeError("injected CodeGraph refresh failure")
        graph_commit = git(Path(args[1]), "rev-parse", "HEAD")
        return ""

    monkeypatch.setattr(services.CodeGraphProvider, "_status_for_repo", status)
    monkeypatch.setattr(services.CodeGraphProvider, "_run", run)
    services.sync_project(config)
    baseline = graph_commit
    advance(origin, "newgraphmarker")
    fail_next = True
    repo = services.sync_project(config)["repos"][1]
    assert repo["head_commit"] == baseline == graph_commit
    assert repo["reindex_needed"]
    assert repo["retrieval_available"]
    assert "CodeGraph refresh failure" in repo["last_sync_error"]
    assert not services.sync_project(config)["repos"][1]["reindex_needed"]


def test_remote_index_excludes_ignored_content_even_after_native_refresh(tmp_path):
    config, origin = remote_project(tmp_path)
    (origin / ".gitignore").write_text("ignored.md\n")
    git(origin, "add", ".gitignore")
    git(origin, "commit", "-m", "ignore derived output")
    services.sync_project(config)
    (config.ops_repo.path / "ignored.md").write_text("derivedoutputmarker")
    services.index_project_from_config(config_path=config.config_path)
    assert not ProjectIndex.open(config.storage.state_dir).search("derivedoutputmarker")


def test_stale_brief_recent_changes_cannot_claim_current_absence(tmp_path):
    config, origin = remote_project(tmp_path)
    services.sync_project(config)
    origin.rename(origin.with_name("offline"))
    services.sync_project(config)
    brief = services.generate_session_brief_from_config(
        config_path=config.config_path, task="missing", view="doctrine", since="tomorrow"
    )
    recent = brief["sections"]["recent_changes"]
    assert recent["freshness_state"] == "stale"
    assert recent["match_state"] == "indeterminate"


def test_remote_codegraph_rejects_untracked_ignored_results(tmp_path):
    config, origin = remote_project(tmp_path)
    (origin / ".gitignore").write_text("ignored.py\n")
    git(origin, "add", ".gitignore")
    git(origin, "commit", "-m", "ignore derived code")
    services.sync_project(config)
    (config.ops_repo.path / "ignored.py").write_text('print("unpublished")')
    provider = services.CodeGraphProvider(config)
    assert not provider._provider_path_allowed(config.ops_repo, "ignored.py")


def test_remote_worktree_fingerprint_mismatch_cannot_report_current(tmp_path):
    import sqlite3

    config, _ = remote_project(tmp_path)
    services.sync_project(config)
    with sqlite3.connect(config.storage.state_dir / "index.sqlite3") as database:
        database.execute("UPDATE repos SET last_indexed_worktree_fingerprint = 'mismatch'")
    status = services.check_project_staleness_from_config(config_path=config.config_path)["repos"][
        0
    ]
    assert status["reindex_needed"]
    assert not status["retrieval_available"]


def stale_code_project(tmp_path):
    config, origin = remote_project(tmp_path)
    (origin / "src").mkdir()
    (origin / "src/baseline.py").write_text("def baseline(): return 1\n")
    git(origin, "add", ".")
    git(origin, "commit", "-m", "code fixture")
    data = yaml.safe_load(config.config_path.read_text())
    data["repos"].append(
        {
            **data["repos"][0],
            "id": "work",
            "role": "work",
            "writable": False,
            "include_globs": ["*.md", "src/**/*.py"],
        }
    )
    data["code_context"] = {
        "required_for_code_repos": False,
        "fallback_on_unhealthy": True,
        "codegraph": {"enabled": False},
    }
    config.config_path.write_text(yaml.safe_dump(data))
    config = load_project_config(config.config_path)
    services.sync_project(config)
    origin.rename(origin.with_name("offline"))
    services.sync_project(config)
    return config


@pytest.mark.parametrize(
    "retrieve",
    [
        services.search_ops_from_config,
        services.search_code_from_config,
        services.get_code_context_from_config,
    ],
)
def test_stale_retrieval_markdown_withholds_absence_and_retains_results(tmp_path, retrieve):
    config = stale_code_project(tmp_path)
    for query in ("absentmarker", "baseline"):
        argument = (
            "symbol_or_file" if retrieve is services.get_code_context_from_config else "query"
        )
        packet = retrieve(config_path=config.config_path, **{argument: query})
        assert packet["freshness_state"] == "stale"
        assert "No results." not in packet["markdown"]
        assert "retrieval is incomplete" in packet["markdown"].lower()
        assert "absence cannot be inferred" in packet["markdown"].lower()
        if query == "baseline":
            assert packet["results"]
            assert "baseline" in packet["markdown"]


def mixed_source_project(tmp_path):
    config, origin = remote_project(tmp_path)
    workspace = tmp_path / "workspace"
    git(tmp_path, "clone", str(origin), str(workspace))
    data = yaml.safe_load(config.config_path.read_text())
    data["repos"].append(
        {
            "id": "work",
            "role": "work",
            "path": str(workspace),
            "source_mode": "workspace",
            "writable": True,
        }
    )
    data["code_context"] = {"required_for_code_repos": False, "codegraph": {"enabled": False}}
    data["write_policy"] = {
        "default_capture_repo": "ops",
        "capture_git_mode": "local_only",
        "allow_direct_capture": True,
    }
    config.config_path.write_text(yaml.safe_dump(data))
    config = load_project_config(config.config_path)
    services.sync_project(config)
    services.index_project_from_config(config_path=config.config_path, repo_id="work")
    return config, origin


@pytest.mark.parametrize("project_state", [False, True])
def test_mixed_source_code_freshness(tmp_path, monkeypatch, project_state):
    from project_knowledge_mcp.code_context import CodeProviderHealth

    config, origin = mixed_source_project(tmp_path)
    monkeypatch.setattr(
        services.CodeGraphProvider,
        "health",
        lambda self: CodeProviderHealth(
            configured_provider="codegraph",
            active_provider="codegraph",
            codegraph_enabled=True,
            codegraph_healthy=True,
            fallback_available=True,
        ),
    )
    monkeypatch.setattr(services.CodeGraphProvider, "search_code", lambda *args, **kwargs: [])
    origin.rename(origin.with_name("offline"))
    services.sync_project(config)
    statuses = services.check_project_staleness_from_config(config_path=config.config_path)["repos"]
    assert not next(s for s in statuses if s["repo_id"] == "work")["reindex_needed"]
    selected = services.search_code_from_config(
        config_path=config.config_path, query="absentmarker", repo_id="work"
    )
    assert not selected.get("error")
    assert selected.get("freshness_state") != "stale"
    assert "retrieval is incomplete" not in selected["markdown"].lower()
    if project_state:
        packet = services.get_project_state_from_config(
            config_path=config.config_path, topic="absentmarker", view="doctrine"
        )["sections"]["implementation_evidence"]
    else:
        packet = services.search_code_from_config(
            config_path=config.config_path, query="absentmarker"
        )
    assert packet.get("retrieval_state") == "partial"
    assert packet["freshness_state"] == "stale"
    assert packet["match_state"] == "indeterminate"


@pytest.mark.parametrize(
    "writer_kind",
    ["note", "draft", "authority", "index_repo", "index_document", "index_config", "index_open"],
)
def test_failed_refresh_serializes_workspace_writes(tmp_path, monkeypatch, writer_kind):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from project_knowledge_mcp import index

    config, origin = mixed_source_project(tmp_path)
    data = yaml.safe_load(config.config_path.read_text())
    data["repos"][0].update(id="remote", role="work", writable=False)
    data["repos"][1].update(id="ops", role="ops")
    config.config_path.write_text(yaml.safe_dump(data))
    config = load_project_config(config.config_path)
    services.sync_project(config)
    services.index_project_from_config(config_path=config.config_path, repo_id="ops")
    git(config.ops_repo.path, "config", "user.email", "test@example.com")
    git(config.ops_repo.path, "config", "user.name", "Test")
    monkeypatch.setattr(services, "_command_exists", lambda command: False)
    if writer_kind.startswith("index_"):
        (config.ops_repo.path / "writer.md").write_text("# concurrentwritermarker")
    advance(origin, "failedgeneration")
    entered = threading.Event()
    release = threading.Event()
    started = threading.Event()
    finished = threading.Event()
    write_entered = threading.Event()
    writer_thread = None
    original_validate = services._load_validated_config_for_write
    original_connect = index._connect

    def observe(function):
        def wrapped(*args, **kwargs):
            if threading.get_ident() == writer_thread:
                write_entered.set()
            return function(*args, **kwargs)

        return wrapped

    monkeypatch.setattr(services, "_load_validated_config_for_write", observe(original_validate))
    monkeypatch.setattr(index, "_connect", observe(original_connect))
    original = services.index_project_from_config

    def fail_refresh(**kwargs):
        if kwargs.get("repo_id") == "remote":
            original(**kwargs)
            entered.set()
            assert release.wait(10)
            raise RuntimeError("injected post-commit refresh failure")
        return original(**kwargs)

    monkeypatch.setattr(services, "index_project_from_config", fail_refresh)

    def write():
        nonlocal writer_thread
        writer_thread = threading.get_ident()
        started.set()
        try:
            if writer_kind == "note":
                return services.add_project_note_from_config(
                    "Writer", "concurrentwritermarker", config_path=config.config_path
                )
            if writer_kind == "draft":
                return services.create_draft_artifact_from_config(
                    "adr_draft", "Writer", "concurrentwritermarker", config_path=config.config_path
                )
            if writer_kind == "authority":
                return services.propose_authority_change_from_config(
                    "Writer",
                    "Review",
                    [
                        {
                            "operation": "add_file",
                            "path": "writer.md",
                            "content": "concurrentwritermarker",
                        }
                    ],
                    config_path=config.config_path,
                )
            if writer_kind == "index_open":
                return ProjectIndex.open(config.storage.state_dir)
            if writer_kind == "index_config":
                return original(config_path=config.config_path, repo_id="ops")
            kwargs = dict(state_dir=config.storage.state_dir, repo_id="ops", role="ops")
            if writer_kind == "index_document":
                return index.index_document(config.ops_repo.path, "writer.md", **kwargs)
            return index.index_repo(config.ops_repo.path, **kwargs)
        finally:
            finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        refresh = executor.submit(services.sync_project, config, "remote")
        assert entered.wait(10)
        writer = executor.submit(write)
        try:
            assert started.wait(10)
            assert not write_entered.wait(0.2), "writer must wait for synchronization restoration"
            assert not finished.is_set()
        finally:
            release.set()
        refreshed = refresh.result(timeout=10)
        result = writer.result(timeout=10)
    assert refreshed["repos"][0]["last_sync_error"]
    if writer_kind == "index_open":
        assert result.db_path.exists()
    elif writer_kind == "authority":
        assert result["status"] == "branch_prepared_pr_not_opened"
        assert (config.ops_repo.path / "writer.md").read_text() == "concurrentwritermarker"
    else:
        if writer_kind in ("note", "draft"):
            assert result["indexed"]
        assert ProjectIndex.open(config.storage.state_dir).search("concurrentwritermarker")


def test_capture_lock_reclaims_dead_pid(tmp_path):
    import os
    import sys

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    process.kill()
    process.wait(timeout=5)
    with pytest.raises(ProcessLookupError):
        os.kill(process.pid, 0)
    path = tmp_path / "capture.lock"
    path.write_text(str(process.pid))
    lock = services._CaptureLock(path, timeout_seconds=0.05)
    try:
        assert lock.acquire() is None
        assert path.read_text() == str(os.getpid())
    finally:
        lock.release()
    assert not path.exists()


@pytest.mark.parametrize("content", ["current", "", "garbled", "-1", "0"])
def test_capture_lock_preserves_live_or_unverifiable_owner(tmp_path, content):
    import os

    path = tmp_path / "capture.lock"
    content = str(os.getpid()) if content == "current" else content
    path.write_text(content)
    lock = services._CaptureLock(path, timeout_seconds=0.01)
    assert "Timed out" in lock.acquire()
    lock.release()
    assert path.read_text() == content


def test_capture_lock_does_not_unlink_replacement(tmp_path, monkeypatch):
    import os

    path = tmp_path / "capture.lock"
    path.write_text("123456789")

    def replaced_while_checking(pid, signal):
        if pid == 123456789:
            path.rename(tmp_path / "old.lock")
            path.write_text(str(os.getpid()))
            raise ProcessLookupError

    monkeypatch.setattr(services.os, "kill", replaced_while_checking)
    lock = services._CaptureLock(path, timeout_seconds=0.01)
    assert "Timed out" in lock.acquire()
    assert path.read_text() == str(os.getpid())


def test_owner_metadata_stores_only_remote_digest(tmp_path):
    import hashlib
    import json

    config, origin = remote_project(tmp_path)
    services.sync_project(config)
    owner = config.ops_repo.path / ".git/pkmcp-owner.json"
    identity = json.loads(owner.read_text())
    assert identity == {
        "project": config.project.id,
        "repo": "ops",
        "remote_sha256": hashlib.sha256(str(origin).encode()).hexdigest(),
    }
    assert str(origin) not in owner.read_text()
    assert not services.sync_project(config)["repos"][0]["reindex_needed"]
    identity["remote_sha256"] = hashlib.sha256(b"another exact URL").hexdigest()
    owner.write_text(json.dumps(identity))
    packet = services.sync_project(config)["repos"][0]
    assert "ownership" in packet["last_sync_error"]
    assert not packet["retrieval_available"]
