"""capture が使う LLM provider 抽象。

本 plan では実 API（OpenAI / Anthropic）の呼び出しは行わない。
:class:`MockLLM` だけを実装し、provider 選択の切替え点をクリーンに用意しておく。
実 provider の追加は別 plan で行う（環境変数からの認証読み取り含む）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from ..config import DEFAULT_DUE_OFFSET_DAYS, TemplateSection
from ..templates_gen import _append_template_sections, okf_frontmatter
from .models import Draft, DraftKind


@dataclass
class LLMRequest:
    """LLM に渡す抽出依頼。"""

    conversation: str
    project_hint: str | None = None
    max_drafts: int = 5
    offset_days: dict[str, int] | None = None
    template_sections: Mapping[str, tuple[TemplateSection, ...]] | None = None


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
            if any(k in stripped for k in ("決定", "やる", "TODO", "実装する")):
                kind = DraftKind.PLAN.value
            elif any(k in stripped for k in ("バグ", "不具合", "壊れ", "エラー")):
                kind = DraftKind.BUGFIX.value
            elif any(k in stripped for k in ("保留", "あとで")):
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
        raise NotImplementedError(
            f"LLM provider '{norm}' は未実装です。現時点では provider='mock' のみ使えます"
            " (実 provider 追加は別 plan で対応)"
        )
    raise ValueError(f"未知の LLM provider: {provider}")


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
    )
    body = front + _render_body_seed(
        kind, title, body_seed, template_sections=template_sections
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
) -> str:
    """kind に応じた必須セクションを持つテンプレ本文を組む。"""
    if kind == DraftKind.PLAN.value:
        label = "[計画]"
        sections = "## context配分\n\n| C | 種別 | 内容 | 備考/注意点 |\n|---|---|---|---|\n| C1 | planned | <TODO> | — |\n\n## 概要\n\n<TODO: " + seed + ">\n"
    elif kind == DraftKind.BUGFIX.value:
        label = "[実行中]"
        sections = (
            "## context配分\n\n| C | 種別 | 内容 | 備考/注意点 |\n|---|---|---|---|\n| C1 | planned | <TODO> | — |\n\n"
            "## 症状\n\n<TODO: " + seed + ">\n\n"
            "## 根本原因\n\n<TODO>\n\n"
            "## 修正内容\n\n<TODO>\n\n"
            "## 変更ファイル\n\n<TODO>\n\n"
            "## 検証\n\n<TODO>\n\n"
            "## 備忘\n\n<TODO>\n"
        )
    else:
        label = "[保留]"
        sections = (
            "## 概要\n\n<TODO: " + seed + ">\n\n"
            "## 保留理由\n\n<TODO>\n\n"
            "## 着手条件\n\n<TODO>\n"
        )
    body = f"# {label} {title}\n\n{sections}"
    return _append_template_sections(
        body, doc_type=kind, template_sections=template_sections
    )
