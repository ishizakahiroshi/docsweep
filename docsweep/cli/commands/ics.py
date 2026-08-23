"""CLI command handlers: ics."""

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

def cmd_ics(args: argparse.Namespace) -> int:
    from ...ics_export import write_ics

    cfg = _build_config(args)
    try:
        out = write_ics(cfg, Path(getattr(args, "out", None) or "docsweep-due.ics"))
    except OSError as exc:
        print(f"ics: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {out}")
    return 0
