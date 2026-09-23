"""capture が使う LLM provider 抽象。

本 plan では実 API（OpenAI / Anthropic）の呼び出しは行わない。
:class:`MockLLM` だけを実装し、provider 選択の切替え点をクリーンに用意しておく。
実 provider の追加は別 plan で行う（環境変数からの認証読み取り含む）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from ..config import DEFAULT_DUE_OFFSET_DAYS, TemplateSection
from ..doc_vocab import heading
from ..i18n import SUPPORTED_LANGS, current_lang, t, texts
from ..templates_gen import (
    _append_template_sections,
    _context_table,
    _h1,
    _sections,
    okf_frontmatter,
)
from .models import Draft, DraftKind


@dataclass
class LLMRequest:
    """LLM に渡す抽出依頼。"""

    conversation: str
    project_hint: str | None = None
    max_drafts: int = 5
    offset_days: dict[str, int] | None = None
    template_sections: Mapping[str, tuple[TemplateSection, ...]] | None = None
    owner: str | None = None
    # 草案の本文（見出し・ラベル）の言語。None なら表示言語。
    lang: str | None = None


# 会話を分類するキーワードは言語ごとに terms.json に置き、どの言語の会話も読めるよう
# 全言語の語で照合する。照合のしかたも言語のデータ（``capture.marker_match``）で決める:
# ``substring`` は部分一致（大文字小文字を区別。語の区切りを空白で書かない日本語等）、
# ``word`` は語単位（大文字小文字を無視。英語等）。``word`` 以外は ``substring`` と読む。
def _compile_markers(key: str) -> tuple[tuple[str, ...], tuple[re.Pattern[str], ...]]:
    substrings: dict[str, None] = {}
    words: dict[str, None] = {}
    for lang in SUPPORTED_LANGS:
        style = t("capture.marker_match", lang=lang)
        target = words if style == "word" else substrings
        for item in texts(key, lang=lang):
            target.setdefault(item, None)
    return tuple(substrings), tuple(
        re.compile(rf"\b{re.escape(word.lower())}\b") for word in words
    )


# 段落単位で拾う語（heuristics.py）と、MockLLM が行単位で拾う語。
_MARKER_KEYS = (
    "capture.markers.plan",
    "capture.markers.bugfix",
    "capture.markers.pending",
    "capture.mock_markers.plan",
    "capture.mock_markers.bugfix",
    "capture.mock_markers.pending",
)
_MARKERS = {key: _compile_markers(key) for key in _MARKER_KEYS}


def has_marker(text: str, key: str) -> bool:
    """``text`` に ``key``（``capture.markers.*`` 等）のキーワードがあるか。"""
    substrings, words = _MARKERS[key]
    if any(word in text for word in substrings):
        return True
    lowered = text.lower()
    return any(pattern.search(lowered) for pattern in words)


class LLMClient(Protocol):
    """capture が使う最小プロトコル。"""

    def extract(self, request: LLMRequest) -> list[Draft]: ...


class MockLLM:
    """テスト・オフライン用のダミー実装。

    会話テキストの先頭 5 行から「決定された / TODO / バグ」キーワードを拾って Draft を返す。
    実 LLM 相当の高品質抽出は行わないが、CLI / Web / MCP の口を試すには十分。
    """

    def extract(self, request: LLMRequest) -> list[Draft]:
        drafts: list[Draft] = []
        for _i, line in enumerate(request.conversation.splitlines()[:20]):
            stripped = line.strip()
            if not stripped:
                continue
            kind: str | None = None
            if has_marker(stripped, "capture.mock_markers.plan"):
                kind = DraftKind.PLAN.value
            elif has_marker(stripped, "capture.mock_markers.bugfix"):
                kind = DraftKind.BUGFIX.value
            elif has_marker(stripped, "capture.mock_markers.pending"):
                kind = DraftKind.PENDING.value
            if kind is None:
                continue

            title = stripped[:60].replace("\n", " ")
            drafts.append(_make_draft(
                idx=len(drafts) + 1,
                kind=kind,
                title=title,
                body_seed=stripped,
                source_hint="llm:mock",
                project=request.project_hint,
                offset_days=request.offset_days,
                template_sections=request.template_sections,
                owner=request.owner,
                lang=request.lang,
            ))
            if len(drafts) >= request.max_drafts:
                break
        return drafts


def get_llm(provider: str | None = None) -> LLMClient:
    """provider 名から LLMClient を返す factory。

    実 provider は別 plan。現状は "mock" / None のいずれも MockLLM を返す。
    "openai" / "anthropic" は将来用にエラーで案内する（黙ってモックに落とすと
    ユーザーが本物を呼んでいると誤認するため）。
    """
    norm = (provider or "mock").strip().lower()
    if norm in ("", "mock"):
        return MockLLM()
    if norm in ("openai", "anthropic"):
        raise NotImplementedError(t("capture.llm.not_implemented", provider=norm))
    raise ValueError(t("capture.llm.unknown_provider", provider=provider))


def _slugify(title: str) -> str:
    """日本語混在のタイトルから安全なファイル名 slug を作る。"""
    import re

    # 日本語以外の制御文字 / 記号を除去
    cleaned = re.sub(r"[^\w぀-ゟ゠-ヿ一-鿿 -]", "", title)
    cleaned = cleaned.strip().replace(" ", "-").replace("　", "-")
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:48] or "draft"


def _make_draft(
    *,
    idx: int,
    kind: str,
    title: str,
    body_seed: str,
    source_hint: str,
    project: str | None,
    offset_days: dict[str, int] | None = None,
    template_sections: Mapping[str, tuple[TemplateSection, ...]] | None = None,
    owner: str | None = None,
    lang: str | None = None,
) -> Draft:
    from .models import Draft as _Draft

    slug = _slugify(title)
    if kind == DraftKind.BUGFIX.value:
        # bugfix は日付付き命名規約
        from datetime import date
        today = date.today().isoformat()
        fname = f"bugfix_{slug}_{today}.md"
    else:
        fname = f"{kind}_{slug}.md"

    # capture 経由でも ``docsweep new`` と同じ OKF frontmatter を載せる。
    # 生まれ方で type / docsweep_state / due の有無が変わると queue の扱いがズレる。
    front = okf_frontmatter(
        kind,
        offset_days=DEFAULT_DUE_OFFSET_DAYS if offset_days is None else offset_days,
        owner=owner,
    )
    body = front + _render_body_seed(
        kind, title, body_seed, template_sections=template_sections, lang=lang or current_lang()
    )
    return _Draft(
        id=f"draft-{idx:03d}",
        kind=kind,
        title=title,
        body=body,
        suggested_filename=fname,
        source_hint=source_hint,
        project=project,
    )


def _render_body_seed(
    kind: str,
    title: str,
    seed: str,
    *,
    template_sections: Mapping[str, tuple[TemplateSection, ...]] | None = None,
    lang: str,
) -> str:
    """kind に応じた必須セクションを持つテンプレ本文を組む（見出しは ``docsweep new`` と同じ語彙）。"""
    seed_line = f"<TODO: {seed}>"
    if kind == DraftKind.PLAN.value:
        body = (
            _h1("planned", title, lang)
            + _context_table(lang)
            + f"## {heading('summary', lang)}\n\n{seed_line}\n"
        )
    elif kind == DraftKind.BUGFIX.value:
        body = (
            _h1("in-progress", title, lang)
            + _context_table(lang)
            + f"## {heading('symptoms', lang)}\n\n{seed_line}\n\n"
            + _sections(("root_cause", "fix", "changed_files", "verification", "notes"), lang)
        )
    else:
        body = (
            _h1("pending", title, lang)
            + f"## {heading('summary', lang)}\n\n{seed_line}\n\n"
            + _sections(("pending_reason", "resume_when"), lang)
        )
    return _append_template_sections(
        body, doc_type=kind, template_sections=template_sections
    )
