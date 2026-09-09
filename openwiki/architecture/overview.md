---
type: architecture
title: Architecture Overview
description: How docsweep is put together — package layout, entrypoints, the core scan/detect/classify/archive pipeline, and the three-layer configuration model that drives it.
tags: [architecture, cli, configuration, python]
verified:
  - by: openwiki/0.5.0
    at: 2026-09-08T12:41:37.379Z
sources:
  - id: openwiki-source-1a356edabb201c449400c410
    resource: repo://docsweep/archive.py
  - id: openwiki-source-d2f83bc407419bf3b49ee711
    resource: repo://docsweep/cli/__init__.py
  - id: openwiki-source-995d4fcbf54c5706a4b44466
    resource: repo://docsweep/config.py
  - id: openwiki-source-2cedab54da8ad2178ae8468b
    resource: repo://docsweep/engine.py
generated: { by: "claude-code", at: "2026-09-08T12:41:37.379Z" }
---

# Architecture Overview

docsweep is a single Python package (`docsweep/`) that is used three ways: as a
CLI (`python -m docsweep ...`), as a local FastAPI web UI (`docsweep serve`),
and as an MCP stdio server (`docsweep mcp`). All three surfaces call into the
same core modules — there is one engine, not three.

## Entrypoints

- `docsweep/__main__.py` is the `python -m docsweep` entrypoint; it just calls
  `docsweep.cli.main`.
- `pyproject.toml` also registers a `docsweep` console script pointing at
  `docsweep.cli:main`, but the project's own docs deliberately recommend
  `python -m docsweep` over relying on that script being on `PATH`.
- `docsweep/cli/__init__.py:main` is the actual dispatcher. It parses argv with
  the parser built in `docsweep/cli/parser.py`, looks the subcommand up in a
  `_DISPATCH` dict mapping command name → `cmd_*` handler function, and calls
  it. If the first token isn't a known subcommand, isn't a flag, and isn't an
  existing directory, `main` fails fast with "unknown command or scan
  directory" instead of silently treating a typo as an empty scan; if it *is*
  an existing directory or starts with `-`, it is rewritten as an implicit
  `scan` invocation (`docsweep ./thisproject` shorthand).
- `docsweep/cli/commands/` holds the actual `cmd_*` implementations, split into
  `read.py` (read-only reporting commands), `write.py` (commands that mutate
  files or state), plus dedicated modules per feature area: `index.py`,
  `inject.py`, `provenance.py`, `mcp.py`, `serve.py`, `init.py`, `ics.py`,
  `memory.py`, `notify.py`, `completion.py`, `excluded.py`.

## Core pipeline: scan → detect → classify → act

The heart of the tool is `docsweep/engine.py`, which composes three lower
layers:

1. **`docsweep/scan.py`** walks each configured root directory
   (`scan_root`/`scan`), skipping `.git`, `node_modules`, `__pycache__`,
   `.venv`, `.mypy_cache` and each project's own configured archive directory
   names outright, then applying `.gitignore`-style patterns (best-effort
   `fnmatch`, not full gitignore semantics) plus any custom `ignore:` globs
   from config. A private work-queue path (e.g. `docs/local/`) is kept in
   scan results even when `.gitignore` excludes it, via
   `_is_work_queue_path`, so `brief`/`triage` can still see an AI agent's
   private planning documents. Each matched file becomes a `ScannedDoc`
   pairing a `FileRecord` with a `Detection` result and its raw text.
2. **`docsweep/detect.py`** (`detect_status`) reads a document's H1 line and
   frontmatter to determine its `state` (e.g. `planned`, `in-progress`,
   `watching`, `done`, `discarded`, `pending`) and flags a `conflict` when the
   three possible signals — frontmatter `docsweep_state`/legacy `status`, the
   H1 bracket label, and a filename prefix — disagree. Detection never
   auto-resolves a conflict; it surfaces it as a fact for `classify` to flag.
