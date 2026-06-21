# docSweep

AI コーディングツール（Claude Code / Codex 等）が生成する `plan_*.md` / `bugfix_*.md` /
`pending_*.md` の **蓄積・陳腐化問題を解決する** クロスプラットフォーム CLI + Web UI ツール。

H1 ステータスラベル（`[完了]` / `[計画]` / `[廃止]` 等）を機械的に読み取り、完了を各プロジェクトの
`archive/` へ自動移送し、陳腐化を「要判断」フラグで可視化し、複数プロジェクトを横断 INDEX で一望できます。

## インストール

```bash
pip install docSweep         # コア + CLI
pip install 'docSweep[all]'  # Web UI / 対話レビュー / MCP も含む
```

docSweep は **PATH に `docSweep` コマンドを通さない運用を標準**にしています。
CLI / Web UI / MCP は、Python 実行ファイルから module として起動できます。

```bash
python -m docSweep triage
python -m docSweep mcp
python -m docSweep serve --root ~/dev
```

MCP クライアントへ登録する場合も、`docSweep mcp` ではなく `python -m docSweep mcp`
を推奨します。より再現性を上げるなら、`command` には Python 実行ファイルの絶対パスを指定します。

```json
{
  "mcpServers": {
    "docSweep": {
      "command": "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
      "args": ["-m", "docSweep", "mcp"]
    }
  }
}
```

> 上記の `Python312` は **インストールされている Python のバージョンに置き換えてください**。
> 確認方法: Windows は `where python`、macOS / Linux は `which python3`。
> もしくは `python -c "import sys; print(sys.executable)"` でフルパスが取れます。

`docSweep ...` は Python の Scripts/bin ディレクトリが PATH に入っている環境向けの短縮形です。

## インストール後どこに置かれて、どう使えるか（OS 別）

`pip install docSweep` は **Python の site-packages にライブラリを配置**します。
別バイナリは生成されず、`python -m docSweep ...` で起動するのが標準動線です。

### 共通の挙動

- **`docsweep/` パッケージ本体**: 起動中の Python が解決する site-packages に展開される
- **設定・状態**: `~/.docSweep/`（全 OS 共通の論理パス）
- **MCP 設定**: AI クライアント（Claude Code 等）の設定ファイルに `python -m docSweep mcp` を 1 行登録するだけ
- **PATH 設定不要**: `docSweep` を PATH に通さなくても、`python -m docSweep ...` ですべての機能にアクセス可能

### Windows

| 項目 | 場所 |
|---|---|
| Python 実体（per-user 標準インストール） | `C:\Users\<you>\AppData\Local\Programs\Python\Python3XX\python.exe` |
| docSweep 本体（pip install 後） | `C:\Users\<you>\AppData\Local\Programs\Python\Python3XX\Lib\site-packages\docsweep\` |
| docSweep 設定・状態 | `C:\Users\<you>\.docSweep\`（= `%USERPROFILE%\.docSweep\` = `~/.docSweep`） |
| `docSweep` ショートカット | `...\Python3XX\Scripts\docSweep.exe`（PATH 通っていれば `docSweep` 直で起動可） |

> **Windows ストア版 Python は避けることを推奨**。`%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe`
> 経由のストア版は仮想ストア配置の制約で **`Scripts/` ディレクトリへの書き込みが弾かれる**ことがあり、
> `pip install` 自体は通っても `docSweep.exe` ランチャーが正しく作られない／PATH に乗らないトラブルが
> 起きます。python.org 配布のインストーラ（per-user）か pyenv-win を使うのが安全です。

```powershell
# インストール
pip install 'docSweep[all]'

# 起動（PATH を気にせず常に動く形）
python -m docSweep triage
python -m docSweep serve --root C:\dev
python -m docSweep mcp

# MCP 登録例（~\.claude\mcp.json）— 絶対パスにすると Python 切替時も安定
# {
#   "mcpServers": {
#     "docSweep": {
#       "command": "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
#       "args": ["-m", "docSweep", "mcp"]
#     }
#   }
# }
# Python312 は実環境のバージョンに置換。確認: `where python` または `python -c "import sys; print(sys.executable)"`
```

### macOS

| 項目 | 場所 |
|---|---|
| Python 実体（Homebrew 例） | `/opt/homebrew/bin/python3` (Apple Silicon) / `/usr/local/bin/python3` (Intel) |
| docSweep 本体 | `/opt/homebrew/lib/python3.XX/site-packages/docsweep/` 等（`python3 -m site` で確認） |
| docSweep 設定・状態 | `~/.docSweep/`（＝ `/Users/<you>/.docSweep/`） |
| `docSweep` ショートカット | `/opt/homebrew/bin/docSweep` 等（PATH に既に入っていることが多い） |

```bash
# インストール（PEP 668 で system Python 保護がかかっていれば --user か venv 経由を選ぶ）
pip3 install 'docSweep[all]'

# 起動
python3 -m docSweep triage
python3 -m docSweep serve --root ~/dev
python3 -m docSweep mcp

