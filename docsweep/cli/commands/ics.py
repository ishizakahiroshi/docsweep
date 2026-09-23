"""CLI command handlers: ics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..parser import _build_config

def cmd_ics(args: argparse.Namespace) -> int:
    from ...i18n import t
    from ...ics_export import write_ics

    cfg = _build_config(args)
    try:
        out = write_ics(cfg, Path(getattr(args, "out", None) or "docsweep-due.ics"))
    except OSError as exc:
        print(f"ics: {exc}", file=sys.stderr)
        return 2
    print(t("cli_ics.wrote", path=out))
    return 0
