---
type: operations
title: Adopting docsweep in a Project (inject/eject and templates/)
description: What python -m docsweep inject/eject actually writes into a target project, how state presets and the shipped templates/ ruleset relate, and the config precedence that governs work-queue and label behavior.
tags: [inject, templates, configuration, onboarding]
verified:
  - by: openwiki/0.5.0
    at: 2026-09-08T12:41:37.379Z
sources:
  - id: openwiki-source-995d4fcbf54c5706a4b44466
    resource: repo://docsweep/config.py
  - id: openwiki-source-1ff76b5f306dff17b583fa6e
    resource: repo://docsweep/inject/api.py
  - id: openwiki-source-b1223c4f312d0403bac06b2c
    resource: repo://docsweep/inject/blocks.py
  - id: openwiki-source-5f6357306945a91456885466
    resource: repo://docsweep/presets.py
generated: { by: "claude-code", at: "2026-09-08T12:41:37.379Z" }
---

# Adopting docsweep in a Project (inject/eject and templates/)

Two related but distinct things make up "adopting docsweep" in a project: the
static files under `templates/` in this repository (a ruleset a maintainer
can copy in by hand), and `docsweep inject`/`eject` (a command that writes and
removes an equivalent, auto-generated, machine-synced version of that ruleset
directly). `templates/CLAUDE.md`/`templates/AGENTS.md` are the **shipped
product** meant for *adopters'* own repositories — a different artifact from
this repository's own root `CLAUDE.md`/`AGENTS.md`, which document how to
develop docsweep itself.

## `inject`: managed blocks, not whole-file ownership

`docsweep/inject/api.py`'s module docstring describes exactly what a project
`inject` writes:

1. `.docsweep.yaml` — the tool's own config (states/preset), written only if
   one doesn't already exist; an existing file is left untouched
   (`result.skipped.append(".docsweep.yaml (既存・温存)")`).
2. A managed block inside `CLAUDE.md` (and, if `AGENTS.md` already exists in
   the project, inside that file too) delimited by literal
   `<!-- docsweep:managed:start -->`/`<!-- docsweep:managed:end -->` markers
   (`docsweep/inject/blocks.py`).

`CLAUDE.md` is always the *primary* document — it receives the full label
block (a table of the state model's internal keys, labels, and auto-archive
status) plus, unless `include_guidance=False` is passed, the full guidance
block (session-start briefing instructions, OKF-frontmatter rules,
provenance routing, delegation-plan rules, template-section rules, and due-date
rules). `AGENTS.md` never duplicates that content — it only receives a short
pointer sentence directing the reader back to CLAUDE.md's managed block, so
there is exactly one source of truth for the ruleset text even in a
multi-agent-file repo (Codex reads `AGENTS.md`, which then points back at
`CLAUDE.md`).

### Idempotence and hand-edit protection

`_write_managed_file()` treats the block boundary as the unit of change: it
diffs the *current* block's content hash against the hash recorded the last
time `inject` ran (stored in `~/.docsweep/injected.json`, the inject
manifest). If they match, nothing is rewritten (`result.skipped`). If a
project's block content has drifted from what was last injected — meaning a
human hand-edited inside the managed markers — `inject` does **not** silently
overwrite it: it logs a warning and writes a private backup of the *entire
file* under docsweep's own manifest directory (`inject-backups/`, keyed by a
hash of the file's resolved path, deliberately **not** inside the project
itself) before proceeding to write the new block. If a file somehow ends up
with more than one managed block (e.g. from an older bug or manual
duplication), `inject` collapses them all into a single block at the first
occurrence's position rather than leaving multiple. Text outside the markers
— everything the user wrote themselves — is preserved byte-for-byte,
including its original newline convention (`\r\n` vs `\n`), which is
detected and matched before any block is written.

### `eject`: block-only removal, `.docsweep.yaml` optional

