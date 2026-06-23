# Changelog

本ファイルは [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) の考え方を緩く参照しています。
バージョニングは [SemVer](https://semver.org/lang/ja/) に従います。

## [Unreleased]

### Added — 看板方式 Web UI と MCP 書き込み口（親 plan `kanban-board-write-ops`）

- frontmatter `due:` の書き込み側基盤を実装
  - `docsweep/atomic.py`: アトミック書き込み（`os.replace` 経由）+ 楽観ロック（`expected_mtime`）+ バックアップ（`.docsweep/backup/`・30 日保持・自動掃除）
  - `docsweep/state.py`: `.docsweep/state.json`（`postpone_count` / `due_history` / `label_history`・v1 スキーマ）の読み書き層
  - 軸 1（ラベル）の遷移時に `postpone_count` を自動リセット（`[計画]→[実行中]` `[実行中]→[様子見]` `[対応中]→[様子見]` `[保留]→[計画]/[実行中]`）
- 書き込み API の services 層（CLI / MCP / Web UI が共通で呼ぶ単一実装）
  - `docsweep/services/due.py`: `update_due` — frontmatter `due:` 書き換え + `postpone_count` インクリメント + 相対指定（`today` / `+1d` / `+1w` / `+1m`）解釈
  - `docsweep/services/status.py`: `update_status` — H1 ラベル書き換え + ファイル種別×ラベル組み合わせバリデーション + 自動リセット連携
  - `docsweep/services/content.py`: `update_content` — 本文全置換（楽観ロック必須）
  - `docsweep/services/archive.py`: `archive_done` — `[完了]` / `[廃止]` のみ移送（`[様子見]` は明示指定でも拒否・寝かせを守る）
- 配布規約 `templates/CLAUDE.md` と運用解説 `docs/conventions.md` に「期日（due）と看板方式」セクションを追加
- 配布設定サンプル `templates/.docsweep.yaml` に `due:` / `web_ui:` セクションのコメント例を追加
- `templates/AGENTS.md` の frontmatter 例に `due:` を追記
- Web UI に **看板（カンバン）ボード**を新設（`/board`・FastAPI + htmx）
  - 3 列レイアウト（🔴やり忘れ / 🟡今日 / 🟢実行中）+ 折りたたみ（▼卒業判定 / ▶未来期日 / ▶期日未設定 / ▶archive 候補）
  - カード UI（ラベルバッジ / 期日バッジ / 先送り回数バッジ + 3 ボタン）
  - ラベル変更（バッジクリック → セグメント、数字キー 1-6）
  - 期日変更（クイック +1d / +3d / +1w / +1m / 任意、キーボード d / w / m）
  - 本文編集ペイン（textarea + Ctrl+S + mtime 競合検出 → 409 Conflict）
  - 列ドラッグ&ドロップは**期日操作のみ**（ラベル変更には使わない・直交軸を守る）
  - 旧 dashboard（`/`）は無改変で残存
- MCP **書き込みツール 4 種**を追加（`docsweep mcp` 経由で AI から呼べる）
  - `update_status(path, new_status, expected_mtime?)` — H1 ラベル書き換え + ファイル種別×ラベル検証 + `postpone_count` 自動リセット + `[完了]` / `[廃止]` で archive 自動連携
  - `update_due(path, new_due, reason?, expected_mtime?)` — frontmatter `due:` 書き換え + 相対指定（`today` / `+1d` / `+1w` / `+1m`）解釈 + `postpone_count` インクリメント + しきい値 warning
  - `update_content(path, new_content, expected_mtime?)` — 本文全置換 + 楽観ロック + H1 欠落警告
  - `archive_done(paths?, auto?)` — `[完了]` / `[廃止]` のみ archive 移送（`[様子見]` は明示指定でも拒否）
- 既存 MCP `triage` の戻り値スキーマを拡張（旧クライアント非破壊）
  - 各 item に `due` / `due_raw` / `due_parse_error` / `overdue_kind` / `overdue_days` / `postpone_count` / `label_history_count` を追加
  - `allowed_actions` 集合に `update_due` / `update_content` を追加
  - `summary` ブロックに `overdue_todo` / `today` / `overdue_graduate` / `future` / `missing_due` のカウントを追加
- パス境界チェック層を新設（`docsweep/security/path.py`）— スキャンルート配下のみ書き込み可・`realpath` 解決後にスコープ境界チェック・`..` 拒否・`.md` ファイルのみ
- 不変条件のホワイトボックステストを追加（`tests/test_invariants.py`）— 物理削除口の不存在を AST レベルで担保
- `Config` に `due:` セクションを追加（`due_warn_threshold` / `due_alert_threshold` / `due_default_offset_days`）— `.docsweep.yaml` の `due:` ブロックから上書き可能
- Web UI / MCP の `update_due` がしきい値を Config から受け取るように配線（`postpone_warn_threshold` / `postpone_alert_threshold` が実際に効くようになった）
- `docsweep new <type> <topic>` で frontmatter `due:` 初期値を自動付与（`Config.due_default_offset_days` から `today + N` を計算）
  - `plan` は既定 today+7、`pending` は today+14、`bugfix` は新規時に付けない（`[様子見]` 遷移時に追記する設計）
  - `--due YYYY-MM-DD` で明示指定、`--no-due` で自動付与を抑止
- Web UI 編集ペイン用に `GET /api/cards/raw` を新規追加 — 編集 textarea を生 MD で初期化（プレビュー HTML のテキスト化では Markdown 構造が壊れるため）
- 受信トレイ (`/`) のサイドバーに「📋 看板（カンバン）」リンクを追加（新旧 UI の導線確保）

### Added — 看板の一括編集（派生 plan `kanban-bulk-edit`）

- 看板の全 7 セクション（3 列 + 卒業判定 + 未来期日 + 期日未設定 + archive 候補）を横断して一括操作:
  - 各カードに checkbox を追加
  - 各セクションヘッダに「全選択 + 4 ボタン（+1d/+1w/着手/廃止）」セット
    - 卒業判定は「着手」の代わりに「完了」、archive 候補は「archive へ」のみ
  - 上部 sticky バー（N 件選択中・+1d/+1w/着手/廃止/archive/解除/画面全選択）
  - キーバインド `a` = 画面全カード選択 / `Esc` = ピッカー閉じ + 選択解除
- Web UI 専用 bulk API 3 種（`/api/cards/bulk/{due,status,archive}`）— 既存 services を for ループで呼ぶ薄いラッパ
  - 部分成功 `{ok:[], failed:[]}` を返す（1 件失敗しても他は続行）
  - スコープ外パス / mtime conflict / validation エラーは個別に `failed[]` に振り分け
  - `[完了]` / `[廃止]` 一括指定で archive 移送が連動（単数 API と同じ閉じた口を通す）
- 確認ダイアログ強化 — `[廃止]` / `[完了]` / archive は ⚠ 強警告メッセージ
- 部分失敗時の集約ダイアログ — 「成功 N 件／失敗 M 件」と失敗 path の先頭 5 件を表示
- カードの「📂 プロジェクト名」バッジをクリックで **プロジェクト単位選択切替**（同じプロジェクトのカードだけ全選択・他プロジェクトは解除・再クリックで全解除）。「1 プロジェクトずつ捌く」ワークフロー用
- バルクバーに **プロジェクト絞り込みドロップダウン**（名前順チェックリスト・部分選択 indeterminate 表示・全 ON / 全 OFF クイックボタン）

### Added — UX 拡張（kanban-bulk-edit 後追加）

- **Undo（直近 archive バッチの取り消し）** — `archive_done` 実行ごとに `batch_id` を生成（`MoveLogEntry` スキーマ拡張）、`undo_last_batch()` で逆操作（archive 配下 → 元の場所へ shutil.move、restore エントリで二重 Undo 防止）。Web UI は archive 後に右下トーストで「↶ Undo」ボタンを 10 秒表示
- `POST /api/cards/undo` — Undo API エンドポイント（services 層のラッパ）
- **カード検索ボックス** — topbar に検索 input（ファイル名・タイトル・概要から絞り込み）。`/` キーでフォーカス・`Esc` でクリア。検索ヒット件数も表示
- **絶対日付指定の一括設定** — バルクバーに「📅 日付」ボタン → date picker dialog → 選択 N 件の `due` を YYYY-MM-DD で一括設定

### Changed — Web UI を看板に集約（plan `consolidate-to-board`）

- 旧 dashboard（`/`）を廃止し、Web UI を **看板（`/board`）一本に集約**
- `/` は `/board` へ **302 リダイレクト**（トークン引き継ぎ）
- 旧 dashboard 専用ルート `GET /list` / `GET /fragment` は削除
- 看板 topbar に **「⚙ 設定・注入」モーダル** を追加（プロジェクト一覧 / inject / eject / グローバル inject Claude・Codex 切替）
- 看板 topbar に **health chip**（上位 5 プロジェクトの最古経過日数を `📊 mer 90d` 形式で表示）
- 旧 dashboard テンプレ・CSS・JS（`dashboard.html` / `_dashboard_body.html` / `_list.html` / `index.html` / `app.css` / `app.js`）は **削除せず残置**（後で復活可能・Web からは到達不能）
- 看板の編集ペインからは引き続き `/preview` `/api/cards/raw` を使用

### Changed

- `docsweep new` で生成される plan / bugfix / pending テンプレから `> 最終更新:` 行を撤去
  （`st_mtime` ベースで判定しており重複情報だったため）
- 配布規約 `templates/CLAUDE.md` と運用解説 `docs/conventions.md` からも
  `> 最終更新: ...` 行の指示・記述を撤去（書く側と読む側を整合）
- 自リポ `CLAUDE.md` 先頭の `> 最終更新:` 行を撤去（ドッグフーディング整合）

### 不変条件（新機能でも厳守）

- 物理削除の口を実装として持たない（最悪でも `archive_done` 止まり・復元可能）
- `[様子見]` は明示指定でも archive されない（寝かせを守る）
- 期日切れだけでは絶対に `[廃止]` 化しない（AI / 人の明示意図が必須）
- Web UI に新しい特権を持たせない（CLI / MCP と同じ services 関数を呼ぶ）
- バインド `127.0.0.1` 固定・スキャンルート配下のみ書き込み可・`realpath` 解決後にスコープ境界チェック

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
