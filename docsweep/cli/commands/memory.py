"""CLI command handlers: memory."""

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

def cmd_memory(args: argparse.Namespace) -> int:
    from ...memory_scan import scan_memory

    res = scan_memory(
        paths=getattr(args, "paths", None),
        stale_days=int(getattr(args, "stale_days", 90) or 90),
    )
    if getattr(args, "json", False):
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(f"memory scan: {len(res.files)} files (stale≥{res.stale_over_days}d: "
          f"{sum(1 for f in res.files if f.age_days >= res.stale_over_days)})")
    for f in res.files[:30]:
        mark = "STALE" if f.age_days >= res.stale_over_days else "ok"
        print(f"  [{mark}] {f.age_days:>4}d  {f.path}")
    return 0
