"""``docsweep stale`` — 陳腐化の前倒し検知。

``review_status: draft`` で 14 日 / ``review`` で 7 日 / ``published`` + ``last_reviewed`` が
90 日経過したものを列挙する。閾値は ``Config.stale_thresholds`` で上書き可能。

判定の基準日:
- draft / review: mtime からの経過日数（``FileRecord.age_days``）
- published: ``last_reviewed`` 日付からの経過日数（last_reviewed 未設定なら age_days を使う）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .config import Config
from .engine import run_scan
from .models import FileRecord


@dataclass
class StaleItem:
    path: str
    project: str
    type: str | None
    review_status: str
    days_over: int  # しきい値を超えた日数（しきい値 + days_over = 経過日数）
    threshold: int
    last_reviewed: str | None
    title: str | None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "project": self.project,
            "type": self.type,
            "review_status": self.review_status,
            "days_over": self.days_over,
            "threshold": self.threshold,
            "last_reviewed": self.last_reviewed,
            "title": self.title,
        }


@dataclass
class StaleResult:
    items: list[StaleItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"items": [i.to_dict() for i in self.items]}


def _days_since(date_str: str | None, *, today: date | None = None) -> int | None:
    """``YYYY-MM-DD`` 文字列から今日までの経過日数。パース失敗で None。"""
    if not date_str:
        return None
    try:
        d = date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return None
    base = today or date.today()
    return max(0, (base - d).days)


def _evaluate(rec: FileRecord, thresholds: dict[str, int], today: date) -> StaleItem | None:
    rs = (rec.review_status or "").strip().lower()
    if not rs:
        return None
    threshold = thresholds.get(rs)
    if threshold is None:
        return None
    if rs == "published":
        # published は last_reviewed が無ければ mtime にフォールバック（運用初期の救済）。
        elapsed = _days_since(rec.last_reviewed, today=today)
        if elapsed is None:
            elapsed = rec.age_days
    else:
        elapsed = rec.age_days
    if elapsed < threshold:
        return None
    return StaleItem(
        path=rec.path,
        project=rec.project,
        type=rec.type,
        review_status=rs,
        days_over=elapsed - threshold,
        threshold=threshold,
        last_reviewed=rec.last_reviewed,
        title=rec.title,
    )


def find_stale(
    config: Config,
    *,
    project: str | None = None,
    today: date | None = None,
    thresholds: dict[str, int] | None = None,
) -> StaleResult:
    """``review_status`` 別しきい値を超過したファイルを列挙する。"""
    used_thresholds = dict(config.stale_thresholds)
    if thresholds:
        used_thresholds.update(thresholds)
    base = today or datetime.now().astimezone().date()
    from .engine import scan_records as _scan_records

    records = _scan_records(config, project=project)
    items: list[StaleItem] = []
    for rec in records:
        if project and rec.project != project:
            continue
        item = _evaluate(rec, used_thresholds, base)
        if item is not None:
            items.append(item)
    items.sort(key=lambda i: (-i.days_over, i.path))
    return StaleResult(items=items)
