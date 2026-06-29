"""capture が扱うデータクラス。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class DraftKind(str, Enum):
    PLAN = "plan"
    BUGFIX = "bugfix"
    PENDING = "pending"


@dataclass
class Draft:
    """会話履歴から抽出された 1 つの草案。ユーザー番号選択で採用される単位。"""

    id: str                 # 短い一意 ID（draft-001 等）
    kind: str               # "plan" | "bugfix" | "pending"
    title: str              # H1 タイトル候補
    body: str               # md 本文（H1 / セクション込み）
    suggested_filename: str  # 例: plan_<slug>.md
    source_hint: str = ""   # 抽出根拠（heuristic キーワード / "llm" など）
    project: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
