---
type: integration
title: MCP Server and AI Agent Integration
description: The stdio MCP tool surface in docsweep/mcp_server.py, how each tool maps onto the same core engine and services the CLI uses, and the CLI-first philosophy for AI agents that skip MCP entirely.
tags: [mcp, ai-agents, integration, cli]
verified:
  - by: openwiki/0.5.0
    at: 2026-09-08T12:41:37.379Z
sources:
  - id: openwiki-source-740a5fdcf96aafcab20028c6
    resource: repo://docs/ai-agent-integration.md
  - id: openwiki-source-a77c8c2b8a4faa8317267cf5
    resource: repo://docsweep/cli/commands/mcp.py
  - id: openwiki-source-6dc2bfd91eed10f64b5af6c0
    resource: repo://docsweep/mcp_server.py
generated: { by: "claude-code", at: "2026-09-08T12:41:37.379Z" }
---

# MCP Server and AI Agent Integration

docsweep exposes a Model Context Protocol (MCP) stdio server so that MCP-aware
AI coding tools (Claude Code, Codex CLI, Cursor, Continue, etc.) can call it
as structured tools instead of shelling out. The server is started with
`python -m docsweep mcp` (`docsweep/cli/commands/mcp.py`'s `cmd_mcp`), which
lazily imports `docsweep.mcp_server` so that a docsweep install without the
optional `mcp` extra fails with a clear "pip install 'docsweep[mcp]'" message
(exit code 3) instead of an import traceback, and never even attempts the
import unless the `mcp` subcommand is actually invoked.

## One command, one tool — same engine as the CLI

`docsweep/mcp_server.py`'s module docstring states the design rule directly:
each tool is published at "1 command = 1 MCP tool" granularity, and MCP
support is added without modifying the CLI — both surfaces call the same
underlying functions. `build_server()` constructs a `FastMCP("docsweep")`
instance and registers 24 `@mcp.tool()` functions (matching the count
`docs/ai-agent-integration.md` documents for v0.5.0):

`scan`, `list_projects`, `set_project_enabled`, `route_intent`, `doctor`,
`day`, `brief`, `capture_extract`, `capture_save`, `cross`, `triage`, `apply`,
`sweep`, `promote`, `index`, `summary`, `inject`, `eject`, `inject_global`,
`eject_global`, `update_status`, `update_due`, `update_content`,
`archive_done`.

Read-oriented tools (`scan`, `triage`, `brief`, `cross`, `summary`, `doctor`,
`route_intent`, `list_projects`) call straight into the same core functions
covered in [Architecture Overview](/openwiki/architecture/overview.md)
(`run_scan`, `build_triage`, `build_brief`, `build_cross`, `render_summary`).
Write-oriented tools that mutate a Markdown file's H1/frontmatter, due date, or
content (`update_status`, `update_due`, `update_content`) are thin wrappers
around `docsweep.services.status`/`due`/`content` — the very same service
functions the FastAPI web UI's edit routes call — so an AI agent editing a
file through MCP and a human editing the same file through the board go
through one shared code path, not two independently-maintained ones. `apply`,
`sweep`, and `promote` call `engine.apply_action` / `auto_sweep` /
`promote_state` directly.

There is no per-tool enable/disable flag on `docsweep mcp` — all 24 tools are
always registered together. `docs/ai-agent-integration.md` calls out
explicitly that registering the MCP server is *not* a read-only integration:
`apply`/`sweep`/`promote`/`update_status`/`update_due`/`update_content`/
`archive_done` rewrite Markdown files, `inject`/`eject` rewrite a target
project's own configuration, and `inject_global`/`eject_global`/
`set_project_enabled` rewrite the *user's* global settings — an operator
granting MCP access should understand that full scope up front.

## Structural invariant: no delete tool

`update_status`'s docstring and the module docstring both note the same
invariant already established at the engine layer: relabeling a document to
`[完了]`/`[廃止]` internally calls `archive_done` to complete the move in one
round trip (treating the MCP call itself as the human-equivalent decision),
but there is no tool, argument, or code path in `mcp_server.py` that deletes a
file outright — the worst case any write tool can reach is an `archive/` move,
by construction, not just by convention.

## Path-scope enforcement on every write tool

Every write tool that takes a `path` argument (`update_status`, `update_due`,
`update_content`, `archive_done`'s explicit `paths`) first resolves it through
`resolve_writable_md()` from `docsweep.security.path` via the local
`_resolve_or_error()` helper, and returns a structured
`{"error": ..., "kind": "path_scope"}` dict — rather than raising — when the
path falls outside the configured scan roots. MCP tool functions are written
to return error dictionaries instead of raising exceptions specifically
because, per the module's inline comment, a JSON-RPC error is harder for a
calling AI to interpret than an in-band `{"error": ...}` payload it can branch
on. The same pattern is used for optimistic-locking conflicts: a mismatched
`expected_mtime` comes back as `{"kind": "conflict", "expected_mtime":
..., "actual_mtime": ...}` rather than a generic failure.

`inject`/`eject`'s `project` argument is similarly constrained by
`_valid_project_dir()`, which resolves the given path and accepts it only if
it exactly matches (case-insensitively) one of the project roots already
discovered by a live `run_scan()` — an MCP caller cannot target an arbitrary
filesystem path outside what scanning has already established as a real,
in-scope project.

## CLI-first: MCP is one of three access paths, not the only one

`docs/ai-agent-integration.md` frames docsweep's AI-integration strategy as
"support every agent," with MCP as only one of three paths:

1. **MCP** (24 tools, described above) for MCP-capable agents.
2. **Direct CLI** (`docsweep <command> --json`) for any agent with a shell
   tool — this is the universal fallback and is why every MCP read tool has a
   `--json` CLI equivalent returning the identical schema (e.g. `docsweep
   brief --json` returns the same shape as the `brief` MCP tool).
3. A Claude Code–specific `/D` skill that thinly dispatches to the MCP tools
   and CLI for natural-language shortcuts.

The document's own changelog note is explicit that the original design intent
was narrower — expose only the highest-value "morning entry point" tools
(`brief`/`cross`/`capture_*`) over MCP and push everything else to CLI — but
the implementation grew incrementally to the current 24, and the table was
corrected in a 2026-08-25 pass to describe actual behavior rather than the
original intent. Several CLI-only commands (`linkcheck`, `auto-triage`,
`resurrect`, `graph`, `show`, `find`, `claim`, `index-sync`, `index-rebuild`,
`index-watch`, `pending`) have deliberately not been promoted to MCP tools;
an agent without MCP, or one whose MCP registration a user wants kept
minimal, reaches them via `docsweep <command> --json` instead.

## The `--due-expired` / `due_expired_only` naming mismatch

`docs/ai-agent-integration.md` flags one specific naming inconsistency an
integrating agent must handle: the CLI flag for restricting `promote` to
`watching` documents whose due date has already arrived is `--due-expired`,
while the equivalent MCP/`apply` argument is spelled `due_expired_only`. The
guide also gives an explicit safety instruction for this path: an agent should
always run the dry-run form first and show the resulting candidate list to
the user before running the real promotion, because omitting the due-expired
restriction entirely targets *all* `watching` documents, not just due ones.

## Related pages

- [Architecture Overview](/openwiki/architecture/overview.md) — the `run_scan`/`classify`/`apply_action` core functions every MCP tool ultimately calls.
- [Triage, Archive and Closeout Workflow](/openwiki/workflows/lifecycle-management.md) — the `triage`→`apply`→`sweep`/`promote` cycle mirrored 1:1 between MCP and CLI.
- [AI Authorship and Execution Provenance](/openwiki/integrations/provenance.md) — the separate, opt-in system that records which AI/session performed writes like the ones MCP tools above make.
