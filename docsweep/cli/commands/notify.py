"""CLI command handlers: notify."""

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

def cmd_notify(args: argparse.Namespace) -> int:
    """OS ローカル通知（UX W4 / P53）。"""
    from ...notify import notify_overdue

    cfg = _build_config(args)
    res = notify_overdue(cfg, dry_run=bool(getattr(args, "dry_run", False)))
    if getattr(args, "json", False):
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"[{res.backend}] {res.title}: {res.body}")
        if res.detail and not res.sent:
            print(f"  detail: {res.detail}", file=sys.stderr)
    return 0 if res.sent or getattr(args, "dry_run", False) else 1
