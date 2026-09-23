"""CLI handlers for workspace-wide release tracking migration."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..interactive import stdin_is_interactive


def _ci_enabled() -> bool:
    value = os.environ.get("CI", "").strip().lower()
    return value not in {"", "0", "false", "no", "off"}


def _interactive_review_allowed(args: argparse.Namespace) -> bool:
    """Return whether ``--review`` may use the human TTY wizard.

    JSON, auto, explicit apply, manifests, redirected stdin, and CI must all
    remain deterministic and prompt-free.  This boundary is deliberately in
    the CLI layer; the migration/domain functions never ask for input.
    """
    if not getattr(args, "review", False):
        return False
    if getattr(args, "json", False) or getattr(args, "auto", False):
        return False
    if getattr(args, "apply", False) or getattr(args, "apply_manifest", None):
        return False
    if _ci_enabled():
        return False
    return stdin_is_interactive()


def _prompt(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return "q"


def _choice(prompt: str) -> str:
    """Read a review choice; an empty/closed input is a safe cancellation."""
    while True:
        value = _prompt(prompt).lower()
        if value in {"y", "yes", "1", "enable", "enabled"}:
            return "yes"
        if value in {"n", "no", "0", "skip", "disabled"}:
            return "no"
        if value in {"q", "quit", "cancel", "c", ""}:
            return "cancel"
        print("y（enable）/ n（skip）/ q（cancel）のいずれかを入力してください", file=sys.stderr)


def _review_target(repo: dict, args: argparse.Namespace) -> tuple[str | None, bool]:
    """Resolve a target for the initial wizard, returning (value, cancelled)."""
    value = getattr(args, "default_target", None)
    if value:
        return str(value).strip(), False
    inventory = repo.get("inventory") or {}
    if not inventory.get("target_release_missing"):
        return None, False
    value = _prompt("default target_release（必須、q でキャンセル）: ")
    if value.lower() in {"q", "quit", "cancel", "c"}:
        return None, True
    from ...release import validate_release_label

    while True:
        if not value:
            print(
                "target未設定の文書があるため default target_release は必須です",
                file=sys.stderr,
            )
            value = _prompt("default target_release（必須、q でキャンセル）: ")
            if value.lower() in {"q", "quit", "cancel", "c"}:
                return None, True
            continue
        try:
            return validate_release_label(value, field="default_target"), False
        except ValueError as exc:
            print(f"default target_release が不正です: {exc}", file=sys.stderr)
            value = _prompt("default target_release（再入力、q でキャンセル）: ")
            if value.lower() in {"q", "quit", "cancel", "c", ""}:
                return None, True


def _initial_review_candidate(repo: dict) -> bool:
    """Identify a safe, genuinely unconfigured repository for first-run UX."""
    before = repo.get("before") or {}
    tracking = before.get("release_tracking") or {}
    inventory = repo.get("inventory") or {}
    if tracking.get("mode") is not None:
        return False
    if repo.get("status") in {"disabled", "failed", "excluded"}:
        return False
    if inventory.get("configured_work_dir_valid") is not True:
        return False
    blocked_reasons = {"unreadable", "unsafe_archive_dir", "nested_reparse_point"}
    return not any(
        item.get("reason") in blocked_reasons
        for item in repo.get("review_items") or []
        if isinstance(item, dict)
    )


def _target_actions(repo: dict, target: str) -> None:
    """Add queue-scoped target actions after the user chose a default."""
    from ...workspace_migration import _file_precondition

    actions = [item for item in repo.get("actions") or [] if isinstance(item, dict)]
    existing = {str(item.get("path")) for item in actions}
    for document in repo.get("documents") or []:
        if not isinstance(document, dict):
            continue
        path = str(document.get("path", ""))
        if (
            path
            and document.get("readable")
            and not document.get("target_release")
            and not document.get("never_archive")
            and path not in existing
        ):
            actions.append(
                {
                    "path": path,
                    "field": "target_release",
                    "value": target,
                    "scope": "queue",
                    "precondition": _file_precondition(
                        Path(path), field="target_release"
                    ),
                    "reason": "interactive_default_target",
                    "evidence": "interactive first-run configuration",
                    "confidence": "high",
                }
            )
    repo["actions"] = actions


def _set_initial_enable(repo: dict, target: str | None) -> None:
    before = repo.get("before") or {}
    before_tracking = before.get("release_tracking") or {}
    archive_dir = str(
        (repo.get("after") or {}).get("archive_dir")
        or before.get("archive_dir")
        or "docs/local/archive"
    )
    tracking = {
        "mode": "enabled",
        "tag_pattern": before_tracking.get("tag_pattern") or "semver",
        "archive_group_by": before_tracking.get("archive_group_by") or "minor",
    }
    inventory = repo.get("inventory") or {}
    if inventory.get("target_release_missing") and not target:
        raise ValueError(
            "target未設定の文書があるrepoはdefault target_releaseなしでenableできません"
        )
    if target:
        tracking["default_target"] = target
        _target_actions(repo, target)
    repo["config_action"] = {
        "archive_partition": "release",
        "archive_dir": archive_dir,
        "release_tracking": tracking,
        "precondition": repo.get("config_precondition"),
        "reason": "interactive_enable_release_tracking",
    }
    repo["status"] = "ready"
    repo["review_approved"] = True
    repo["review_resolution"] = "enabled"
    after = dict(repo.get("after") or {})
    after_tracking = dict(after.get("release_tracking") or {})
    after_tracking.update(tracking)
    after_tracking["source"] = "interactive_review"
    after["release_tracking"] = after_tracking
    after["archive_partition"] = "release"
    after["archive_dir"] = archive_dir
    repo["after"] = after


def _set_initial_skip(repo: dict) -> None:
    before = repo.get("before") or {}
    archive_dir = str(
        before.get("archive_dir")
        or (repo.get("after") or {}).get("archive_dir")
        or "docs/local/archive"
    )
    repo["actions"] = []
    repo["config_action"] = {
        "archive_partition": before.get("archive_partition") or "flat",
        "archive_dir": archive_dir,
        "release_tracking": {"mode": "disabled"},
        "precondition": repo.get("config_precondition"),
        "reason": "interactive_skip_release_tracking",
    }
    repo["status"] = "ready"
    repo["review_approved"] = True
    repo["review_resolution"] = "disabled"
    after = dict(repo.get("after") or {})
    after_tracking = dict(after.get("release_tracking") or {})
    after_tracking["mode"] = "disabled"
    after_tracking["source"] = "interactive_review"
    after["release_tracking"] = after_tracking
    repo["after"] = after


def _interactive_review_manifest(
    manifest: dict, args: argparse.Namespace
) -> dict | None:
    """Resolve first-run choices and return None when the user cancels."""
    candidates = [
        repo
        for repo in manifest.get("repositories") or []
        if isinstance(repo, dict) and _initial_review_candidate(repo)
    ]
    if candidates:
        for repo in candidates:
            root = repo.get("root", "<unknown repository>")
            choice = _choice(
                f"{root} は release tracking 未設定です。enable しますか? "
                "[y=enable / n=skip / q=cancel]: "
            )
            if choice == "cancel":
                return None
            if choice == "no":
                _set_initial_skip(repo)
                continue
            target, cancelled = _review_target(repo, args)
            if cancelled:
                return None
            _set_initial_enable(repo, target)
    has_work = any(
        isinstance(repo, dict)
        and repo.get("status") == "ready"
        and (repo.get("actions") or repo.get("config_action"))
        for repo in manifest.get("repositories") or []
    )
    if not has_work:
        return manifest
    print("\n最終的に適用する内容:")
    _print_review(manifest)
    choice = _choice(
        "review 内容を適用しますか? [y=apply / n=cancel / q=cancel]: "
    )
    return manifest if choice == "yes" else None


def _refresh_manifest_fingerprint(manifest: dict) -> None:
    from ...workspace_migration import _manifest_fingerprint

    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = _manifest_fingerprint(manifest)


def _has_applyable_work(manifest: dict) -> bool:
    return any(
        isinstance(repo, dict)
        and repo.get("status") == "ready"
        and (repo.get("actions") or repo.get("config_action"))
        for repo in manifest.get("repositories") or []
    )


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
        config_action = repo.get("config_action")
        if isinstance(config_action, dict):
            tracking = config_action.get("release_tracking") or {}
            print(
                "    config: "
                f"mode={tracking.get('mode')} "
                f"default_target={tracking.get('default_target')} "
                f"group={tracking.get('archive_group_by')} "
                f"archive_dir={config_action.get('archive_dir')}"
            )
        for action in actions[:20]:
            if not isinstance(action, dict):
                continue
            print(
                "    action: "
                f"{action.get('field')}={action.get('value')} "
                f"{action.get('path')} ({action.get('reason')})"
            )
        if len(actions) > 20:
            print(f"    action: ... and {len(actions) - 20} more")
        archived = inventory.get("archived_documents", 0)
        if archived:
            print(f"    archived release documents: {archived}")
    for item in (manifest.get("excluded") or [])[:20]:
        print(f"  excluded: {item.get('path')} ({item.get('reason')})")


def cmd_workspace_migrate(args: argparse.Namespace) -> int:
    from ...config import load_config
    from ...workspace_migration import apply_manifest, migrate_release_tracking

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
    interactive_review = _interactive_review_allowed(args)
    requested_manifest_path = (
        Path(args.manifest) if getattr(args, "manifest", None) else None
    )
    try:
        result = migrate_release_tracking(
            [Path(root) for root in root_values],
            excludes=exclude_values,
            default_target=getattr(args, "default_target", None),
            # A canceled interactive review must not even create the optional
            # manifest output.  Non-interactive callers retain the historical
            # explicit ``--manifest`` behavior.
            manifest_path=None if interactive_review else requested_manifest_path,
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
    if interactive_review:
        print("適用前の棚卸し:")
        _print_review(manifest)
        reviewed = _interactive_review_manifest(manifest, args)
        if reviewed is None:
            result["review"] = {"mode": "interactive", "status": "cancelled"}
        elif _has_applyable_work(reviewed):
            _refresh_manifest_fingerprint(reviewed)
            try:
                if requested_manifest_path is not None:
                    from ...workspace_migration import _write_json

                    _write_json(requested_manifest_path, reviewed)
                applied = apply_manifest(
                    reviewed,
                    journal_path=(
                        Path(args.journal) if getattr(args, "journal", None) else None
                    ),
                    global_path=(
                        Path(args.config) if getattr(args, "config", None) else None
                    ),
                )
            except (OSError, UnicodeError, ValueError) as exc:
                print(f"workspace migration: {exc}", file=sys.stderr)
                return 2
            apply_counts = applied.get("counts") or {}
            review_status = (
                "needs_review"
                if apply_counts.get("failed", 0)
                or apply_counts.get("needs_review", 0)
                else "applied"
            )
            result = {
                "mode": "apply",
                "manifest": reviewed,
                "apply": applied,
                "review": {"mode": "interactive", "status": review_status},
            }
        else:
            result["review"] = {"mode": "interactive", "status": "no_changes"}

    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if getattr(args, "review", False) and not interactive_review:
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
        review = result.get("review") or {}
        if review.get("status") == "cancelled":
            print("  review: cancelled (no changes)")

    apply_result = result.get("apply") or {}
    apply_counts = apply_result.get("counts") or {}
    return 2 if apply_counts.get("failed", 0) or apply_counts.get("needs_review", 0) else 0


__all__ = ["cmd_workspace_migrate"]
