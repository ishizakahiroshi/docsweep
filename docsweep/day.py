"""``docsweep day open|close`` — 1 日の開閉儀式（UX W2 / P18）。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .brief.service import build_brief
from .config import Config
from .engine import scan_records
from .models import FileRecord, Flag


@dataclass
class DayOpenResult:
    mode: str = "open"
    generated_at: str = ""
    today_pick: dict | None = None
    overdue_count: int = 0
    yesterday_done: list[dict] = field(default_factory=list)
    open_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DayCloseResult:
    mode: str = "close"
    generated_at: str = ""
    touched_today: list[dict] = field(default_factory=list)
    incomplete_due: list[dict] = field(default_factory=list)
    suggest_defer: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slim(rec: FileRecord) -> dict:
    return {
        "path": rec.path,
        "project": rec.project,
        "name": rec.path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
        "state": rec.state,
        "state_label": rec.state_label,
        "title": rec.title,
        "due": rec.due,
        "age_days": rec.age_days,
    }


def day_open(config: Config) -> DayOpenResult:
    brief = build_brief(config, all_projects=True)
    today_pick = None
    best = -1.0
    yesterday: list[dict] = []
    open_count = 0
    for p in brief.projects:
        open_count += p.open_count
        yesterday.extend(p.yesterday_done)
        tp = p.today_pick
        if not tp:
            continue
        sc = tp.get("score") or {}
        total = float(sc.get("total", 0.0)) if isinstance(sc, dict) else 0.0
        if today_pick is None or total > best:
            today_pick = tp
            best = total

    records = scan_records(config)
    overdue = sum(
        1 for r in records
        if Flag.OVERDUE_TODO.value in (r.flags or [])
    )
    return DayOpenResult(
        generated_at=_now_iso(),
        today_pick=today_pick,
        overdue_count=overdue,
        yesterday_done=yesterday[:10],
        open_count=open_count,
    )


def day_close(config: Config, *, today: date | None = None) -> DayCloseResult:
    today = today or date.today()
    records = scan_records(config)
    now = datetime.now(timezone.utc).astimezone()
    start = datetime(today.year, today.month, today.day, tzinfo=now.tzinfo).timestamp()
    end = start + 86400

    touched: list[dict] = []
    incomplete: list[dict] = []
    for r in records:
        if r.mtime and start <= r.mtime < end:
            touched.append(_slim(r))
        if r.state in {"done", "discarded"}:
            continue
        if not r.due:
            continue
        try:
            d = date.fromisoformat(r.due)
        except ValueError:
            continue
        if d <= today:
            incomplete.append(_slim(r))

    # 明日送り候補 = incomplete のうち今日 due または overdue
    suggest = list(incomplete)[:20]
    return DayCloseResult(
        generated_at=_now_iso(),
        touched_today=touched[:50],
        incomplete_due=incomplete[:50],
        suggest_defer=suggest,
    )
