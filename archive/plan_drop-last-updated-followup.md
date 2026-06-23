# [完了] 配布規約・自リポガイド・CHANGELOG への最終更新行廃止の波及

## context配分

| C | 内容 | 種別 |
|---|---|---|
| C1 | 配布物 `templates/CLAUDE.md` と運用解説 `docs/conventions.md`、自リポ `CLAUDE.md` から `> 最終更新:` 行に関する記述・実体を撤去 | plan |
| C2 | CHANGELOG.md に Unreleased セクションを追加し、今回の一連の最終更新行撤去（templates_gen.py + 配布規約）を記録 | plan |

---

## 概要

先行 plan `archive/plan_templates-drop-last-updated-line.md` で `docsweep new` 生成物から `> 最終更新:` 行を撤去した。だが**書く側の正本である配布規約 (`templates/CLAUDE.md`)・運用解説 (`docs/conventions.md`)・このリポ自身の `CLAUDE.md`** にはまだ「H1 直下に `> 最終更新:` を 1 行置く」と書いてあり、生成物との整合が取れていない。配布物の変更まで含めて 1 まとめに収束させる。

## 背景

- 「書く側」だけが残っており、docsweep 自体は **`st_mtime` ベース** で判定している（`docsweep/scan.py:167`・ダッシュボードの `mtime_str` 経由）。`> 最終更新:` 行は読まれない（`docsweep/detect.py:207` で `>` 始まりの引用行は summary 抽出時にスキップ）
- ユーザー側グローバル運用ルール（`~/.claude/guides/plan_rules.md` ほか）は 2026-06-22 に同行を廃止済み
- 配布物の規約だけ古い指示が残ると、docsweep を採用したプロジェクトの AI が「規約に従って最終更新行を書く → docsweep は無視する」という二度手間が永続化する

## 影響範囲

- **読む側に変更なし**：`detect.py` の `>` 引用行スキップロジックと、過去ファイル互換テスト (`tests/test_detect.py:74`) は残置（過去に書かれたファイルとの後方互換のため）
- **UI のツールチップ表示「最終更新 {{ r.mtime_str }}」は残置**（filesystem mtime 由来であり正本）
- **書く側のルールが配布物から消える**ので、docsweep を新しく採用するプロジェクトでは AI が最終更新行を書かなくなる

---

## C1: 規約ドキュメントから記述・実体を撤去

### 作業内容

1. `templates/CLAUDE.md`（配布規約）— 該当箇所:
   - L88（plan 命名規約）「H1 直下に `> 最終更新:` を 1 行」記述削除
   - L108（bugfix）同上
   - L126（pending）同上
   - L150-152 の「最終更新日時の取得」節そのものを削除
2. `docs/conventions.md`（運用解説）—
   - L73 「最終更新から N 日以上経過した」→ docsweep が見ているのは `st_mtime` であることを明示する文言へ
   - L106 「plan / bugfix / pending は H1 直下に `> 最終更新: ...` を置きます」記述削除
   - 自身の L3 の `> 最終更新:` メタ行も削除（ドッグフーディング整合）
3. リポルート `CLAUDE.md:3` の `> 最終更新:` 行を削除

### 完了条件

- 配布物・解説・自リポガイドのいずれにも `> 最終更新:` の指示が残っていない
- 既存テスト（`tests/test_detect.py:74` の後方互換 fixture を含む）が緑

## C2: CHANGELOG に Unreleased セクション追加

### 作業内容

`CHANGELOG.md` の `## [0.1.0] - 2026-06-21` の上に `## [Unreleased]` セクションを追加し、`### Changed` で:

- `docsweep new` 生成テンプレから `> 最終更新:` 行を撤去（先行 plan 完了分）
- 配布規約 (`templates/CLAUDE.md`) と運用解説 (`docs/conventions.md`) からも `> 最終更新:` 行の指示を撤去
- リポルート `CLAUDE.md` の最終更新メタ行を撤去（ドッグフーディング整合）

を記す。

### 完了条件

- 次リリース時にそのまま見出しを置換できる体裁
