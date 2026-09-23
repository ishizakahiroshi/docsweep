"""CLI command handlers: init."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ...i18n import t
from ..parser import _build_config

def cmd_init(args: argparse.Namespace) -> int:
    """初回ウィザード（UX W1 / P1）。"""
    from ...init_cmd import interactive_prompts, run_init

    yes = bool(getattr(args, "yes", False))
    root = getattr(args, "root", None)
    # 省いたときは run_init が表示言語を書く（固定の "ja" にすると英語の利用者も日本語になる）
    lang = getattr(args, "lang", None)
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
            print(t("cli_init.next_steps"))
            for s in result.next_steps:
                print(f"  {s}")
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    """直近 archive/promote/mv バッチを復元（UX W1 / P12 CLI）。"""
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
        "refs_restored": list(res.refs_restored),
        "states_restored": list(res.states_restored),
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not res.batch_id:
            print(t("cli_init.undo_nothing"))
            return 1
        print(t("cli_init.undo_restored", batch_id=res.batch_id, count=len(res.restored)))
        for e in res.restored:
            print(f"  {e.dst} -> {e.src}")
        if res.refs_restored:
            print(t("cli_init.undo_refs_restored", count=len(res.refs_restored)))
            for ref in res.refs_restored:
                print(f"  {ref['path']} ({ref['field']})")
        if res.states_restored:
            print(t("cli_init.undo_states_restored", count=len(res.states_restored)))
            for change in res.states_restored:
                print(f"  {change['path']} ({change['field']}: {change['from']} -> {change['to']})")
        if res.failed:
            print(t("cli_init.undo_failed", count=len(res.failed)))
            for f in res.failed:
                print(f"  {f}")
    return 1 if res.failed and not res.restored else 0
