"""CLI command handlers: inject."""

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

def cmd_inject(args: argparse.Namespace) -> int:
    from ...inject import inject, inject_global

    tag = "（dry-run）" if args.dry_run else ""
    if getattr(args, "is_global", False):
        r = inject_global(
            agent=args.agent, target=args.global_target, lang=args.lang or "ja", dry_run=args.dry_run,
        )
        print(f"inject {r.project}{tag}: 書込={r.written or '-'} 温存/不変={r.skipped or '-'}")
        for w in r.warnings:
            print(f"  ⚠ {w}")
        return 0

    r = inject(
        Path(args.project), preset=args.preset, write_yaml=not args.no_yaml,
        include_guidance=not args.no_guidance, lang=args.lang, dry_run=args.dry_run,
    )
    print(f"inject {r.project}{tag}: 書込={r.written or '-'} 温存/不変={r.skipped or '-'}")
    if r.yaml_path:
        print(f"  .docsweep.yaml: {r.yaml_path}")
    for w in r.warnings:
        print(f"  ⚠ {w}")
    return 0


def cmd_eject(args: argparse.Namespace) -> int:
    from ...inject import eject, eject_global, list_injected

    def _report(r) -> None:
        tag = "（dry-run）" if args.dry_run else ""
        yaml = " +yaml" if getattr(r, "purged_yaml", False) else ""
        print(f"eject {r.project}{tag}: 除去={r.removed or '-'}{yaml}")
        for w in r.warnings:
            print(f"  ⚠ {w}")

    if getattr(args, "is_global", False):
        _report(eject_global(agent=args.agent, target=args.global_target, dry_run=args.dry_run))
        return 0
    if args.all:
        for it in list_injected():
            if it.get("scope") == "global":
                _report(eject_global(agent=it.get("agent") or "claude", target=it["path"], dry_run=args.dry_run))
            else:
                _report(eject(Path(it["path"]), purge=args.purge, dry_run=args.dry_run))
        return 0
    _report(eject(Path(args.project).resolve(), purge=args.purge, dry_run=args.dry_run))
    return 0
