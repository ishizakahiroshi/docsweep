"""意図 → コマンド ルーティング（UX W2 / P28）。

静的マップ。LLM は呼ばない。自然言語の短いフレーズをサブコマンドに落とす。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class IntentRoute:
    intent: str
    command: str
    args: list[str]
    reason: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# (キーワード群, command, extra args, reason)
_RULES: list[tuple[tuple[str, ...], str, list[str], str]] = [
    (("昨日", "yesterday", "何やった", "振り返り"), "activity", ["--date", "yesterday"], "昨日触った md を日付で見る"),
    (("今日", "today", "朝", "brief", "1個", "一件"), "brief", [], "朝の今日の 1 個"),
    (("横断", "cross", "腐", "ヘルス"), "cross", [], "プロジェクト横断ヘルス"),
    (("doctor", "診断", "壊", "ヘルスチェック", "動かない"), "doctor", [], "環境ヘルスチェック"),
    (("init", "初期", "セットアップ", "導入"), "init", [], "初回セットアップ"),
    (("undo", "戻す", "取り消", "元に戻"), "undo", [], "直近 archive を戻す"),
    (("day open", "デイオープン", "日を開"), "day", ["open"], "1 日の開始"),
    (("day close", "デイクローズ", "日を閉", "今日を閉"), "day", ["close"], "1 日の終了"),
    (("archive", "掃除", "sweep", "片付ける"), "sweep", ["--dry-run"], "完了/廃止の移送（まず dry-run）"),
    (("promote", "様子見", "昇格"), "promote", ["--dry-run"], "様子見の release sweep"),
    (("capture", "会話", "書き出"), "capture", [], "会話から md 候補を抽出"),
    (("triage", "要判断", "判断"), "triage", [], "要判断一覧"),
    (("index", "同期", "index-sync"), "index-sync", [], "SQLite 索引同期"),
    (("serve", "看板", "web", "ボード"), "serve", [], "Web 看板起動"),
    (("graph", "関連", "グラフ"), "graph", [], "related グラフ"),
    (("resurrect", "復活", "類似"), "resurrect", [], "archive 類似検索"),
    (("search", "探す", "find", "検索"), "find", [], "メタデータ検索"),
    (("inject", "導線", "注入"), "inject", ["--global"], "AI 導線注入"),
]


def route_intent(text: str) -> IntentRoute:
    """短い自然言語をサブコマンドにマップする。"""
    raw = (text or "").strip()
    if not raw:
        return IntentRoute(
            intent=raw,
            command="doctor",
            args=[],
            reason="空の意図 → doctor で環境確認を推奨",
            confidence=0.2,
        )
    low = raw.lower()
    best: IntentRoute | None = None
    best_hits = 0
    for keys, cmd, args, reason in _RULES:
        hits = sum(1 for k in keys if k.lower() in low or k in raw)
        if hits > best_hits:
            best_hits = hits
            best = IntentRoute(
                intent=raw,
                command=cmd,
                args=list(args),
                reason=reason,
                confidence=min(0.95, 0.4 + 0.2 * hits),
            )
    if best is None:
        return IntentRoute(
            intent=raw,
            command="brief",
            args=[],
            reason="一致なし → 朝の入口 brief を既定推奨",
            confidence=0.25,
        )
    return best
