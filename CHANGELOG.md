# Changelog

本ファイルは [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) の考え方を緩く参照しています。
バージョニングは [SemVer](https://semver.org/lang/ja/) に従います。

## [0.1.0] - 2026-06-21

初回リリース。AI コーディングツール（Claude Code / Codex 等）が生成する
`plan_*.md` / `bugfix_*.md` / `pending_*.md` の蓄積・陳腐化問題を解決する CLI + Web UI + MCP サーバー。

主な機能:

- H1 ステータスラベル（`[完了]` / `[計画]` / `[様子見]` / `[保留]` / `[廃止]`）の機械的読み取り
- 完了したファイルを各プロジェクトの `archive/` へ自動移送
- 陳腐化を「要判断」フラグで可視化
- 複数プロジェクトを横断する INDEX
- AI エージェント向け **MCP サーバー**（`scan` / `triage` / `apply` / `sweep` / `promote` / `summary` / `index` / `inject` / `eject` ほか）
- 個人グローバル設定への運用ルール **`inject --global`**（Claude Code / Codex 対応）
- Web UI（FastAPI + htmx・`docSweep[web]`）
- 対話レビュー（`--review` / `docSweep[review]`）

[0.1.0]: https://github.com/ishizakahiroshi/docsweep/releases/tag/v0.1.0
