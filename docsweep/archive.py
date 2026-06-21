"""archive 移送と移動ログ JSONL。

- 場所は config 可変（既定 archive/）。同名衝突は連番（_2）。
- 移動ログ {ts, op, project, status, src, dst} を JSONL 追記（eject/復元の土台）。
- 同一ボリューム前提に依存しない（shutil.move で吸収）。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from .models import MoveLogEntry

MOVE_LOG_NAME = "moves.jsonl"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def dedupe_path(dst: Path) -> Path:
    """衝突時に stem に _2, _3... を付けて空きパスを返す。"""
    if not dst.exists():
        return dst
    stem, suffix, parent = dst.stem, dst.suffix, dst.parent
    n = 2
    while True:
        cand = parent / f"{stem}_{n}{suffix}"
        if not cand.exists():
            return cand
        n += 1


def move_log_path(root: Path) -> Path:
    return root / ".docsweep" / MOVE_LOG_NAME


def append_move_log(root: Path, entry: MoveLogEntry) -> None:
    p = move_log_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")


def archive_file(
    *,
    src: Path,
    project_dir: Path,
    archive_dir: str,
    root: Path,
    project: str,
    status: str | None,
    op: str = "archive",
    dry_run: bool = False,
) -> Path:
    """src を project_dir/<archive_dir>/ へ移送し、移動ログに記録する。移送先を返す。"""
    src = src.resolve()
    dest_dir = (project_dir / archive_dir).resolve()
    dst = dedupe_path(dest_dir / src.name)

    if dry_run:
        return dst

    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    append_move_log(
        root,
        MoveLogEntry(
            ts=_now_iso(), op=op, project=project, status=status,
            src=src.as_posix(), dst=dst.as_posix(),
        ),
    )
    return dst
