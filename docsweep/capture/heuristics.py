"""LLM を使わずに会話履歴から草案を拾うヒューリスティック。

LLM 経路の前段として、決定事項マーカー（「決定」「TODO」「バグ」等）を含む段落を
切り出し、最低限の Draft を作る。LLM が無くても CLI / MCP の口は動く。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from ..config import TemplateSection
from .llm import _make_draft, has_marker
from .models import Draft, DraftKind

# 決定事項マーカー（段落単位で拾う）は terms.json の ``capture.markers.*``。
# 照合のしかた（部分一致 / 語単位）は ``llm.has_marker`` を参照。


def _split_paragraphs(text: str) -> list[str]:
    """1 行空きで段落を切る。連続改行は 1 つの区切りに丸める。"""
    chunks = re.split(r"\n\s*\n", text)
    return [c.strip() for c in chunks if c.strip()]


def _classify_paragraph(para: str) -> str | None:
    if has_marker(para, "capture.markers.bugfix"):
        return DraftKind.BUGFIX.value
    if has_marker(para, "capture.markers.pending"):
        return DraftKind.PENDING.value
    if has_marker(para, "capture.markers.plan"):
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
    text: str,
    *,
    project: str | None = None,
    max_drafts: int = 5,
    offset_days: dict[str, int] | None = None,
    template_sections: Mapping[str, tuple[TemplateSection, ...]] | None = None,
    owner: str | None = None,
    lang: str | None = None,
) -> list[Draft]:
    """LLM 不要のヒューリスティック抽出。``lang`` は草案の本文の言語（None なら表示言語）。"""
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
            offset_days=offset_days,
            template_sections=template_sections,
            owner=owner,
            lang=lang,
        ))
        if len(drafts) >= max_drafts:
            break
    return drafts
