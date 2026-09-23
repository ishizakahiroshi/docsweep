"""CLI command handlers: memory."""

from __future__ import annotations

import argparse
import json


def cmd_memory(args: argparse.Namespace) -> int:
    from ...i18n import t
    from ...memory_scan import scan_memory

    res = scan_memory(
        paths=getattr(args, "paths", None),
        stale_days=int(getattr(args, "stale_days", 90) or 90),
    )
    if getattr(args, "json", False):
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(
        t(
            "cli_memory.summary",
            count=len(res.files),
            days=res.stale_over_days,
            stale=sum(1 for f in res.files if f.age_days >= res.stale_over_days),
        )
    )
    for f in res.files[:30]:
        mark = t("cli_memory.mark_stale") if f.age_days >= res.stale_over_days else "ok"
        print(f"  [{mark}] {f.age_days:>4}d  {f.path}")
    return 0
