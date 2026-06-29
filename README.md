# docsweep

AI コーディングツール（Claude Code / Codex 等）が生成する `plan_*.md` / `bugfix_*.md` /
`pending_*.md` の **蓄積・陳腐化問題を解決する** クロスプラットフォーム CLI + Web UI ツール。

H1 ステータスラベル（`[完了]` / `[計画]` / `[廃止]` 等）を機械的に読み取り、完了を各プロジェクトの
`archive/` へ自動移送し、陳腐化を「要判断」フラグで可視化し、複数プロジェクトを横断 INDEX で一望できます。

## OKF（Open Knowledge Format）互換

docsweep は [OKF（Open Knowledge Format）](https://zenn.dev/knowledgesense/articles/14a874a9f423bb)
の **frontmatter による type / status / related の機械可読化** を採用しています。
md 冒頭の YAML frontmatter で `type` / `status` / `tags` / `owner` / `review_status` /
`related` / `last_reviewed` を機械可読化し、docsweep を入れていない別ツールから読んでも
意味が通る形式に揃えています。

docsweep 固有の追加規約は 2 点だけです:

- **type 集合を `plan` / `bugfix` / `pending` に固定**（OKF より少し強い規約）。
  archive 自動化のための制約で、自由な type 値は管理対象外として扱います。
- **H1 ステータスラベル運用は廃止せず併用**。md を開いた瞬間に状態が見える人間向け価値を
  残し、frontmatter が無いファイルは H1 ラベルへフォールバックします（後方互換 100%）。

詳細な対応表は [docs/okf-mapping.md](docs/okf-mapping.md) を参照。
「docsweep を抜けても md が腐らない」を実演する `docsweep export --okf` も提供しています
（[docs/okf-export-format.md](docs/okf-export-format.md)）。

## インストール

```bash
pip install docsweep                  # コア + CLI（wings の主要コマンド・SQLite 索引込み）
pip install 'docsweep[all]'           # Web UI / 対話レビュー / MCP / watch / resurrect も含む
pip install 'docsweep[watch]'         # index-watch のみ追加（watchdog）
pip install 'docsweep[resurrect]'     # resurrect の embedding 経路（sentence-transformers）
```

docsweep は **PATH に `docsweep` コマンドを通さない運用を標準**にしています。
CLI / Web UI / MCP は、Python 実行ファイルから module として起動できます。

```bash
python -m docsweep triage
python -m docsweep mcp
python -m docsweep serve --root ~/dev
```

MCP クライアントへ登録する場合も、`docsweep mcp` ではなく `python -m docsweep mcp`
を推奨します。より再現性を上げるなら、`command` には Python 実行ファイルの絶対パスを指定します。

```json
{
  "mcpServers": {
    "docsweep": {
      "command": "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
      "args": ["-m", "docsweep", "mcp"]
    }
  }
}
```

> 上記の `Python312` は **インストールされている Python のバージョンに置き換えてください**。
> 確認方法: Windows は `where python`、macOS / Linux は `which python3`。
> もしくは `python -c "import sys; print(sys.executable)"` でフルパスが取れます。

`docsweep ...` は Python の Scripts/bin ディレクトリが PATH に入っている環境向けの短縮形です。

## インストール後どこに置かれて、どう使えるか（OS 別）

`pip install docsweep` は **Python の site-packages にライブラリを配置**します。
別バイナリは生成されず、`python -m docsweep ...` で起動するのが標準動線です。

### 共通の挙動

- **`docsweep/` パッケージ本体**: 起動中の Python が解決する site-packages に展開される
- **設定・状態**: `~/.docsweep/`（全 OS 共通の論理パス）
- **MCP 設定**: AI クライアント（Claude Code 等）の設定ファイルに `python -m docsweep mcp` を 1 行登録するだけ
- **PATH 設定不要**: `docsweep` を PATH に通さなくても、`python -m docsweep ...` ですべての機能にアクセス可能

### Windows

| 項目 | 場所 |
|---|---|
| Python 実体（per-user 標準インストール） | `C:\Users\<you>\AppData\Local\Programs\Python\Python3XX\python.exe` |
| docsweep 本体（pip install 後） | `C:\Users\<you>\AppData\Local\Programs\Python\Python3XX\Lib\site-packages\docsweep\` |
| docsweep 設定・状態 | `C:\Users\<you>\.docsweep\`（= `%USERPROFILE%\.docsweep\` = `~/.docsweep`） |
| `docsweep` ショートカット | `...\Python3XX\Scripts\docsweep.exe`（PATH 通っていれば `docsweep` 直で起動可） |

> **Windows ストア版 Python は避けることを推奨**。`%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe`
> 経由のストア版は仮想ストア配置の制約で **`Scripts/` ディレクトリへの書き込みが弾かれる**ことがあり、
> `pip install` 自体は通っても `docsweep.exe` ランチャーが正しく作られない／PATH に乗らないトラブルが
> 起きます。python.org 配布のインストーラ（per-user）か pyenv-win を使うのが安全です。

```powershell
# インストール
pip install 'docsweep[all]'

# 起動（PATH を気にせず常に動く形）
python -m docsweep triage
python -m docsweep serve --root C:\dev
python -m docsweep mcp

# MCP 登録例（~\.claude\mcp.json）— 絶対パスにすると Python 切替時も安定
# {
#   "mcpServers": {
#     "docsweep": {
#       "command": "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
#       "args": ["-m", "docsweep", "mcp"]
#     }
#   }
# }
# Python312 は実環境のバージョンに置換。確認: `where python` または `python -c "import sys; print(sys.executable)"`
```

### macOS

| 項目 | 場所 |
|---|---|
| Python 実体（Homebrew 例） | `/opt/homebrew/bin/python3` (Apple Silicon) / `/usr/local/bin/python3` (Intel) |
| docsweep 本体 | `/opt/homebrew/lib/python3.XX/site-packages/docsweep/` 等（`python3 -m site` で確認） |
| docsweep 設定・状態 | `~/.docsweep/`（＝ `/Users/<you>/.docsweep/`） |
| `docsweep` ショートカット | `/opt/homebrew/bin/docsweep` 等（PATH に既に入っていることが多い） |

```bash
# インストール（PEP 668 で system Python 保護がかかっていれば --user か venv 経由を選ぶ）
pip3 install 'docsweep[all]'

# 起動
python3 -m docsweep triage
python3 -m docsweep serve --root ~/dev
python3 -m docsweep mcp

# MCP 登録例（~/.claude/mcp.json）
# {
#   "mcpServers": {
#     "docsweep": {
#       "command": "/opt/homebrew/bin/python3",
#       "args": ["-m", "docsweep", "mcp"]
#     }
#   }
# }
# 自環境の Python パスは `which python3` で確認
```

### Linux

> **PEP 668 注意（Ubuntu 23.04+ / Debian 12+ / Fedora 38+ など）**: 近年の distro は system Python を保護しており、
> 素の `pip install` は `error: externally-managed-environment` で拒否されます。
> 解決策は **venv** か **`--user`** か **pipx** のいずれか（下記コマンド例の通り）。
> `--break-system-packages` フラグでの強行は OS 管理パッケージとコンフリクトする原因になるので推奨しません。

| 項目 | 場所 |
|---|---|
| Python 実体（distro pkg / pyenv 等） | `/usr/bin/python3` / `~/.pyenv/versions/3.XX.X/bin/python` 等 |
| docsweep 本体 | `/usr/lib/python3.XX/site-packages/docsweep/` か `~/.local/lib/python3.XX/site-packages/docsweep/`（`--user` 利用時） |
| docsweep 設定・状態 | `~/.docsweep/` |
| `docsweep` ショートカット | `/usr/local/bin/docsweep` / `~/.local/bin/docsweep`（PATH に `~/.local/bin` が無い distro では PATH 設定要） |

```bash
# 多くの distro は system Python を pip で汚せない。venv か --user か pipx を推奨
python3 -m venv ~/.venvs/docsweep && source ~/.venvs/docsweep/bin/activate
pip install 'docsweep[all]'

# または
pip install --user 'docsweep[all]'

# 起動
python3 -m docsweep triage
python3 -m docsweep serve --root ~/dev
python3 -m docsweep mcp

# MCP 登録例（~/.claude/mcp.json）— venv 内 Python の絶対パスを指定する
# {
#   "mcpServers": {
#     "docsweep": {
#       "command": "/home/you/.venvs/docsweep/bin/python",
#       "args": ["-m", "docsweep", "mcp"]
#     }
#   }
# }
# 自環境の Python パスは `which python3` で確認
```

### インストール形態を選ぶ

| 方式 | 向き不向き |
|---|---|
| **直接 `pip install`**（ユーザー Python に入れる） | 個人ツールとして気軽に使いたい時。最短手数 |
| **venv 隔離**（`python -m venv ...` で専用環境） | 依存をユーザー Python に混ぜたくない時。MCP 設定は venv 内 Python の絶対パスを書く |
| **pipx**（CLI として隔離 install） | docsweep CLI だけ使う時。MCP からは pipx の内部 venv Python を絶対パス指定 |
| **`pip install -e .`**（リポを clone して editable install） | 自分で docsweep を開発・拡張する時。src 編集が即反映 |

### アンインストール

```bash
pip uninstall docsweep
# 設定を消したい場合は別途
rm -rf ~/.docsweep                # macOS / Linux
Remove-Item -Recurse ~/.docsweep  # Windows PowerShell
```

## 使い方

> **朝の入口は `brief`**: 何も思い出せなくても `python -m docsweep brief` で「今日の 1 個」が断定的に出ます。
> プロジェクト横断は `python -m docsweep cross`。詳細: [docs/ai-agent-integration.md](docs/ai-agent-integration.md)。

```bash
# === wings（v0.2 系）の主要新コマンド ===

# 朝の入口 — 今日 1 個だけやろうを断定する（cwd プロジェクト）
python -m docsweep brief
python -m docsweep brief --all              # 全プロジェクト横並び要約
python -m docsweep brief --continue         # 末尾の対話を出さず context を即クリップボードへ

# 全プロジェクト束ねた俯瞰 — top_pick + 凍結予備軍 + project_summaries
python -m docsweep cross
python -m docsweep cross --project alpha,beta
python -m docsweep cross --explain plan_x.md   # スコア内訳

# 会話履歴から plan/bugfix/pending の草案を抽出（heuristic / LLM mock）
python -m docsweep capture --from clipboard
python -m docsweep capture --from file ./conv.md --save-all

# plan の「変更予定ファイル」と実装実態の整合チェック
python -m docsweep linkcheck --json

# 状態遷移を提案（ruleset / 将来 LLM 委譲）+ 一括適用
python -m docsweep auto-triage --suggest > decisions.json
python -m docsweep auto-triage --apply decisions.json --dry-run

# 関係性ネットワーク（plan/bugfix/pending と frontmatter related のグラフ）
python -m docsweep graph --json

# archive と現役の類似ペアを抽出（embedding opt-in / 既定は Jaccard）
python -m docsweep resurrect --threshold 0.5

# SQLite 索引 ~/.docsweep/index.db
python -m docsweep index-sync               # 差分のみ取り込み（高速）
python -m docsweep index-rebuild            # 全件再構築
python -m docsweep index-watch              # ファイル監視で自動同期（watchdog 必要）

# === 従来コマンド（既存・後方互換） ===

# スキャン（既定は要判断＋保留のみ表示）
python -m docsweep --root ~/dev
python -m docsweep ./thisproject            # config 不要の単発スキャン
python -m docsweep scan --all --json        # 全件を機械可読 JSON で

# 自動移送（cron / CI / AI 委譲向け・非対話）。done/discarded のみ・様子見は守る
python -m docsweep sweep --dry-run
python -m docsweep sweep

# 横断 INDEX を再生成（.docsweep/INDEX.md と INDEX.json）
python -m docsweep index
python -m docsweep pending                  # 全プロジェクトの [保留] だけ一発表示
python -m docsweep report                   # 人間向け週次レポート
python -m docsweep summary                  # AI に渡す圧縮 JSON

# リリース整理（様子見をまとめて完了へ昇格し archive へ）
python -m docsweep promote --state watching --to done

# 対話チェックリスト（人間専用）
python -m docsweep review

# テンプレ即生成
python -m docsweep new plan my-topic
python -m docsweep new bugfix crash-on-start

# OKF 互換 zip でエクスポート（docsweep を抜けても md が腐らないことを実演する材料）
python -m docsweep export --okf                          # ./docsweep-okf-<date>.zip
python -m docsweep export --okf --out /tmp/snapshot.zip  # 出力先を明示
python -m docsweep export --okf --include-archive        # archive/ 配下も含める

# 運用ルールを各プロジェクトへ注入／取り消し（CLAUDE.md=正本・AGENTS.md はそこを指すポインタ）
python -m docsweep inject --project ./foo --preset claude-jp
python -m docsweep inject --project ./foo --no-guidance   # 導線を省きラベル節だけ（導線をグローバルに寄せる場合）
python -m docsweep eject  --project ./foo                  # 管理ブロックだけ剥がす（手書きは温存。--purge で .docsweep.yaml も）

# 個人グローバルへ「セッション開始時に triage を読む」導線を一度だけ注入（全プロジェクトで有効）
python -m docsweep inject --global                         # 既定 agent=claude（~/.claude/CLAUDE.md に @import 1 行）
python -m docsweep inject --global --agent codex           # ~/.codex/AGENTS.md にインライン（CODEX_HOME 尊重）
python -m docsweep eject  --global

python -m docsweep list                                    # 注入済み（プロジェクト＋グローバル）一覧

# Web UI（UX 主役・127.0.0.1・トークン付き URL）。注入/解除もダッシュボードから（プレビュー必須）
python -m docsweep serve --root ~/dev

# MCP サーバー（AI エージェント面・stdio）
python -m docsweep mcp
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

### よくある 3 パターン（グローバル `~/.docsweep/config.yaml`）

プロジェクト境界は `.git` / `package.json` / `pyproject.toml` 等の実体マーカーで自動判定するので、
**root の中で各プロジェクトがどの深さにあっても OK**（フォルダ階層を決め打ちしない）。

**A. 1 つの親ディレクトリ以下を丸ごと管理**

```yaml
roots:
  - ~/dev
```

**B. 飛び飛びの複数ディレクトリを管理**

```yaml
roots:
  - ~/dev/github/public
  - ~/dev/works/clientA
  - ~/dev/works/clientB
  - /d/sandbox/experiments
```

**C. 用途別に切り替えたい（profiles）**

```yaml
roots:
  - ~/dev/github/public        # 既定（無引数）で見る範囲
profiles:
  work:                         # python -m docsweep triage --profile work
    - ~/dev/works/clientA
    - ~/dev/works/clientB
  all:                          # python -m docsweep triage --profile all
    - ~/dev/github/public
    - ~/dev/works
```

**一回きりの単発スキャン**は config を書かずに位置引数で指定もできます:

```bash
python -m docsweep triage ~/dev/foo ~/projects/bar
```

## AI エージェント連携

> **wings（v0.2 系）の方針**: 「全 AI 対応」を最優先し、自然言語起動の価値が高い **朝の入口 3 tool**
> （`brief` / `cross` / `capture_extract`+`capture_save`）だけを MCP に露出、それ以外は **CLI 直叩き** で
> 全 AI に対応します。詳細・自然言語マッピング表は [docs/ai-agent-integration.md](docs/ai-agent-integration.md)。

### 推奨運用: MCP 登録せず CLI 一本化

各 AI ツール（Claude Code / Codex / Cursor …）に MCP サーバーを個別登録するより、
**CLI 経由（`python -m docsweep ...`）を AI に直接叩かせる運用**が最もシンプルです。

- インストール 1 回で全 AI ツールから使える（MCP 登録は AI ツールごとに別ファイル）
- 戻り値・triage の中身は MCP と同じ — AI から見た体験はほぼ変わらない
- 唯一の手間は **AI 側で `python -m docsweep` を allowlist に追加** すること

Claude Code の場合、`~/.claude/settings.json` の `permissions.allow` に 1 行追記:

```json
{
  "permissions": {
    "allow": [
      "Bash(python -m docsweep:*)"
    ]
  }
}
```

> docsweep からは **この JSON を自動で書き換えません**（権限境界の操作になるため）。
> ユーザーが意図的に貼り付ける運用に倒しています。MCP として使いたい場合は
> 上の「MCP 登録例」を使ってください（こちらも自動登録はしません）。

### triage の中身

`python -m docsweep triage`（または MCP の `triage` ツール）は、**要判断＋保留を古い順に絞った残作業**を
`counts` ＋ `items[]` ＋ `needs_fix[]` で返します。各 item は `rel`（相対パス）・`title`（H1）・
`state`（ラベル）・`type`・`age_days`・`summary`・`actions`（`discard`/`keep`/`resume`/`relabel`/`promote`
の閉じた集合）を持ち、エージェントは「次にどのファイルの何を続けるか」を判断 → `python -m docsweep apply` で
機械実行します。横断 INDEX 全体の俯瞰は `python -m docsweep summary`。docsweep 自身は AI API を叩きません（ベンダー非依存）。

セッション開始時に AI へ自動でこの残作業を渡すには `python -m docsweep inject --global`（Claude は `@import`、
Codex はインラインで「作業前に triage を読む」導線を個人グローバル設定へ一度だけ注入）。

### 特定プロジェクトだけ調べたい時（`--project`）

複数 root を横断管理していると、AI からの自然言語クエリは「全体」より
「`<このリポ>` だけ」になりがちです。`sweep` / `promote` / `triage` / `scan` / `summary` は
共通の `--project <name>` フラグでプロジェクト名（境界フォルダ名）に絞り込めます。

```bash
# 「many-ai-cli の archive 移送対象いくつ?」
python -m docsweep sweep --dry-run --project many-ai-cli

# 「docsweep プロジェクトの様子見昇格候補は?」
python -m docsweep promote --dry-run --project docsweep

# 「many-ai-cli の残作業ある?」
python -m docsweep triage --project many-ai-cli

# 「docsweep プロジェクトの全件だけ JSON で吐いて」
python -m docsweep scan --all --project docsweep --json

# 「docsweep プロジェクトの俯瞰を圧縮 JSON で」
python -m docsweep summary --project docsweep
```

> 絞り込みは **スキャンルートを動かさず後段でフィルタ** します。各プロジェクトの `.gitignore`
> が `docs/local/` を除外していても、グローバル config の `roots:` から見えていれば対象になります
> （位置引数 `.` で当該プロジェクトを単発スキャンすると `.gitignore` で除外されて 0 件になる
> 落とし穴を回避するための設計）。
>
> `triage` / `summary` の `counts` も per-project スコープに揃えて返します（フィルタ後の
> `items` 数と一致するので、AI/人間どちらが見ても齟齬がありません）。

MCP 経由でも同じく引数で絞れます。例: `triage(project="many-ai-cli")` / `summary(project="docsweep")` /
`sweep(project="many-ai-cli", dry_run=True)`。CLI と MCP で引数名・挙動を完全に揃えています。

詳細は [docs/conventions.md](docs/conventions.md) と
[templates/AGENT_GUIDE.md](templates/AGENT_GUIDE.md) を参照してください。

## ライセンス

MIT
