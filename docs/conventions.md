# AI 作業ドキュメント命名・ステータス規約

docsweep が前提とする Markdown 作業記録の命名規則とステータス表現の解説です。
AI エージェントに従わせる**運用ルール本体**は `templates/CLAUDE.md`（正本）にあり、
このファイルは**採用を検討する人間が読むための仕様解説**です。

---

## なぜこの規約があるのか

AI コーディングエージェントと作業すると、計画・障害対応・保留判断を Markdown として
大量に残すことになります。これを放置すると次の問題が起きます:

1. 完了ファイルがアーカイブされず溜まり続ける
2. 陳腐化した計画ファイルが「やるべきか・捨てていいか」判断不能になる
3. 現状確認のたびに全ファイルを AI に読ませる（遅い・トークン浪費）
4. 複数プロジェクトにまたがると全体像を把握できない

docsweep は **H1 ステータスラベルを機械的に読み取り**、完了を自動アーカイブし、
陳腐化を要判断フラグで可視化することでこれを解消します。
そのために、ファイルの命名と状態表現を一定の規約に揃える必要があります。

---

## 3 つの作業ファイル種別

| 種別 | 役割 | ファイル名 | 状態の終点 |
|---|---|---|---|
| plan | 計画・調査・検討・リリース準備 | `plan_<topic>.md` / `plan_release-vX.Y.Z_*.md` | `[完了]` → archive |
| bugfix | 障害対応の事後記録 | `bugfix_<topic>_YYYY-MM-DD.md` | `[完了]` → archive |
| pending | 着手しない判断の保留 | `pending_<topic>.md` | 昇格 or 廃棄 |

種別をまたぐ昇格は一方向です:

```
pending ──着手──▶ plan ──完了──▶ bugfix
```

調査・リサーチも独立した `research_*.md` にはせず、`plan_<topic>.md` にまとめます。
`manual_*.md` / `reference_*.md` / `setup_*.md` は作業ファイルではなく、再利用する手順・仕様・参照資料です。
これらは docsweep の作業状態・期日・自動 archive の対象にせず、OKF の `type` / `status` だけで文書 lifecycle を表します。
リリースの一般手順は `manual_release.md`、個別リリースの実行計画とログは `plan_release-vX.Y.Z_*.md` とします。

## 作業キューと Obsidian 記録

標準の作業キューは `docs/local/` に置く次の 3 種だけです。

`plan_*.md`、`bugfix_*.md`、`pending_*.md`

セッション振り返り・skill 化評価・監査証跡は作業キューではなく、対象 repo の
`docs/obsidian/` に置く knowledge artifact です。

| artifact | ファイル名 | lifecycle | docsweep |
|---|---|---|---|
| セッション振り返り | `recap_YYYY-MM-DD_<topic>.md` | `status: draft/stable/deprecated` | active queue 外 |
| skill 化評価 | `report_skill_<topic>_YYYY-MM-DD.md` | `status: draft/stable/deprecated` | active queue 外 |
| 監査証跡 | `report_audit_<topic>_YYYY-MM-DD.md` | `status: draft/stable/deprecated` | active queue 外 |

これらには `docsweep_policy: never_archive` を付け、`docsweep_state` と `due` は付けません。
監査や振り返りから作業が発生した場合は、`docs/local/` に別の plan / bugfix / pending を作り、
artifact と相互リンクします。docsweep の global/project ignore は Git の tracking 方針とは
別契約なので、private repo で tracked な Obsidian note も active scan には含めません。

---

## ステータス表現（H1 ラベル）

各ファイルは H1 タイトルの**先頭**にステータスラベルを持ちます。
docsweep はこの角括弧ラベルを正規表現で抽出します。

| 種別 | 取りうるラベル |
|---|---|
| plan | `[計画]` / `[実行中]` / `[様子見]` / `[完了]` / `[廃止]` |
| bugfix | `[実行中]` / `[様子見]` / `[完了]` / `[廃止]` |
| pending | `[保留]` |

各ラベルは内部状態に対応し、docsweep は内部状態で挙動を決めます（**内蔵デフォルト**）:

