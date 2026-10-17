#!/usr/bin/env bash
# ============================================================
#  docsweep Web UI ランチャ（Linux / macOS 共通）
#  - ブラウザは自動で開きます。停止は Ctrl+C。
#  - 引数にフォルダを渡すと、その場所をスキャンします:  ./docsweep-ui.sh ~/projects
#  使い方:
#    chmod +x docsweep-ui.sh   # 初回だけ実行権限を付与
#    ./docsweep-ui.sh
# ============================================================
set -euo pipefail

# このスクリプトが置かれた場所＝docsweep リポジトリ直下を想定（未インストールでも動かすため）。
# 別の場所（デスクトップ等）へコピーして使う場合は REPO をリポジトリの絶対パスに書き換える。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$SCRIPT_DIR"

# ▼ 既定のスキャンルート（自分の開発フォルダに書き換えてください）。引数があればそちらを優先。
ROOT="${1:-$HOME/dev}"

# 起動ポート（使用中なら変更）。
PORT="${PORT:-8765}"

# アクセストークンは起動のたびに乱数で生成される（固定値は使わない）。
# 固定したい場合は環境変数 DOCSWEEP_TOKEN に自分だけが知る値を設定する。

# python3 を優先、無ければ python。
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "python3 が見つかりません。Python をインストールしてください。" >&2
  exit 1
fi

cd "$REPO"
echo
echo " docsweep Web UI を起動します"
echo " （ブラウザが自動で開きます / 停止は Ctrl+C。アドレスは下に表示されます）"
echo
exec "$PY" -m docsweep serve --root "$ROOT" --port "$PORT"
