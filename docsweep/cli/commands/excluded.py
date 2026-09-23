"""CLI command handlers: excluded."""

from __future__ import annotations

import argparse
import json
import sys

from ...i18n import t
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
    hints: list[str] = [
        t("cli_excluded.hint_project_list"),
        "docsweep fix-conflict --list",
        "docsweep auto-triage --suggest",
        "docsweep promote --due-expired --dry-run",
    ]
    payload = {
        "watching_count": len(watching),
        "watching": [
            {"path": r.path, "title": "[sensitive]" if r.sensitive else r.title, "age_days": r.age_days}
            for r in watching[:20]
        ],
        "old_planned_count": len(old_planned),
        "old_planned": [
            {"path": r.path, "title": "[sensitive]" if r.sensitive else r.title, "age_days": r.age_days}
            for r in old_planned[:20]
        ],
        "conflict_count": len(conflict),
        "suggestion_count": len(suggestions),
        "suggestions": [s.to_dict() for s in suggestions[:20]],
        "hints": hints,
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(t("cli_excluded.review_week_title"))
        print(t("cli_excluded.review_week_watching", count=payload["watching_count"]))
        print(t("cli_excluded.review_week_old_planned", count=payload["old_planned_count"]))
        print(t("cli_excluded.review_week_conflict", count=payload["conflict_count"]))
        print(t("cli_excluded.review_week_suggestions", count=payload["suggestion_count"]))
        for h in hints:
            print(t("cli_excluded.review_week_next", hint=h))
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
                print(f"{k} = {v if v is not None else t('cli_excluded.unset_value')}")
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
        print(t("cli_excluded.unset_done", key=key))
        return 0
    key = args.key
    value = args.value
    if not key:
        print(t("cli_excluded.config_usage", keys=sorted(SETTABLE_KEYS)))
        return 2
    if getattr(args, "from_github", False):
        # GitHub アカウントは 1 つしかなく、リポジトリ単位で上書きできる git config user.name より
        # 識別子として安定している。ただし解決はここ 1 回だけで、生成のたびに gh は叩かない。
        if key != "user.name":
            print(t("cli_excluded.from_github_user_name_only"), file=sys.stderr)
            return 2
        if value is not None:
            print(t("cli_excluded.from_github_with_value"), file=sys.stderr)
            return 2
        from ...services.frontmatter import github_login

        login = github_login()
        if not login:
            print(t("cli_excluded.from_github_failed"), file=sys.stderr)
            return 1
        value = login
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
    print(t("cli_excluded.config_set", name=key, value=value, path=path))
    return 0