| 内部状態 | 日本語 | 英語 | 自動 archive |
|---|---|---|---|
| planned | `[計画]` | `[Planned]` | ✗ |
| in-progress | `[実行中]` | `[In Progress]` | ✗ |
| watching | `[様子見]` | `[Watching]` | ✗（寝かせ中＝守る） |
| done | `[完了]` | `[Done]` | ✓ |
| discarded | `[廃止]` | `[Discarded]` | ✓ |
| pending | `[保留]` | `[Pending]` | ✗ |

例:

```markdown
# [実行中] 認証フローのリファクタ
```

`[完了]` / `[廃止]` を検出したファイルは設定された `archive/` へ自動移送されます
（`[廃止]` は削除ではなく隔離。復元可能）。`[様子見]` は寝かせ中なので**自動移送されません**。
ファイルの最終更新時刻（filesystem の mtime）から N 日以上経過した `[計画]` は「要判断」としてフラグが立ちます（N は type ごとに設定可能）。

> ラベル語彙はプロジェクト設定（`.docsweep.yaml` の `states:`）が単一の正本です。
> 上表は内蔵デフォルトで、利用者は状態の追加・改名・言語追加（日英）ができます。
> `python -m docsweep inject` は `states:` から `CLAUDE.md` のラベル節を生成するので、設定・AI への指示・検出が常に同期します。

---

## 期日（due）と看板方式

H1 ラベル（**状態軸**）とは直交する**第 2 軸として期日 `due`** を frontmatter に置けます。
これにより docsweep の Web UI 看板ボードと MCP 経由の AI による期日操作が成立します。

```markdown
---
due: 2026-06-29
---

# [計画] タイトル
```

### 2 軸モデルのメンタル

- **軸 1（H1 ラベル）** = ライフサイクル。**archive を決める唯一の根拠**。一直線で進む。
- **軸 2（`due`）** = 締切 / 着手期日。**archive には絶対触らせない**。並べ替え・絞り込み・気づきフラグ専用。

`due` は archive 制御に**一切絡めず**、超過は候補フラグ止まりです。
`[廃止]` / `[完了]` の確定は必ず人のワンクリック（Web UI / MCP / CLI どこからでも）。

### 「今の状態でいられる締切」 1 フィールド統一

`due` の意味は状態で出し分けます（フィールドを増やさず 1 本にまとめる）:

| 状態 | `due` 超過の意味 |
|---|---|
| `[計画]` / `[実行中]` / `[保留]` | やり忘れ（着手すべき期日を過ぎた・🔴やり忘れ列） |
| `[様子見]` | 卒業判定どき（寝かせ期限到来・▼卒業判定セクション） |
| `[完了]` / `[廃止]` | 判定対象外（既に archive 行き） |

### 看板（カンバン）方式のメタファー

朝一に Web UI（`python -m docsweep server` で起動）を開くと、**3 列レイアウト**で
「ぶら下がっている看板」だけが視覚化されます:

| 列 | 含まれるカード |
|---|---|
| 🔴 やり忘れ | 状態が可動かつ `due < today` |
| 🟡 今日 | `due == today` |
| 🟢 実行中 | `[実行中]` で `due >= today` |

**看板を外す操作は 3 つに収束** します:

1. **着手** — `[実行中]` に変更し期日を今日 + N 日に更新
2. **期日更新** — `+1d` / `+1w` / `+1m` または任意日付
3. **廃止** — `[廃止]` に変更し archive へ（確認ダイアログ後）

トヨタ看板方式のメタファーがそのまま画面に乗ります（看板 = カード、ぶら下がる = 期日切れ、外す = 3 ボタン）。

### 先送りの可視化（`postpone_count`）

期日更新（`update_due`）を呼ぶたびに、各プロジェクトの `.docsweep/state.json` で
**先送り回数**がインクリメントされます。MD 本文は汚さない設計です。

| 回数 | 表示 |
|---|---|
| 1〜2 | グレー |
| 3〜4 | 黄色（警告色） |
| 5+ | 赤（廃止候補色）— カード全体が深い赤に変わり「廃止」ボタンが目立つ |

ラベル遷移（`[計画]→[実行中]`、`[実行中]→[様子見]` 等の「実際に動いた」サイン）で
カウンタは自動リセットされます。しきい値は `.docsweep.yaml` で可変です。

