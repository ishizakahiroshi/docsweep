"""``docsweep mv`` — 作業 queue の中で文書を移し、他の文書からの参照も書き換える。

- 移動元と移動先は、どちらも同じ project の作業 queue の中に限る（archive の中は対象外）
- git で追跡されているファイルは動かさない（公開リポジトリに差分を作らない）
- 移動先に同名があれば、何も動かさずに止める
- 移動は移動ログに ``op: "move"`` で残し、``docsweep undo`` で参照の書き換えごと戻せる
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from ...archive import archive_file, new_batch_id
from ...config import Config, load_config
from ...i18n import t
from ...move_refs import Move, ProjectPaths, rewrite_refs
from ...work_queue import find_project_dir


def _log_root(project_root: Path, config: Config) -> Path:
    """移動ログを置くスキャンルート（``docsweep undo`` が読む場所）。"""
    best: Path | None = None
    for raw in config.roots:
        root = Path(raw).resolve()
        try:
            project_root.relative_to(root)
        except ValueError:
            continue
        if best is None or len(root.parts) > len(best.parts):
            best = root
    return best or project_root


def _git_tracked(paths: ProjectPaths, source: Path) -> bool | None:
    """git で追跡されていれば True。確かめられなければ None。"""
    if not (paths.root / ".git").exists():
        return False
    rel = paths.repo_rel(source)
    if rel is None:
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(paths.root), "ls-files", "-z", "--", rel],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return bool(completed.stdout.strip("\0"))


def _in_archive(parts: tuple[str, ...], paths: ProjectPaths) -> bool:
    return any(part in paths.archive_names for part in parts)


def _plan_moves(
    sources: list[str], to: str, paths: ProjectPaths
) -> tuple[list[Move], list[str]]:
    """引数を検査して移動の一覧を作る。問題が 1 つでもあれば何も動かさない。"""
    errors: list[str] = []
    cwd = Path.cwd()
    dest_dir = Path(to) if Path(to).is_absolute() else cwd / to
    dest_parts = paths.queue_parts(dest_dir)
    if dest_parts is None:
        errors.append(t("cli_move.dest_outside_queue", path=to))
    elif _in_archive(dest_parts, paths):
        errors.append(t("cli_move.dest_in_archive", path=to))
    elif dest_dir.exists() and not dest_dir.is_dir():
        errors.append(t("cli_move.dest_not_dir", path=to))
    dest_real = dest_dir.resolve()

    moves: list[Move] = []
    seen_names: dict[str, str] = {}
    for raw in sources:
        source = Path(raw) if Path(raw).is_absolute() else cwd / raw
        if not source.is_file():
            errors.append(t("cli_move.file_not_found", path=raw))
            continue
        parts = paths.queue_parts(source)
        if parts is None:
            errors.append(t("cli_move.source_outside_queue", path=raw))
            continue
        if _in_archive(parts[:-1], paths):
            errors.append(t("cli_move.source_in_archive", path=raw))
            continue
        tracked = _git_tracked(paths, source)
        if tracked is None:
            errors.append(t("cli_move.git_unknown", path=raw))
            continue
        if tracked:
            errors.append(t("cli_move.git_tracked", path=raw))
            continue
        real = source.resolve()
        target = dest_real / real.name
        key = real.name.casefold()
        if key in seen_names:
            errors.append(t("cli_move.duplicate_name", first=seen_names[key], second=raw))
            continue
        seen_names[key] = raw
        if real.parent == dest_real:
            errors.append(t("cli_move.already_there", path=raw))
            continue
        if target.exists():
            errors.append(t("cli_move.dest_exists", path=target.as_posix()))
            continue
        moves.append(Move(real, target))
    return moves, errors


def cmd_mv(args: argparse.Namespace) -> int:
    project_dir = (
        Path(args.project_dir) if getattr(args, "project_dir", None)
        else find_project_dir(cwd=Path.cwd())
    )
    config_path = Path(args.config) if getattr(args, "config", None) else None
    as_json = bool(getattr(args, "json", False))
    try:
        cfg = load_config(project_dir=project_dir, global_path=config_path)
        paths = ProjectPaths(project_dir, cfg)
    except (OSError, ValueError) as exc:
        print(f"mv: {exc}", file=sys.stderr)
        return 2
    moves, errors = _plan_moves(list(args.sources), args.to, paths)
    if errors:
        if as_json:
            print(json.dumps({"errors": errors, "moved": [], "ref_updates": []},
                             ensure_ascii=False, indent=2))
        for error in errors:
            print(f"mv: {error}", file=sys.stderr)
        print(t("cli_move.nothing_moved"), file=sys.stderr)
        return 2

    root = _log_root(paths.root, cfg)
    project = paths.root.name
    batch_id = None if args.dry_run else new_batch_id()
    body = not getattr(args, "no_body", False)
    done: list[Move] = []
    failed: list[dict] = []
    if args.dry_run:
        done = list(moves)
    else:
        for move in moves:
            try:
                archive_file(
                    src=move.src, project_dir=paths.root, archive_dir=str(move.dst.parent),
                    root=root, project=project, status=None, op="move",
                    batch_id=batch_id, strict_collision=True,
                )
            except (OSError, UnicodeError, ValueError) as exc:
                failed.append({"path": move.src.as_posix(), "error": str(exc)})
                continue
            done.append(move)
    refs = rewrite_refs(
        done, project_root=paths.root, config=cfg, root=root, project=project,
        batch_id=batch_id, dry_run=args.dry_run, body=body,
    )
    payload = {
        "dry_run": bool(args.dry_run),
        "batch_id": batch_id,
        "moved": [{"from": m.src.as_posix(), "to": m.dst.as_posix()} for m in done],
        "failed": failed,
        "ref_updates": [u.to_dict() for u in refs.updates],
    }
    if refs.failed:
        payload["ref_failed"] = refs.failed
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        moved_key = "cli_move.moved_dry" if args.dry_run else "cli_move.moved"
        for move in done:
            print(t(moved_key, src=move.src.as_posix(), dst=move.dst.as_posix()))
        body_key = "cli_move.ref_body_dry" if args.dry_run else "cli_move.ref_body"
        field_key = "cli_move.ref_field_dry" if args.dry_run else "cli_move.ref_field"
        for update in refs.updates:
            if update.field == "body":
                print(t(body_key, path=update.path, count=len(update.before)))
            else:
                print(
                    t(
                        field_key,
                        path=update.path,
                        field=update.field,
                        before=update.before,
                        after=update.after,
                    )
                )
        for failure in failed:
            print(
                t("cli_move.failed", path=failure["path"], error=failure["error"]),
                file=sys.stderr,
            )
        for failure in refs.failed:
            print(
                t("cli_move.ref_failed", path=failure.get("path"), error=failure.get("error")),
                file=sys.stderr,
            )
        if not args.dry_run and done:
            print(t("cli_move.undo_hint", root=root.as_posix()))
    return 1 if failed or refs.failed else 0