3. **`docsweep/engine.py`** (`classify`) turns a `ScannedDoc`'s raw detection
   into decision-support data on the `FileRecord`: `flags` (e.g.
   `needs_fix`, `conflict`, `stale`, `needs_decision`, `overdue_todo`,
   `overdue_graduate`, `due_parse_error`) and `allowed_actions` (a closed set
   drawn from `keep`/`discard`/`resume`/`promote`/`relabel`, computed purely
   from the document's current `state`). `run_scan` composes `scan()` +
   `classify()` and then filters through the global exclude list
   (`excluded.py`); on a broken exclude config it fails closed by returning
   zero documents with an explicit error rather than showing everything
   (privacy fail-open would be the wrong default).
4. `docsweep/engine.py` also holds the action layer that turns a decision into
   a filesystem effect: `apply_action` executes exactly one of a record's
   `allowed_actions` against a single file (backed by `docsweep/services/status.py`'s
   `update_status` for H1/frontmatter rewrites and `docsweep/archive.py`'s
   `archive_file` for the actual move), while `auto_sweep` and `promote_state`
   are the batch equivalents used by the `sweep` and `promote` commands.

`docsweep/archive.py` implements the physical move: it reserves a
collision-free destination filename with `O_CREAT|O_EXCL` (closing a
check-then-move race between concurrent archive operations), moves the file
with `shutil.move` (so archive and source do not need to be on the same
filesystem volume), and appends one JSON line per move to
`<root>/.docsweep/moves.jsonl` — the audit trail that `docsweep undo` and
`docsweep history` read back.

## Decisions vs. work: a deliberate split

`engine.py`'s module docstring states the design principle directly: raising a
flag / deciding a label is a human-or-AI judgment call, while *moving a file to
archive* is mechanical work that only `done` and `discarded` states are
eligible for (`auto_move=True`); `watching` is never touched by an automatic
sweep, and a `watching` document only becomes archive-eligible again through
the explicitly-invoked `promote --due-expired` path. This split is what lets
`sweep`/`--auto` be safe to run unattended (cron, CI, an AI agent) — it can
never discard or complete a document on its own opinion, only physically move
documents whose *state* a prior explicit decision already put into `done` or
`discarded`.

## Configuration layering

`docsweep/config.py` documents and implements a three-layer precedence:
**① CLI flags > ② project `.docsweep.yaml` > ③ global `~/.docsweep/config.yaml`**,
so that a user who only maintains the global file gets one effective layer,
and any project can locally override specific keys by placing a
`.docsweep.yaml` next to itself. `Config` is the resulting merged dataclass;
it carries `roots`/`profiles` (scan scope), `work_dir`/`work_policy`
(default `docs/local` / `private`), `secret_policy`, `types` (the
`TypeDef` registry — filename glob, required sections, stale-days — seeded
from `DEFAULT_TYPES` for `plan`/`bugfix`/`pending`/legacy/report types), a
`state_model` (see the state-model page), due-date thresholds and default
offsets, `bulk_confirm_threshold`, and more. Project boundaries themselves are
not a fixed folder depth: `detect_project_root` walks upward from a matched
file looking for the nearest ancestor containing one of
`DEFAULT_PROJECT_MARKERS` (`.git`, `.docsweep.yaml`, `package.json`,
`pyproject.toml`), so a `roots:` entry can point at a parent directory holding
many independently-nested projects at different depths.

`docsweep/scan.py`'s `scan_records` gives read-heavy commands a fast path: it
first tries to satisfy the query from the optional SQLite index at
`~/.docsweep/index.db` (`docsweep/index.py`), scoping any returned records to
the currently configured roots so a stale/shared index can't leak
out-of-scope projects, and only falls back to a full `run_scan` when the index
is empty, absent, or scoped to nothing relevant.

## Related pages

- [State Model and Document Lifecycle](/openwiki/architecture/state-model.md) — the `state_model`/label vocabulary this pipeline classifies documents against.
- [Triage, Archive and Closeout Workflow](/openwiki/workflows/lifecycle-management.md) — how `classify`'s output drives the human/AI-facing triage → apply → sweep → promote flow.
- [CLI Command Surface](/openwiki/workflows/cli-reference.md) — the full set of subcommands dispatched from `docsweep/cli/__init__.py`.
