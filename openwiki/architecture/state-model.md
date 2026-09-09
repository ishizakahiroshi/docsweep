---
type: concept
title: State Model and Document Lifecycle
description: The single source-of-truth state vocabulary in docsweep/states.py, how a document's state is detected from three competing signals, and the due-date second axis that drives the kanban board.
tags: [state-model, lifecycle, detection, configuration]
verified:
  - by: openwiki/0.5.0
    at: 2026-09-08T12:41:37.379Z
sources:
  - id: openwiki-source-3ded912d826ae4768af44c33
    resource: repo://docsweep/detect.py
  - id: openwiki-source-2cedab54da8ad2178ae8468b
    resource: repo://docsweep/engine.py
  - id: openwiki-source-09385e410b50d412a95b71ff
    resource: repo://docsweep/state.py
  - id: openwiki-source-91845947290dbc538fd6cb98
    resource: repo://docsweep/states.py
generated: { by: "claude-code", at: "2026-09-08T12:41:37.379Z" }
---

# State Model and Document Lifecycle

Every document docsweep manages (`plan_*.md`, `bugfix_*.md`, `pending_*.md`,
and any custom `TypeDef`) carries exactly one **state** drawn from a closed
vocabulary. That vocabulary, and the rules for what a state is allowed to do,
live in exactly one place.

## `states.py` is the single source of truth

`docsweep/states.py`'s module docstring states this directly: the `states:`
config key (backed by `DEFAULT_STATES` when unset) is the sole source of
truth, and detection logic, auto-archive eligibility, web display, and
injected-template wording are all *derived* from it — nothing hardcodes a
label string elsewhere. `docs/conventions.md` is the human-facing explanation
of this model, not an independent specification.

A `State` is a frozen dataclass of:

- `key` — the internal identifier (`planned`, `in-progress`, `watching`,
  `done`, `discarded`, `pending`).
- `labels` — a `{lang: bracket-label}` dict (e.g. `{"ja": "完了", "en":
  "Done"}`); `LANGS = ("ja", "en")` are the two built-in detection languages.
- `archive` — whether this state is archive-*eligible* at all (only `done`
  and `discarded` are `True` in `DEFAULT_STATES`).
- `auto_move` — whether an unattended `--auto`/`sweep` run may physically move
  a document in this state; also only `done` and `discarded`. `watching` is
  `archive=False, auto_move=False` — it is explicitly excluded from automatic
  movement so that "fixed but resting to confirm it doesn't regress" documents
  are never swept away on their own.
- `extra_aliases` — retired label strings accepted only on the *read* side.
  For example `in-progress` carries `extra_aliases=("対応中", "Active")`
  because a prior version had a separate `[対応中]` (bugfix-only "Active")
  label that was merged into `in-progress`; existing files using the old label
  keep working without being rewritten.

`StateModel.match()` resolves a raw label token to a `State` by exact match
first, then by a bounded suffix match (so `[v0.1.0 完了]` or `[draft 計画]`
still resolve, guarded by requiring a non-alphanumeric character immediately
before the matched alias, to avoid false positives). `build_state_model()`
parses a project's `states:` YAML config and **fails fast** (raises
`ValueError`) on a duplicate `key` or on two different keys sharing an alias —
because a silently-overwritten duplicate would resolve a label to the wrong
state's `archive`/`auto_move` behavior without any visible error.

## Detecting a document's state: three signals, one precedence

`docsweep/detect.py`'s `detect_status()` reads a document through three
independent lenses and combines them with a fixed precedence, documented in
the module docstring as **frontmatter > H1 > filename** ("明示が強い" — the
more explicit signal wins):

1. **Frontmatter** — `docsweep_state:` (checked first) or, for backward
   compatibility, the OKF `status:` field *only when its value is not one of
   the reserved OKF lifecycle values* (`is_okf_lifecycle_status`), since OKF's
   `status` is a document-lifecycle axis (`draft`/`stable`/`deprecated`)
   distinct from docsweep's own work-state axis.
