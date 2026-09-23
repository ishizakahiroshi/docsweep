"""CLI command handlers: mcp."""

from __future__ import annotations

import argparse
import sys

from ...i18n import t
from ..parser import _build_config

def cmd_mcp(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    try:
        from ... import mcp_server
    except ImportError:
        print(t("cli_mcp.needs_mcp_extra"), file=sys.stderr)
        return 3
    try:
        mcp_server.run(cfg)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 3
    return 0