`eject()` is the reverse: it strips every managed block it finds (again using
the same hand-edit-detection/backup logic from `_strip_managed_blocks`,
sharing code with the write path) and, only when `--purge` is passed,
additionally deletes `.docsweep.yaml`. Hand-written content around the
managed block is never touched by `eject` either. If any target file
failed a UTF-8 decode during block stripping, `eject --purge` refuses to
delete `.docsweep.yaml` in the same run, rather than partially completing an
operation whose safety it can't fully verify.

## State presets: the two official starting points

`docsweep/presets.py` defines the fixed, versioned catalog `inject` chooses
from via `--preset`: `claude-jp` (Japanese, H1-label-only, the default) and
`frontmatter` (English, H1 labels *plus* a mirrored `status:` frontmatter
field for tools that prefer reading structured data over parsing a bracketed
H1). Both currently use the same underlying `DEFAULT_STATES` state model —
the presets differ in *language* and *detection surface*, not vocabulary.
Each `Preset` carries its own `version` string, bumped by hand whenever the
generated content's *meaning* changes; this version is recorded in the inject
manifest so the UI (and `docsweep list`) can show which preset revision is
currently applied to a given project, distinct from whether the block content
happens to be up to date.

## Global injection: one central file, per-tool minimal hooks

`inject_global()` (`docsweep inject --global`) takes a different shape:
because the guidance text is project-independent, it is generated once, and
tools are pointed at it rather than each getting their own full copy. For
`agent="claude"`, the entire guidance block is written to a docsweep-owned
central file at `~/.docsweep/guidance.md`, and the actual injected hook in
Claude's config is a single `@~/.docsweep/guidance.md` import line (Claude
Code's own `@import` mechanism) — one line to maintain, one place to update.
Other supported agents (currently `codex`) don't support that kind of
external import, so `inject_global` inlines the *entire* guidance block
directly into their config file instead; `eject_global` correspondingly only
deletes the shared `guidance.md` file once no remaining global injection for
an import-capable agent (Claude) still references it, so removing Codex's
global hook alone never orphans a file Claude still needs.

`inject --global` also creates a scaffold `~/.docsweep/config.yaml` — but
**only if one doesn't already exist** — pre-populated with commented-out
`work_dir`/`work_policy`/`secret_policy` and `due.default_offset_days` blocks
whose values exactly mirror the built-in defaults, so uncommenting a line
alone changes nothing until a value is actually edited (no "silent behavior
change from an unedited scaffold").

## Config precedence: CLI flag > project `.docsweep.yaml` > global config

The three-layer precedence introduced in
[Architecture Overview](/openwiki/architecture/overview.md) is implemented
concretely for the work queue by `docsweep/config.py`'s
`project_work_settings()`/`config_for_project()`: a project's own
`.docsweep.yaml` values for `work_dir`/`work_policy`/`secret_policy` are read
directly from that project's YAML and take precedence over whatever the
already-loaded global `Config` carries, falling back to the global values
(and finally to `DEFAULT_WORK_DIR`/`"private"`/`"block"`) only for keys the
project file doesn't set — a genuine per-key partial override, not an
all-or-nothing replacement. `resolve_work_dir()` additionally hardens this
against a misconfigured `work_dir`: it rejects any value that resolves
outside the project directory, including a POSIX-style leading absolute
path, a Windows drive-qualified path (`C:...`), or a `..`-escaping relative
path — closing the specific case where a Windows path like `C:foo` is *not*
flagged absolute by plain `pathlib` (because it lacks a leading separator)
but is still drive-qualified and would otherwise resolve against the current
process's drive instead of the intended project.

## Related pages

- [State Model and Document Lifecycle](/openwiki/architecture/state-model.md) — the `states:` vocabulary `inject`'s label block renders for a project.
- [CLI Command Surface](/openwiki/workflows/cli-reference.md) — where `inject`/`eject`/`new`/`list` sit among the rest of the CLI.
