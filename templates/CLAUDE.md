# AI 作業ドキュメント運用ルール（docsweep 標準）

このセクションは AI コーディングエージェント（Claude Code 等）が `docs/` 配下に
作業記録の Markdown を作成・更新するときの共通ルールです。
このファイルが **唯一の正本（single source of truth）** です。
Codex など他エージェント向けの `AGENTS.md` はこのファイルを参照するだけにしてください
（ルールを複製しないこと。複製するとズレて事故ります）。

---

## ファイル種別と命名規則

| 種別 | 用途 | ファイル名 |
|---|---|---|
| `plan_*.md` | 計画・調査・検討・リリース準備とその実行ログ | `plan_<topic>.md` |
| `bugfix_*.md` | 障害対応記録（完了・進行中） | `bugfix_<topic>_YYYY-MM-DD.md` |
| `pending_*.md` | 保留・将来対応・着手条件待ち | `pending_<topic>.md` |
| その他 | 参照資料・手順・セットアップ（作業管理外） | `reference_*.md` / `manual_*.md` / `setup_*.md` |

- `<topic>` はケバブケース（例: `files-tree-search-child-filter`）。
- 既存ファイルと衝突したら末尾に枝番（`_2` 等）。
- リリース 1 回分の MD も `plan_release-vX.Y.Z_YYYY-MM-DD.md` とする。旧 `manual_release-*` は履歴として読み取り互換にするが、新規作成しない。
- 調査・リサーチも独立した `research_*.md` にはせず、`plan_<topic>.md` にまとめる。

### 静的な manual / reference / setup

`manual_*.md` / `reference_*.md` / `setup_*.md` は、後から読む手順・仕様・参照資料です。
`plan` / `bugfix` / `pending` の作業状態管理には入れません。OKF frontmatter の `type` と
lifecycle `status` は付けてもよいですが、通常は `docsweep_state` と `due` を付けません。
更新作業が必要になったときだけ、別途 `plan_update-*.md` を作成して対象文書を更新します。

### Obsidian knowledge artifacts

The configured `work_dir` is the active work queue for `plan_*.md`, `bugfix_*.md`, and
`pending_*.md`. Session recaps, skill-review reports, and audit reports belong in
the repository-relative `docs/obsidian/` entry:

`recap_YYYY-MM-DD_<topic>.md`

`report_skill_<topic>_YYYY-MM-DD.md`

`report_audit_<topic>_YYYY-MM-DD.md`

These artifacts use OKF lifecycle `status: draft | stable | deprecated`, set
`docsweep_policy: never_archive`, and do not use `docsweep_state` or `due`.
They are excluded from the docsweep active queue. Follow-up work must be a separate
plan, bugfix, or pending file under the configured `work_dir`, linked from the artifact.

### 配置先（共通）

1. `python -m docsweep new <type> <topic>` を使い、設定済みのプロジェクト相対 `work_dir` に作る
2. `work_dir` 未設定時の既定は `docs/local/`（`docs/` の有無で暗黙に切り替えない）
3. 会話履歴の草案保存（`capture` / `capture_save`）も同じ queue を使う

---

## H1 ステータスラベル（必須）

すべての種別で、H1 タイトルの**先頭にステータスラベル**を付けます。
docsweep はこのラベルを読み取って自動アーカイブ・要判断フラグを判定します。

| 種別 | 使えるラベル |
|---|---|
| `plan_*.md` | `[計画]`（新規・未着手） / `[実行中]`（一部着手） / `[様子見]`（直したが寝かせ中） / `[保留]`（一時停止） / `[完了]` / `[廃止]` |
| `bugfix_*.md` | `[実行中]`（調査・修正中） / `[様子見]`（修正して寝かせ中） / `[保留]`（一時停止） / `[完了]` / `[廃止]` |
| `pending_*.md` | `[保留]`（既定） / `[計画]`（着手を決めた） / `[廃止]` |

例: `# [計画] 認証フローのリファクタ`

`pending_*.md` に `[完了]` は付けられません（着手を決めたら `[計画]` か、下記の昇格で
`plan_*.md` を作る）。逆に `bugfix_*.md` に `[計画]` は付けられません（事後記録のため）。

