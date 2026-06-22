# [完了] templates_gen.py から `> 最終更新:` 行を削除

## context配分

| C | 内容 | 種別 |
|---|---|---|
| C1 | `_plan_body` / `_bugfix_body` / `_pending_body` から `> 最終更新:` 行を削除し、未使用になる `_now_meta()` ヘルパーと `_WEEKDAYS` 定数も整理 | plan |

---

## 概要

`docsweep new <type> <topic>` で生成される plan/bugfix/pending テンプレから `> 最終更新: YYYY-MM-DD(曜) HH:MM:SS` 行を撤去する。

## 背景

ユーザー側（ishizakahiroshi）のグローバル md 運用ルールから `> 最終更新:` 行を 2026-06-22 に廃止した（`~/.claude/guides/plan_rules.md` ほか）。理由:

- filesystem の `st_mtime` と `git log -1 <file>` で完全に派生取得できる重複情報
- AI が記入のたびに get-datetime コマンドを叩く必要があり、`& 'foo.ps1'` を Bash ツールに渡す事故が 10 回以上再発
- docsweep 自身もダッシュボード表示・triage は `stat.st_mtime` ベース（`docsweep/scan.py:167`）で `> 最終更新:` 行は読まない（`docsweep/detect.py:207-209` で `>` 始まりの引用行は summary 抽出時にスキップ）

つまり「書く側だけ」が残っており、消せばユーザールールと docsweep の挙動が完全整合する。

## 影響範囲

- `docsweep new <type> <topic>` 生成物から `> 最終更新:` 行が消える
- 既存ファイル（過去に `docsweep new` で生成済み）には影響なし
- ダッシュボード表示・triage 順序・archive 判定はすべて `st_mtime` ベースなので無影響

---

## C1: テンプレ本体から最終更新行を削除

### 作業内容

`docsweep/templates_gen.py` を以下のとおり編集:

1. `_plan_body`（line 42-49）: `f"> 最終更新: {_now_meta()}\n\n"` を削除
2. `_bugfix_body`（line 52-58）: 同上
3. `_pending_body`（line 61-66）: 同上
4. `_now_meta()` 関数（line 17-19）: 参照者が無くなるので削除
5. `_WEEKDAYS` 定数（line 14）: 同上
6. `from datetime import datetime`（line 11）: `_today()` で引き続き使うので **残置**

### 変更予定ファイル

- `docsweep/templates_gen.py` — `> 最終更新:` 行 3 箇所と関連ヘルパー / 定数の削除
- `tests/` 配下に `templates_gen` のスナップショット系テストがあれば期待値を更新（実装時に grep で確認）

### 完了条件

- `docsweep new plan foo` 生成物に `> 最終更新:` 行が含まれない
- `docsweep new bugfix bar` / `docsweep new pending baz` も同様
- 既存テストが緑のまま通る
- ローカル運用ルール（`~/.claude/guides/plan_rules.md` 等）の指示と挙動が完全一致