### 物理削除しない（不変条件）

- AI / MCP / `--auto` を含む**すべての経路で物理削除を持たない**。
- `[廃止]` は削除ではなく `archive/` への隔離。復元は archive から戻すだけ。
- docsweep は `rm` 相当の口を実装として持っていない（コードレベルで構造的に不可能）。

### 既存 MD への影響なし

- `due` が未記入の既存ファイルは「期日なし（締切管理対象外）」として従来どおり扱われる。
- `docsweep migrate --add-due` のような**一括設定コマンドは作らない**（嘘の日付の量産防止）。
- 新規 `plan_*.md` / `pending_*.md` のみ自然と `due:` 入りで生まれてくる移行。

---

## 検出方式の選択

`.docsweep.yaml` で 3 方式を併用できます:

| 方式 | 例 | 向き |
|---|---|---|
| H1 ラベル | `# [完了] タイトル` | Claude Code（標準） |
| フロントマター | `status: draft` + `docsweep_state: done` | Codex / 汎用 |
| ファイル名プレフィックス | `done_plan_xxx.md` | スクリプト連携 |

3 方式は同時併用でき、作業状態の優先順位は **docsweep_state（旧形式は status） > H1 > ファイル名**
（明示が強い）。OKF v0.2 の lifecycle `status: draft/stable/deprecated` は作業状態と別軸です。
食い違うファイルは自動で直さず「要修正」フラグで可視化します。

---

## 書式の要点（種別別）

詳細な必須セクションは `templates/CLAUDE.md` を参照。ここでは差分のみ:

- **plan**: 先頭に `## context配分` 表（章番号 `C1/C2/C3`、種別は `planned`/`done` の 2 値・旧表記の `plan`/`fix` は同義）。リリース plan は `リリース引数` / `実行計画` / `申し送り` の専用構成を使う。
- **bugfix**: `## 症状 / 根本原因 / 修正内容 / 変更ファイル / 検証 / 備忘` の 6 セクション。
  `context配分` 表は持たない（事後記録のため）。
- **pending**: `## 概要 / 保留理由 / 着手条件 / 関連情報` の 4 セクション。

いずれも `> ステータス:` 行は持たず、状態は H1 ラベルに集約します。

## 実装完了と親子 plan の closeout

「実装が終わった」という報告、状態変更、archive は別の判断です。親 plan を終端へ進める前に、
read-only の `closeout-check` で子 plan、H1 / `docsweep_state` の一致、完了条件、検証証跡、
受入条件、Git の変更予定ファイル重複を確認します。

```bash
python -m docsweep closeout-check \
  --path docs/local/plan_parent.md --to watching --json
```

- `ready` は「機械的 blocker がない」という意味で、自動的に `[様子見]` や `[完了]` にする意味ではありません。
- `manual_review_required` はブラウザ・実機・Obsidian・Drive・本番確認など、人が証跡を確認する gate が残る状態です。
- `not_ready` は conflict、未完了 checkbox、失敗/未実施の検証、必須 section 欠損、親子関係の不解決などが残る状態です。
- `watching` は寝かせ確認を残す段階、`done` はその確認後の終端です。どちらも closeout 検査だけでは状態変更しません。
- 状態変更は承認後に child → parent の順で `apply --action relabel` を行い、archive は別途 `sweep --dry-run` → 承認 → `sweep` とします。

新規 split は `docsweep_parent` を親子関係の正本にします。旧 plan は、child filename が
`<parent-stem>_c<N>_<short>.md` に一致し、child の `related` が親を指す場合だけ inferred child です。
親の `related` だけでは child と確定しません。

---

## 配置先

1. `docs/local/` があればそこ（個人作業ログ向け・gitignore 推奨）
2. 無ければ `docs/` 直下

---

## 採用方法

1. `templates/CLAUDE.md` の内容を自分のプロジェクトの `CLAUDE.md` に取り込む。
2. Codex も使うなら `templates/AGENTS.md` を置く（CLAUDE.md を参照するだけの薄いファイル）。
3. `templates/.docsweep.yaml` をプロジェクトに置き、スキャンルートと陳腐化日数を設定。
4. `python -m docsweep --dry-run` で挙動を確認してから `--auto` で運用に乗せる。
