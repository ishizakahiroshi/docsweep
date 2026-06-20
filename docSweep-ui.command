#!/usr/bin/env bash
# ============================================================
#  docSweep Web UI ランチャ（macOS・Finder でダブルクリック可）
#  - ブラウザは自動で開きます。停止は Ctrl+C。
#  - フォルダを変えたいときは下の ROOT を編集してください。
#  初回だけ実行権限が要ります（Finder で「ターミナル」起動を許可するか、
#  ターミナルで:  chmod +x docSweep-ui.command ）。
# ============================================================
set -euo pipefail

# このスクリプトが置かれた場所＝docSweep リポジトリ直下を想定。
# デスクトップ等へコピーして使う場合は REPO をリポジトリの絶対パスに書き換える。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$SCRIPT_DIR"

# ▼ 既定のスキャンルート（自分の開発フォルダに書き換えてください）。
ROOT="${1:-$HOME/dev}"
PORT="${PORT:-8765}"
# アクセストークンを固定（URL が毎回同じになる）。気になるなら好きな文字列に変更。
TOKEN="${TOKEN:-docSweep}"

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "python3 が見つかりません。Python をインストールしてください。" >&2
  read -r -p "Enter で閉じます… " _
  exit 1
fi

cd "$REPO"
echo
echo " docSweep Web UI を起動します"
echo " ブラウザで開くアドレス: http://127.0.0.1:$PORT/?token=$TOKEN"
echo " （ブラウザが自動で開きます / 停止は Ctrl+C）"
echo
"$PY" -m docSweep serve --root "$ROOT" --port "$PORT" --token "$TOKEN"

echo
read -r -p "終了しました。Enter で閉じます… " _
