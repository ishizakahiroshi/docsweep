"""CLI handlers for Git release tracking."""

from __future__ import annotations

import argparse
import json
import sys

from ..parser import _build_config


def cmd_release_close(args: argparse.Namespace) -> int:
    """Run the deterministic release close preview/apply operation."""
    from ...i18n import t
    from ...release import close_release

    try:
        config = _build_config(args)
        result = close_release(
            config,
            args.tag,
            dry_run=bool(getattr(args, "dry_run", False)),
            project=getattr(args, "project", None),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"release close: {exc}", file=sys.stderr)
        return 2

    payload = result.to_dict()
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        mode = "dry-run" if result.dry_run else "apply"
        print(f"release close {result.tag} ({mode})")
        for key in (
            "movable",
            "moved",
            "watching",
            "incomplete",
            "target_mismatch",
            "target_unset",
            "never_archive",
            "tag_missing",
            "disabled",
            "released_in_conflict",
            "collision",
            "failed",
        ):
            # 区分名は --json の payload キー（機械向け）。人に見せる名前は辞書から引く
            print(
                t(
                    "cli_release.count_row",
                    label=t(f"cli_release.category.{key}"),
                    count=len(payload.get(key) or []),
                )
            )

    if not result.dry_run and result.moved and config.roots:
        from ...aggregate_index import write_index

        write_index(config)

    # A missing/unrecognized tag is a fail-closed result even in dry-run.  The
    # structured payload is still printed so callers can repair the inputs.
    return (
        2
        if payload.get("tag_missing")
        or payload.get("failed")
        or payload.get("collision")
        else 0
    )


__all__ = ["cmd_release_close"]
