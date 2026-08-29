# Agent Entry Point (docsweep)

This repository's operational guidance for **developing docsweep itself** is
maintained in `CLAUDE.md`.

- Project overview & dev guide: `./CLAUDE.md`
- Naming / status convention (human-facing spec): `./docs/conventions.md`
- Design source of truth (all tracked): state vocabulary in `docsweep/states.py`
  (`DEFAULT_STATES` / the `states:` config), `./docs/conventions.md`,
  `./templates/CLAUDE.md`, `./docs/okf-mapping.md`, `./README.md`
- Author's working log (private, absent in a fresh clone; never required to read): `./docs/local/`
- Local/private additions (if present, not committed): `./CLAUDE.local.md` / `./AGENTS.local.md`

⚠️ **Do not confuse this with `templates/AGENTS.md` and `templates/CLAUDE.md`.**
Those under `templates/` are the **shipped product** — the ruleset that *adopters*
copy into their own projects. The root `CLAUDE.md` / `AGENTS.md` (this file) are the
**maintainer-facing** dev guide for this OSS repo.

Personal/global AI rules are intentionally kept outside this repository. Use each AI
tool's supported global instruction location for user-specific rules; this file must
remain valid for a fresh public clone with no private files.

If any project guidance conflicts, follow `CLAUDE.md`.

## AI 作業共通ルール

- ビルド・コミット禁止、secrets-scan 責務、plan/bugfix/pending md の作成ルール等の AI 作業共通ルールは、各利用者のグローバル AI 設定に従う（作者環境の例: `~/.claude/CLAUDE.md` および `~/.claude/guides/`）