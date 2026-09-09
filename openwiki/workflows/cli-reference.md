---
type: reference
title: CLI Command Surface
description: A grouped map of docsweep's argparse-based subcommands, where each is implemented, and the shared scope/config flags every scanning command accepts.
tags: [cli, reference, commands]
verified:
  - by: openwiki/0.5.0
    at: 2026-09-08T12:41:37.379Z
sources:
  - id: openwiki-source-54b65f67110f95b917a834e3
    resource: repo://docsweep/cli/parser.py
generated: { by: "claude-code", at: "2026-09-08T12:41:37.379Z" }
---

# CLI Command Surface

`docsweep/cli/parser.py`'s `build_parser()` constructs one `argparse`
parser with roughly 60 subcommands (some, like `provenance` and `project`,
nested further into their own sub-subcommands). `docsweep/cli/__init__.py`
dispatches each parsed subcommand name through a flat `_DISPATCH` dict to a
`cmd_*` handler, most of which live in `docsweep/cli/commands/read.py`
(read-only reporting) or `write.py` (mutating commands), with the rest split
into one module per feature area under `docsweep/cli/commands/`.

## Shared scope arguments

Most scanning/reporting commands (`scan`, `triage`, `apply`, `sweep`, `serve`,
`promote`, `index`, and others) share one helper, `_add_scope_args()`, which
adds: positional `paths` (an ad-hoc scan root list, no config file needed),
`--root` (repeatable), `--profile` (a named profile from config), `--config`
(override the global config path), `--project-dir` (which project's
`.docsweep.yaml` to read), `--lang`, `--work-dir`/`--work-policy`
(`private`/`shared`)/`--secret-policy` (`block`/`warn`/`off`), and
`--allow-sensitive`. `_build_config()` (also in `parser.py`) is the one place
that turns these parsed flags into a merged `Config`, implementing the CLI-flag
layer of the precedence chain described in
[Adopting docsweep in a Project](/openwiki/operations/adoption-and-templates.md).

## Scan and decide: the core triage loop

- `scan` (default when no subcommand is given, or when the first argument is
  an existing directory) — lists matched documents; `--all` shows everything
  instead of the default needs-decision/pending-only view; `--json` for
  machine output; `--project` to filter.
- `triage` — the AI/human "what's next" view: needs-decision + pending items
  oldest-first, each annotated with its `allowed_actions`. `--tag`/`--show`
  filter/augment the human table view; `--review` launches an interactive
  triage loop (`c`=done, `w`=watching, `x`=discard, `s`=skip, `l`=later,
  `o`=open, `q`=quit); `--head N` limits to N items for a one-item-at-a-time
  loop.
- `apply --path <file> --action <action>` — executes exactly one of a
  document's allowed actions (`discard`/`keep`/`resume`/`relabel`/`promote`);
  `--to` supplies the relabel target, `--watching-days N` is the one-off
  graduation-due override valid only for `relabel --to watching`, `--dry-run`
  previews without writing.
- `sweep` — the `--auto`-equivalent batch mover: archives every `done`/
  `discarded` document, never touches `watching`; `--project` scopes it,
  `--dry-run` previews.
- `promote` — release-time batch state change: `--state`/`--to` (default
  `watching`→`done`), `--due-expired` restricts to `watching` documents whose
  graduation date has arrived, `--yes` accepts the bulk-confirmation prompt
  that kicks in above `bulk_confirm_threshold` documents.
- `closeout-check --path <parent-plan> --to watching|done` — read-only
  parent/child plan completion check (see
  [Triage, Archive and Closeout Workflow](/openwiki/workflows/lifecycle-management.md)).
- `fix-conflict` — resolves a document flagged with a frontmatter/H1/filename
  state disagreement (optionally `--prefer h1`).
- `stale` — lists documents past their `review_status`-based staleness
  threshold.
- `find` / `show` — locate a document, or show what else references it
  (reverse lookup via `related`/`docsweep_parent`).
- `claim` — overwrites a document's frontmatter `owner` with the current
  user.

## Morning entry points and capture

- `brief` [`--all`/`--continue`] — decisively returns "today's one pick" for
  the current project (or all projects side by side).
- `cross [--project a,b] [--explain <path>]` — cross-project rollup: one
  top pick, runners-up, and per-project summaries.
- `capture --from clipboard|file <path> [--save-all]` — extracts
  plan/bugfix/pending drafts from pasted conversation text (heuristic or LLM
  mock extraction).
- `linkcheck` — cross-checks a plan's declared "files to change" against
  actual repository changes.
- `auto-triage --suggest` / `--apply <decisions.json>` — rule-based (future:
  LLM-delegatable) bulk state-transition suggestions.
- `graph` — the relationship network across `related`/`docsweep_parent`
  links.
- `resurrect` — finds archived documents similar to active ones (embedding
  opt-in, Jaccard by default).
- `pending` / `report` / `summary` — all-projects pending list, human weekly
  report, and AI-oriented compressed JSON, respectively.

## Index (SQLite cache)

- `index` — regenerates the cross-project `.docsweep/INDEX.md`/`.json`.
- `index-sync` — incremental sync into `~/.docsweep/index.db` (the fast path
  `scan_records()` prefers — see
  [Architecture Overview](/openwiki/architecture/overview.md)).
- `index-rebuild` — full rebuild.
- `index-watch` — filesystem-watch-driven continuous sync (needs the
  `watch` extra).
