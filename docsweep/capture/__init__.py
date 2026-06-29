"""C2 (wings): capture — 会話履歴から plan / bugfix / pending の草案を抽出する上流の双子。

設計の要点:
- Heuristic 経路（LLM 無し）と LLM 経路（mock / 実 provider）の 2 系統
- 草案は :class:`models.Draft` で表現し、ユーザーが番号選択で採用 → frontmatter 付き md を生成
- 実 LLM API（OpenAI/Anthropic）は本 plan では呼ばない（abstract + Mock のみ）
"""

from .models import Draft, DraftKind
from .service import (
    extract_drafts,
    save_drafts,
)

__all__ = ["Draft", "DraftKind", "extract_drafts", "save_drafts"]
