---
type: workflow
title: Triage, Archive and Closeout Workflow
description: The end-to-end operating loop docsweep is built around — finding what needs a decision, executing that decision, batch-archiving what's already decided, and read-only closeout inspection for parent/child plans.
tags: [workflow, triage, archive, closeout]
verified:
  - by: openwiki/0.5.0
    at: 2026-09-08T12:41:37.379Z
sources:
  - id: openwiki-source-84b42a7575279f55d3364fc6
    resource: repo://docsweep/auto_triage.py
  - id: openwiki-source-53be89d1a382caec319f98be
    resource: repo://docsweep/bulk_confirm.py
  - id: openwiki-source-462eb39e148ae937ca42ebd4
    resource: repo://docsweep/closeout.py
  - id: openwiki-source-2cedab54da8ad2178ae8468b
    resource: repo://docsweep/engine.py
  - id: openwiki-source-d0d81f35cf9e952cdf8c141a
    resource: repo://docsweep/related.py
generated: { by: "claude-code", at: "2026-09-08T12:41:37.379Z" }
---

# Triage, Archive and Closeout Workflow

docsweep's day-to-day loop separates three concerns that are easy to conflate:
*deciding* what a document's next state should be, *executing* that decision
on one file, and *batch-moving* files whose decision has already been made.
This page walks the loop end to end; the underlying `classify()`/state
machinery it rests on is covered in
[Architecture Overview](/openwiki/architecture/overview.md) and
[State Model and Document Lifecycle](/openwiki/architecture/state-model.md).

## Step 1: `triage` surfaces what needs a decision

`triage` (CLI or the `triage` MCP tool) returns only documents flagged
`needs_decision` or in the `pending` state, oldest first, each carrying its
own `allowed_actions` — a closed set computed purely from that document's
current state (see `engine._allowed_actions()`). Because the action set is
attached to each item, a caller (human or AI) never has to guess which
operations are valid for a given document; it only has to pick one from the
list it was handed.

## Step 2: `apply` executes exactly one decision

`apply --path <file> --action <action>` (or the `apply` MCP tool) is the
single choke point for turning a decision into an effect on one file.
`engine.apply_action()` first re-validates that the requested action is still
in that document's `allowed_actions` — since state can change between when
`triage` was read and when `apply` runs — and then dispatches:

- `keep` — a no-op record, useful for audit trails ("looked at it, no change").
- `discard`/`promote` — relabels to `discarded`/`done` (unless the document
  carries `docsweep_policy: never_archive`, which raises rather than
  silently skipping) and archives it in the same call.
- `resume` — relabels back to `in-progress` (a `watching` or `discarded`
  document coming back into active work).
- `relabel --to <state>` — an arbitrary state transition to any known label,
  optionally carrying a one-off `watching_days` override (see
  [State Model and Document Lifecycle](/openwiki/architecture/state-model.md)).

## Step 3: `sweep`/`promote` move what's already decided, in bulk

`sweep` is the unattended batch mover: it only ever touches documents already
in `done`/`discarded` (`auto_movable`) and moves them to their project's
`archive/`, per the [decisions-vs-work split](/openwiki/architecture/overview.md#decisions-vs-work-a-deliberate-split).
`promote` is the release-time equivalent, generalized to any `from_state →
to_state` pair (defaulting to `watching → done`) and gated by `--due-expired`
when the intent is "only graduate what's actually due."

### Bulk operations get a second confirmation step above a threshold

`docsweep/bulk_confirm.py` implements a mechanism shared by `promote` and the
Web UI's bulk-archive/bulk-relabel actions: `evaluate()` is a pure function
that decides, from an operation's affected-item count and the configured
`bulk_confirm_threshold` (default 20, or `0` to always require confirmation),
whether a second confirmation step is needed. Each operation type has its
own distinct confirmation phrase (`PROMOTE` / `ARCHIVE` / `RELABEL`) rather
than one shared "yes" — the module's comment explains this is deliberate, so
that muscle-memory "hit Enter/type yes" reflexes don't carry across different
destructive operations. The CLI path is explicit that this cannot be
bypassed by running non-interactively: because docsweep's non-interactive
guarantee (no prompts for cron/CI/AI-delegated runs) is a hard invariant, the
CLI requires `--yes` outright above the threshold rather than falling back to
an interactive prompt — the same behavior whether or not a TTY is attached,
so a script's behavior doesn't silently change based on how it happens to be
invoked.

### Heuristic transition suggestions: `auto-triage`

`docsweep/auto_triage.py`'s `suggest` mode is a rule-based (not currently
LLM-backed, despite the module accommodating a future LLM decider)
alternative entry point into the same decision space: `_ruleset_decide()`
proposes `promote` for a `plan` whose `linkcheck` result shows its declared
files are already implemented, `discard` for a `needs_decision`-flagged
document older than 180 days, and `promote` for a `watching` document whose
due has arrived (or, for legacy documents with no `due` at all, older than 14
days as a fallback heuristic). Each suggestion carries a `confidence` score
and a human-readable `reason`; `auto-triage --apply <decisions.json>` then
replays a reviewed batch of suggestions through the same `apply_action()`
used everywhere else — the heuristic layer only ever *proposes*, and a
separate, explicit `--apply` step is what actually executes.

