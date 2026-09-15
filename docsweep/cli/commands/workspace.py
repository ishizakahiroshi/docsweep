"""CLI handlers for workspace-wide release tracking migration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print_review(manifest: dict) -> None:
    print("workspace release tracking migration")
    print(f"  repositories: {len(manifest.get('repositories') or [])}")
    print(f"  excluded paths: {len(manifest.get('excluded') or [])}")
    for repo in manifest.get("repositories") or []:
        inventory = repo.get("inventory") or {}
        actions = repo.get("actions") or []
        print(
            f"  {repo.get('status')}: {repo.get('root')} "
            f"active={inventory.get('active_documents', 0)} "
            f"missing_target={inventory.get('target_release_missing', 0)} "
            f"actions={len(actions)}"
        )
        for reason in repo.get("diagnostics") or []:
            print(f"    reason: {reason}")
        for item in (repo.get("review_items") or [])[:20]:
            print(
                f"    review: {item.get('kind', 'item')} "
                f"{item.get('path', '')} ({item.get('reason', '')}; "
                f"confidence={item.get('confidence', 'unknown')})"
            )
        archived = inventory.get("archived_documents", 0)
        if archived:
            print(f"    archived release documents: {archived}")
    for item in (manifest.get("excluded") or [])[:20]:
        print(f"  excluded: {item.get('path')} ({item.get('reason')})")


def cmd_workspace_migrate(args: argparse.Namespace) -> int:
    from ...config import load_config
    from ...workspace_migration import migrate_release_tracking

    if getattr(args, "apply", False) and getattr(args, "apply_manifest", None):
        print("workspace migration: --apply と --apply-manifest は同時に指定できません", file=sys.stderr)
        return 2
    config = load_config(
        global_path=(Path(args.config) if getattr(args, "config", None) else None)
    )
    root_values = list(getattr(args, "roots", None) or config.workspace_roots)
    if not root_values and not getattr(args, "apply_manifest", None):
        print(
            "workspace migration: --root または config の workspace.roots が必要です",
            file=sys.stderr,
        )
        return 2
    exclude_values = list(config.workspace_exclude)
    for value in list(getattr(args, "exclude", None) or []):
        if value not in exclude_values:
            exclude_values.append(value)
    try:
        result = migrate_release_tracking(
            [Path(root) for root in root_values],
            excludes=exclude_values,
            default_target=getattr(args, "default_target", None),
            manifest_path=(Path(args.manifest) if getattr(args, "manifest", None) else None),
            apply_manifest_path=(
                Path(args.apply_manifest) if getattr(args, "apply_manifest", None) else None
            ),
            apply=bool(getattr(args, "apply", False)),
            journal_path=(Path(args.journal) if getattr(args, "journal", None) else None),
            global_path=(Path(args.config) if getattr(args, "config", None) else None),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"workspace migration: {exc}", file=sys.stderr)
        return 2

    manifest = result.get("manifest") or {}
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if getattr(args, "review", False):
            _print_review(manifest)
        else:
            print(
                f"workspace migration {result.get('mode')}: "
                f"{len(manifest.get('repositories') or [])} repositories"
            )
        if result.get("apply"):
            counts = result["apply"].get("counts") or {}
            print(
                "  apply: "
                f"applied={counts.get('applied', 0)} "
                f"skipped={counts.get('skipped', 0)} "
                f"needs_review={counts.get('needs_review', 0)} "
                f"failed={counts.get('failed', 0)}"
            )

    apply_result = result.get("apply") or {}
    return 2 if (apply_result.get("counts") or {}).get("failed", 0) else 0


__all__ = ["cmd_workspace_migrate"]
