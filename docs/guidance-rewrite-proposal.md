# `~/.docsweep/guidance.md` 書き換え案文（適用済み）

> **適用済み（2026-06-30）**: 本 proposal の内容は `docsweep/inject.py` の
> `generate_guidance_block()` を書き換え、`GUIDANCE_VERSION` を `"1"` → `"2"` に bump し、
> `docsweep inject --global` を実行することで `~/.docsweep/guidance.md` へ反映済。
> 以下は履歴として残す。

## 重要な訂正（反映方法）

`~/.docsweep/guidance.md` は **docsweep 自身が auto-管理する** ファイルで、先頭に
「直接編集しないでください」と書かれている。エディタで開いて貼り付けても次回
`docsweep inject --global` 実行時に上書きされる。

正しい反映手順:

1. `docsweep/inject.py` の `generate_guidance_block(lang)` を書き換える（source of truth）
2. 同ファイル冒頭の `GUIDANCE_VERSION` を bump する（マニフェスト記録のため）
3. ユーザーが `python -m docsweep inject --global` を実行して再生成

## 変更点

- セッション開始時に走らせるコマンドを `docsweep triage` → `docsweep brief` に変更
- 「続きやって」の翻訳先を「triage 先頭 item」 → 「brief の `today_pick.path`」に変更
- 「### 経路選択（全 AI 対応）」節を追加（MCP 3 tool / CLI 直叩き / `/D` の使い分け）

## 反映後の `~/.docsweep/guidance.md`（適用結果）

````markdown
## docsweep — セッション開始時の残作業確認（必須）

作業を始める前に、まず `python -m docsweep brief`（MCP 接続時は `brief` ツール）を実行して
今日の 1 個を確認すること。`brief` は cwd プロジェクトの最高スコア 1 件を断定して返す。
PATH に `docsweep` コマンドが無くても、この `python -m docsweep` 形式を優先すること。

ユーザーが「続きやって」「今日の続き」と言ったら、`brief` の `today_pick.path` を対象にする。
複数プロジェクトを横断したい時は `python -m docsweep cross` か MCP `cross()` を使う。

### 経路選択（全 AI 対応）

- 朝の入口（自然言語起動の価値が高い）: MCP `brief` / `cross` / `capture_extract` / `capture_save`
- それ以外（整合チェック・archive 蘇生・グラフ等）: Bash で `docsweep <command> --json`
- Claude Code では `/D` slash command でも 3 経路をまとめてディスパッチできる
````

詳細: `docs/ai-agent-integration.md`
