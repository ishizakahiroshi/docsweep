# `/D` Skill 更新案文（適用済み）

> **適用済み（2026-06-30）**: 本 proposal の内容は `~/.claude/commands/D.md` への直接編集で反映済。
> 以下は履歴として残す。

## 重要な訂正（反映先パス）

旧 proposal は `C:/dev/workshop/skills/D/SKILL.md` を反映先として記載していたが、
**`/D` は既に slash command に移行済**（`~/.claude/CLAUDE.md` の「P / S / D は slash command
へ移行済」記述参照）。実体は `~/.claude/commands/D.md`。

正しい反映先:

```sh
# 唯一の反映先
$EDITOR ~/.claude/commands/D.md
```

`C:/dev/workshop/skills/D/` ディレクトリは存在しない（混同しないこと）。

## 変更点

- 経路選択ルール（MCP 3 tool は MCP / それ以外は CLI 直叩き）を冒頭に明文化
- マッピング表に `brief` / `cross` / `capture` / `linkcheck` / `auto-triage` / `graph` /
  `resurrect` / `index-sync` / `index-rebuild` / `index-watch` / `show` / `find` / `claim` /
  `pending` を追加
- `/D` 単独の既定動作を `triage` → `brief` に変更（MCP 経由を優先）
- frontmatter `description` も最新コマンドを反映

## 反映後の `/D` Skill 説明（適用結果・抜粋）

```markdown
## 経路選択ルール（重要）

朝の入口 3 つだけは **MCP tool を優先**（自然言語起動の精度が高い）。それ以外は **CLI 直叩き**:

- MCP 経由: `brief` / `cross` / `capture_extract` + `capture_save`
- CLI 直叩き: それ以外すべて（`python -m docsweep <cmd> --json`）

MCP が未接続の環境では brief / cross / capture も CLI で代替する。

## クエリ意図と内部コマンドの対応

| クエリの語 | 経路 / コマンド |
|---|---|
| 「今日の続きやって」「ブリーフして」「今日の 1 個」 | MCP `brief()` または `python -m docsweep brief` |
| 「全プロジェクトの状況」「クロスで見せて」「凍結予備軍」 | MCP `cross()` または `python -m docsweep cross` |
| 「これ plan にして」「キャプチャ」「直前の会話を docsweep に」 | MCP `capture_extract` → `capture_save` |
| 「整合チェック」「この plan もう実装されてる？」「linkcheck」 | `python -m docsweep linkcheck --json` |
| 「状態遷移提案」「廃止確認」「auto-triage」 | `python -m docsweep auto-triage --suggest` |
| 「関係性」「グラフ」「孤立してる plan」 | `python -m docsweep graph --json` |
| 「archive で似たやつ」「resurrect」「過去の類似 plan」 | `python -m docsweep resurrect --json` |
| 「インデックス更新」「索引同期」「index-sync」 | `python -m docsweep index-sync` |
| 「全部作り直して」「索引再構築」「index-rebuild」 | `python -m docsweep index-rebuild` |
| 「監視しといて」「index-watch」 | `python -m docsweep index-watch` |
| 既存 5 種（triage / sweep / promote / summary / scan） | 従来通り |
```

詳細: `docs/ai-agent-integration.md`
