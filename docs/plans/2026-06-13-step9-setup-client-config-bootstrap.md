# Step 9 Setup and Client Config Bootstrap Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Keep controller ownership of CLI shape and authority/network defaults; use subagents for spec/quality review.

**Goal:** Complete the next post-Step-8 MVP setup gap: `project-knowledge setup`, `status`, and `print-client-config` should generate config, Docker/compose mount guidance, and client snippets without users manually assembling YAML or transport settings.

**Architecture:** Add a deterministic setup helper module behind Typer commands. The setup command supports non-interactive dry-run preview and safe file generation; it does not start containers or expose remote services. Client config rendering is transport-specific but always points to the same policy-enforced server. Status composes config validation and staleness checks.

**Tech Stack:** Python 3.12, Typer, PyYAML, existing config/staleness services, pytest CliRunner.

---

## Scope / Guardrails

- No shell execution tool in MCP.
- No automatic Docker startup in setup; emit commands/instructions only.
- Local transport remains stdio or loopback by default.
- Remote HTTPS config requires explicit token placeholder/instructions; never persist secrets.
- Do not create a special remote MCP surface.
- Preserve existing Step 8 Docker/Caddy artifacts and tests.

## Task 1: Add failing CLI setup/client/status tests

**Objective:** Capture Step 9 behavior before implementation.

**Files:**
- Modify: `tests/test_cli.py`

**Steps:**
1. Update CLI help test to require `setup`, `status`, and `print-client-config`.
2. Add a non-interactive dry-run setup test using a temp git ops repo and existing config helper.
3. Assert dry-run returns JSON with `would_write: false`, config target, loopback Docker command, compose command, and client snippet paths/instructions.
4. Add `print-client-config` tests for `hermes` stdio and generic HTTP/remote snippets.
5. Add `status --config` test asserting config validity and repo/staleness presence.

**Verification:**
```bash
PYTHONPATH=src python3 -m pytest tests/test_cli.py::test_cli_help_lists_step1_index_commands tests/test_cli.py::test_setup_dry_run_generates_config_mount_and_client_instructions tests/test_cli.py::test_print_client_config_outputs_policy_enforced_connection_snippets tests/test_cli.py::test_status_reports_config_and_repo_freshness -q
```
Expected before implementation: FAIL because commands are missing.

## Task 2: Implement deterministic setup helpers

**Objective:** Keep setup rendering testable outside Typer.

**Files:**
- Create: `src/project_knowledge_mcp/setup.py`

**Steps:**
1. Implement `build_client_config(...)` for `hermes`, `claude-desktop`, `cursor`, and `generic` clients across `stdio`, `streamable-http`, and `remote-https` transports.
2. Implement `build_setup_plan(...)` that can load an existing config or synthesize a minimal config from explicit project/ops/work repo paths.
3. Include generated Docker/compose commands, mount metadata, remote bridge instructions, and safety boundaries.
4. Implement `write_setup_artifacts(...)` for non-dry-run file output only under explicit config/project output paths.
5. Return structured JSON-serializable dicts only.

**Verification:**
```bash
PYTHONPATH=src python3 -m pytest tests/test_cli.py::test_setup_dry_run_generates_config_mount_and_client_instructions tests/test_cli.py::test_print_client_config_outputs_policy_enforced_connection_snippets -q
```

## Task 3: Wire Typer commands

**Objective:** Expose setup helpers through the package CLI.

**Files:**
- Modify: `src/project_knowledge_mcp/server.py`

**Steps:**
1. Add `setup` command with `--non-interactive`, `--dry-run`, `--config`, `--project-root`, `--ops-repo`, repeatable `--work-repo`, and `--client` options.
2. Add `print-client-config` command with `--config`, `--client`, `--transport`, `--http-url`, and `--remote-url`.
3. Add `status` command with `--config` that validates config and includes staleness summary when config is valid.
4. Keep JSON output deterministic with `sort_keys=True`.

**Verification:**
```bash
PYTHONPATH=src python3 -m pytest tests/test_cli.py -q
```

## Task 4: Document Step 9 setup usage

**Objective:** Make the new flow discoverable and aligned with Step 8 deployment artifacts.

**Files:**
- Modify: `README.md`

**Steps:**
1. Add quickstart using `project-knowledge setup --non-interactive --dry-run`.
2. Document `print-client-config` examples for Hermes stdio, local HTTP, and remote HTTPS.
3. Document `status --config`.
4. State setup does not start Docker or enable remote bridge automatically.

**Verification:**
```bash
PYTHONPATH=src python3 -m pytest tests/test_deployment_artifacts.py tests/test_cli.py -q
```

## Task 5: Final validation and review gates

**Objective:** Prove Step 9 does not regress Step 8 or prior MVP behavior.

**Steps:**
1. Run targeted Step 9 tests.
2. Run full tests.
3. Run ruff.
4. Run `git diff --check`.
5. Delegate spec compliance review against this plan and the MVP spec setup requirements.
6. Delegate code quality review for setup helpers/CLI/docs/tests.
7. Fix review blockers and rerun validation.

**Verification:**
```bash
PYTHONPATH=src python3 -m pytest tests/test_cli.py tests/test_deployment_artifacts.py -q
PYTHONPATH=src python3 -m pytest tests -q
python3 -m ruff check .
git diff --check
```
