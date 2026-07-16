"""CLI command handlers: excluded."""

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

def cmd_review_week(args: argparse.Namespace) -> int:
    """週次レビューサマリ（UX W3 / P19 MVP）。"""
    from ...auto_triage import suggest_transitions
    from ...engine import scan_records
    from ...models import Flag

    cfg = _build_config(args)
    records = scan_records(cfg)
    watching = [r for r in records if r.state == "watching"]
    old_planned = [
        r for r in records
        if r.state == "planned" and (r.age_days or 0) >= 90
    ]
    conflict = [r for r in records if Flag.CONFLICT.value in (r.flags or [])]
    suggestions = suggest_transitions(cfg).suggestions
    payload = {
        "watching_count": len(watching),
        "watching": [
            {"path": r.path, "title": r.title, "age_days": r.age_days}
            for r in watching[:20]
        ],
        "old_planned_count": len(old_planned),
        "old_planned": [
            {"path": r.path, "title": r.title, "age_days": r.age_days}
            for r in old_planned[:20]
        ],
        "conflict_count": len(conflict),
        "suggestion_count": len(suggestions),
        "suggestions": [s.to_dict() for s in suggestions[:20]],
        "hints": [
            "docsweep project list  # 不要プロジェクトを disable",
            "docsweep fix-conflict --list",
            "docsweep auto-triage --suggest",
            "docsweep promote --dry-run",
        ],
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"review-week")
        print(f"  watching: {payload['watching_count']}")
        print(f"  planned≥90d: {payload['old_planned_count']}")
        print(f"  conflict: {payload['conflict_count']}")
        print(f"  auto-triage suggestions: {payload['suggestion_count']}")
        for h in payload["hints"]:
            print(f"  next: {h}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    from ...review import run_review

    return run_review(_build_config(args))


def cmd_config(args: argparse.Namespace) -> int:
    """``~/.docsweep/config.yaml`` の user 設定を CLI から読み書き。"""
    from ...config import (
        SETTABLE_KEYS,
        get_user_setting,
        list_settings,
        set_user_setting,
    )

    if getattr(args, "list_all", False):
        settings = list_settings()
        if getattr(args, "json", False):
            print(json.dumps(settings, ensure_ascii=False, indent=2))
        else:
            for k in sorted(settings):
                v = settings[k]
                print(f"{k} = {v if v is not None else '(未設定)'}")
        return 0
    if getattr(args, "get_key", None):
        key = args.get_key
        try:
            v = get_user_setting(key)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        if getattr(args, "json", False):
            print(json.dumps({key: v}, ensure_ascii=False))
        else:
            print(v if v is not None else "")
        return 0
    if getattr(args, "unset_key", None):
        key = args.unset_key
        try:
            set_user_setting(key, None)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        print(f"unset: {key}")
        return 0
    key = args.key
    value = args.value
    if not key:
        print(f"使い方: docsweep config <key> [<value>]  /  --list  /  --get KEY  /  --unset KEY  （許可キー: {sorted(SETTABLE_KEYS)}）")
        return 2
    if value is None:
        try:
            v = get_user_setting(key)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        print(v if v is not None else "")
        return 0
    try:
        path = set_user_setting(key, value)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"設定: {key} = {value}  ({path})")
    return 0