## `related`/`docsweep_parent`: the relationship graph underneath `show`/`graph`/closeout

`docsweep/related.py` centralizes the logic for resolving a document's
`related:` frontmatter list into concrete other documents — shared by CLI
`show`/`context`/`fix-related` and the Web UI's card-detail backreference
panel, rather than reimplemented per caller. A reference can be written as
either a bare filename or a full path; `resolve_ref()` accepts both.
`backref_records()` computes the *reverse* direction (which other documents
name this one in their own `related:`), which is what powers "what
references this plan" views. `fix-related --apply` is the tool that makes a
one-directional reference symmetric by writing the missing reverse entry.

## Parent/child plans and `docsweep_parent`

A plan produced via `new plan <topic> --split N` gets N child plans named
`plan_<topic>_c<N>[_<short>].md`, each carrying `docsweep_parent` pointing
back at the parent — the authoritative signal for parent/child relationship on
new-style plans. For plans predating this field, `closeout.py` falls back to
inferring the relationship only when *both* the filename matches
`^<parent-stem>_c\d+(?:_|$)` *and* the child's `related` list actually names
the parent — the parent's `related` list alone is deliberately not sufficient
to establish a child relationship, since that direction is a weaker, less
specific signal.

## `closeout-check`: read-only, mechanical-blocker vs. human-gate separation

`docsweep/closeout.py`'s module docstring states its scope precisely: this
service answers exactly one question — which mechanical conditions and human
gates remain before a parent/child plan set can move to a requested state —
and **never calls any write or archive service** itself. `check_closeout()`
produces one of three verdicts, computed as `not_ready` if any `blockers` are
present, else `manual_review_required` if any `manual_checks` remain, else
`ready`:

- **Blockers** (`not_ready`) include things a machine can conclusively
  determine are wrong: an unresolved `related`/`docsweep_parent` conflict, a
  required section (`## context配分`, `## 完了条件`, `## 検証`) missing
  entirely, an unchecked `- [ ]` checkbox, or verification prose that itself
  admits a nonzero failure count.
- **Manual checks** (`manual_review_required`) are things a human witness has
  to confirm even though the text is present and internally consistent — the
  regex-based classifier in `closeout.py` distinguishes *automated*
  evidence (mentions of `pytest`/`ruff`/`mypy`/CI/静的検査) from *manual*
  evidence (browser, 実機, production, Obsidian, Google Drive), and treats a
  claimed verification that reads as manual-only as something the tool cannot
  itself close out. A missing `## 受入条件` (acceptance criteria) section is
  specifically a manual check rather than a hard blocker, because that
  section only became a requirement after many existing plans were already
  written, and the tool deliberately doesn't retroactively fail plans written
  before the convention existed.
- Failure-language detection is itself nuanced: `_FAILURE_COUNT_RE` and
  `_ZERO_FAILURE_RE` specifically distinguish "0 failed" (a success signal)
  from "3 failed" (a real blocker) — the mere textual presence of the word
  "failed" is never used alone to decide pass/fail.

`ready` means *no machine-detectable blocker remains* — it is explicitly not
an instruction to automatically relabel or archive anything. State changes
and archiving are always a separate, subsequent, explicitly-approved step:
child plans are relabeled before the parent, and any archive move is preceded
by its own separate `--dry-run` and its own separate approval, never bundled
into the closeout check itself.

## Related pages

- [State Model and Document Lifecycle](/openwiki/architecture/state-model.md) — the state vocabulary and `allowed_actions` this workflow operates over.
- [MCP Server and AI Agent Integration](/openwiki/integrations/mcp-server.md) — the MCP tool equivalents of `triage`/`apply`/`sweep`/`promote`.
- [CLI Command Surface](/openwiki/workflows/cli-reference.md) — full flag reference for every command mentioned here.
