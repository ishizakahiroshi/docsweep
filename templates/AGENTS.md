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
front matter のほうが扱いやすい環境では、H1 ラベルに加えて front matter を併記してよい:

```markdown
---
status: planning   # planning | in-progress | done | pending
type: plan         # plan | bugfix | pending
updated: 2026-06-12
---

# [計画] タイトル
```

ai-docs-sweep は H1 ラベル方式・フロントマター方式・ファイル名プレフィックス方式の
いずれでも検出できます（`.ai-docs-sweep.yaml` の `status_detection` で切り替え）。

### 参照の起点

- ルール本体: `./CLAUDE.md`
- 命名・ラベル仕様の解説: `./docs/conventions.md`
- 設定サンプル: `./templates/.ai-docs-sweep.yaml`
