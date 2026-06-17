# docsweep

AI コーディングツール（Claude Code / Codex 等）が生成する `plan_*.md` / `bugfix_*.md` /
`pending_*.md` の **蓄積・陳腐化問題を解決する** クロスプラットフォーム CLI + Web UI ツール。

H1 ステータスラベル（`[完了]` / `[計画]` / `[廃止]` 等）を機械的に読み取り、完了を各プロジェクトの
`archive/` へ自動移送し、陳腐化を「要判断」フラグで可視化し、複数プロジェクトを横断 INDEX で一望できます。

## インストール

```bash
pip install docsweep         # コア + CLI
pip install 'docsweep[all]'  # Web UI / 対話レビュー / MCP も含む
```

## 使い方

```bash
# スキャン（既定は要判断＋保留のみ表示）
docsweep --root ~/dev
docsweep ./thisproject            # config 不要の単発スキャン
docsweep scan --all --json        # 全件を機械可読 JSON で

# 自動移送（cron / CI / AI 委譲向け・非対話）。done/discarded のみ・様子見は守る
docsweep sweep --dry-run
docsweep sweep

# 横断 INDEX を再生成（.docsweep/INDEX.md と INDEX.json）
docsweep index
docsweep pending                  # 全プロジェクトの [保留] だけ一発表示
docsweep report                   # 人間向け週次レポート
docsweep summary                  # AI に渡す圧縮 JSON

# リリース整理（様子見をまとめて完了へ昇格し archive へ）
docsweep promote --state watching --to done

# 対話チェックリスト（人間専用）
docsweep review

# テンプレ即生成
docsweep new plan my-topic
docsweep new bugfix crash-on-start

# 運用ルールを各プロジェクトへ注入／取り消し（CLAUDE.md=正本・AGENTS.md はそこを指すポインタ）
docsweep inject --project ./foo --preset claude-jp
docsweep inject --project ./foo --no-guidance   # 導線を省きラベル節だけ（導線をグローバルに寄せる場合）
docsweep eject  --project ./foo                  # 管理ブロックだけ剥がす（手書きは温存。--purge で .docsweep.yaml も）

# 個人グローバルへ「セッション開始時に triage を読む」導線を一度だけ注入（全プロジェクトで有効）
docsweep inject --global                         # 既定 agent=claude（~/.claude/CLAUDE.md に @import 1 行）
docsweep inject --global --agent codex           # ~/.codex/AGENTS.md にインライン（CODEX_HOME 尊重）
docsweep eject  --global

docsweep list                                    # 注入済み（プロジェクト＋グローバル）一覧

# Web UI（UX 主役・127.0.0.1・トークン付き URL）。注入/解除もダッシュボードから（プレビュー必須）
docsweep serve --root ~/dev

# MCP サーバー（AI エージェント面・stdio）
docsweep mcp
```

## 状態モデル（一直線・単一正本）

```
plan:    [保留] → [計画] → [実行中] → [様子見] → [完了]
bugfix:           [対応中] → [様子見] → [完了]
         （どちらも、どの状態からでも [廃止] へ分岐できる）
```

- **`[様子見]`** = 直したが寝かせ中。**自動移送されません**（再発確認の待機列として守る）。
- **`[完了]` / `[廃止]`** だけが archive 対象。`[廃止]` は削除ではなく `archive/` へ隔離（復元可能）。
- ラベル語彙・archive 可否・自動移送可否は `states:` 設定が **唯一の正本**で、検出・Web 表示・
  注入テンプレを全部そこから導出します。

## 設定の層

優先順位 **① CLI フラグ > ② プロジェクト `.docsweep.yaml` > ③ グローバル `~/.docsweep/config.yaml`**。
グローバルだけ書けば体感 1 層。`.docsweep.yaml` は置いた時だけ部分上書きで効きます。

## AI エージェント連携

`docsweep triage`（または MCP の `triage` ツール）は、**要判断＋保留を古い順に絞った残作業**を
`counts` ＋ `items[]` ＋ `needs_fix[]` で返します。各 item は `rel`（相対パス）・`title`（H1）・
`state`（ラベル）・`type`・`age_days`・`summary`・`actions`（`discard`/`keep`/`resume`/`relabel`/`promote`
の閉じた集合）を持ち、エージェントは「次にどのファイルの何を続けるか」を判断 → `docsweep apply` で
機械実行します。横断 INDEX 全体の俯瞰は `docsweep summary`。docsweep 自身は AI API を叩きません（ベンダー非依存）。

セッション開始時に AI へ自動でこの残作業を渡すには `docsweep inject --global`（Claude は `@import`、
Codex はインラインで「作業前に triage を読む」導線を個人グローバル設定へ一度だけ注入）。

詳細は [docs/conventions.md](docs/conventions.md) と
[templates/AGENT_GUIDE.md](templates/AGENT_GUIDE.md) を参照してください。

## ライセンス

MIT
