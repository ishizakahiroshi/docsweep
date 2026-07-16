# docsweep ↔ OKF（Open Knowledge Format）マッピング

[OKF（Open Knowledge Format）](https://zenn.dev/knowledgesense/articles/14a874a9f423bb) は
Google Cloud が 2026-06 に提案したベンダー非依存の Markdown ナレッジ表現形式です。
docsweep は OKF の **frontmatter による type / status / related の機械可読化** の考え方を
取り込みつつ、自動 archive 移送のために **type 集合と status 語彙を OKF より少しだけ強く固定**
しています。本文書はその対応表です。

## 設計の立場

docsweep は OKF の「ルール最小」思想に **完全準拠していません**。理由は 2 つ:

1. **archive 自動化のため**、type 集合（plan / bugfix / pending / manual_release）と
   status 語彙を固定したい。
   OKF はここを自由にしているが、それだと「何を `archive` 移送してよいか」が判定不能になる。
2. **H1 ステータスラベル運用を廃止しない**。md を開いた瞬間に状態が見える価値は人間向けに残す。
   frontmatter は併用（frontmatter があればそちらを優先、なければ H1 ラベルへフォールバック）。

つまり docsweep は **OKF 互換のサブセット**（OKF として読み込み可能だが、docsweep 規約として
読むには追加制約あり）です。

## type 語彙のマッピング

| docsweep type | OKF 推奨対応値 | 説明 |
|---|---|---|
| `plan`    | `plan` | 計画 / 調査メモ / 検討メモ（着手前〜進行中の作業） |
| `bugfix`  | `incident` | 障害対応の事後記録（症状 / 根本原因 / 修正内容） |
| `pending` | `deferred` | 保留 / 将来対応（着手条件待ち） |
| `manual_release` | `release` | 手動リリースの実行記録 |

OKF 側の type 語彙は緩く、`note` / `decision` / `meeting` などの値も許容されますが、
docsweep は **上記 4 つを自動 archive 制御の対象に含めます**。それ以外の type を
frontmatter に書いた md は docsweep スキャンの対象外として扱われます（破壊しないが管理もしない）。

## status 語彙のマッピング

docsweep 内部 state key → OKF 互換 status 値（`docsweep export --okf` の manifest が出す対応）:

| docsweep state | H1 ラベル | OKF 互換 status | 自動 archive |
|---|---|---|---|
| `planned`     | `[計画]`   | `draft`     | ✗ |
| `in-progress` | `[実行中]` | `active`    | ✗ |
| `watching`    | `[様子見]` | `active`    | ✗（寝かせを守る） |
| `done`        | `[完了]`   | `done`      | ✓ |
| `discarded`   | `[廃止]`   | `discarded` | ✓（隔離・復元可） |
| `pending`     | `[保留]`   | `deferred`  | ✗ |

bugfix 専用ラベル `[対応中]` も内部 state は `in-progress` に正規化されるので、
OKF 互換上は `active` 扱いです。

OKF 側は `draft` / `active` / `done` / `archived` などの粗い語彙を想定しており、
docsweep の `[様子見]` のような細粒度は `active` に丸めて表現します（読み手側で
OKF 寄せに処理できる粒度に揃える狙い）。逆方向（OKF → docsweep）の自動変換は
提供しません（細粒度が落ちるため、人間が判断する）。

## review_status の値域

OKF 仕様には `review_status` の明示がありませんが、docsweep は陳腐化の前倒し検知
（`docsweep stale`）のために以下の値域を **OSS として宣言する許容値** に固定します:

| 値 | 意味 | docsweep `stale` の既定しきい値 |
|---|---|---|
| `draft`     | 書きかけ・最初の草稿 | 14 日以上更新なし → 候補 |
| `review`    | レビュー中 | 7 日以上更新なし → 候補 |
| `published` | 確定版 | `last_reviewed` が 90 日以上前 → 候補 |

しきい値は `.docsweep.yaml` の `stale_thresholds:` で上書き可能です。
`review_status` が未記入の md は `draft` 相当で扱われます。

## related の双方向化

OKF の `related:` は単方向リスト記法ですが、docsweep は **`docsweep fix-related` で
双方向に自動対称化** します（片側更新で必ずズレるため）。`fix-related --apply` を
走らせた直後の md は OKF 規約から見ても完全に互換です。

## tags / owner / last_reviewed

これらは OKF と完全に同じ意味で使います。

- `tags`: 自由 list（語彙統制なし）。`.docsweep.yaml` で `known_tags:` を宣言する
  と、未知 tag の使用を `docsweep find --tag` の dry-run モードで警告できます。
- `owner`: ユーザー名スカラ。`docsweep claim` で `git config user.name` または
  `docsweep config user.name` の値が書き込まれます。
- `last_reviewed`: `YYYY-MM-DD`。`docsweep stale` の判定に使われます。

## OKF 採用 / 非採用ファイルの混在

frontmatter なしの旧来 md（H1 ラベルのみ）と OKF 採用 md（frontmatter あり）は **同じ
プロジェクトに混在しても問題ありません**。docsweep のパーサは
**frontmatter > H1 > filename** の優先順で検出し、frontmatter があればそちらを使い、
無ければ H1 ラベルへフォールバックします。

一括変換したい場合は `docsweep migrate-frontmatter --apply` で全 md に frontmatter を
非破壊挿入できます（H1 ラベルは温存）。
