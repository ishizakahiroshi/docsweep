---
type: overview
title: docsweep Wiki Quickstart
description: What docsweep is, and a task-routing map pointing at which wiki page answers which kind of question.
tags: [quickstart, overview, index]
verified:
  - by: openwiki/0.5.0
    at: 2026-09-08T12:41:37.379Z
sources:
  - id: openwiki-source-a2371d6362e5db4bc834ad03
    resource: repo://CLAUDE.md
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
generated: { by: "claude-code", at: "2026-09-08T12:41:37.379Z" }
---

# docsweep Wiki Quickstart

**docsweep** is a cross-platform CLI, local web UI, and MCP server that
scans, triages, and archives the `plan_*.md`/`bugfix_*.md`/`pending_*.md`
working documents that AI coding agents (Claude Code, Codex, etc.) tend to
accumulate. It reads a machine-checkable H1 status label (or frontmatter
equivalent) on each document, automatically moves completed/discarded
documents into an `archive/` directory, flags stale unfinished documents for a
decision, and rolls all of that up into a cross-project index — distributed
as a single PyPI package (`pip install docsweep`) with optional extras for
its web UI, MCP server, interactive review mode, filesystem watching, and
embedding-based archive search.

Two very differently-scoped rulesets live in this same repository, and the
distinction matters when reading either the code or the docs: the root
`CLAUDE.md`/`AGENTS.md` are **maintainer-facing** — how to develop docsweep
itself — while `templates/CLAUDE.md`/`templates/AGENTS.md` are the **shipped
product**: the ruleset an adopter copies (or has `docsweep inject` write) into
their *own*, unrelated project.

## Where to go for what

**"How does docsweep actually decide what state a document is in, and what
can it do to it?"** → [State Model and Document Lifecycle](/openwiki/architecture/state-model.md)
— the `states.py` vocabulary, the frontmatter/H1/filename detection
precedence, and the `due:` second axis that drives the kanban board.

**"How is the codebase put together — packages, entrypoints, the core
pipeline?"** → [Architecture Overview](/openwiki/architecture/overview.md)
— `scan → detect → classify → apply/archive`, and the three-layer
configuration model (CLI flag > project `.docsweep.yaml` > global config).

**"What's the actual day-to-day operating loop — triage, apply, sweep,
closing out a plan?"** → [Triage, Archive and Closeout Workflow](/openwiki/workflows/lifecycle-management.md)
— including the bulk-confirmation mechanism and the read-only
`closeout-check` blocker/manual-review/ready split.

**"What CLI subcommands exist and what do their flags do?"** →
[CLI Command Surface](/openwiki/workflows/cli-reference.md) — a grouped map
of the ~60 subcommands.

**"How do AI agents (Claude Code, Codex, etc.) actually talk to docsweep —
MCP tools vs. CLI?"** → [MCP Server and AI Agent Integration](/openwiki/integrations/mcp-server.md)
— the 24 MCP tools, their write-scope, and the CLI-first fallback philosophy.

**"How does docsweep's Markdown relate to the OKF (Open Knowledge Format)
spec — export, conformance checking?"** → [OKF (Open Knowledge Format) Compatibility](/openwiki/integrations/okf-compatibility.md)
— the `status`-vs-`docsweep_state` axis split, version profiles, `export
--okf` Bundles, `okf-check`.

**"How does docsweep track which AI wrote or executed a document?"** →
[AI Authorship and Execution Provenance](/openwiki/integrations/provenance.md)
— the opt-in authoring/execution ledger, session-transcript resolution, and
the "never fabricate a value" invariant.

**"How does the local web UI (the kanban board) work, and what keeps it
safe to preview untrusted Markdown?"** → [Web UI (FastAPI Board)](/openwiki/integrations/web-ui.md)
— token auth, path-scope enforcement, sanitization, and the CSP invariant
enforced by a dedicated test.

**"I want to adopt docsweep in my own project — what does `inject` actually
write, and what are the state presets?"** → [Adopting docsweep in a Project (inject/eject and templates/)](/openwiki/operations/adoption-and-templates.md)
— managed-block mechanics, hand-edit protection, and the `templates/` product
this repo ships.

**"How is docsweep itself tested and released?"** →
[Testing and Development Workflow](/openwiki/testing/development-workflow.md)
— the test suite's environment-isolation fixtures, the dedicated
audit/incident regression tests, the opt-in pre-push hook, and the staged
ruff/mypy configuration.
