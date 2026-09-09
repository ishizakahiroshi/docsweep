---
type: integration
title: AI Authorship and Execution Provenance
description: The opt-in system that records which AI created a work document and which AI executed each context section, its external CSV ledger, session-transcript capture, and the manager repo/docsweep split.
tags: [provenance, ai-agents, audit-trail, configuration]
verified:
  - by: openwiki/0.5.0
    at: 2026-09-08T12:41:37.379Z
sources:
  - id: openwiki-source-c3461de151952358f827da43
    resource: repo://docsweep/provenance.py
  - id: openwiki-source-afa32d7726865d0c74163a0d
    resource: repo://docsweep/session_logs.py
generated: { by: "claude-code", at: "2026-09-08T12:41:37.379Z" }
---

# AI Authorship and Execution Provenance

docsweep can optionally record *which* AI (agent, runtime, provider, model)
authored a `plan`/`bugfix`/`pending` document and *which* AI executed each
labeled context section (`C1`, `C2`, ...) of it later — separate from the
document's own state, and kept in an external ledger rather than only in the
Markdown. This is entirely opt-in (`provenance.enabled: true` in config) and
implemented in `docsweep/provenance.py`.

## Two records per document: authoring vs. per-context execution

`initialize_document()` runs once when a work document is created (called
from `docsweep new`): it assigns the document a `work_id`, writes an
`authoring`-role row to the ledger, and patches the document's own frontmatter
with `work_id`, `ai_provenance_version`, the seven `ai_author_*` fields, and
`ai_execution_refs` (a list of ledger execution IDs the document currently
claims). If the document already has a `work_id` and complete `ai_author_*`
fields, a second call is a no-op (`status: "existing"`) unless `update=True`
is passed, in which case `_update_authoring_metadata()` corrects the existing
authoring row and frontmatter in place — but never touches `execution_id`,
`work_id`, or `started_at`, and appends an `ai-metadata-corrected` marker to
the ledger row's `notes` column rather than silently rewriting history, because
the ledger is designed to be append-only.

`start_execution()`/`finish_execution()` (used by `docsweep provenance
start`/`finish`, called before/after implementing, reviewing, or verifying a
specific `C<N>` context) record a *separate* row per unit of work, keyed by a
freshly generated `execution_id` and one or more context IDs matching
`^C[1-9][0-9]*$` (or the sentinel `not-applicable`, which cannot be combined
with real context IDs). `start_execution` writes that execution's ID into the
document's `## context配分` table under an `AI実行` column (creating the
column, and an adjacent `実行モデル` column recording
`"<role>: <provider>/<model_id>/<reasoning_profile>"`, if they don't exist
yet) via `_append_context_execution()`, validating that every requested
context actually exists as a row in that table *before* the ledger gets a new
row, specifically to avoid leaving an orphaned execution entry when a bad
context ID is requested. `finish_execution` can only close a row that is
still in the `started` result state, and rejects trying to finish an
already-finished execution.

## The ledger: an external, locked, append-mostly CSV