各ラベルの内部状態と docsweep の扱い（**内蔵デフォルト**）:

| 内部状態 | 日本語 | 英語 | 自動 archive |
|---|---|---|---|
| planned | `[計画]` | `[Planned]` | ✗ |
| in-progress | `[実行中]` | `[In Progress]` | ✗ |
| watching | `[様子見]` | `[Watching]` | ✗（寝かせ中＝archive しない・守る） |
| done | `[完了]` | `[Done]` | ✓ |
| discarded | `[廃止]` | `[Discarded]` | ✓ |
| pending | `[保留]` | `[Pending]` | ✗ |

- `[様子見]` = 直した／一周したが、再発確認のため寝かせている状態。**docsweep は自動移送しない**。
  再発が無いと確認できたら手で `[完了]` に上げる（その時点で archive 対象）。再発したら `[実行中]` へ戻す。既存の `[対応中]` は読み取り互換。
- `[廃止]` = 陳腐化して捨てると判断したもの。**削除ではなく** `archive/` へ隔離（復元可能）。
- `> ステータス:` 行は**書かない**（状態は H1 ラベルに集約）。状態が変わったら H1 ラベルを書き換える。
- ラベル語彙はプロジェクト設定（`.docsweep.yaml` の `states:`）で追加・改名・言語追加できる。
  上表は内蔵デフォルト。`python -m docsweep inject` は `states:` からこのラベル節を生成する（設定と検出が常に同期）。

> フロントマター方式を併用してもよい。OKF v0.2 の `status: draft | stable | deprecated` は
> 文書 lifecycle、docsweep の作業状態は `docsweep_state: planned | in-progress | watching |
> done | discarded | pending` に置く。旧形式の `status: planned` などは読み取り互換である。
> docsweep は **docsweep_state（または旧 status） > H1 > ファイル名** の優先順で作業状態を検出し、
> lifecycle `status` は作業状態と誤認しない。方式が食い違うファイルは可視化する（自動では直さない）。

---

## OKF（Open Knowledge Format）互換 frontmatter（推奨）

docsweep は [OKF v0.2 の公式仕様](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) 互換の
frontmatter を採用しています。新規 md は `python -m docsweep new` で
以下の最小ブロック付きで生成されます:

```markdown
---
type: plan                       # OKF は任意の非空文字列。docsweep 管理対象は plan 等
status: draft                    # OKF lifecycle: draft | stable | deprecated
docsweep_state: planned          # docsweep 作業状態: planned | in-progress | watching | done | discarded | pending
tags: []                         # 自由 list（語彙統制なし）
owner:                           # ユーザー名スカラ（claim コマンドで自動セット）
review_status: draft             # draft | review | published（陳腐化前倒し検知用）
related: []                      # 関連する md のファイル名 list（fix-related で双方向化）
# docsweep_parent: docs/local/plan_parent.md  # child の repo-relative 親 path（related とは別の正本）
last_reviewed: 2026-06-29        # YYYY-MM-DD（stale 判定に使用）
due: 2026-07-06                  # 任意・期日（看板方式）
---

# [計画] タイトル
```

docsweep 固有の追加規約は次のとおりです:

- **archive / due / triage の標準的な作業管理対象は `plan` / `bugfix` / `pending`**。
  `manual_release` は旧形式として読み取り互換を残すが、新規 release md は `plan_release-*` とする。
  `manual` / `reference` / `setup` などの未知 type や追加フィールドは壊さず保持し、`okf-check` では拒否しない。
- **H1 ステータスラベル運用は廃止せず併用**。frontmatter があればそちら優先、無ければ
  H1 へフォールバック（後方互換 100%）。
- 親 plan を `python -m docsweep new plan <topic> --split N` で分割すると、各 child に
  `docsweep_parent: <repo-relative path>` が付く。`related` は汎用の関連資料用であり、親子の
  機械判定を単独で担わない。

### 既存採用者向け移行ガイド

frontmatter なしで運用していた md は触らなくても動き続けます。一括で OKF 互換に
揃えたい場合は以下の手順を踏みます:

