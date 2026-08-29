# Changelog

本ファイルは [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) の考え方を緩く参照しています。
バージョニングは [SemVer](https://semver.org/lang/ja/) に従います。

## [Unreleased]

### Added

- 作業 md の frontmatter に `ai_session_logs` を追加。その md を書いた AI セッションの
  transcript（AI CLI 自身が残す生ログ）のフルパスを記録し、md の記述だけでは追えない判断の
  経緯へ後から会話ログで戻れるようにする。対応は Claude Code / Codex / Grok / Copilot /
  Cursor Agent。Claude Code はセッション ID が環境変数に出るため一致で確定し、それ以外は
  「作業ディレクトリが一致し、直近まで書かれ続けているセッションが 1 つだけ」のときに限って
  記録する（2 つ以上に絞れなければ何も書かない）。opencode は全セッションが単一 SQLite に
  集約され 1 セッションを特定できないため対象外。記録するのはパスだけで内容は読まない。
  `work_policy: private` の queue に限る。`new --ai-session-log <path>` と
  `DOCSWEEP_AI_SESSION_LOG` で明示指定もできる。`provenance start` でも追記するため、
  plan が複数セッションにまたがっても C2 以降の実行を追える（同じパスは重複しない）。
- `provenance init --update` を追加。作成 AI が `unknown` で記録されてしまった work を、
  台帳の authoring 行と frontmatter の `ai_author_*` をまとめて実値へ直せる。
  `execution_id` / `work_id` / `started_at` は変えず、訂正したことは台帳の `notes` へ残す。
- provenance が有効なのに AI metadata を渡さずに `new` / `provenance start` /
  `provenance init` を実行すると、`unknown` で記録される前に stderr へ 1 行警告する
  （作成・記録自体は従来どおり成功する）。
- `new --split N` に `--titles A,B,C` を追加。子 plan のファイル名が
  `plan_<親topic>_c<N>_<short>.md` になり、どの子が何を担当するかがファイル名から読める。

### Changed

- **設定を書いていないプロジェクトの archive 移送先を、repo 直下 `archive/` から
  `<work_dir>/archive`（既定 `docs/local/archive`）へ変更。** 既定値は
  `work_dir=docs/local` / `work_policy=private` なので、従来の既定は private な作業文書を
  git 追跡され得る場所へ移送していた。`archive_dir` の明示（プロジェクト / グローバル）と
  `work_policy: shared` の repo 直下互換は変えていない。既に repo 直下 `archive/` を
  運用しているプロジェクトには `sweep` が移送先変更の警告を出す。従来どおりにするには
  `.docsweep.yaml` へ `archive_dir: archive` を明示する。**既存ファイルの移動はしない。**
- `sweep` の JSON に `archive_routes` を追加。移送先だけでなく選択根拠
  （`explicit_project` / `explicit_global` / `private_queue` / `shared_root`）を返すので、
  dry-run だけで destination contract を判断できる。移送候補が 0 件でも返す。
- **`new --split N` の子ファイル名を `plan_<親topic>-c<N>.md` から
  `plan_<親topic>_c<N>.md` へ変更**（区切りがハイフンからアンダースコアへ）。
  規約・既存の親子 plan 群・`closeout-check` の子判定フォールバック
  （`^<親stem>_c\d+(?:_|$)`）はいずれもアンダースコアを期待しており、生成器だけが
  外れていた。既存ファイルはリネームしない。

### Fixed

- 秘密ガードが `task-management-and-scheduling-system` のような通常のハイフン語を
  OpenAI API キーと誤検知し、秘密を含まない作業文書の保存を拒否していた問題を修正。
  `sk-` の前に境界表明が無く、語の途中（`ta|sk-...`）から一致していた。
- ja-JP Windows の cp932 コンソールへリダイレクトすると `scan` と `doctor` が
  `UnicodeEncodeError` の raw traceback で落ちていた問題を修正。人間向け出力の装飾記号を
  ASCII 化し、あわせて描画境界だけを緩めて**文書側のデータ**に含まれる表現不能な文字でも
  落ちないようにした（`--json` は stdout のバイト列が契約なので緩めず、表現できない場合は
  短い stderr と exit 2 にする）。ファイル上のデータは変えない。
- Web UI からの scan-roots 更新で、`~/.docsweep/config.yaml` の `roots:` リストの途中に
  コメント行があると、その後ろのエントリが置換されずに残っていた問題を修正。外したはずの
  root が消えず、しかも結果が yaml として妥当なため既存の検証も通っていた。
- `closeout-check` の検証行分類が、`pytest: 0 failed` や `エラーなし` のような**成功の証跡**を
  「検証の失敗」と読んで closeout を止めていた問題を修正。判定を
  未実施 → 正の失敗件数 → ゼロ失敗 summary → 明示成功 → claimed の順に固定し、
  `continue-on-error` のような識別子の一部を失敗語として拾わないようにした。
  `未検出` / `空集合` / `0件` のような曖昧な散文は従来どおり manual review へ回す
  （自動 pass へは緩めていない）。
- inject の guidance ファイル / `.docsweep.yaml` / グローバル config の書き込みが非 atomic
  だった問題を修正（途中で落ちると壊れたファイルが残り得た）。

## [0.4.0] - 2026-08-10

### アップグレード時の注意

- **本版の適用後に `docsweep index-rebuild` を 1 度実行することを推奨。** 索引の project 単位を
  スキャンルートからリポジトリ（project_root）へ揃えたため、旧採番のファイル行が残る。
  次回の `index-sync` でも自動的に掃除されるが、rebuild の方が確実。
- `docsweep[mcp]` を使っている場合、`mcp` の下限が `1.28.1` に上がる（CVE-2026-59950 対応）。
  古い版に固定している環境では解決に失敗し得るので、依存の pin を見直すこと。

### Added

- OKF v0.2 profile 駆動の frontmatter 状態分離を追加。標準 `status`（`draft` / `stable` /
  `deprecated`）と docsweep の作業状態 `docsweep_state` を別軸で扱い、旧 `status: planned`
  などは読み取り互換と dry-run migration を維持する。
- `okf-check`（read-only Bundle 適合検査）と `okf-profiles` を追加。未知 type / 追加キー /
  欠けた任意フィールド / 壊れた link は warning または受理とし、構造上の error と区別する。
- `export --okf` が同梱 JSON profile を既定で使い、Bundle root `index.md` と v0.2 lifecycle
  frontmatter を生成する。外部 profile は `--okf-profile` を明示した場合だけ読み込む。
- `closeout-check`（read-only）で親 plan と child plan の関係、状態 conflict、完了条件、検証証跡、
  manual gate、Git dirty overlap を JSON で検査できるようにした。`new plan --split` は
  `docsweep_parent` を子へ付け、旧 plan は限定的な filename + related inference のみで互換判定する。
- `work_dir` / `work_policy` / `secret_policy` を追加し、`new` / `capture` / MCP `capture_save` の
  保存先を共通解決する。既定はプロジェクト相対 `docs/local`、private queue は export から除外し、
  Git ignore / tracked 状態と秘密情報を保存前に検査する。

### Changed

- **cytoscape.js 3.30.0 を同梱し、graph ページの CDN 依存を解消した。** これまで graph ページだけが
  実行時に unpkg から取得しており、オフライン環境で描画できず、利用のたびに外部サーバーへ
  アクセスが渡っていた。`docsweep/server/static/cytoscape.min.js` として同梱し、MIT License 全文と
  SHA-256 を `NOTICES.md` に記載した。Web UI の「このアプリについて」も CDN 節を廃して同梱一覧へ
  統合した。なお **graph が描画されなかった原因は CDN 依存とは別**にもう 1 つあり、それは下記の
  Fixed（inline script が CSP に拒否されていた件）で解消している。
- profile のルールを Python ソースから分離し、`docsweep/okf_profiles/<version>.json` で管理する。
  ローカル JSON や commit 固定の GitHub Raw URL を利用できるため、ルール更新だけなら
  docsweep の実装 Release を必要としない。旧形式の廃止時期は別途告知する。
- 作業記録の新規接頭辞を `plan` / `bugfix` / `pending` に整理した。個別リリースの MD は
  `plan_release-vX.Y.Z_*.md`、再利用する手順・参照資料は `manual_*.md` / `reference_*.md` /
  `setup_*.md` とし、旧 `manual_release-*` は履歴互換として読み取る。
- `apply --action relabel` / `promote` / `resume` の状態変更を共通 status service 経由に寄せ、
  H1 と `docsweep_state`（旧形式では `status`）を同期する。relabel は archive を暗黙実行しない。
- `inject` / `init` / shipped templates / README の AI 導線を設定済み work queue 前提へ同期した。

### Security

- **`docsweep[mcp]` の範囲を `mcp>=1.28.1,<2` にした。** 従来の `mcp>=1.0` では
  CVE-2026-59950（fix: 1.28.1）を含む版が install され得た（2026-07-21 監査 F-06）。
  上限を切ったのは **mcp 2.0.0 で `mcp.server.fastmcp` が無くなり、`docsweep mcp` が
  import 時点で落ちる**ため（上限なしだと新規 install が 2.0.0 を掴んで即座に壊れる）。
  2.0 系への対応は次の版で行う。

### Fixed

- **Web UI の graph / brief / capture の 3 ページが、自分自身の CSP に拒否されて
  機能していなかったのを修正した。** サーバーは全レスポンスに `script-src 'self'`
  （`'unsafe-inline'` なし）を付けているのに、この 3 ページだけ処理を inline `<script>`
  （brief は `onclick=` も）で書いていた。ブラウザは実行を拒否するが**画面には何のエラーも
  出ない**ため、症状は「graph が真っ白」「brief のコピー ボタンが無反応」「capture が
  生成ボタンを押しても何も起きない」という**黙った機能停止**だった。処理を
  `/static/{graph,brief,capture}.js` へ出し、データは `<script type="application/json">`
  （実行されないので CSP の対象外）と `data-*` 属性で渡す形に統一した。
  template に inline script / inline イベント属性が入ったら落ちるテストを追加している。
- **`.gitignore` が `docs/local/`（末尾スラッシュ）のとき、新規プロジェクトの 1 本目が
  「private work queue が Git ignore されていません」で作れなかったのを修正した。**
  `git check-ignore` は実在しないパスをディレクトリと判断できないため、queue が未作成の
  初回だけディレクトリ限定パターンに一致しなかった。docsweep 自身のテンプレートと
  ドキュメントが `docs/local/` 表記なので、手順どおり設定した利用者が必ず踏む経路だった。
  末尾スラッシュ付きでも問い合わせるようにした。
- **`fix-conflict --prefer h1` が H1 の値を書き戻せていなかったのを修正した。** 書き戻す値に
  「frontmatter > H1 > filename」で解決済みの state を使っていたため、frontmatter がある
  conflict では **frontmatter 自身の値を自分に書き戻すだけ**になり、`[ok]` を報告しながら
  1 文字も直らなかった（0.3.1 で直したのは `--path` の絞り込み側だけで、この本体は残っていた）。
  H1 の値を本文から取り直すようにし、既に同値なら書き込まないようにした。
- **frontmatter を 1 フィールド書き換えるたびに、本文との間の空行が消えていたのを修正した。**
  frontmatter を判定する正規表現の閉じ `---` の後ろが `\s*` で、直後の空行まで飲み込んでいた。
  tracked な docs では relabel のたびによけいな差分が出ていた。
- **現行の OpenAI API キー形式（`sk-proj-…` / `sk-svcacct-…`）を秘密情報スキャンが
  検出できていなかったのを修正した。** 旧パターンが `sk-` の直後に英数 20 文字以上を
  要求しており、途中に `-` が入る現行形式は 1 件も一致しなかった（`secret_policy: block`
  でも素通りしていた）。併せて Slack / Google API key / Stripe の形式を追加し、
  `PRIVATE KEY` ブロックの検出をアルゴリズム名の列挙から一般化した（DSA / PGP / ENCRYPTED を含む）。
- **`secret_policy` に未知の値（設定の綴り違い等）を渡すと、block が黙って warn に
  なっていたのを修正した。** `off` 以外は `block` として扱う（fail-closed）。無効化できるのは
  明示的な `off` だけになった。
- **private work queue の保護を互換 fallback で外すときに、黙って外していたのをやめた。**
  `work_dir` / `work_policy` が未設定の既存利用者では private queue の検査結果が error から
  取り除かれるが、その内容が警告としても残らず「保護されている」と誤解できる状態だった。
  落とした検査結果は理由と明示方法つきで warning に残す。`export --okf` も同様に、
  work_policy=private の queue を含めた場合は対象プロジェクト名つきで警告し、
  除外した場合は件数を通知する（黙って含めない・黙って減らさない）。
- **`work_dir` を junction / symlink でリポジトリ外へ逃がした構成で、その作業記録が
  別プロジェクトの持ち物として登録されていたのを修正した。** `docs/local` をリンク化すると
  `Path.resolve()` がリポジトリの外へ抜け、実体側から上へ辿っても project marker が見つからず、
  `detect_project_root` の fallback が「スキャンルート直下の先頭セグメント」を project にしていた。
  一方 cwd 判定は実体ディレクトリを見るので別名になり、**`docsweep brief` がエラーも警告も出さずに
  0 件を返していた**（実測: 未完 65 件のリポジトリで 0 件。同じ配置の 20 リポジトリすべてが該当）。
  `work_dir` は「そのプロジェクトの作業キュー」と宣言済みの設定なので、実体パスから宣言元
  プロジェクトへ引き戻す対応表を作り、project 判定の入口で marker 走査より先に引くようにした。
  同じ実体を 2 つのプロジェクトが `work_dir` として宣言している場合は、**黙って片方を採らず
  対応表から外す**（どちらが正しいか機械には決められないため）。通常構成（`docs/local` が実体）の
  挙動は変わらない。
- **索引の project 単位をスキャンルートからリポジトリ（project_root）へ揃えた。**
  索引経由の `WHERE project_id = ?` はスキャンルート名で、索引なしフォールバックは
  `FileRecord.project`（= リポ名）で絞っており、**同じ `--project <リポ名>` が経路に
  よって別の列と比較されていた**。索引が有効だと `docsweep triage --project mer` が
  エラーも警告も出さずに 0 件を返し、「残作業なし」と誤読する状態だった。
  併せて `projects.root_path` は project_root、`files.rel_path` は project_root 相対に
  なる。副作用として、basename が同じスキャンルート（`D:/dev` と `C:/Users/x/dev`）が
  同一 project_id に潰れて互いの登録を消し合う問題も解消した（実測で
  `files_added: 639 / files_deleted: 639 / files_unchanged: 0` だった差分同期が、
  `files_unchanged: 641` の真の no-op になった）。処理順次第で索引が空のまま終わり得た
  危険もなくなる。
  **本修正の適用後は `docsweep index-rebuild` を 1 度実行することを推奨**（旧採番の
  ファイル行は次回の `index-sync` でも自動的に掃除されるが、rebuild の方が確実）。
  掃除した時は stderr に 1 行だけ通知する。空になった project 行は自動削除せず、
  従来どおり `docsweep index-sync --prune-projects` に委ねる。
  `--prune-projects` の現行 ID 算出もスキャンルート単位から「今回の走査で見つかった
  全 project」へ追従させた（直し忘れると生きている project が孤児と誤判定されて
  CASCADE 削除される）。
- `index-sync` / `index-rebuild` の全走査を DB 書き込みより前に完了させるようにした。
  `index-rebuild` は `DELETE FROM files` の直後に走査ループへ入っていたため、走査中に
  落ちると索引が不完全なまま残っていた（下記 junction 問題で実際に壊れた）。
- **`docsweep fix-conflict --path` が効いていなかったのを修正。** `--path` の dest が
  `_add_scope_args` の positional `paths`（スキャンルート）と衝突し、positional 側の
  既定値 `[]` が `append` の結果を潰していたため、**1 件だけ指定したつもりで全 conflict が
  処理対象になっていた**（`--prefer` の向きが合わない conflict まで巻き込むと、完了済み
  plan が未着手へ戻る誤修正になる）。dest を分離し、併せて渡されたパスを
  `resolve().as_posix()` で正規化して Windows のバックスラッシュ表記でも一致するようにし、
  1 件も一致しなかった指定は stderr に警告するようにした（黙って「対象なし」で終わると
  誤指定に気づけない）。
- `index-sync` / `index-rebuild` が、スキャンルート配下に junction / symlink があると
  `ValueError: '...' is not in the subpath of '...'` で中断していたのを修正。
  `FileRecord.path` は `resolve()` 済みのためリンク先がルート外の実体パスに解決され、
  `relative_to(root)` が失敗していた。ルート相対が取れない場合は実体の絶対パスを
  `rel_path` の識別子に使う（表示・移送に使う絶対パスは従来どおり `abs_path` 列を参照する
  ため実害はない）。`index-rebuild` は `DELETE FROM files` の直後にこのループへ入るので、
  中断すると索引が不完全なまま残っていた。

## [0.3.1] - 2026-07-18

v0.3.0 patch。`.docsweep/backup/` 経由で `docs/local/*.md`（家宣言 private）が
公開リポの origin/main に意図せず到達する漏洩事故が実際に発生した（many-ai-cli /
offline-md-editor-viewer / PlainSheet / ShotTTL）ため、書き込み前 backup 機構を
根本から撤去して漏洩経路そのものを消した。同時に `manual_release-*.md` を sweep 対象
type として正式認識した。削除したのは全て内部ヘルパで CLI/MCP の外向き API は無変更。

### Added

- `manual_release-*.md` を内蔵の `manual_release` type として認識し、`[完了]` / `[廃止]`
  に達したリリース記録を通常の `docsweep sweep` で自動 archive できるようにした。

### Fixed

- **`.docsweep/backup/` 経由の docs/local 漏洩を根本対処**。書き込み前に
  `.docsweep/backup/<name>.<ts>` へ md 丸コピーを 30 日保持する `atomic.backup()` 機構を
  丸ごと撤去し、`take_backup` 引数・`backup_dir_for` / `_ensure_gitignored` /
  `_cleanup_backups` および関連定数（`BACKUP_DIR_NAME` / `BACKUP_RETENTION_SECONDS` /
  `GITIGNORE_MARK` / `GITIGNORE_RULE`）を削除。
  背景: 実質 99% の書き込みは `update_line` 経由の 1 行差分（H1 ラベル `[完了]` 化・
  `due:` 差替）で、その世代を md 全文で残すのは過剰。加えて `.docsweep/backup/` を
  gitignore し忘れた公開リポで `docs/local/*.md`（非公開ポリシー宣言済み）が意図せず
  push される事故が実際に発生した（many-ai-cli / offline-md-editor-viewer など）。
  復元は git 側の履歴に一本化し、docsweep はプロジェクト空間内に何も生成しない。
  外部利用者向け API（CLI サブコマンド / MCP tool）は無変更、削除したのは全て内部
  ヘルパのため **v0.3.1（patch）** に留める。既存ユーザーは各プロジェクトの
  `.docsweep/backup/` を手動削除して構わない（`state.json` は温存）。

## [0.3.0] - 2026-07-16

2026-07-16 の ai-audit-prompts / ultracode 監査（52 findings 検出）と、その進言事項 15 件を
全消化した「監査シリーズ完結版」。critical / high の security fix、Cookie 認証への移行、
`cli.py` / `inject.py` の分割 refactor を含む minor bump。互換性維持: URL クエリ認証は
hybrid 経路として残る（v0.4.x で廃止予定）。

### Security

- **[critical]** capture 経路（`POST /api/capture/save` と MCP `capture_save`）の任意ファイル
  書き込みを塞いだ。`save_drafts` に `target_dir` のスキャンルート境界チェックと
  `suggested_filename` の basename 化・`.md` 拡張子検証を導入し、`../evil.md` 系の
  トラバーサルと絶対パス書き込みを拒否する（`CaptureScopeError`）。**トークン漏洩時に
  スタートアップ / 認証情報 / cron 等へ任意書き込みできた実質 RCE 経路を除去**。
- **[medium]** capture 画面（`server/templates/capture.html`）の innerHTML 経路で
  `d.kind` / `d.suggested_filename` を `escapeHtml` 未通しだった箇所を修正（XSS 除去）。
- Web UI 認証を **HttpOnly / SameSite=Strict Cookie と `x-docsweep-token` ヘッダへ移行**。
  初回の `?token=` 付き URL は Cookie へ交換後に token 無し URL へ redirect する
  hybrid モード（URL クエリ認証は v0.4.x で完全廃止予定・本版はその告知フェーズ）。
- `POST /api/config/roots` を `--allow-root-mutation` 起動フラグで守るようになった。
  未指定なら 403、指定時も `/`・`C:\`・HOME 直下の追加は allowlist で常時拒否
  （トークン漏洩時のスキャン範囲拡張＝任意 .md 読取／書換の連鎖を防ぐ）。
- `docsweep/server/sanitize.py` の stdlib HTMLParser フォールバック（88 行）を削除し、
  `nh3` を `web` extras の必須依存に。パーサ実装差に依存した bypass 余地を除去。

### Added

- **CI ワークフロー**（`.github/workflows/ci.yml`）を新設。Python 3.10 / 3.12 マトリクスで
  `pytest` / `ruff check` / `mypy` を回す。mypy は段階導入のため `continue-on-error` で
  警告扱い（既存コードでの初回真っ赤を回避）。
- `--allow-root-mutation` CLI フラグ（上記 Security の項参照）。
- `[project.optional-dependencies].all-lite` extras 新設。`resurrect`（torch/CUDA を
  引きずる GB 級）を除いた実用最小構成（`web,review,mcp,watch`）。大半のユーザーは
  こちらで足りる想定。
- `services/frontmatter.py` に `read_frontmatter(path)` / `read_frontmatter_text(text)` の
  read API を追加。既存 9 モジュールに散っていた `_FRONTMATTER_RE` 直接利用を services
  内に集約するための基盤（下記 Refactor 参照）。
- `services/frontmatter.py` に `FrontmatterBlockStyleError` を追加し、手書き block-style
  list（`tags:\n  - a\n  - b`）を検出したら書き換えを拒否（フロー記法前提の 1 行置換で
  継続行が孤立し YAML パースが壊れる事故を予防）。
- `migrate-frontmatter` を「素の md を OKF 形式に整えるフォーマッタ」へ一般化。従来は
  frontmatter が 1 行でもあると無条件スキップだったが、OKF キー（type/status/tags/owner/
  review_status/related/last_reviewed）が欠けている md へ**不足キーだけを追記**する
  mode=`upgrade` を追加（`due:` だけの部分 frontmatter 等が対象。既存キーの値・行は不変・
  H1 温存の不変条件は維持）。JSON 出力と CLI 表示に `mode` を追加。
- inject の導線（グローバル / プロジェクト）に「新規 md の作成（OKF frontmatter 必須）」節を追加
  （`GUIDANCE_VERSION` 3 → 4）。AI が `docsweep new` を通さず手書きすると `due:` だけの
  最小 frontmatter になり OKF フィールドが欠落する穴を、docsweep 自身の注入ルールで塞ぐ
  （特定の AI ツールに依存しない）。反映には `docsweep inject --global` の再実行が必要。
- pytest を `--strict-markers --strict-config` で厳格化。未宣言 marker と設定ミスを即エラー化。
- `[tool.mypy]` セクション追加（`ignore_missing_imports = true` / `warn_unused_ignores = true`）。
- `[tool.ruff.lint].select` を `E,F,I,UP,B,SIM,RUF` に拡張（バグ検出系 PLE/PIE は次版で
  段階導入予定）。

### Fixed

以下は 2026-07-16 監査で確定した finding のうち、最小修正で対応可能なもの 12 件。

- `engine.relabel_file` を `write_atomic` 経由へ寄せて、atomic.py の宣言（「全ての書き込み
  API はこのヘルパ経由で MD を更新する」）と整合。バックアップと原子的差し替えが効くように
  なり、Web UI 編集中の md を CLI/MCP 側から書き換える race が壊れにくくなる。（A-01）
- `config.load_config` が `postpone_warn_threshold` に文字列や null が入った YAML を読ませ
  られると `ValueError` で全コマンドが起動不能になっていたのを、`_safe_int` フォールバック
  に修正（既定値 3 / 5 へ落とす）。（A-02）
- `docsweep auto-triage --apply` が対象 JSON の欠損や破損で生の traceback を吐いていたのを、
  `FileNotFoundError` / `JSONDecodeError` / `UnicodeDecodeError` を捕捉して exit 2 に修正。
  （A-03）
- 空 `due:` 行（値なし）を含む md への `update_due` で `due:` キーが重複挿入され YAML
  パーサ依存の last-wins になっていたのを、`_DUE_LINE_RE` を `[ \t]` 空白限定に修正して
  1 本置換に。（B-01）
- `inject.save_manifest` を tmp → `os.replace` のアトミック書き込みに変更。inject/eject 中の
  プロセス停止で `injected.json` が truncate → 全プロジェクトの注入履歴（block ハッシュ・
  preset_version）が事実上失われる事故を防止。（B-04）
- `graph` の node id で複数プロジェクトの同名 md（例: 各プロジェクトの `plan_v0.1.md`）が
  basename 衝突していたのを、**衝突時のみ** `project/basename` の複合キーに昇格させる部分
  互換方式で修正（衝突なしの通常ケースは basename のまま・後方互換）。（C-02）
- `brief` の `yesterday_done` が age_days 降順（＝古い順）になっていたのを mtime 降順
  （＝新しい順）に修正。24h ウィンドウ内での自然な並び順に。（C-03）
- `timeline._resolve_date` が `rec.mtime` None のレコード 1 件で TypeError → timeline 全体が
  落ちていたのを、`("", "unknown")` フォールバックに修正。（C-04）
- 対話 `triage --review` を Ctrl+C で中断すると蓄積した判定が全ロスしていたのを、
  `KeyboardInterrupt` を捕捉してここまでの pairs を dispatch するよう修正
  （EOFError と対称の挙動に）。（C-05）
- `atomic.backup` のファイル名 suffix を `time.time()` → `time.time_ns()` に上げ、同一秒内
  2 回書きで前世代を上書きする問題を修正。（A-05）

### Changed / Refactor

- `docsweep/cli.py`（2311 行）を `docsweep/cli/` パッケージに分割。`cli/parser.py`
  （argparse 定義）と `cli/commands/*.py`（読取系 / 書込系 / 特殊系）に責務分離。
  公開エントリ `docsweep.cli:main` と既存 `from docsweep.cli import ...` は `__init__.py`
  の re-export で完全互換。（M-06）
- `docsweep/inject.py`（828 行）を `docsweep/inject/` パッケージに分割。`blocks.py` /
  `manifest.py` / `agent_claude.py` / `agent_codex.py` / `api.py` の 5 モジュールへ責務分離。
  公開 API（`inject` / `eject` / `inject_global` / `eject_global` / `MANIFEST_PATH` / etc）は
  `__init__.py` から re-export され後方互換 100%。（M-10）
- **frontmatter 読み書き API を `services/frontmatter.py` に一本化**。9 モジュール
  （`claim.py` / `context.py` / `related.py` / `migrate.py` / `timeline.py` / `fix_conflict.py` /
  `services/due.py` 他）に散っていた `_FRONTMATTER_RE` 直接利用を services 内に集約。
  `services/due._replace_or_insert_due` は廃し、`services/frontmatter.update_frontmatter_field`
  に統合。9 箇所の drift（`migrate.py:32` が「厳密版が必要」と告白していた既知の負債）を
  解消。（M-07 / M-08）

### Tests

- 97 テスト追加（既存 535 → 632）。内訳:
  - `tests/test_audit_fixes_2026_07_16.py`（16 件）: 上記 Fixed の再現テスト
  - `tests/test_server_routes_extras.py`（17 件）: `/graph` `/resurrect` `/brief` `/cross`
    `/capture` の 200 / 401 / エラー系 smoke
  - `tests/test_cli_smoke.py`（55 件）: 全 CLI サブコマンドの `--help` exit 0 smoke
  - `tests/test_mcp_tools.py` に read 系 smoke 7 件追記
    （`route_intent` / `doctor` / `day` / `list_projects` / `set_project_enabled` /
    `inject_global` / `eject_global`）
  - `tests/test_services_frontmatter.py` に block 記法検出テスト 2 件追記
- Phase 2 (L-01/02/03/04/05) の追加分と合わせて最終 **650 テスト green**。

## [0.2.1] - 2026-07-04

### Fixed

- `serve` を Ctrl+C で停止した際に KeyboardInterrupt のスタックトレースが出ていたのを
  1 行の停止メッセージに変更（Python 3.14 の asyncio.runners 再送出を捕捉）。
- frontmatter と H1 ラベルの status 食い違い warning が Web UI の描画のたびに
  繰り返し出力されていたのを、同一 (path, message) につきプロセス内 1 回に抑制
  （矛盾自体は従来どおり needs_fix フラグで可視化され続ける）。
- 看板左上のロゴがファビコンと異なる「DS」テキストアイコンだったのを、
  favicon.svg と同一画像に統一。
- 狭幅ウィンドウ（埋め込みブラウザ等）でトップバーの「再スキャン」ボタンや編集ペインの
  「プレビュー/編集」タブに `white-space: nowrap` が無く、文字が折り返して縦積みに
  なっていたのを修正。900px 以下では看板と編集ペインを上下 2 段に、640px 以下では
  3 列カードを 1 列に切り替えるレスポンシブ対応も追加。

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
