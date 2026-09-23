"""意図 → コマンド ルーティング（UX W2 / P28）。

静的マップ。LLM は呼ばない。自然言語の短いフレーズをサブコマンドに落とす。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .i18n import t, variants


@dataclass
class IntentRoute:
    intent: str
    command: str
    args: list[str]
    reason: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# (規則の名前, command, extra args)。上から順に見て、当たった語の数が同じなら先の規則を採る。
# 語は terms.json の ``intent.keywords.<名前>``、理由は ``intent.reason.<名前>``。
# 語は利用者が書く自然文を読むためのデータなので、表示言語で切り替えず全言語の語で照合する
# （英語の表示言語でも日本語の依頼を読む）。
_RULES: list[tuple[str, str, list[str]]] = [
    ("activity", "activity", ["--date", "yesterday"]),
    ("brief", "brief", []),
    ("cross", "cross", []),
    ("doctor", "doctor", []),
    ("init", "init", []),
    ("undo", "undo", []),
    ("day_open", "day", ["open"]),
    ("day_close", "day", ["close"]),
    ("sweep", "sweep", ["--dry-run"]),
    ("promote", "promote", ["--dry-run"]),
    ("capture", "capture", []),
    ("triage", "triage", []),
    ("index_sync", "index-sync", []),
    ("serve", "serve", []),
    ("graph", "graph", []),
    ("resurrect", "resurrect", []),
    ("find", "find", []),
    ("inject", "inject", ["--global"]),
]


def route_intent(text: str) -> IntentRoute:
    """短い自然言語をサブコマンドにマップする。"""
    raw = (text or "").strip()
    if not raw:
        return IntentRoute(
            intent=raw,
            command="doctor",
            args=[],
            reason=t("intent.empty"),
            confidence=0.2,
        )
    low = raw.lower()
    best: IntentRoute | None = None
    best_hits = 0
    for name, cmd, args in _RULES:
        keys = variants(f"intent.keywords.{name}")
        hits = sum(1 for k in keys if k.lower() in low or k in raw)
        if hits > best_hits:
            best_hits = hits
            best = IntentRoute(
                intent=raw,
                command=cmd,
                args=list(args),
                reason=t(f"intent.reason.{name}"),
                confidence=min(0.95, 0.4 + 0.2 * hits),
            )
    if best is None:
        return IntentRoute(
            intent=raw,
            command="brief",
            args=[],
            reason=t("intent.no_match"),
            confidence=0.25,
        )
    return best