The ledger (`config.provenance_ledger`, a per-user CSV outside the repository
by default) is written under a simple file-based lock
(`_ledger_lock`, using `O_CREAT|O_EXCL` with a 120-second stale-lock
timeout and a 5-second acquisition timeout) so concurrent docsweep invocations
don't interleave writes. `_read_ledger()` refuses to read a CSV whose header
doesn't exactly match the current `LEDGER_FIELDS` tuple, raising
`ProvenanceError` rather than silently reading a schema-drifted file.
`_append_row()` refuses to insert a duplicate `execution_id`. `check_document()`
(`docsweep provenance check`) cross-validates a document against the ledger:
every `ai_execution_refs` entry must resolve to a real ledger row with a
matching `work_id`; there must be at least one `authoring` row; every
execution ID recorded in the `## context配分` table's `AI実行` column must
also appear in the frontmatter's `ai_execution_refs`; and conversely, every
non-authoring ledger row for this `work_id` (that isn't `not-applicable`) must
appear in the context table. Mismatches are `errors` (making the document
`valid: false`); a ledger `work_path` that no longer matches the document's
current path is only a `warning`, since files legitimately move.

## Never a fabricated value

`AIMetadata` is the dataclass carrying agent/runtime/provider/model
information for one authoring or execution row. Its `resolve()` classmethod
reads each field from an explicit override or a `DOCSWEEP_AI_*` environment
variable, but falls back to the literal string `"unknown"` for most fields and
specifically `"unavailable"` for `model_source` — and if `model_source`
resolves to `"unavailable"`, `model_id`/`model_display` are *also* forced back
to `"unknown"` even if a value was supplied for them, because an unconfirmed
model name without a stated source is worse than an explicit unknown. This is
the concrete implementation of the "never fabricate" invariant: a value that
cannot actually be determined is recorded as `unknown`/`unavailable`, never
guessed.

## Session transcript capture: refuse to guess

`docsweep/session_logs.py` resolves the filesystem path (never the contents)
of the AI CLI transcript that is writing the current document, so a document's
`ai_session_logs` frontmatter field can later point back to the full
conversation that produced it. Its module docstring states the operating
principle bluntly: **every resolver refuses to guess** — if candidate sessions
for the current working directory don't narrow to exactly one, nothing is
returned, because "a path pointing at a neighbouring session is worse than no
path at all... the record looks equally authoritative either way." Concretely,
`_only()` treats zero matches and two-or-more matches identically (`None`) —
picking one of several equally-plausible candidates is explicitly rejected as
worse than reporting nothing.

Claude Code is the one exception that needs no such narrowing: it exports its
own session ID via `CLAUDE_CODE_SESSION_ID`, which is used directly to glob
for `<config-dir>/projects/*/<session-id>.jsonl` — an exact identifier beats
any heuristic. Every other supported runtime (Codex, Grok, Copilot, Cursor) is
resolved by narrowing on **both** an exact working-directory match recorded in
that session's own metadata file *and* freshness (`_is_fresh`, a
`FRESH_WINDOW` of 10 minutes since the session's own last write, tolerating a
small negative clock skew), so a stale session in the same directory is
excluded rather than mistaken for the live one. opencode is explicitly
unsupported for this feature, per the module docstring, because it stores
every session in one shared SQLite database with no way to isolate a single
session's identity. Codex Desktop and VS Code's Codex extension sessions are
filtered out of the CLI-session candidate pool by an exclude-list check on
`source`/`originator` metadata (`vscode`/`desktop`/`ide`), because — per an
inline comment — what a client calls itself varies across versions, so an
exclude list is more durable than an allow list here.

Because an absolute session-log path embeds the OS username, `_session_log_lines()`
only ever writes it into frontmatter when the target document's queue is
`work_policy: private` (and `privacy_enforced(config)` holds) — a shared/public
work queue never receives this field at all.

## `manager: repo` vs. `manager: docsweep` vs. `disabled`

Every entry point (`initialize_document`, `start_execution`,
`finish_execution`, `check_document`) begins by calling `_delegated(config)`.
When `config.provenance_manager == "repo"`, every call short-circuits into a
`{"status": "delegated", "manager": "repo", "delegate_skill": ...}` response
and **does not touch the generic ledger at all** — this is the escape hatch
for a repository that already owns its own provenance schema and doesn't want
a second, competing record kept by docsweep. When provenance is disabled
(`provenance_enabled=False` or `manager="disabled"`), every entry point raises
`ProvenanceError` instead of silently doing nothing, so a caller that expected
provenance to be active gets an explicit failure rather than a document with
missing metadata. Only `manager: docsweep` (the default posture when
`provenance.enabled: true` is set) actually writes to the generic per-user
CSV ledger described above.

## Related pages

- [MCP Server and AI Agent Integration](/openwiki/integrations/mcp-server.md) — the MCP/CLI write operations whose executions provenance can attribute.
- [State Model and Document Lifecycle](/openwiki/architecture/state-model.md) — the H1/frontmatter document these provenance fields are attached to, distinct from the document's own work state.
