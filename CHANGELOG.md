# Changelog

本ファイルは [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) の考え方を緩く参照しています。
バージョニングは [SemVer](https://semver.org/lang/ja/) に従います。

## [Unreleased]

### Changed

- `docsweep new` で生成される plan / bugfix / pending テンプレから `> 最終更新:` 行を撤去
  （`st_mtime` ベースで判定しており重複情報だったため）
- 配布規約 `templates/CLAUDE.md` と運用解説 `docs/conventions.md` からも
  `> 最終更新: ...` 行の指示・記述を撤去（書く側と読む側を整合）
- 自リポ `CLAUDE.md` 先頭の `> 最終更新:` 行を撤去（ドッグフーディング整合）

`docsweep/detect.py` の `>` 引用行スキップロジックと
`tests/test_detect.py` の後方互換 fixture は残置（過去ファイルとの互換のため）。

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
- Web UI（FastAPI + htmx・`docsweep[web]`）
- 対話レビュー（`--review` / `docsweep[review]`）

[0.1.0]: https://github.com/ishizakahiroshi/docsweep/releases/tag/v0.1.0
