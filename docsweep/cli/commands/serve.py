"""CLI command handlers: serve."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ...i18n import t
from ..parser import _build_config

def cmd_serve(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    if not cfg.roots:
        # --root も config の roots も無ければカレントフォルダを採用（手軽起動）。
        cfg.roots = [Path.cwd()]
        print(t("cli_serve.using_cwd", path=Path.cwd()))
    try:
        import secrets

        import uvicorn

        from ...server.app import create_app
    except ImportError:
        print(t("cli_serve.needs_web_extra"), file=sys.stderr)
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
    print(t("cli_serve.bookmark_url"))
    print(f"  {url}")
    print(t("cli_serve.initial_url"))
    print(f"  {initial_url}")
    if getattr(args, "read_only", False):
        print(t("cli_serve.read_only"))
    print("=" * 60)
    print(t("cli_serve.stop_hint"))
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
        print(t("cli_serve.stopped"))
    return 0
