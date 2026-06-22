# docsweep 開発ガイド

> このファイルは **docsweep そのものを開発する** ための AI 向けプロジェクト固有ガイドです。
> リポジトリを clone した人・AI が常時参照する想定の最小ロード分だけを置きます。

> ⚠️ **`templates/CLAUDE.md` と混同しないこと。** あちらは **配布物**（採用者が自分のプロジェクトに
> 取り込む AI 作業ドキュメント運用ルールの正本テンプレート）です。本ファイルは
> **この OSS の中の人向け** 開発ガイドであり、役割がまったく異なります。

## プロジェクト概要

**docsweep** — AI コーディングツール（Claude Code / Codex 等）が生成する
`plan_*.md` / `bugfix_*.md` / `pending_*.md` の **蓄積・陳腐化問題を解決する
クロスプラットフォーム CLI + Web UI ツール**。H1 ステータスラベル（`[完了]` / `[計画]` /
`[廃止]` 等）を機械的に読み取り、完了を各プロジェクトの `archive/` へ自動移送し、
陳腐化を「要判断」フラグで可視化し、複数プロジェクトを横断 INDEX で一望できるようにする。

- GitHub リポジトリ名: `docsweep`
- 配布: `pip install docsweep` + 単体バイナリ（PyInstaller）、Win / Mac / Linux 対応
- 設計の正本: [docs/local/plan_v0.1.0-product-requirements.md](docs/local/plan_v0.1.0-product-requirements.md)
  および [docs/local/plan_state-tag-orthogonalization.md](docs/local/plan_state-tag-orthogonalization.md)

> 個人/グローバルな AI ルール（言語・確認フォーマット・スクリーンショット規約・
> ターン終端の出力ルール等）は **このリポジトリには置かない**。各利用者が使う AI ツールの
> グローバル設定に置くこと。公開リポジトリの `CLAUDE.md` / `AGENTS.md` は
> **プロジェクト固有ルールだけ**を扱い、private ファイルが無い fresh clone でも有効であること。

## このリポジトリ自身のドキュメント運用

docsweep は **自分自身のルールでドッグフーディング** する。
`docs/local/` に作業記録（plan / bugfix / pending）を残す際は、**配布物である
[templates/CLAUDE.md](templates/CLAUDE.md) の「AI 作業ドキュメント運用ルール」に従う**
（命名・H1 ステータスラベル・必須セクション・ライフサイクルの正本はあちら）。
規約の人間向け解説は [docs/conventions.md](docs/conventions.md)。

- `docs/local/` は個人作業ログ（gitignore 済み・非公開）
- 規約を変更するときは `templates/CLAUDE.md`（正本）→ `docs/conventions.md`（解説）の
  順で更新し、両者をズラさない

## 技術スタック（予定）

| レイヤ | 採用 |
|------|------|
| 言語 | Python（クロスプラットフォーム） |
| パッケージ | `docsweep/`（`pip install docsweep`） |
| CLI | サブコマンド + フラグ（`--auto` / `--dry-run` / `--review` / `--json` / `--report` / `--summary` / `new`） |
| インタラクティブ UI | `InquirerPy` / `questionary`（`--review` のチェックリスト専用） |
| 設定 | YAML（`~/.docsweep/config.yaml` / プロジェクトの `.docsweep.yaml`） |
| 単体配布 | PyInstaller |

## リポジトリ構成（予定）

```
docsweep/
├─ docsweep/        # Python ツール本体（CLI・コアエンジン・Web UI）
├─ templates/            # 配布物（採用者が取り込む）
│  ├─ CLAUDE.md          #   Claude Code 向けルールの正本テンプレ
│  ├─ AGENTS.md          #   Codex 向けの薄いポインタ
│  └─ .docsweep.yaml#   設定サンプル
├─ docs/
│  ├─ conventions.md     # 命名・ステータス規約の人間向け解説
│  ├─ mockups/           # Web UI モックアップ
│  └─ local/             # 設計書・作業ログ（非公開・gitignore）
└─ CLAUDE.md / AGENTS.md # ← 本ファイル（リポジトリ開発者向け）
```

## クロスプラットフォーム原則

- **パス操作は `pathlib.Path` / `os.path` を使い、区切り文字をハードコードしない**。
- **ホームディレクトリ起点の設定・状態は全 OS 共通の `~/.docsweep/`**
  （Windows でも `%USERPROFILE%\.docsweep\` で同じ意味になるようにする）。
- **ファイル移送（archive 移動）は同一ボリューム前提に依存しない**（`shutil.move` 等で吸収）。
- **インタラクティブ UI は `--review` 専用**。`--auto` / `--json` は非対話を厳守
  （cron・CI・AI エージェント委譲向け。プロンプトを出さない）。

## 作業運用ルール（AI 共通）

- **ビルド・パッケージング・公開・コミット・プッシュは全てユーザーが行う**。
  AI からは自動実行も提案もしない（確認質問も出さない）。
  - 例外: ユーザーが明示的に「ビルドして」「`pip install -e .` 走らせて」「コミットして」等と
    指示した場合のみ。
  - 対象: `pyinstaller` / `pip install` / `pnpm publish` / `git commit` / `git push` / `git tag` 等。
  - 型チェック・テスト実行（`pytest` / `ruff` / `mypy` 等、コードの正しさ確認）は本ルールの対象外。
- 完了報告では「公開しますか？」のような提案を出さず、コード変更の要約だけ伝える。

## 公開・配布の方針

- 秘匿情報が無ければ全公開（ツール本体 + テンプレ + 規約ドキュメントをセットで）。
- 配布の既定は **npm ではなく PyPI**（Python パッケージ）。詳細・npm 連携が絡む場合の
  運用は予約済みハンドル/トークンの状況に依存するため、公開作業前にユーザーへ確認する。