# MCP 登録例（~/.claude/mcp.json）
# {
#   "mcpServers": {
#     "docSweep": {
#       "command": "/opt/homebrew/bin/python3",
#       "args": ["-m", "docSweep", "mcp"]
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
| docSweep 本体 | `/usr/lib/python3.XX/site-packages/docsweep/` か `~/.local/lib/python3.XX/site-packages/docsweep/`（`--user` 利用時） |
| docSweep 設定・状態 | `~/.docSweep/` |
| `docSweep` ショートカット | `/usr/local/bin/docSweep` / `~/.local/bin/docSweep`（PATH に `~/.local/bin` が無い distro では PATH 設定要） |

```bash
# 多くの distro は system Python を pip で汚せない。venv か --user か pipx を推奨
python3 -m venv ~/.venvs/docsweep && source ~/.venvs/docsweep/bin/activate
pip install 'docSweep[all]'

# または
pip install --user 'docSweep[all]'

# 起動
python3 -m docSweep triage
python3 -m docSweep serve --root ~/dev
python3 -m docSweep mcp

# MCP 登録例（~/.claude/mcp.json）— venv 内 Python の絶対パスを指定する
# {
#   "mcpServers": {
#     "docSweep": {
#       "command": "/home/you/.venvs/docsweep/bin/python",
#       "args": ["-m", "docSweep", "mcp"]
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
| **pipx**（CLI として隔離 install） | docSweep CLI だけ使う時。MCP からは pipx の内部 venv Python を絶対パス指定 |
| **`pip install -e .`**（リポを clone して editable install） | 自分で docSweep を開発・拡張する時。src 編集が即反映 |

### アンインストール

```bash
pip uninstall docSweep
# 設定を消したい場合は別途
rm -rf ~/.docSweep                # macOS / Linux
Remove-Item -Recurse ~/.docSweep  # Windows PowerShell
```

## 使い方

```bash
# スキャン（既定は要判断＋保留のみ表示）
python -m docSweep --root ~/dev
python -m docSweep ./thisproject            # config 不要の単発スキャン
python -m docSweep scan --all --json        # 全件を機械可読 JSON で

# 自動移送（cron / CI / AI 委譲向け・非対話）。done/discarded のみ・様子見は守る
python -m docSweep sweep --dry-run
python -m docSweep sweep

# 横断 INDEX を再生成（.docSweep/INDEX.md と INDEX.json）
python -m docSweep index
python -m docSweep pending                  # 全プロジェクトの [保留] だけ一発表示
python -m docSweep report                   # 人間向け週次レポート
python -m docSweep summary                  # AI に渡す圧縮 JSON

# リリース整理（様子見をまとめて完了へ昇格し archive へ）
python -m docSweep promote --state watching --to done

# 対話チェックリスト（人間専用）
python -m docSweep review

# テンプレ即生成
python -m docSweep new plan my-topic
python -m docSweep new bugfix crash-on-start

# 運用ルールを各プロジェクトへ注入／取り消し（CLAUDE.md=正本・AGENTS.md はそこを指すポインタ）
python -m docSweep inject --project ./foo --preset claude-jp
python -m docSweep inject --project ./foo --no-guidance   # 導線を省きラベル節だけ（導線をグローバルに寄せる場合）
python -m docSweep eject  --project ./foo                  # 管理ブロックだけ剥がす（手書きは温存。--purge で .docSweep.yaml も）

# 個人グローバルへ「セッション開始時に triage を読む」導線を一度だけ注入（全プロジェクトで有効）
python -m docSweep inject --global                         # 既定 agent=claude（~/.claude/CLAUDE.md に @import 1 行）
python -m docSweep inject --global --agent codex           # ~/.codex/AGENTS.md にインライン（CODEX_HOME 尊重）
python -m docSweep eject  --global

python -m docSweep list                                    # 注入済み（プロジェクト＋グローバル）一覧

# Web UI（UX 主役・127.0.0.1・トークン付き URL）。注入/解除もダッシュボードから（プレビュー必須）
python -m docSweep serve --root ~/dev

# MCP サーバー（AI エージェント面・stdio）
python -m docSweep mcp
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

優先順位 **① CLI フラグ > ② プロジェクト `.docSweep.yaml` > ③ グローバル `~/.docSweep/config.yaml`**。
グローバルだけ書けば体感 1 層。`.docSweep.yaml` は置いた時だけ部分上書きで効きます。

## AI エージェント連携

`python -m docSweep triage`（または MCP の `triage` ツール）は、**要判断＋保留を古い順に絞った残作業**を
`counts` ＋ `items[]` ＋ `needs_fix[]` で返します。各 item は `rel`（相対パス）・`title`（H1）・
`state`（ラベル）・`type`・`age_days`・`summary`・`actions`（`discard`/`keep`/`resume`/`relabel`/`promote`
の閉じた集合）を持ち、エージェントは「次にどのファイルの何を続けるか」を判断 → `python -m docSweep apply` で
機械実行します。横断 INDEX 全体の俯瞰は `python -m docSweep summary`。docSweep 自身は AI API を叩きません（ベンダー非依存）。

セッション開始時に AI へ自動でこの残作業を渡すには `python -m docSweep inject --global`（Claude は `@import`、
Codex はインラインで「作業前に triage を読む」導線を個人グローバル設定へ一度だけ注入）。

詳細は [docs/conventions.md](docs/conventions.md) と
[templates/AGENT_GUIDE.md](templates/AGENT_GUIDE.md) を参照してください。

## ライセンス

MIT
