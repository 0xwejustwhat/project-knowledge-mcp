# PKMCP Agent Skill

[`pkmcp-codebase-questions/SKILL.md`](pkmcp-codebase-questions/SKILL.md) is the canonical user-facing Agent Skill for working with a configured Project Knowledge MCP server. It covers authority-aware project retrieval, code evidence, staleness, targeted reindexing, direct verification, and safe write boundaries.

It intentionally excludes PKMCP maintainer procedures such as release management, implementation conventions, and repository-specific promotion workflows.

## Install

Copy the complete skill directory into the skills directory used by your agent harness. Preserve the directory name and `SKILL.md` filename.

For Hermes Agent, from the PKMCP repository checkout:

```bash
mkdir -p "$HOME/.hermes/skills"
cp -R samples/skills/pkmcp-codebase-questions "$HOME/.hermes/skills/"
```

Start a new agent session after installation so the skill catalog reloads.

The skill is otherwise harness-neutral. If an MCP client prefixes tool names with the server name, map those names to the unprefixed PKMCP tools while preserving arguments and semantics.
