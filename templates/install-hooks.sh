#!/usr/bin/env bash
# docsweep pre-commit hook の opt-in 配置スクリプト（POSIX 環境用）。
#
# 何をするか:
#   templates/.githooks/docsweep-check.py を ``.git/hooks/pre-commit`` にコピーする。
#   docsweep を入れていないリポでも動くスタンドアロン hook。
#
# 使い方（リポルートで実行）:
#   bash templates/install-hooks.sh
#
# 取り消したい場合:
#   rm .git/hooks/pre-commit
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/.githooks/docsweep-check.py"
GIT_DIR="$(git rev-parse --git-dir 2>/dev/null || true)"

if [ -z "$GIT_DIR" ]; then
    echo "error: git リポジトリ内で実行してください" >&2
    exit 1
fi
if [ ! -f "$SRC" ]; then
    echo "error: $SRC が見つかりません" >&2
    exit 1
fi

mkdir -p "$GIT_DIR/hooks"
DST="$GIT_DIR/hooks/pre-commit"

if [ -f "$DST" ]; then
    BACKUP="$DST.bak.$(date +%Y%m%d-%H%M%S)"
    mv "$DST" "$BACKUP"
    echo "既存 pre-commit を退避: $BACKUP"
fi

cp "$SRC" "$DST"
chmod +x "$DST"
echo "docsweep pre-commit hook を配置しました: $DST"
echo "（取り消す場合: rm $DST）"
