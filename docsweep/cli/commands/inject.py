"""CLI command handlers: inject."""

from __future__ import annotations

import argparse
from pathlib import Path

from ...i18n import t


def cmd_inject(args: argparse.Namespace) -> int:
    from ...inject import inject, inject_global

    tag = t("cli_inject.dry_run_tag") if args.dry_run else ""
    if getattr(args, "is_global", False):
        r = inject_global(
            agent=args.agent, target=args.global_target, lang=args.lang, dry_run=args.dry_run,
        )
        print(
            t(
                "cli_inject.inject_result",
                project=r.project,
                tag=tag,
                written=r.written or "-",
                skipped=r.skipped or "-",
            )
        )
        for w in r.warnings:
            print(t("cli_inject.warning", warning=w))
        return 0

    r = inject(
        Path(args.project), preset=args.preset, write_yaml=not args.no_yaml,
        include_guidance=not args.no_guidance, lang=args.lang, dry_run=args.dry_run,
    )
    print(
        t(
            "cli_inject.inject_result",
            project=r.project,
            tag=tag,
            written=r.written or "-",
            skipped=r.skipped or "-",
        )
    )
    if r.yaml_path:
        print(f"  .docsweep.yaml: {r.yaml_path}")
    for w in r.warnings:
        print(t("cli_inject.warning", warning=w))
    return 0


def cmd_eject(args: argparse.Namespace) -> int:
    from ...inject import eject, eject_global, list_injected

    def _report(r) -> None:
        tag = t("cli_inject.dry_run_tag") if args.dry_run else ""
        yaml = " +yaml" if getattr(r, "purged_yaml", False) else ""
        print(
            t(
                "cli_inject.eject_result",
                project=r.project,
                tag=tag,
                removed=r.removed or "-",
                yaml=yaml,
            )
        )
        for w in r.warnings:
            print(t("cli_inject.warning", warning=w))

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
