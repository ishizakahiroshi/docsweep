# docsweep ↔ OKF（Open Knowledge Format）v0.2 マッピング

[OKF v0.2 公式仕様](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
は、Markdown を中心にしたベンダー非依存のナレッジ形式です。docsweep はその必須条件と
予約ファイルを尊重し、docsweep 固有の作業管理情報を producer extension として追加します。

## 基本方針

- 通常の Markdown は YAML frontmatter を持ち、空でない `type` を必須とします。
- OKF の `type` は自由な文字列です。`note` / `decision` / `meeting` など未知の値を
  docsweep が読み取り検査で拒否することはありません。
- `status` は OKF の文書ライフサイクルです。v0.2 の値は `draft` / `stable` /
  `deprecated` です。
- docsweep の作業状態は `docsweep_state` に分離します。H1 ラベルは人間向け表示として残します。
- `index.md` / `log.md` は OKF の予約ファイルとして一般の concept と別に扱います。
- `sources` / `generated` / `verified` / `stale_after` など、docsweep が意味を判定しない
  追加フィールドは壊さず保持します。

## Frontmatter の二軸

新規ファイルは次のようになります。

```markdown
---
type: plan
status: draft
docsweep_state: planned
tags: []
owner:
review_status: draft
related: []
docsweep_parent: docs/local/parent.md
last_reviewed: 2026-08-09
due: 2026-08-16
---

# [計画] タイトル
```

| フィールド | 意味 | 扱い |
|---|---|---|
| `type` | concept の種別 | OKF 必須。任意の非空文字列。docsweep は管理対象だけ archive 操作する |
| `status` | 文書の lifecycle | OKF v0.2。`draft` / `stable` / `deprecated` |
| `docsweep_state` | docsweep の作業状態 | docsweep 拡張。`planned` / `in-progress` / `watching` / `done` / `discarded` / `pending` |
| `tags` | 検索用タグ | docsweep 拡張。自由 list |
| `owner` | 作業担当者 | docsweep 拡張。`claim` が更新 |
| `review_status` | docsweep の陳腐化判定用状態 | docsweep 拡張。`draft` / `review` / `published` |
| `related` | 関連ファイル | docsweep 拡張。本文の Markdown link も OKF の関係表現として保持 |
| `docsweep_parent` | 親 plan への方向付き参照 | docsweep 拡張。repo-relative path を 1 件だけ持ち、`related` の汎用関係とは別に親子を確定 |
| `last_reviewed` | 最終レビュー日 | docsweep 拡張。`stale` が利用 |
| `due` | 作業期限 | docsweep 拡張。看板と期限超過フラグが利用 |

`created_at` / `updated_at` は docsweep の OKF frontmatter には生成しません。最終更新は
filesystem の `mtime` として読み取り、docsweep 経由の状態遷移時刻はプロジェクトの
`.docsweep/state.json` の履歴に記録します。`[様子見]` の卒業期限は `due` に置き、
`promote --due-expired` を明示したときだけ期限到来分の昇格対象を絞り込みます。

`status` が v0.2 lifecycle の値なら docsweep の作業状態として解釈しません。`H1 > filename`
へフォールバックします。旧形式の `status: planned` などは legacy state として読み取り、
新形式の `docsweep_state` がある場合はそれを最優先します。新旧フィールドや H1 が食い違う
場合は自動修正せず warning / conflict として表示します。

## docsweep が管理する type

OKF 全体の type 語彙を制限するものではありません。docsweep の archive / due / triage
自動化が標準の作業記録として理解する type は次の 3 種類です。

| docsweep type | 用途 | archive 操作 |
|---|---|---|
| `plan` | 計画・調査・検討 | 作業状態に応じて対象 |
| `bugfix` | 障害・不具合の事後記録 | 作業状態に応じて対象 |
| `pending` | 保留・将来対応 | 作業状態に応じて対象 |

`manual` / `reference` / `setup` など、それ以外の type は `okf-check` では通過させ、
docsweep の作業状態・期日・archive 管理の対象外とします。過去の `manual_release` は旧形式として
読み取り互換を残しますが、新規のリリース MD は `plan_release-vX.Y.Z_*.md` とします。

## docsweep state と lifecycle の export 対応

export の manifest は、選択した profile の `docsweep_status_map` を使って lifecycle を示します。
v0.2 の同梱 profile では次の対応です。

| docsweep state | H1 ラベル | OKF `status` | 自動 archive |
|---|---|---|---|
| `planned` | `[計画]` | `draft` | ✗ |
| `in-progress` | `[実行中]` | `draft` | ✗ |
| `watching` | `[様子見]` | `draft` | ✗ |
| `done` | `[完了]` | `stable` | ✓ |
| `discarded` | `[廃止]` | `deprecated` | ✓ |
| `pending` | `[保留]` | `draft` | ✗ |

この対応は docsweep の運用上の近似であり、`done` が OKF の `stable` と同一の意味になる
という主張ではありません。Bundle 内の本文には `docsweep_state` も残ります。

## Profile とバージョン

検査・export の契約は Python ソースではなく、同梱 `docsweep/okf_profiles/0.2.json` に置きます。
通常実行はオフラインで同梱 profile を使います。別 profile を試すときだけ明示します。

```bash
python -m docsweep okf-profiles
python -m docsweep okf-check ./bundle --okf-version 0.2 --json
python -m docsweep okf-check ./bundle --okf-profile ./okf-profile.json
python -m docsweep okf-check ./bundle \
  --okf-profile https://raw.githubusercontent.com/org/repo/<commit>/okf.json \
  --okf-profile-sha256 <sha256>
```

URL profile の自動取得や別 version への黙った fallback はありません。CI では commit 固定 URL
または SHA-256 固定を推奨します。新しい profile だけを配布する場合は docsweep の実装 Release
を増やさずに利用できます。

## 関係と移行

OKF の標準的な関係表現は本文中の Markdown link です。`related` は docsweep の検索・双方向化
を補助する extension で、`docsweep fix-related --apply` で対称化できます。

`docsweep_parent` は docsweep 固有の方向付き extension です。新規の `new plan <topic> --split N`
は子 plan に repo-relative の親 path を付けます。closeout 判定ではこれを正本とし、旧 plan は
filename が `<parent-stem>_c<N>_<short>.md` に一致し、かつ子の `related` が親を指す場合だけ
inferred child として扱います。親の `related` だけでは child を確定しません。

旧来の H1 のみの md と frontmatter 付き md は混在可能です。一括移行は必ず dry-run から行います。

```bash
python -m docsweep migrate-frontmatter --dry-run
python -m docsweep migrate-frontmatter --apply
```

dry-run では旧 `status` を `status: draft` と `docsweep_state: <旧状態>` に分ける差分も表示します。
元の H1・本文・既存の未知フィールドは温存されます。