```bash
# 1. 全 plan/bugfix/pending に frontmatter を非破壊挿入（dry-run、差分を表示）
python -m docsweep migrate-frontmatter --dry-run

# 2. 内容を確認した上で適用（H1 ラベル・本文・未知フィールドは触らない）
python -m docsweep migrate-frontmatter --apply

# 3. 片側 related を双方向化
python -m docsweep fix-related --apply
```

旧 `status: planned` のような文書は、migrate により `status: draft` と
`docsweep_state: planned` に分離されます。OKF と docsweep 内部 state key の対応は
`docs/okf-mapping.md` を参照してください。

### `docsweep export --okf`

「docsweep を抜けても md が腐らない」を実演する zip エクスポートも提供しています:

```bash
python -m docsweep export --okf                    # ./docsweep-okf-<date>.zip
python -m docsweep export --okf --include-archive  # archive/ 配下も含める
python -m docsweep okf-check ./bundle --json       # read-only 適合検査
python -m docsweep okf-profiles                    # 同梱 profile 一覧
```

zip 内の通常 md は frontmatter を Bundle 内だけ正規化し、`index.md` と
`okf-manifest.json` を生成します。元ファイルは変更しません。profile は既定では同梱 JSON を
オフラインで読み、外部 URL / GitHub Raw は `--okf-profile` を明示したときだけ取得します。

### pre-commit hook（任意）

frontmatter 不整合（空の type、lifecycle / docsweep_state の誤記、related で存在しない md 指定、
review_status が許容外）をコミット時に止める hook を opt-in で配置できます。未知の type は
OKF の許容範囲なので hook でも拒否しません。
スクリプトは自身と同じ場所の `.githooks/docsweep-check.py` を `.git/hooks/pre-commit` へ
コピーするだけなので、パスはこのテンプレ一式（`install-hooks.*` + `.githooks/`）を
置いた場所に読み替えてください:

```bash
# POSIX（例: docsweep リポジトリの templates/ を参照する場合）
bash <テンプレ配置先>/install-hooks.sh

# Windows
pwsh <テンプレ配置先>/install-hooks.ps1
```

docsweep 本体がインストールされていない環境でもスタンドアロンで動きます。

---

## 期日（due）と看板方式（任意・推奨）

H1 ラベル（状態軸）と直交する**第 2 軸として期日 `due:` を frontmatter に入れる**ことで、docsweep の Web UI 看板ボードと MCP 経由の AI による期日操作が成立します。

```markdown
---
due: 2026-06-29        # 「今の状態でいられる締切」（[計画]/[実行中] なら着手日、[様子見] なら卒業日）
status: draft           # OKF lifecycle
docsweep_state: planned # docsweep の作業状態
---

# [計画] タイトル
```

- 形式は **`YYYY-MM-DD` 厳格マッチ**。日付以外（`expiry`/`期日`/`next_action` 等の別表現）は吸収しない。
- 期日超過は **候補フラグ止まり**。`[廃止]`/`[完了]` 確定は **必ず人のワンクリック**（MCP / Web UI / CLI どこからでも）。
- `due:` 未記入のファイルは「期日なし（締切管理対象外）」として従来どおり扱われる（既存 plan を遡及書き換えしない）。
- `update_due` が呼ばれるたびに `.docsweep/state.json` の `postpone_count` が +1 され、しきい値（既定 3 で警告色・5 で廃止候補色）に達すると Web UI で色分け表示される。
- ラベル遷移（`[計画]→[実行中]` 等）で `postpone_count` は自動リセットされる。
- **物理削除の口は持たない**（最悪でも archive 移動止まり）。AI 経由・MCP 経由でも同じ。

### 状態別の `due` 超過の意味

| 状態 | `due` 超過の意味 | docsweep の扱い |
|---|---|---|
| `[計画]` / `[実行中]` / `[保留]` | やり忘れ（着手すべき期日を過ぎた） | 🔴 やり忘れ列・赤フラグ |
| `[様子見]` | 卒業判定どき（寝かせ期限到来） | ▼ 卒業判定セクション |
| `[完了]` / `[廃止]` | 判定対象外（既に archive 行き） | 判定しない |

