"""CLI command handlers: serve."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from ...config import DEFAULT_PROJECT_MARKERS, load_config
from ...engine import apply_action, auto_sweep, run_scan
from ..parser import _build_config

def cmd_serve(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    if not cfg.roots:
        # --root も config の roots も無ければカレントフォルダを採用（手軽起動）。
        cfg.roots = [Path.cwd()]
        print(f"（--root 未指定のためカレントを使用: {Path.cwd()}）")
    try:
        import secrets

        import uvicorn

        from ...server.app import create_app
    except ImportError:
        print("Web UI には web extra が必要です: pip install 'docsweep[web]'", file=sys.stderr)
        return 3

    # トークンはコマンドライン引数（他プロセスから見える）より環境変数を推奨。
    token = args.token or os.environ.get("DOCSWEEP_TOKEN") or secrets.token_urlsafe(16)
    app = create_app(
        cfg,
        token=token,
        read_only=bool(getattr(args, "read_only", False)),
        allow_root_mutation=bool(getattr(args, "allow_root_mutation", False)),
    )
    url = f"http://127.0.0.1:{args.port}/board"
    initial_url = f"http://127.0.0.1:{args.port}/?token={token}"
    print("=" * 60)
    print("  ブックマーク用 URL（初回認証後はこちらで開けます）:")
    print(f"  {url}")
    print("  初回認証 URL（?token= は Cookie 交換後に自動で消えます）:")
    print(f"  {initial_url}")
    if getattr(args, "read_only", False):
        print("  [read-only] 書き込み API は 403 です")
    print("=" * 60)
    print("（Ctrl+C または画面右上の ⏻ で停止）")
    if not args.no_browser:
        import threading
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(initial_url)).start()
    # uvicorn.run(app, ...) だと外から graceful 停止できないため、Server/Config を直接使い、
    # インスタンスを app.state に渡すことで /api/shutdown から should_exit=True できるようにする。
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    app.state.docsweep.server = server
    try:
        server.run()
    except KeyboardInterrupt:
        # Python 3.14 の asyncio.runners は Ctrl+C を KeyboardInterrupt として再送出する。
        # 正常な停止操作なのでスタックトレースを見せず 1 行で終える。
        print("停止しました（Ctrl+C）")
    return 0
