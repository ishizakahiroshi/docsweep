"""CLI command handlers: completion."""

from __future__ import annotations

import argparse

from ..parser import _build_config

def cmd_completion(args: argparse.Namespace) -> int:
    """シェル補完スクリプトを stdout 出力。"""
    from ...completion import render_completion

    cfg = _build_config(args)
    print(render_completion(args.shell, cfg))
    return 0
