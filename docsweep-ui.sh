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

# アクセストークンを固定（URL が毎回同じになる）。気になるなら好きな文字列に変更。
TOKEN="${TOKEN:-docsweep}"

# python3 を優先、無ければ python。
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "python3 が見つかりません。Python をインストールしてください。" >&2
  exit 1
fi

cd "$REPO"
echo
echo " docsweep Web UI を起動します"
echo " ブラウザで開くアドレス: http://127.0.0.1:$PORT/?token=$TOKEN"
echo " （ブラウザが自動で開きます / 停止は Ctrl+C）"
echo
exec "$PY" -m docsweep serve --root "$ROOT" --port "$PORT" --token "$TOKEN"
