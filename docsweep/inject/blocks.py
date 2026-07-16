"""docsweep 管理ブロックの検出・整形ユーティリティ。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

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


def _strip_managed_blocks(
    path: Path, prev_hash: str | None, result: Any, *, dry_run: bool
) -> bool:
    """ファイルから全管理ブロックを除去する。手編集は .bak へ退避する。"""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    spans = _find_all_blocks(text)
    if not spans:
        return False
    if prev_hash and _block_hash(_inner_of(text, spans[0])) != prev_hash:
        result.warnings.append(f"{path.name}: 手編集を検出。.bak を作成しました。")
        if not dry_run:
            path.with_suffix(path.suffix + ".bak").write_text(text, encoding="utf-8")
    new_text = text
    for span in reversed(spans):
        before = new_text[:span[0]].rstrip("\n")
        after = new_text[span[1]:].lstrip("\n")
        new_text = before + ("\n\n" if before and after else "") + after
    new_text = new_text.rstrip("\n")
    new_text = new_text + "\n" if new_text else ""
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return True
