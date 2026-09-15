"""archive 移送と移動ログ JSONL。

- 場所は config 可変（既定 archive/）。同名衝突は連番（_2）。
- 移動ログ {ts, op, project, status, src, dst} を JSONL 追記（eject/復元の土台）。
- 同一ボリューム前提に依存しない（shutil.move で吸収）。
"""

from __future__ import annotations

import json
import importlib
import os
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Iterator

from .models import MoveLogEntry

MOVE_LOG_NAME = "moves.jsonl"


class ArchiveTransactionError(OSError):
    """A move or its log entry could not be committed atomically."""

    def __init__(self, details: dict):
        self.details = details
        super().__init__(json.dumps(details, ensure_ascii=False))


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


@contextmanager
def _move_log_lock(root: Path) -> Iterator[BinaryIO]:
    """Serialize move-log append/rollback across archive workers."""
    lock_path = move_log_path(root).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            if lock_file.seek(0, os.SEEK_END) == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield lock_file
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl = importlib.import_module("fcntl")

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield lock_file
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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
    strict_collision: bool = False,
) -> Path:
    """src を project_dir/<archive_dir>/ へ移送し、移動ログに記録する。移送先を返す。

    ``batch_id`` を与えると JSONL の同名フィールドに記録され、後で Undo で逆引きできる。
    """
    src = src.resolve()
    dest_dir = (project_dir / archive_dir).resolve()

    if dry_run:
        return dedupe_path(dest_dir / src.name)

    dest_dir.mkdir(parents=True, exist_ok=True)
    requested = dest_dir / src.name
    if strict_collision:
        try:
            fd = os.open(requested, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise FileExistsError(f"archive destination already exists: {requested}") from exc
        os.close(fd)
        dst = requested
    else:
        dst = _reserve_destination(requested)
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
    log_path = move_log_path(root)
    with _move_log_lock(root):
        log_existed = log_path.is_file()
        try:
            log_size = log_path.stat().st_size if log_existed else 0
        except OSError as exc:
            preflight_rollback_error: str | None = None
            try:
                if dst.exists() and not src.exists():
                    shutil.move(str(dst), str(src))
            except Exception as rollback_exc:
                preflight_rollback_error = str(rollback_exc)
            raise ArchiveTransactionError(
                {
                    "reason": (
                        "rollback_failed"
                        if preflight_rollback_error
                        else "move_log_preflight_failed"
                    ),
                    "error": str(exc),
                    "source": src.as_posix(),
                    "destination": dst.as_posix(),
                    "log_path": log_path.as_posix(),
                    "rolled_back": preflight_rollback_error is None,
                    "source_exists": src.exists(),
                    "destination_exists": dst.exists(),
                    **(
                        {"rollback_error": preflight_rollback_error}
                        if preflight_rollback_error
                        else {}
                    ),
                }
            ) from exc
        try:
            append_move_log(
                root,
                MoveLogEntry(
                    ts=_now_iso(), op=op, project=project, status=status,
                    src=src.as_posix(), dst=dst.as_posix(), batch_id=batch_id,
                ),
            )
        except Exception as exc:
            rollback_error: str | None = None
            log_rollback_error: str | None = None
            try:
                if log_existed:
                    with log_path.open("r+b") as fh:
                        fh.truncate(log_size)
                elif log_path.exists():
                    log_path.unlink()
            except Exception as log_exc:
                log_rollback_error = str(log_exc)
            try:
                if dst.exists():
                    if src.exists():
                        raise FileExistsError(
                            f"rollback destination already exists: {src}"
                        )
                    shutil.move(str(dst), str(src))
                elif not src.exists():
                    raise FileNotFoundError(
                        f"moved file is missing from both source and destination: {src}"
                    )
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)
            details = {
                "reason": "rollback_failed" if rollback_error or log_rollback_error else "move_log_failed",
                "error": str(exc),
                "source": src.as_posix(),
                "destination": dst.as_posix(),
                "log_path": log_path.as_posix(),
                "rolled_back": rollback_error is None and log_rollback_error is None,
                "source_exists": src.exists(),
                "destination_exists": dst.exists(),
            }
            if rollback_error:
                details["rollback_error"] = rollback_error
            if log_rollback_error:
                details["log_rollback_error"] = log_rollback_error
            raise ArchiveTransactionError(details) from exc
    return dst