### 期日の更新口（3 ボタンに収束）

Web UI 看板でカードを「外す」操作は 3 つのみに集約される（MCP からも同じ 3 操作）:

1. **着手** — `update_status(path, '実行中')` + 期日を今日 + N 日に自動更新
2. **期日更新** — `update_due(path, '+1d' | '+1w' | '+1m' | 'YYYY-MM-DD')`
3. **廃止** — `update_status(path, '廃止')` → archive 移送（確認ダイアログ後）

新規 `plan_*.md` / `pending_*.md` を作るときは frontmatter に `due:` を入れて生まれてくることを推奨します（`bugfix_*.md` は新規時には付けず、`[様子見]` へ移した時に docsweep が `due.default_offset_days.bugfix_watching` から付けます。既存の `due:` は上書きしません）。`plan_release-*` も plan として扱います。

---

## セッション開始時の残作業確認（必須）

新しい AI セッションを始めたら、コードに触る前に **まず `python -m docsweep triage`**
（MCP 接続時は `triage` ツール）を実行し、返ってきた残作業を確認すること。
PATH に `docsweep` コマンドが無くても動くよう、AI 向け導線では `python -m docsweep` 形式を優先する。

- triage は **要判断（陳腐化した計画）＋保留** を **古い順**（放置されたものを上に）で返す。
- 各 item は `project` / `rel`（相対パス）/ `title`（H1）/ `state`（ラベル）/ `type` /
  `age_days` / `summary` / `actions`（機械実行できる操作）を持つ。
- ファイル名や場所を思い出せなくても、これで「次にやるべき作業」が先頭に出る。
  ユーザーが「続きやって」と言ったら triage 先頭 item の `path` を対象にする。
- 横断 INDEX 全体を俯瞰したいときは `python -m docsweep summary`、人間向け週次は `python -m docsweep report`。

---

## 親子 plan の closeout

親 plan を含む実装完了報告を受けたら、状態を変える前にまず
`python -m docsweep closeout-check --path <parent-plan> --json` を実行する。
global の `plan-closeout` skill が導入済みなら補助に使ってよいが、skill が無くてもこのコマンドで確認できる。

- 結果の機械 blocker、手動確認、dirty worktree と変更予定ファイルの重複を分けて扱う。
- blocker があれば relabel せず、手動確認が残ればユーザーへ確認する。明示承認前に H1 / `docsweep_state` を変更しない。
- 承認後も子 plan から親 plan の順に進める。archive は別の `sweep --dry-run` と別承認に分ける。
- 「実装完了」「静的検証済み」「手動確認済み」「watching」「done」「archive済み」は同義ではない。

---

## plan_*.md の書式

- ファイル名 `plan_<topic>.md`。
- H1 先頭にステータスラベル（`[計画]` から開始）。
- H1 直下に `## context配分` 表を**必ず先頭セクション**として置く。

  | C | 種別 | 内容 | 備考/注意点 |
  |---|---|---|---|
  | C1 | planned | … | — |
  | C2 | done | … | — |

  - 列の順は `C` → `種別` → `内容` → `備考/注意点` に揃える（状態を左端寄りに置いて、
    表を縦に眺めたときに残作業が読める形にする）。旧い順（`C` / `内容` / `種別`）の既存 md も
    読める（列は名前で解決している）ので、一括変換は必須ではない。
  - 章番号は `C1` `C2` `C3` の連番（`Step 1/2` 表記は禁止）。
  - 種別は `planned` / `done` の 2 値のみ（OKF lifecycle と同じ語彙）。
  - 備考/注意点は空欄でよい（`—` を置く）。並列可否・子 plan への相対リンク・前提条件など、
    その C を実行する人が実行前に知っておくべきことを 1 行で書く。
  - 全 C が `planned` なら H1 は `[計画]`、一部 `done` なら `[実行中]`、全 `done` なら `[様子見]`
    （直したが寝かせ中＝archive されない。寝かせ不要と分かっていれば手で直接 `[完了]` にしてよい）。
  - 旧表記の `plan` / `fix` は同義として読む（新規では使わない）。
