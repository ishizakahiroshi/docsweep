# AI エージェント向け docsweep 操作ガイド

このプロジェクトには **docsweep** が導入されています。あなた（AI エージェント）は対話で
docsweep を操作し、作業ドキュメント（plan/bugfix/pending）の整理を代行できます。

## 作成・保存の入口

新しい作業文書は、物理パスを手入力せず `python -m docsweep new <type> <topic>` を使います。
保存先はプロジェクト相対の設定 `work_dir`（既定 `docs/local/`）で、会話履歴の
`capture` / MCP `capture_save` も同じ queue を使います。private queue の Git ignore / tracked
状態と本文の高確度 secret は保存前に検査されます。必要な場合だけ `--allow-sensitive` を明示し、
秘密値をログや JSON に貼り付けないでください。

## 基本の流れ（triage → 判断 → apply）

1. **材料を集める**: `python -m docsweep triage --json`（または MCP の `triage` ツール）を呼ぶ。
   各ファイルの `path` / `state` / `age_days` / `summary` / `flags` / `allowed_actions` が返る。
2. **判断する**: `flags` に `needs_decision`（陳腐化）や `needs_fix`（ラベル欠落）が付いたものを
   必要なら実ファイルを読んで精査し、どうするか決める。
3. **実行する**: `python -m docsweep apply --path <p> --action <a>`（MCP なら `apply` ツール）。
   `action` は **閉じた集合** から選ぶ:

   | action | 意味 |
   |---|---|
   | `keep` | 現状維持（何もしない） |
   | `discard` | `[廃止]` にして `archive/` へ隔離（削除ではない・復元可能） |
   | `resume` | 様子見/廃止候補を `[実行中]` へ戻す（旧 `[対応中]` は読み取り互換） |
   | `relabel` | 任意ラベルへ書き換え（`--to <label>` を伴う） |
   | `promote` | `[様子見]` を `[完了]` へ昇格し `archive/` へ（リリース整理。`--due-expired` で期限到来だけに絞れる） |

   `[様子見]` へ移す今回だけ卒業期限を変える場合は、CLI の
   `--watching-days N` または MCP `watching_days=N` を `relabel` に追加します。
   設定ファイルは変更されず、既に `[様子見]` の文書の due は上書きしません。

   ※ `allowed_actions` に無い action はエラーになる（機械的に安全）。

## 守るべき原則

- **`[様子見]` は勝手に動かさない**。再発確認の待機列。`sweep` も触らない。
  期限到来分を整理するときだけ、`promote --due-expired --dry-run` で確認して明示実行する。
- **完了/廃止の判断はあなた（または人間）がラベルを立てる**。docsweep は「運ぶ作業」だけ自動化する。
- 破壊的操作は無い（archive は隔離・復元可能）。それでも `--dry-run` で確認してから本実行するとよい。

## 親子 plan の closeout（read-only check）

実装完了を報告された親 plan は、relabel や archive の前に次を実行します。

```bash
python -m docsweep closeout-check --path <parent-plan> --json
```

結果別の次手:

- `not_ready`: 機械 blocker（状態不整合、未完了 child、検証不足など）を解消するまで状態を変えない。
- `manual_review_required`: `manual_checks` と dirty worktree の重複を確認し、ユーザーの明示承認を待つ。
- `ready`: ready は自動完了の許可ではない。明示承認後、child plan から parent plan の順に relabel する。

`plan-closeout` skill が導入済みならこの check の補助に使えますが、必須依存ではありません。H1 / `docsweep_state` の変更と archive は別操作です。archive は `python -m docsweep sweep --dry-run` → 確認 → 別承認 → 実行の順に進め、「実装完了」「静的検証済み」「手動確認済み」「watching」「done」「archive済み」を同じ状態として扱いません。

## よく使うコマンド / ツール

- `python -m docsweep sweep` — done/discarded を archive へ一括移送（様子見は守る）。
- `python -m docsweep promote --state watching --to done` — リリース前に様子見をまとめて昇格。
- `python -m docsweep promote --due-expired --dry-run` — 期限到来（当日を含む）の様子見だけを下見。
- `python -m docsweep index` / `python -m docsweep pending` — 横断 INDEX 再生成 / 保留だけ表示。
- `python -m docsweep summary` — 要点だけに絞った JSON。コンテキストに載せやすい。
- `python -m docsweep project list` — 監視対象プロジェクトの一覧と ON / OFF。`project disable <root>` / `project enable <root>` で board と scan から外す・戻す（`~/.docsweep/excluded.json` に記録されるだけで、ファイルは動かない）。

## ラベルと状態（このプロジェクトの正本）

H1 先頭ラベルの語彙・archive 可否は `.docsweep.yaml` の `states:` が正本。CLAUDE.md の
docsweep 管理ブロック（`docsweep:managed` マーカー間）はそこから生成されている。手で書き換えず、
変更が要るときは `python -m docsweep inject` で再同期すること。
