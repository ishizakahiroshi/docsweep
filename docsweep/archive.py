"""archive 移送と移動ログ JSONL。

- 場所は config 可変（既定 archive/）。同名衝突は連番（_2）。
- 移動ログ {ts, op, project, status, src, dst} を JSONL 追記（eject/復元の土台）。
- 同一ボリューム前提に依存しない（shutil.move で吸収）。
"""

from __future__ import annotations

import json
import os
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


def _reserve_destination(dst: Path) -> Path:
    """Atomically reserve an archive filename without replacing an existing file.

    ``dedupe_path`` alone has a check-then-use race: two archive workers can
    both observe the same free name and the later ``shutil.move`` can replace
    the first document.  Creating the candidate with ``O_EXCL`` makes the
    choice itself atomic.  The empty placeholder belongs to this operation
    and is replaced only after the reservation succeeds.
    """
    stem, suffix, parent = dst.stem, dst.suffix, dst.parent
    n = 1
    while True:
        candidate = dst if n == 1 else parent / f"{stem}_{n}{suffix}"
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            n += 1
            continue
        os.close(fd)
        return candidate


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
    batch_id: str | None = None,
) -> Path:
    """src を project_dir/<archive_dir>/ へ移送し、移動ログに記録する。移送先を返す。

    ``batch_id`` を与えると JSONL の同名フィールドに記録され、後で Undo で逆引きできる。
    """
    src = src.resolve()
    dest_dir = (project_dir / archive_dir).resolve()

    if dry_run:
        return dedupe_path(dest_dir / src.name)

    dest_dir.mkdir(parents=True, exist_ok=True)
    dst = _reserve_destination(dest_dir / src.name)
    try:
        shutil.move(str(src), str(dst))
    except Exception:
        # Remove only the placeholder created above.  A failed cross-volume
        # copy can leave a partial destination, which is also not a valid
        # archive entry and must not be mistaken for a successful move.
        try:
            dst.unlink()
        except OSError:
            pass
        raise
    append_move_log(
        root,
        MoveLogEntry(
            ts=_now_iso(), op=op, project=project, status=status,
            src=src.as_posix(), dst=dst.as_posix(), batch_id=batch_id,
        ),
    )
    return dst