- `[完了]` / `[廃止]` は手の意思決定でのみ付ける（自動導出しない）。再発したら `[実行中]` へ戻す。

### `plan_release-*.md` の特例

リリース 1 回分の plan は、通常の `context配分` 表の代わりに、Release skill が読む次の 3 節を順序固定で持ちます。

1. `## リリース引数` — その回の repo / version / channels / dry-run などの確定値
2. `## 実行計画` — 前提チェックから配布後確認までの検証付き手順
3. `## 申し送り` — 実行結果・残作業・復旧内容

H1 は `[計画]` → `[実行中]` → `[完了]` を使い、部分未了は `[保留]` とします。

---

## bugfix_*.md の書式

- ファイル名 `bugfix_<topic>_YYYY-MM-DD.md`（アンダースコア区切り）。
- H1 先頭にラベル（調査・修正中=`[実行中]` → 修正して寝かせ中=`[様子見]` → 確認済み=`[完了]`。陳腐化＝`[廃止]`）。
- H1 直下に `## context配分` 表を置く（plan と同じ列順 `C` / `種別` / `内容` / `備考/注意点`）。
  切り分けと修正が 1 手で終わるなら `C1` 1 行でよい。表があることで `provenance start --context C1`
  が bugfix でも使える。**ただし H1 ラベルは表から自動導出しない**（bugfix のラベルは手で付ける）。
- 必須セクション（`## context配分` の後に、この順序で固定）:
  1. `## 症状`
  2. `## 根本原因`
  3. `## 修正内容`
  4. `## 変更ファイル`
  5. `## 検証`
  6. `## 備忘`

---

## pending_*.md の書式

「着手しない判断を残す」軽量メモ。plan ほど構造化せず、bugfix ほど厳密にしない。

- ファイル名 `pending_<topic>.md`（日付サフィックス不要）。
- H1 は `# [保留] <タイトル>` 固定。
- 必須セクション（順序固定）:
  1. `## 概要` — 何が問題か / 何をしようとして止めたか
  2. `## 保留理由` — なぜ今着手しないか（外部依存・優先度・リソース・判断待ち等）
  3. `## 着手条件` — 何が揃えば再開できるか（箇条書き）
  4. `## 関連情報` — 関連 PR / Issue / plan / bugfix へのリンク（任意・無ければ省略）
- `## context配分` 表・`## 検証` は**不要**（未着手のため）。

---

## ライフサイクル（昇格）

```
pending_*.md ──着手決定──▶ plan_*.md ──修正完了──▶ bugfix_*.md
   （[保留]）              （[計画]→[実行中]）        （[完了]）
```

- pending を着手したら `plan_*.md` を新規作成。元の `pending_*.md` は archive へ。
- plan の作業が完了したら `bugfix_*.md` を別途作成し、plan の H1 を `[完了]` に。
- `[完了]` / `[廃止]` になったファイルは docsweep が `archive/` へ自動移送する。
- `[様子見]` は寝かせ中なので**自動移送されない**（守られる）。`[廃止]` は削除ではなく archive へ隔離（復元可能）。

---

## 自動生成ショートカット（任意・推奨）

ユーザーが下記の短い指示を出したら、追加質問を最小化して**その場で md を生成**する。
topic・配置先・本文の埋め草は直近の会話文脈から推定する。

| トリガー例（部分一致） | 生成物 |
|---|---|
| 「プラン作成」「plan 作って」「これでプラン書いて」 | `plan_<topic>.md` |
| 「バグフィックス作成」「bugfix 作って」 | `bugfix_<topic>_YYYY-MM-DD.md` |
| 「ペンディング作って」「保留として入れといて」「いったん保留」 | `pending_<topic>.md` |

- 「ローカルに入れて」「docs ローカル」は**設定済み work queue の指示**。種別は会話文脈で判断。
- 取れない情報は `<TODO: ...>` プレースホルダで残す。
- 「プラン読んで」「bugfix を更新して」など**既存ファイル操作**を意味する場合は
  このショートカットを発動せず通常通り読み書きする。
