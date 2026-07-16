"""CLI command handlers: init."""

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

def cmd_init(args: argparse.Namespace) -> int:
    """初回ウィザード（UX W1 / P1）。"""
    from ...init_cmd import interactive_prompts, run_init

    yes = bool(getattr(args, "yes", False))
    root = getattr(args, "root", None)
    lang = getattr(args, "lang", None) or "ja"
    agent = getattr(args, "agent", None) or "claude"
    if not yes and root is None and not getattr(args, "force", False):
        answers = interactive_prompts()
        root = answers["root"]
        lang = answers["lang"]
        agent = answers["agent"]
    global_path = Path(args.config) if getattr(args, "config", None) else None
    result = run_init(
        yes=yes,
        root=root,
        lang=lang,
        agent=agent,
        global_path=global_path,
        force=bool(getattr(args, "force", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.message)
        if result.created or not getattr(args, "force", False):
            print("次の一手:")
            for s in result.next_steps:
                print(f"  {s}")
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    """直近 archive/promote バッチを復元（UX W1 / P12 CLI）。"""
    from ...services.archive import undo_last_batch

    cfg = _build_config(args)
    res = undo_last_batch(config=cfg)
    payload = {
        "batch_id": res.batch_id,
        "restored": [
            {"src": e.src, "dst": e.dst, "project": e.project, "state": e.state}
            for e in res.restored
        ],
        "failed": list(res.failed),
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not res.batch_id:
            print("Undo 対象がありません（既に復元済み、または batch_id 無し）")
            return 1
        print(f"batch {res.batch_id}: {len(res.restored)} 件を復元")
        for e in res.restored:
            print(f"  {e.dst} -> {e.src}")
        if res.failed:
            print(f"失敗 {len(res.failed)} 件:")
            for f in res.failed:
                print(f"  {f}")
    return 1 if res.failed and not res.restored else 0
