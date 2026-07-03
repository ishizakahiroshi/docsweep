# Changelog

本ファイルは [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) の考え方を緩く参照しています。
バージョニングは [SemVer](https://semver.org/lang/ja/) に従います。

## [Unreleased]

### Fixed

- `serve` を Ctrl+C で停止した際に KeyboardInterrupt のスタックトレースが出ていたのを
  1 行の停止メッセージに変更（Python 3.14 の asyncio.runners 再送出を捕捉）。
- frontmatter と H1 ラベルの status 食い違い warning が Web UI の描画のたびに
  繰り返し出力されていたのを、同一 (path, message) につきプロセス内 1 回に抑制
  （矛盾自体は従来どおり needs_fix フラグで可視化され続ける）。

## [0.2.0] - 2026-07-03

### Added

- **Web UI の英語対応**（plan_v0.2.0-english-support）。看板・設定モーダル・ピッカー・
  brief / capture / cross / graph / resurrect の全画面文言を ja / en 二言語化
  （サーバー側 `docsweep/server/i18n.py`・JS 側 `static/i18n.js` の二層辞書）。
  言語は設定モーダル（⚙）の「日本語 / English」トグルで切替でき、cookie
  `docsweep_lang` に保存される（`~/.docsweep/config.yaml` の `lang:` は変更しない）。
  解決順は `?lang=` クエリ > cookie > `config.lang` > ja。ピッカーの状態ラベルは
  `.docsweep.yaml` の states 二言語辞書から lang 解決するようになった。
- **README.en.md**（英語版 README）を追加し、README.md と相互リンク。
- **About & Licenses 表記**を設定モーダル末尾に追加（アプリ情報・MIT ライセンス表記・
  同梱 OSS: htmx / CDN 参照: cytoscape.js / pip 依存の三層を UI から確認できる。
  正本は NOTICES.md — CDN 節を追記）。
- **Web UI からのスキャンルート管理**（plan_web-roots-management）。設定モーダルに
  Scan roots セクションを追加し、親ディレクトリ・個別プロジェクトフォルダのどちらも
  追加・削除できる（`POST /api/config/roots`）。runtime に即反映しつつ
  `~/.docsweep/config.yaml` の `roots:` キーだけを surgical に書き換えて永続化する
  （他キー・コメントは温存）。最後の 1 個の root は削除不可。

- 注入文言（inject）の英語対応。guidance 導線・due ルール節・ラベル節・AGENTS.md ポインタ・
  管理注記・`.docsweep.yaml` / `~/.docsweep/config.yaml` ひな型コメントを ja / en の二言語化し、
  CLI `inject --lang {ja,en}`（プロジェクト注入では preset の言語を上書き、`--global` の既定は ja）と
  MCP `inject` / `inject_global` の `lang` パラメータを追加。状態ラベルは従来から二言語辞書を
  持っていたため、`lang: en` でラベルも `[Planned]` / `[Done]` 等の英語表記になる。

### Changed

- トップバーの「看板（カンバン）/ Kanban」サブタイトル表記を撤去（バージョン表示のみ残す）。
- `inject --global` の guidance に対応期日（`due:`）ルール節を同梱（guidance_version 3）。
  従来 due ルールはプロジェクト inject のラベル節にのみ含まれ、プロジェクト注入していない
  リポジトリでは AI が frontmatter を付けられなかった。due 節をラベル節から導線（guidance）側へ
  移設し、既定=グローバル 1 回で全プロジェクトに効く／グローバルに寄せたくない場合は
  プロジェクト inject（既定）で同内容が入る、の切り分けを既存フラグ（`--global` / `--no-guidance`）
  だけで完結させた。

## [0.1.0] - 2026-07-03

初回リリース。AI コーディングツール（Claude Code / Codex 等）が生成する
`plan_*.md` / `bugfix_*.md` / `pending_*.md` の蓄積・陳腐化問題を解決する CLI + Web UI + MCP サーバー。

主な機能:

- H1 ステータスラベル（`[完了]` / `[計画]` / `[様子見]` / `[保留]` / `[廃止]`）の機械的読み取り
- 完了したファイルを各プロジェクトの `archive/` へ自動移送
- 陳腐化を「要判断」フラグで可視化
- 複数プロジェクトを横断する INDEX
- AI エージェント向け **MCP サーバー**（`scan` / `triage` / `apply` / `sweep` / `promote` / `summary` / `index` / `inject` / `eject` + 書き込みツール 4 種）
- 個人グローバル設定への運用ルール **`inject --global`**（Claude Code / Codex 対応）
- Web UI: 看板（カンバン）ボード（FastAPI + htmx・`docsweep[web]`）
- 対話レビュー（`--review` / `docsweep[review]`）

以下は初版に含まれる機能の開発経緯別の内訳。

### Added — OKF（Open Knowledge Format）採用 Phase 3（親 plan `okf-adoption_2026-06-29` C3）

- README 冒頭に OKF 互換宣言を追加（[OKF 仕様](https://zenn.dev/knowledgesense/articles/14a874a9f423bb)
  へのリンク + docsweep 固有の追加規約「固定 type 集合・H1 ラベル併用」の明示）
- 配布物 `templates/CLAUDE.md` に OKF 準拠 frontmatter ブロック例・既存採用者向け移行ガイド
  （`migrate-frontmatter` / `fix-related` / `export --okf` / pre-commit hook 案内）を追記
- 配布物 `templates/AGENTS.md` の frontmatter 例を OKF 推奨フィールド込みに更新
  （`type` / `tags` / `owner` / `review_status` / `related` / `last_reviewed`）
- `docs/okf-mapping.md`（新規）— OKF type 語彙 / status 語彙 / review_status 値域と
  docsweep 固定語彙との対応表
- `docs/okf-export-format.md`（新規）— `export --okf` の zip 構造と `okf-manifest.json` 仕様
- **`docsweep export --okf` サブコマンド**を追加（`docsweep/export.py` 新規）
  - スキャン範囲内の plan / bugfix / pending を frontmatter ごとそのまま zip に取り出し、
    OKF 互換語彙との対応表 `okf-manifest.json` を同梱
  - `--out <path>` で出力先指定（既定 `./docsweep-okf-<date>.zip`）
  - `--include-archive` で archive 配下も含める
  - `--project` で特定プロジェクトに絞る
  - `--json` で結果を機械可読出力
  - 「docsweep を抜けても md が腐らない」を実演する材料
- 配布物 pre-commit hook（opt-in）を追加
  - `templates/.githooks/docsweep-check.py` — frontmatter 不整合検知（type/status の値域違反、
    review_status が許容外、related に存在しない md 指定）。docsweep 未インストール環境でも
    動くスタンドアロン実装（PyYAML 無しでも最小 parser でフォールバック）
  - `templates/install-hooks.sh` / `install-hooks.ps1` — 配置スクリプト（POSIX / Windows）

### Added — OKF 採用 Phase 1 / 2 / 4（親 plan `okf-adoption_2026-06-29` C1 / C2 / C4）

- **Phase 1（C1）— frontmatter 併用パーサ + テンプレ + triage 拡張 + インタラクティブ triage**
  - `docsweep/detect.py` を拡張: frontmatter > H1 > filename の優先順で type / status を検出。
    frontmatter と H1 が矛盾する md は warn を出してユーザー判断に委ねる（自動上書きしない）
  - `docsweep new <type> <topic>` のテンプレに OKF frontmatter ブロックを追加
    （`type` / `status` / `tags: []` / `owner:` / `review_status: draft` / `related: []` /
    `last_reviewed: <today>`）
  - `docsweep triage --tag <name>` で frontmatter `tags:` 絞り込み、`--show owner/tags` で
    表示列追加
  - `docsweep triage --review` でインタラクティブ triage（c=完了 / w=様子見 / x=廃止 /
    s=スキップ / l=後で / o=md を開く / q=終了 の 1 キー判定 → 終了時に一括処理）
- **Phase 2（C2）— 一括変換 + related 双方向化 + 陳腐化検知 + 派生コマンド一式**
  - `docsweep migrate-frontmatter --dry-run` / `--apply` — H1 ラベル + ファイル名プレフィックスから
    frontmatter を生成し既存 md に非破壊挿入（H1 ラベルは温存）
  - `docsweep fix-related` — 片側参照 `related: [B]` を B 側にも追記して対称化
  - `docsweep show <file>` — 指定 md を参照している plan/bugfix/pending を逆参照表示
  - `docsweep stale` — `review_status` 別の前倒し陳腐化候補を列挙
    （draft 14 日 / review 7 日 / published `last_reviewed` 90 日経過）
  - `docsweep context <file>` — 本文 + 親 plan + related の bugfix/pending を 1 つの
    AI 用プロンプト文字列で stdout 出力（`--clipboard` で OS クリップボードへ）
  - `docsweep claim <file>` / `--unclaim` — frontmatter の owner を現ユーザーで上書き
    （解決順: `docsweep config user.name` → `git config user.name` → OS ログイン名）
  - `docsweep config user.name <name>` / `user.email <email>` — `~/.docsweep/config.yaml` の
    user 設定を CLI / Web UI 双方から読み書き
  - `docsweep timeline <topic>` — topic を含む plan/bugfix/pending を時系列で列挙
    （`--format markdown|plain|json`）
  - `docsweep find --owner X --tag Y --status 実行中 --review-status draft` — 自由クエリ
  - `docsweep completion bash|zsh|pwsh` — シェル補完スクリプト生成
- **Phase 4（C4）— カンバン Web UI の OKF 対応**
  - カード表示に `tags` バッジ / `owner` / `related` 件数アイコンを追加
  - 詳細パネルに `review_status` / `last_reviewed` / `related` 一覧 / 逆参照を表示
  - tags / owner / related をインライン編集・書き戻し（services/frontmatter.py 経由・
    H1 ラベル温存）
  - 検索バーに `--tag` / `--owner` / `--status` 相当の絞り込み UI を追加
  - Web UI 上で claim / unclaim 可能（CLI の `claim` と同じファイルを更新）

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
- 旧 dashboard テンプレ・CSS・JS（`dashboard.html` / `_dashboard_body.html` / `_list.html` / `index.html` / `app.css` / `app.js`）は当初残置としたが、その後**物理撤去**（下記 Removed 参照）
- 看板の編集ペインからは引き続き `/preview` `/api/cards/raw` を使用

### Changed

- `docsweep new` で生成される plan / bugfix / pending テンプレから `> 最終更新:` 行を撤去
  （`st_mtime` ベースで判定しており重複情報だったため）
- 配布規約 `templates/CLAUDE.md` と運用解説 `docs/conventions.md` からも
  `> 最終更新: ...` 行の指示・記述を撤去（書く側と読む側を整合）
- 自リポ `CLAUDE.md` 先頭の `> 最終更新:` 行を撤去（ドッグフーディング整合）

### Removed — 旧 dashboard 資産と TS+Vite スタックの撤去（plan `legacy-stack-retirement` C1/C2）

- Web から到達不能になっていた旧 dashboard 資産を物理撤去
  （テンプレ 4 枚 + `static/app.js` / `app.css` + server 内の死コードヘルパ群）
- フロントエンドの TS+Vite+bun ビルドスタック（`src/` / `vite.config.ts` / `tsconfig.json` /
  `package.json` / `bun.lock`）を撤去し、看板の plain JS + `htmx.min.js` に一本化
  （**Python 環境だけで開発・配布が完結**。Node/bun 不要）

### Fixed

- archive 移送先を**対象プロジェクト自身の `.docsweep.yaml` から解決**するように修正。
  sweep / promote は複数プロジェクト横断で動くのに archive 先が起動時の単一 config でしか
  解決されず、`--project-dir` を明示しないとプロジェクト設定の `archive_dir` が無視されていた

### 不変条件（新機能でも厳守）

- 物理削除の口を実装として持たない（最悪でも `archive_done` 止まり・復元可能）
- `[様子見]` は明示指定でも archive されない（寝かせを守る）
- 期日切れだけでは絶対に `[廃止]` 化しない（AI / 人の明示意図が必須）
- Web UI に新しい特権を持たせない（CLI / MCP と同じ services 関数を呼ぶ）
- バインド `127.0.0.1` 固定・スキャンルート配下のみ書き込み可・`realpath` 解決後にスコープ境界チェック

`docsweep/detect.py` の `>` 引用行スキップロジックと
`tests/test_detect.py` の後方互換 fixture は残置（過去ファイルとの互換のため）。

[0.1.0]: https://github.com/ishizakahiroshi/docsweep/releases/tag/v0.1.0
