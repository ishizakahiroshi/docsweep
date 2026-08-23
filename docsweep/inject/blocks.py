"""docsweep 管理ブロックの検出・整形ユーティリティ。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from ..atomic import write_atomic
from .manifest import MANIFEST_PATH

MARK_START = "<!-- docsweep:managed:start -->"
MARK_END = "<!-- docsweep:managed:end -->"


def _block_hash(inner: str) -> str:
    return hashlib.sha256(inner.strip().encode("utf-8")).hexdigest()[:16]


def _wrap(inner: str) -> str:
    return f"{MARK_START}\n{inner.rstrip()}\n{MARK_END}"


def _find_block(text: str) -> tuple[int, int] | None:
    spans = _find_all_blocks(text)
    return spans[0] if spans else None


def _find_all_blocks(text: str) -> list[tuple[int, int]]:
    """管理ブロック（START..END）を全て列挙する。"""
    spans: list[tuple[int, int]] = []
    i = 0
    while True:
        start = text.find(MARK_START, i)
        if start == -1:
            break
        end_marker = text.find(MARK_END, start + len(MARK_START))
        if end_marker == -1:
            break
        end = end_marker + len(MARK_END)
        spans.append((start, end))
        i = end
    return spans


def _inner_of(text: str, span: tuple[int, int]) -> str:
    segment = text[span[0]:span[1]]
    return segment[len(MARK_START):-len(MARK_END)].strip()


def _private_backup(path: Path, content: bytes) -> Path:
    """手編集退避を repo 直下ではなく docsweep の private 管理領域へ保存する。"""
    path_key = os.path.normcase(str(path.resolve())).encode("utf-8", errors="replace")
    digest = hashlib.sha256(path_key).hexdigest()[:16]
    backup_dir = MANIFEST_PATH.parent / "inject-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    candidate = backup_dir / f"{digest}-{path.name}.bak"
    suffix = 2
    while candidate.exists() and candidate.read_bytes() != content:
        candidate = backup_dir / f"{digest}-{path.name}-{suffix}.bak"
        suffix += 1
    if not candidate.exists():
        candidate.write_bytes(content)
    return candidate


def _strip_managed_blocks(
    path: Path, prev_hash: str | None, result: Any, *, dry_run: bool
) -> bool:
    """ファイルから全管理ブロックを除去する。手編集は private 領域へ退避する。"""
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        result.warnings.append(
            f"{path.name}: UTF-8として読み取れないため除去を中止しました ({exc.reason})"
        )
        return False
    spans = _find_all_blocks(text)
    if not spans:
        return False
    if prev_hash and _block_hash(_inner_of(text, spans[0])) != prev_hash:
        result.warnings.append(f"{path.name}: 手編集を検出。private backup を作成しました。")
        if not dry_run:
            _private_backup(path, text.encode("utf-8"))
    new_text = text
    for span in reversed(spans):
        before = new_text[:span[0]].rstrip("\n")
        after = new_text[span[1]:].lstrip("\n")
        new_text = before + ("\n\n" if before and after else "") + after
    new_text = new_text.rstrip("\n")
    new_text = new_text + "\n" if new_text else ""
    if not dry_run:
        write_atomic(path, new_text, encoding="utf-8")
    return True