- `index-stats` / `index-vacuum` — inspect/compact the SQLite file.

## Document creation and provenance

- `new <plan|bugfix|pending> <topic>` — generates a fully-formed OKF-frontmatter
  document. Key flags: `--title`, `--due`/`--no-due`, `--project-dir`
  (auto-detected from cwd via project markers if omitted), `--split N`
  (generate a parent plus N child plans with bidirectional `related` and
  `docsweep_parent` wiring — see
  [Triage, Archive and Closeout Workflow](/openwiki/workflows/lifecycle-management.md)),
  `--titles A,B,C` (name each split child so its filename encodes its role),
  `--delegate` (mark the plan `docsweep_delegation: external` and scaffold its
  required `## C 詳細` section), and a full set of `--ai-*`/`--actor-key`/
  `--ai-session-log` overrides for
  [AI Authorship and Execution Provenance](/openwiki/integrations/provenance.md).
- `provenance {init, start, finish, check}` — the CLI surface for the
  provenance system: `init` backfills authoring metadata onto an existing
  document (`--update` corrects it in place), `start`/`finish` open and close
  a `C<N>` execution row, `check` cross-validates a document against the
  ledger. All four accept `--project-dir`/`--config`/`--json`, and
  `start`/`init` accept the same `--agent`/`--runtime`/`--provider`/
  `--model-id`/`--model-display`/`--reasoning`/`--model-source`/`--actor-key`
  metadata flags as `new`.
- `migrate-frontmatter [--dry-run|--apply]` — bulk-converts legacy single-field
  `status:` frontmatter into the two-axis `status:`/`docsweep_state:` form
  (see [OKF Compatibility](/openwiki/integrations/okf-compatibility.md)).
- `fix-related [--apply]` — symmetrizes one-directional `related:` references.

## Project configuration and adoption

- `inject [--project-dir] [--preset] [--no-guidance] [--no-yaml] [--lang]`
  / `inject --global [--agent claude|codex]` — writes the managed
  CLAUDE.md/AGENTS.md block and/or the global guidance hook (see
  [Adopting docsweep in a Project](/openwiki/operations/adoption-and-templates.md)).
- `eject [--purge]` / `eject --global` — the reverse.
- `list` — shows every project/global location docsweep has injected into.
- `project {list, enable, disable}` — toggles whether a discovered project
  root is included in scans/board (`~/.docsweep/excluded.json`), without
  moving or deleting any files.
- `config` — reads/writes small user-level settings (e.g. `user.name`).
- `claim` (see above) uses this same user identity.
- `init [--yes]` — first-run setup, creates `~/.docsweep/config.yaml`.
- `doctor [--json]` — environment health check: config, roots, index,
  injected projects, optional extras installed.
- `undo` — reverts the most recent recorded move (from `moves.jsonl`).
- `demo [--dir]` — generates a disposable sample project (8 documents
  spanning overdue/today/future/no-due/pending/done) for trying features
  without touching real projects; never registered into `roots:`.

## OKF export/import

- `export --okf [--out] [--project] [--include-archive] [--okf-version]
  [--okf-profile] [--okf-profile-sha256] [--json]` — see
  [OKF Compatibility](/openwiki/integrations/okf-compatibility.md).
- `okf-check <bundle> [--okf-version] [--okf-profile] [--okf-profile-sha256]
  [--json]` — read-only conformance check of an arbitrary OKF Bundle.
- `okf-profiles [--json]` — lists bundled profile versions.

## Web UI and MCP

- `serve [--port] [--no-browser] [--token] [--read-only]
  [--allow-root-mutation]` — starts the FastAPI board (see
  [Web UI](/openwiki/integrations/web-ui.md)). `--token` pins the access
  token (otherwise `DOCSWEEP_TOKEN` env or a fresh random token each run);
  `--read-only` 403s all mutating requests; `--allow-root-mutation` is
  required before the board's own UI can add a new scan root.
- `mcp` — starts the stdio MCP server (see
  [MCP Server and AI Agent Integration](/openwiki/integrations/mcp-server.md)).

## Miscellaneous reporting and utility commands

- `activity` / `timeline` / `history` — recent activity views;
  `history` renders the human-readable `moves.jsonl` log.
- `context` — `## context配分` table helpers.
- `intent` — natural-language-to-subcommand routing used by the MCP
  `route_intent` tool.
- `day {open, close}` — the once-a-day ritual: `open` surfaces today's pick
  plus yesterday's completions, `close` reports what was touched today and
  what's still due.
- `notify` — desktop/system notification integration.
- `memory` — memory/context-scan utility.
- `ics` — exports `due`-bearing documents as an `.ics` calendar feed.
- `completion` — shell completion script generation.
- `cookbook` — a scenario-indexed collection of copy-paste command
  recipes.
- `review-week` — a weekly variant of the interactive review flow.

## Related pages

- [Architecture Overview](/openwiki/architecture/overview.md) — the `run_scan`/`classify`/`apply_action` functions most of these commands ultimately call.
- [MCP Server and AI Agent Integration](/openwiki/integrations/mcp-server.md) — the 24 of these operations also exposed as MCP tools, and the naming differences between the two surfaces.
- [Triage, Archive and Closeout Workflow](/openwiki/workflows/lifecycle-management.md) — how `triage`/`apply`/`sweep`/`promote`/`closeout-check` compose into the day-to-day operating loop.
