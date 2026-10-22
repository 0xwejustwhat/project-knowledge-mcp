from pathlib import Path
import subprocess

from project_knowledge_mcp.code_context import CodeGraphProvider
from project_knowledge_mcp.config import ProjectKnowledgeConfig, RepoConfig
from project_knowledge_mcp.index import ProjectIndex, index_repo


def test_explicit_managed_root_indexes_only_source_files(tmp_path: Path):
    state = tmp_path / ".project-knowledge"
    root = state / "repositories" / "ops"
    root.mkdir(parents=True)
    (root / "knowledge.md").write_text("managedneedle")
    (state / "internal.md").write_text("internalneedle")
    (root / "secrets.md").write_text("secretneedle")
    index_repo(root, state_dir=state, repo_id="ops", role="ops")
    index = ProjectIndex.open(state)
    assert len(index.search("managedneedle")) == 1
    assert not index.search("internalneedle")
    assert not index.search("secretneedle")
    index_repo(state, state_dir=state, repo_id="internal", role="ops")
    assert not index.search("internalneedle")
    assert len(index.search("managedneedle")) == 1


def test_managed_codegraph_scope_keeps_state_and_sensitive_paths_out(tmp_path: Path):
    state = tmp_path / ".project-knowledge"
    root = state / "repositories" / "work"
    root.mkdir(parents=True)
    repo = RepoConfig(
        id="work",
        role="work",
        path=root,
        source_mode="remote",
        remote_url="/example/remote.git",
        canonical_branch="main",
    )
    config = ProjectKnowledgeConfig(
        schema_version=1, project={"id": "test"}, storage={"state_dir": state}, repos=[repo]
    )
    provider = CodeGraphProvider(config)
    for name in [
        "ok.py",
        ".env.py",
        "secrets.py",
        "tokens.py",
        ".git/a.py",
        ".project-knowledge/a.py",
        "excluded/a.py",
    ]:
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("print(1)")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "ok.py"], check=True)
    repo.exclude_globs = ["excluded/**"]
    (state / "internal.py").write_text("private")
    assert provider._provider_path_allowed(repo, "ok.py")
    for name in [
        "../../internal.py",
        ".env.py",
        "secrets.py",
        "tokens.py",
        ".git/a.py",
        ".project-knowledge/a.py",
        "excluded/a.py",
    ]:
        assert not provider._provider_path_allowed(repo, name)
    internal = repo.model_copy(update={"path": state})
    assert not provider._provider_path_allowed(internal, "internal.py")
    nested = repo.model_copy(update={"path": root / "excluded"})
    assert not provider._provider_path_allowed(nested, "a.py")
