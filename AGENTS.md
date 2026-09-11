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

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
