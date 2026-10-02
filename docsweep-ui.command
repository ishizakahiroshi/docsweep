#!/usr/bin/env bash
# ============================================================
#  docsweep Web UI ランチャ（macOS・Finder でダブルクリック可）
#  - ブラウザは自動で開きます。停止は Ctrl+C。
#  - フォルダを変えたいときは下の ROOT を編集してください。
#  初回だけ実行権限が要ります（Finder で「ターミナル」起動を許可するか、
#  ターミナルで:  chmod +x docsweep-ui.command ）。
# ============================================================
set -euo pipefail

# このスクリプトが置かれた場所＝docsweep リポジトリ直下を想定。
# デスクトップ等へコピーして使う場合は REPO をリポジトリの絶対パスに書き換える。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$SCRIPT_DIR"

# ▼ 既定のスキャンルート（自分の開発フォルダに書き換えてください）。
ROOT="${1:-$HOME/dev}"
PORT="${PORT:-8765}"
# アクセストークンは起動のたびに乱数で生成される（固定値は使わない）。
# 固定したい場合は環境変数 DOCSWEEP_TOKEN に自分だけが知る値を設定する。

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "python3 が見つかりません。Python をインストールしてください。" >&2
  read -r -p "Enter で閉じます… " _
  exit 1
fi

cd "$REPO"
echo
echo " docsweep Web UI を起動します"
echo " （ブラウザが自動で開きます / 停止は Ctrl+C。アドレスは下に表示されます）"
echo
"$PY" -m docsweep serve --root "$ROOT" --port "$PORT"

echo
read -r -p "終了しました。Enter で閉じます… " _
