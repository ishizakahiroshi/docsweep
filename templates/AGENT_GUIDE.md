# AI エージェント向け docSweep 操作ガイド

このプロジェクトには **docSweep** が導入されています。あなた（AI エージェント）は対話で
docSweep を操作し、作業ドキュメント（plan/bugfix/pending）の整理を代行できます。

## 基本の流れ（triage → 判断 → apply）

1. **材料を集める**: `python -m docSweep triage --json`（または MCP の `triage` ツール）を呼ぶ。
   各ファイルの `path` / `state` / `age_days` / `summary` / `flags` / `allowed_actions` が返る。
2. **判断する**: `flags` に `needs_decision`（陳腐化）や `needs_fix`（ラベル欠落）が付いたものを
   必要なら実ファイルを読んで精査し、どうするか決める。
3. **実行する**: `python -m docSweep apply --path <p> --action <a>`（MCP なら `apply` ツール）。
   `action` は **閉じた集合** から選ぶ:

   | action | 意味 |
   |---|---|
   | `keep` | 現状維持（何もしない） |
   | `discard` | `[廃止]` にして `archive/` へ隔離（削除ではない・復元可能） |
   | `resume` | 様子見/廃止候補を `[実行中]`/`[対応中]` へ戻す |
   | `relabel` | 任意ラベルへ書き換え（`--to <label>` を伴う） |
   | `promote` | `[様子見]` を `[完了]` へ昇格し `archive/` へ（リリース整理） |

   ※ `allowed_actions` に無い action はエラーになる（機械的に安全）。

## 守るべき原則

- **`[様子見]` は勝手に動かさない**。再発確認の待機列。`sweep` も触らない。
- **完了/廃止の判断はあなた（または人間）がラベルを立てる**。docSweep は「運ぶ作業」だけ自動化する。
- 破壊的操作は無い（archive は隔離・復元可能）。それでも `--dry-run` で確認してから本実行するとよい。

## よく使うコマンド / ツール

- `python -m docSweep sweep` — done/discarded を archive へ一括移送（様子見は守る）。
- `python -m docSweep promote --state watching --to done` — リリース前に様子見をまとめて昇格。
- `python -m docSweep index` / `python -m docSweep pending` — 横断 INDEX 再生成 / 保留だけ表示。
- `python -m docSweep summary` — 要点だけに絞った JSON。コンテキストに載せやすい。

## ラベルと状態（このプロジェクトの正本）

H1 先頭ラベルの語彙・archive 可否は `.docSweep.yaml` の `states:` が正本。CLAUDE.md の
docSweep 管理ブロック（`docSweep:managed` マーカー間）はそこから生成されている。手で書き換えず、
変更が要るときは `python -m docSweep inject` で再同期すること。
