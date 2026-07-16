"""CLI command handlers: mcp."""

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

def cmd_mcp(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    try:
        from ... import mcp_server
    except ImportError:
        print("MCP には mcp extra が必要です: pip install 'docsweep[mcp]'", file=sys.stderr)
        return 3
    try:
        mcp_server.run(cfg)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 3
    return 0
