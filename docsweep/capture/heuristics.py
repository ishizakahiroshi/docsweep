"""LLM を使わずに会話履歴から草案を拾うヒューリスティック。

LLM 経路の前段として、決定事項マーカー（「決定」「TODO」「バグ」等）を含む段落を
切り出し、最低限の Draft を作る。LLM が無くても CLI / MCP の口は動く。
"""

from __future__ import annotations

import re

from .llm import _make_draft
from .models import Draft, DraftKind

# 決定事項マーカー（段落単位で拾う）。
PLAN_MARKERS = ("決定", "やる", "実装する", "TODO", "todo", "次に", "やろう", "対応する")
BUGFIX_MARKERS = ("バグ", "不具合", "壊れ", "再現", "エラー", "落ちる", "出ない")
PENDING_MARKERS = ("保留", "あとで", "ペンディング", "棚上げ")


def _split_paragraphs(text: str) -> list[str]:
    """1 行空きで段落を切る。連続改行は 1 つの区切りに丸める。"""
    chunks = re.split(r"\n\s*\n", text)
    return [c.strip() for c in chunks if c.strip()]


def _classify_paragraph(para: str) -> str | None:
    if any(m in para for m in BUGFIX_MARKERS):
        return DraftKind.BUGFIX.value
    if any(m in para for m in PENDING_MARKERS):
        return DraftKind.PENDING.value
    if any(m in para for m in PLAN_MARKERS):
        return DraftKind.PLAN.value
    return None


def _extract_title(para: str) -> str:
    """段落の最初の意味行をタイトルにする。記号・改行は削る。"""
    for line in para.splitlines():
        s = line.strip()
        if not s:
            continue
        # H1 / 箇条書き記号を剥がす
        s = re.sub(r"^[#\-*>\s]+", "", s).strip()
        if s:
            return s[:60]
    return para[:60]


def extract_drafts_heuristic(
    text: str, *, project: str | None = None, max_drafts: int = 5
) -> list[Draft]:
    """LLM 不要のヒューリスティック抽出。"""
    drafts: list[Draft] = []
    for para in _split_paragraphs(text):
        kind = _classify_paragraph(para)
        if kind is None:
            continue
        title = _extract_title(para)
        drafts.append(_make_draft(
            idx=len(drafts) + 1,
            kind=kind,
            title=title,
            body_seed=para,
            source_hint="heuristic",
            project=project,
        ))
        if len(drafts) >= max_drafts:
            break
    return drafts
