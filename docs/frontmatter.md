# Frontmatter Reference

PKMCP indexes Markdown frontmatter into normalized `type`, `status`, `authority`, `tags`, and `superseded_by` metadata. Query tools use these normalized values for filtering and ranking, so canonical docs should set them explicitly when path inference is not enough.

## Valid Values

`type` must be one of:

- `doctrine`
- `decision`
- `discussion`
- `note`
- `open_question`
- `handover`
- `proposal`
- `doctrine_delta`
- `adr_draft`
- `decision_proposal`
- `review_packet`
- `evidence`
- `code_doc`
- `project_brief`
- `text`
- `code`
- `test`
- `schema`

`status` must be one of:

- `current`
- `accepted`
- `draft`
- `captured`
- `open`
- `closed`
- `superseded`
- `rejected`

`authority` must be one of:

- `implementation_truth`
- `canonical`
- `accepted_decision`
- `proposal`
- `working`
- `capture`
- `historical`
- `superseded`
- `rejected`

## Authority Defaults

When `authority` is omitted but `status` is valid, PKMCP derives authority from status:

| status | default authority |
| --- | --- |
| `current` | `canonical` |
| `accepted` | `accepted_decision` |
| `draft` | `working` |
| `captured` | `capture` |
| `open` | `working` |
| `closed` | `historical` |
| `superseded` | `superseded` |
| `rejected` | `rejected` |

Unknown `type`, `status`, or `authority` values are normalized with path rules and reported as index warnings.

## Path Inference

If frontmatter omits fields, PKMCP infers defaults from the repo role and path:

| path pattern | inferred type | inferred status | inferred authority |
| --- | --- | --- | --- |
| work repo `src/` | `code` | `current` | `implementation_truth` |
| work repo `tests/` | `test` | `current` | `implementation_truth` |
| work repo `schemas/` | `schema` | `current` | `implementation_truth` |
| `docs/doctrine/`, `doctrine/` | `doctrine` | `current` | `canonical` |
| `docs/decisions/accepted/`, `decisions/accepted/` | `decision` | `accepted` | `accepted_decision` |
| `docs/PRD.md`, `project-brief.md` | `project_brief` | `current` | `canonical` |
| `docs/open-questions/`, `open-questions/` | `open_question` | `open` | `working` |
| `docs/proposals/`, `proposals/` | `proposal` | `draft` | `working` |
| `docs/handovers/`, `handovers/` | `handover` | `draft` | `working` |
| `docs/discussions/`, `discussions/` | `discussion` | `captured` | `capture` |
| `docs/notes/`, `notes/`, `inbox/` | `note` | `captured` | `capture` |
| `docs/rejected/`, `rejected-models/` | `rejected_model` | `rejected` | `rejected` |
| other ops repo paths | `note` | `draft` | `working` |
| other non-ops repo paths | `text` | `draft` | `working` |

`rejected_model` is an inferred path value, not a valid explicit frontmatter `type` value. For explicit frontmatter, use one of the valid `type` values above.

Flat decision paths such as `decisions/ADR-0001.md` are not inferred as accepted decisions. Use explicit frontmatter for those files:

```yaml
---
type: decision
status: accepted
authority: accepted_decision
---
```

## Query Tool Constraints

- `search_ops` searches ops docs and accepts filters for `doc_type`, `status`, `authority`, and `include_superseded`.
- `search_decisions` scopes results to decision documents and accepted/draft decision status rules.
- `get_current_doctrine` returns only `type: doctrine` with `status: current` and `type: decision` with `status: accepted`.
- `search_open_questions` returns `type: open_question` docs and defaults to `status: open`.

If a document has no frontmatter and path inference classifies it as `note`, content search may find it through `search_ops`, but scoped tools such as `get_current_doctrine` will not include it.

## Authority Proposal Tool

`propose_authority_change` accepts caller-supplied file changes only. Each item in `changes` must have:

```json
{
  "operation": "add_file",
  "path": "docs/decisions/accepted/0001-example.md",
  "content": "# Full file content\n"
}
```

Rules and constraints:

- `operation` must be `add_file` or `replace_file`.
- `path` must be a safe repo-relative path.
- `content` is the full file content to write.
- `add_file` fails if the target already exists.
- `replace_file` fails if the target does not already exist as a file.
- The configured writable capture repo must have a clean Git workspace before the tool runs.
- The tool prepares a branch, commit, and optional PR; it does not approve, merge, or promote authority by itself.