2. **H1 label** — a leading `[...]` bracket on the document's first `#`
   heading, e.g. `# [完了] タイトル`. Detection masks fenced code blocks
   first (`mask_code_fences`) so a `# ...` inside a ```` ``` ```` block is
   never mistaken for the real H1.
3. **Filename prefix** — e.g. `done_plan_xxx.md`. A leading segment that
   matches the document's own *type* name (e.g. `pending_*.md`'s `pending`
   prefix) is deliberately **not** treated as a state prefix, to avoid the
   type name colliding with the state key `pending`.

All three methods can be used at once; `detect_status` never auto-resolves a
disagreement. If two or more of the three non-`None` candidates differ, it
sets `conflict=True` on the returned `Detection` and leaves the final `state`
as whichever the precedence order picked — `classify()` (in `engine.py`) turns
that `conflict` into a `needs_fix` flag surfaced through `triage`, rather than
silently guessing which source is "right". `docsweep fix-conflict` is the
explicit command that resolves a flagged conflict (optionally with `--prefer
h1` to force the H1 value using `detect_h1_state`, which looks at H1 alone).

`detect_status` also extracts, in the same frontmatter pass, docsweep's OKF
extension fields (`tags`, `owner`, `review_status`, `related`,
`docsweep_parent`, `last_reviewed`, `docsweep_policy`) and a document's `due:`
date, surfacing separate warnings (in `frontmatter_warnings`) for a frontmatter
`type:` that disagrees with the filename-implied type, or a `docsweep_state`
that fails to resolve to any known state key — again, warn rather than
silently coerce.

## Two orthogonal axes: state (lifecycle) and `due` (deadline)

An H1 label is axis 1 — the document's lifecycle position, and the **only**
input to whether a document is archive-eligible. A document's frontmatter
`due:` date is a second, independent axis used purely for surfacing and
sorting; reaching a `due` date never by itself triggers an archive move.
`engine.classify()` turns `due` into flags rather than actions:

- For a movable (non-`done`/`discarded`) state whose `due` has passed, it
  raises `overdue_todo` ("should have been started/finished by now").
  `watching` is treated specially: because `watching` means "fixed and
  resting", its `due` marks a *graduation* checkpoint, so a past-or-equal
  `due` raises `overdue_graduate` instead, and only becomes an actual archive
  candidate when a human or agent explicitly runs `promote --due-expired`
  (`engine.promote_state(..., due_expired_only=True)`), which restricts the
  from-`watching` promotion set to documents whose `due` has already been
  reached (`_due_reached`).
- An unparseable `due` value raises `due_parse_error` rather than silently
  dropping the date.

Moving a document into `watching` sets its new graduation `due` from
`due.default_offset_days.plan_watching`/`bugfix_watching` (built-in default 3
days) unless a one-off `--watching-days N` is given to `apply --action
relabel --to watching`; re-relabeling an already-`watching` document preserves
its existing `due` rather than resetting it.

## Per-file bookkeeping that never touches the Markdown

`docsweep/state.py` maintains a companion `.docsweep/state.json` per project
(keyed by project-relative POSIX path) recording data that is explicitly kept
*out* of the Markdown file itself, because it isn't part of the document's
own state:

- `postpone_count` and `due_history` — incremented every time a due date is
  pushed out via `update_due`, and reset to `0` only on a fixed set of
  "actual progress" label transitions (`planned→in-progress`,
  `in-progress→watching`, `pending→planned`, `pending→in-progress`); moving to
  `done`/`discarded` does not need a reset since the document leaves the
  active board entirely.
- `label_history` — every state transition, recorded by `update_status`.
- `snoozed_until` / `pinned` — a human's temporary override of how a document
  is ranked/shown on the board ("don't show me this today" / "always show
  this first"). The module docstring is explicit that this is a UX-only
  human veto over sorting, never a change to the document's actual state.

`state.json` is treated as disposable, rebuildable cache, not a source of
truth: on missing or corrupt JSON, `state.py`'s `load()` silently returns an
empty `StateDoc` rather than raising, because the Markdown file's own H1/
frontmatter remains the record of truth for state itself.

## Invariant: no physical delete

No code path in docsweep — CLI, MCP, or `--auto` — implements a delete
operation. A `[廃止]`/`discarded` document is moved into an `archive/`
directory exactly like a `done` one (same `archive_file()` machinery covered
in [Architecture Overview](/openwiki/architecture/overview.md)); recovering it
is a matter of moving the file back out of `archive/`, not restoring from a
backup.

## Related pages

- [Architecture Overview](/openwiki/architecture/overview.md) — how `classify()` consumes a `Detection` to produce flags and allowed actions.
- [Triage, Archive and Closeout Workflow](/openwiki/workflows/lifecycle-management.md) — the operational flow (`triage`/`apply`/`sweep`/`promote`/`closeout-check`) built on top of this state model.
- [OKF (Open Knowledge Format) Compatibility](/openwiki/integrations/okf-compatibility.md) — how docsweep's `docsweep_state` axis coexists with OKF's own `status` lifecycle field.
