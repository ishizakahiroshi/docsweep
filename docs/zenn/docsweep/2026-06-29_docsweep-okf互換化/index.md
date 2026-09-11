---
title: "docsweep を OKF（Open Knowledge Format）互換にした話"
emoji: "🧹"
type: "tech"
topics: ["docsweep", "okf", "ai", "claude", "codex"]
published: false
---

> **[取りやめ] 2026-09-11・この下書きは公開しない。**
>
> 内容が現行の docsweep と食い違っているため。中心主題である frontmatter の形が
> 変わっており、本文の例は `status: planned` のように **docsweep の状態値を OKF の
> `status` 欄に入れる旧形式**のまま。現行は OKF ライフサイクルの `status: draft` と
> docsweep 作業状態の `docsweep_state: planned` に分かれている。
> 直すには例と説明をほぼ全面的に書き直すことになり、それは別記事を書くのと同じ。
>
> `docsweep export --okf` と pre-commit hook は現在も存在する（2026-09-11 確認）。
> 機能が消えたのではなく、**記事の説明が追いつかなくなった**という状態。
>
> 消さずに残すのは、当時どう考えていたかの記録として意味があるため。
> 再開するなら新しい下書きから起こす。この md は更新しない。

## TL;DR

- AI コーディングツール（Claude Code / Codex 等）が生成する `plan_*.md` / `bugfix_*.md` /
  `pending_*.md` の蓄積・陳腐化を片付ける拙作 OSS [docsweep](https://github.com/ishizakahiroshi/docsweep)
  に、Google Cloud が 2026-06 に提案した [**OKF（Open Knowledge Format）**](https://zenn.dev/knowledgesense/articles/14a874a9f423bb)
  の考え方を取り込みました。
- md 冒頭の YAML frontmatter で `type` / `status` / `tags` / `owner` / `review_status` /
  `related` / `last_reviewed` を機械可読化。**H1 ステータスラベル運用は廃止せず併用**で
  後方互換 100%。
- 「docsweep を抜けても md が腐らない」を実演する `docsweep export --okf`（OKF 互換 zip
  エクスポート）と、frontmatter 不整合をコミット時に止める pre-commit hook も同梱しました。

## なぜ OKF を取り込んだか

docsweep は元々、H1 タイトル先頭の `[完了]` / `[計画]` / `[廃止]` というステータスラベルを
正規表現で読み取って archive 自動移送する設計でした。これは「md を開いた瞬間に状態が見える」
という人間向けの強みがある一方、**docsweep を入れていない別ツール（別の AI、別のチーム）から
見ると独自フォーマット**でした。

OKF は同じ問題意識を持つベンダー非依存フォーマットで、`type` / `status` / `related` を
frontmatter で機械可読化する考え方が中心です。これを取り込めば:

1. docsweep を抜けても md が OKF として読み続けられる（ロックインしない）
2. `tags` / `owner` / `review_status` / `last_reviewed` が増えるので triage の絞り込みや
   **陳腐化の前倒し検知**（今は `[完了]` 後の archive しかできない＝事後）ができる
3. AI ツール側が OKF を理解している前提なら、docsweep 専用プロンプトを噛まさなくても
   状況が伝わる

ただし OKF の「ルール最小」思想に **完全準拠はしませんでした**。理由は 2 つ:

- docsweep は archive 自動化のために `type` 集合（plan / bugfix / pending）と
  `status` 語彙を固定したい。OKF より少しだけ強い規約を持つことを選んだ
- H1 ステータスラベル運用を廃止しない。md を開いた瞬間に状態が見える価値は捨てない。
  frontmatter は併用（frontmatter があればそちらを優先、無ければ H1 へフォールバック）

## 採用後の使い心地

### 新規 md は frontmatter 付きで生まれてくる

```bash
$ python -m docsweep new plan auth-refactor
```

```markdown
---
type: plan
status: planned
tags: []
owner:
review_status: draft
related: []
last_reviewed: 2026-06-29
due: 2026-07-06
---

# [計画] auth-refactor
```

### 既存 md は触らなくても動く（後方互換 100%）

frontmatter なしで運用してきた既存 plan は何もしなくても動き続けます。
状態モデルの正本は
[`docsweep/states.py`](https://github.com/ishizakahiroshi/docsweep/blob/main/docsweep/states.py)
で、H1 ステータスラベルの読み取りはそのまま残してあります。
一括で OKF 互換に揃えたい場合だけ:

```bash
python -m docsweep migrate-frontmatter --dry-run   # 差分を確認
python -m docsweep migrate-frontmatter --apply     # H1 ラベル温存で挿入
python -m docsweep fix-related --apply             # 片側 related を双方向化
```

### 陳腐化の前倒し検知

`review_status` ＋ `last_reviewed` で「[完了] になる前」の陳腐化を拾えます:

```bash
$ python -m docsweep stale
stale: 3 件
  [draft] +20d (>14)  many-ai-cli/docs/local/plan_x.md
  [review] +9d (>7)  docsweep/docs/local/plan_y.md  last_reviewed=2026-06-15
  [published] +95d (>90)  ...
```

しきい値は `.docsweep.yaml` で上書き可能です（`draft` 14 日 / `review` 7 日 /
`published` 90 日が既定）。

### tag / owner 軸での絞り込み

```bash
python -m docsweep triage --tag auth --show owner
python -m docsweep find --owner me --status 実行中
```

### related の逆参照と AI 連携

```bash
python -m docsweep show plan_auth-refactor.md
# 対象: ...
#   related (forward): 2 件
#     -> [計画] plan plan_oauth-migration.md
#     -> [対応中] bugfix bugfix_token-expiry_2026-06-15.md
#   逆参照 (backref): 1 件
#     <- [完了] plan plan_session-store.md
```

```bash
python -m docsweep context plan_auth-refactor.md --clipboard
# → 本文 + 親 plan の概要 + related の bugfix/pending の要約をクリップボードへ
# → AI チャットにペーストすればコンテキスト 1 hop で渡る
```

## `docsweep export --okf`

「docsweep を抜けても md が腐らない」を実演するためのコマンドです:

```bash
python -m docsweep export --okf
# OKF export: 42 files -> ~/docsweep-okf-2026-06-29.zip
```

zip の中身:

```
docsweep-okf-2026-06-29.zip
├─ okf-manifest.json               # OKF 互換語彙との対応表
├─ many-ai-cli/docs/local/plan_xxx.md
├─ docsweep/docs/local/bugfix_yyy_2026-06-29.md
└─ ...
```

`okf-manifest.json` には docsweep 内部 state key と OKF status 値の対応表
（`planned → draft` / `in-progress → active` / `watching → active` / `done → done` /
`discarded → discarded` / `pending → deferred`）が入るので、別ツールがこれを読めば
意味が通る形で再構成できます。

詳細は [docs/okf-mapping.md](https://github.com/ishizakahiroshi/docsweep/blob/main/docs/okf-mapping.md) と
[docs/okf-export-format.md](https://github.com/ishizakahiroshi/docsweep/blob/main/docs/okf-export-format.md)
を参照。

## frontmatter 不整合は pre-commit で止める

採用者が opt-in で配置できる pre-commit hook も同梱しました:

```bash
bash templates/install-hooks.sh   # POSIX
pwsh templates/install-hooks.ps1  # Windows
```

hook は以下を検知してコミットを止めます:

- `type:` が plan / bugfix / pending 以外
- `status:` が許容値域外
- `review_status:` が draft / review / published 以外
- `related:` で参照される .md が存在しない

docsweep 本体がインストールされていない環境でも動くスタンドアロン Python 実装にしてあるので、
「採用してるけど docsweep は別マシン」みたいなチーム構成でも機能します。

## まとめ

- docsweep は **OKF 互換のサブセット**（OKF として読み込み可能だが docsweep として
  読むには追加制約あり）になりました
- 既存ユーザーは何もしなくても動き続けます（後方互換 100%）
- `pip install docsweep` または `pip install 'docsweep[all]'` でどうぞ
- リポ: <https://github.com/ishizakahiroshi/docsweep>

OKF を提案した Google Cloud と、考え方を整理した Knowledge Sense の
[紹介記事](https://zenn.dev/knowledgesense/articles/14a874a9f423bb) に感謝。
