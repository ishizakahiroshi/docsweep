# Agent Entry Point (docSweep)

This repository's operational guidance for **developing docSweep itself** is
maintained in `CLAUDE.md`.

- Project overview & dev guide: `./CLAUDE.md`
- Naming / status convention (human-facing spec): `./docs/conventions.md`
- Design source of truth (private, may be absent in a fresh clone): `./docs/local/`
- Local/private additions (if present, not committed): `./CLAUDE.local.md` / `./AGENTS.local.md`

⚠️ **Do not confuse this with `templates/AGENTS.md` and `templates/CLAUDE.md`.**
Those under `templates/` are the **shipped product** — the ruleset that *adopters*
copy into their own projects. The root `CLAUDE.md` / `AGENTS.md` (this file) are the
**maintainer-facing** dev guide for this OSS repo.

Personal/global AI rules are intentionally kept outside this repository. Use each AI
tool's supported global instruction location for user-specific rules; this file must
remain valid for a fresh public clone with no private files.

If any project guidance conflicts, follow `CLAUDE.md`.
