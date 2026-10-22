# Canonical repository synchronization

A service repository can use `source_mode: remote`, `remote_url`, and
`canonical_branch` instead of `path`. PKMCP derives its disposable checkout at
`<state_dir>/repositories/<repo-id>`. Set `PROJECT_KNOWLEDGE_CONFIG` to the service
configuration before starting the existing `serve` command. Remote repositories
always synchronize at startup and then every `synchronization.interval_seconds`
(default 60 seconds), plus the time needed for bounded Git and CodeGraph operations
and local indexing. There is no manual synchronization CLI or enable switch.

```yaml
repos:
  - id: ops
    role: ops
    source_mode: remote
    remote_url: git@github.com:owner/project-ops.git
    canonical_branch: main
    writable: true
synchronization:
  interval_seconds: 60
```

Keep the project's existing storage, scope, CodeGraph, and write-policy settings.
The example describes the read synchronization configuration; remote capture has
not passed its separate canary gate. A work repository uses the same source fields
with `role: work` and the configured native CodeGraph command. Git credentials and
CodeGraph installation remain deployment prerequisites.

Only an explicit direct repository root within the managed namespace may be
indexed. Other state files remain excluded. Relative exclusions for `.git`,
`.project-knowledge`, environment files, secrets, tokens, configured scope, symlinks,
and maximum file size still apply. Managed roots must not overlap configured local
roots. A clone receives ownership metadata in its Git directory before PKMCP may
reset it; existing unowned directories are refused. Tracked modifications,
untracked files, and ignored files in an owned clone are disposable and repaired.
Use `workspace` for unpublished local work. `snapshot` remains pinned to its
explicit ref/commit. Synchronization never mutates either local mode.

Reads share a process-local lock with synchronization. Busy synchronization runs
skip; reads wait for a complete transition. A failed refresh restores the prior
checkout, required CodeGraph state, and SQLite index before releasing reads. The
SQLite backup is temporary and in memory, using SQLite's native backup mechanism;
it is not another persistent index. If alignment cannot be established, affected
retrieval is unavailable. A safe prior index remains readable as stale after a
fetch failure, including after process restart. Health reports readiness separately
from freshness. Stale or unavailable sources cannot establish trustworthy absence.

`check_project_staleness`, project state, session briefs, and retrieval expose
commit/scope facts and synchronization warnings. Remote current status requires a
successful fetch in this process, matching canonical branch/origin and checkout,
matching SQLite provenance/scope, a clean worktree, and initialized required
CodeGraph at the expected path with no pending changes or worktree mismatch.
A successful subsequent interval clears transient synchronization failures.

## Canary still required

Automated tests use local Git remotes and simulated native CodeGraph responses.
Before deployment, use one actual HTTPS service with ops and work repositories.
Push unique document and symbol markers from outside PKMCP twice while the same
process runs. Retrieve each through connected HTTPS MCP within an interval plus
operation allowance, without restart or manual trigger, and verify commit
provenance and native CodeGraph status. Inject fetch and downstream refresh
failures, check stale/unavailable behavior, and verify automatic recovery. Confirm
workspace-only content stays local. If that canary needs low-authority capture,
adapt and test the existing isolated-worktree path with targeted synchronization
after push as a separate acceptance step. Do not deploy fleet-wide before this gate.
