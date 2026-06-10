# Decision 0003: Step 7 includes capture writes and authority-change PR proposals

Status: accepted-for-mvp-planning
Date: 2026-06-10

## Context

Project Knowledge MCP is intended to help hosted and local assistants stay current across discussions, implementation work, and project decisions. Read-only context is useful, but it does not close the loop: important conclusions remain trapped in chat unless the assistant can write them back somewhere.

At the same time, the server must not turn casual assistant output into accepted project truth. Canonical doctrine, accepted decisions, PRD/spec changes, and resolved-question state need review.

If Step 7 only adds low-authority capture writes and blocks canonical mutation, users will eventually bypass the policy whenever they need an authority-changing update. If Step 7 only adds PR proposals and skips easy capture, routine observations will remain too expensive to save.

## Decision

Step 7 includes both paths:

1. **Low-authority capture** via `add_project_note(...)`.
   - Fast, direct, safe write into configured capture/proposal areas.
   - Immediately indexed as low-authority context.

2. **Draft/proposal artifacts** via `create_draft_artifact(...)`.
   - Non-canonical working documents for doctrine deltas, ADR drafts, decision proposals, review packets, and open questions.

3. **Reviewable authority-change PRs** via `propose_authority_change(...)`.
   - Accepts caller-supplied file additions/mutations.
   - Creates an isolated branch/commit and opens a PR when credentials are configured.
   - Returns structured manual PR instructions when PR creation is unavailable.
   - Never merges, approves, promotes, or decides truth.

## Boundary

Allowed in MVP:

- direct low-authority capture writes;
- non-canonical draft/proposal writes;
- mechanical PR creation for caller-supplied authority changes.

Not allowed in MVP:

- direct canonical mutation on the active branch;
- MCP-authored doctrine/decision content without caller-supplied proposed changes;
- automatic merge, approval, accepted-status mutation, or governance bypass.

## Rationale

Both write paths are necessary in this phase because they solve different failure modes:

- Capture prevents useful context from being lost.
- PR proposals prevent the write policy from becoming a dead end when a real authority change is needed.
- Human/GitHub review remains the authority boundary, so project truth changes stay deliberate.

Short form: easy to remember, easy to propose, hard to canonize accidentally.
