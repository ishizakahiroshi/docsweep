# AGENTS.md（Codex / 汎用エージェント向け）

このリポジトリの AI 作業ドキュメント運用ルールは **`CLAUDE.md` を唯一の正本**とします。

> **まず `./CLAUDE.md` の「AI 作業ドキュメント運用ルール」セクションを読み、そこに書かれた
> plan_*.md / bugfix_*.md / pending_*.md の命名・H1 ステータスラベル・必須セクション・
> ライフサイクルにそのまま従ってください。**

ここではルールを複製しません（複製すると CLAUDE.md とズレて事故るため）。
Codex / 汎用エージェント固有の差分だけを以下に補足します。

---

## Codex 固有の補足

### ステータス検出方式

CLAUDE.md は H1 ラベル方式（`# [完了] タイトル`）を標準としますが、
[OKF v0.2（Open Knowledge Format）](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
互換の frontmatter を併用するのが推奨です（Codex / 汎用エージェントから扱いやすい）。
OKF v0.2 の `status` は lifecycle、docsweep の作業状態は `docsweep_state` に分けます:

```markdown
---
type: plan                   # OKF は任意の非空文字列。標準の作業管理対象は plan / bugfix / pending
status: draft                # draft | stable | deprecated（OKF lifecycle）
docsweep_state: planned      # planned | in-progress | watching | done | discarded | pending
tags: []
owner:                      # claim コマンドで自動セット
review_status: draft        # draft | review | published
related: []                 # 関連 md のファイル名 list（fix-related で双方向化）
last_reviewed: 2026-06-29
due: 2026-06-29             # 任意・期日（YYYY-MM-DD 厳格マッチ）
---

# [計画] タイトル
```

docsweep は H1 ラベル方式・フロントマター方式・ファイル名プレフィックス方式の
いずれでも検出できます（作業状態の優先順位は **docsweep_state / 旧 status > H1 > filename**。
OKF lifecycle の `status` は作業状態に使いません）。食い違いは「要修正」フラグで可視化します。
OKF 語彙との対応表は `docs/okf-mapping.md` を参照してください。

`manual_*.md` / `reference_*.md` / `setup_*.md` は静的な手順・参照資料です。これらは
作業状態や `due:` の管理対象にせず、必要なら `type: manual` / `type: reference` などの
OKF lifecycle だけを付けます。リリース 1 回分の MD は `plan_release-vX.Y.Z_*.md` です。

### 参照の起点

- ルール本体: `./CLAUDE.md`
- 命名・ラベル仕様の解説: `./docs/conventions.md`
- OKF 互換マッピング: `./docs/okf-mapping.md`
- 設定サンプル: `./templates/.docsweep.yaml`

The configured `work_dir` (default `docs/local/`) is the active work queue for `plan_*.md`, `bugfix_*.md`, and
`pending_*.md`. Recaps, skill-review reports, and audit reports belong in the
repository-relative `docs/obsidian/` entry and are not docsweep queue items.
