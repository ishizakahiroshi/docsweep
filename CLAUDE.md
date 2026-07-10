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
- 配布: PyPI（`pip install docsweep`、extras: `web` / `review` / `mcp` / `watch` / `resurrect` / `all`）、Win / Mac / Linux 対応。単体バイナリ（PyInstaller）は現時点で未提供
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

## 技術スタック

| レイヤ | 採用 |
|------|------|
| 言語 | Python >= 3.10（クロスプラットフォーム・core 依存は PyYAML のみ） |
| パッケージ | `docsweep/`（`pip install docsweep`） |
| CLI | サブコマンド式（`triage` / `apply` / `sweep` / `serve` / `index` / `summary` / `new` / `inject` / `mcp` 等）+ `--json` / `--dry-run` / `--auto` |
| Web UI | FastAPI + Jinja2/htmx（`docsweep serve`、extras `web`） |
| インタラクティブ UI | `questionary`（`review` サブコマンド専用、extras `review`） |
| 設定 | YAML（`~/.docsweep/config.yaml` / プロジェクトの `.docsweep.yaml`） |

## リポジトリ構成

```
docsweep/
├─ docsweep/             # Python ツール本体（CLI・コアエンジン・MCP・Web UI: server/）
├─ templates/            # 配布物（採用者が取り込む）
│  ├─ CLAUDE.md          #   AI 作業ドキュメント運用ルールの正本テンプレ
│  ├─ AGENTS.md          #   Codex 等他エージェント向けの薄いポインタ
│  ├─ AGENT_GUIDE.md     #   AI エージェント向け docsweep 操作ガイド
│  ├─ .docsweep.yaml     #   設定サンプル
│  └─ .githooks/ + install-hooks.{sh,ps1}  # opt-in pre-commit hook
├─ tests/                # pytest
├─ docs/
│  ├─ conventions.md     # 命名・ステータス規約の人間向け解説
│  ├─ okf-mapping.md     # OKF 語彙と内部 state の対応
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

## AI 作業共通ルール

ビルド・コミット禁止、secrets-scan 責務、plan/bugfix/pending md の作成ルール等の
AI 作業共通ルールは、各利用者のグローバル AI 設定に従う
（作者環境の例: `~/.claude/CLAUDE.md` および `~/.claude/guides/`）。

- このリポジトリでの適用注: `pyinstaller` / `pip install -e .` もビルド・パッケージング扱い
  （ユーザー指示があるまで実行しない）。`pytest` / `ruff` / `mypy` 等の正しさ確認は対象外。

## 公開・配布の方針

- 秘匿情報が無ければ全公開（ツール本体 + テンプレ + 規約ドキュメントをセットで）。
- 配布の既定は **npm ではなく PyPI**（Python パッケージ）。詳細・npm 連携が絡む場合の
  運用は予約済みハンドル/トークンの状況に依存するため、公開作業前にユーザーへ確認する。
