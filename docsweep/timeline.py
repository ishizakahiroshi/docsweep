"""``docsweep timeline <topic>`` — topic 関連 md を時系列で並べる。

日付の優先順位:
1. frontmatter の ``last_reviewed``
2. frontmatter の ``claimed_at``
3. git log（``git log -1 --format=%cs <path>``）
4. mtime（最終手段）

``--format markdown | plain | json``。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config
from .engine import run_scan
from .models import FileRecord
from .services.frontmatter import read_frontmatter


def _frontmatter_data(path: str) -> dict:
    return read_frontmatter(Path(path)) or {}


def _git_last_date(path: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", path],
            cwd=str(Path(path).parent),
            capture_output=True, text=True, timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    s = (proc.stdout or "").strip()
    return s or None


def _coerce_date(v) -> str | None:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except (TypeError, ValueError):
            return None
    s = str(v).strip()
    return s or None


def _resolve_date(rec: FileRecord) -> tuple[str, str]:
    """``(date_str, source)`` を返す。date_str は YYYY-MM-DD 想定。"""
    fm = _frontmatter_data(rec.path)
    d = _coerce_date(fm.get("last_reviewed"))
    if d:
        return d, "last_reviewed"
    d = _coerce_date(fm.get("claimed_at"))
    if d:
        return d, "claimed_at"
    d = _git_last_date(rec.path)
    if d:
        return d, "git"
    # 4 段フォールバック最終段: rec.mtime が None（壊れた stat / archive 索引の一部）でも
    # timeline 全体を落とさない。activity.py が同じく mtime を defensive に扱っているのと対称。
    if rec.mtime:
        try:
            d = datetime.fromtimestamp(rec.mtime).astimezone().strftime("%Y-%m-%d")
            return d, "mtime"
        except (OSError, OverflowError, ValueError):
            pass
    return "", "unknown"


@dataclass
class TimelineEntry:
    date: str
    source: str  # last_reviewed / claimed_at / git / mtime
    path: str
    project: str
    type: str | None
    state_label: str | None
    title: str | None

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "source": self.source,
            "path": self.path,
            "project": self.project,
            "type": self.type,
            "state_label": self.state_label,
            "title": self.title,
        }


@dataclass
class TimelineResult:
    topic: str
    entries: list[TimelineEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "entries": [e.to_dict() for e in self.entries],
        }


def _matches_topic(rec: FileRecord, topic_norm: str) -> bool:
    """ファイル名・H1 タイトルのいずれかに topic を含むかで判定（大文字小文字無視）。"""
    name = Path(rec.path).name.lower()
    title = (rec.title or "").lower()
    return topic_norm in name or topic_norm in title


def build_timeline(config: Config, topic: str) -> TimelineResult:
    """topic を含む plan / bugfix / pending を時系列に並べる。"""
    from .engine import scan_records

    records = scan_records(config)
    norm = topic.strip().lower()
    out: list[TimelineEntry] = []
    type_order = {"plan": 0, "bugfix": 1, "pending": 2}
    matches: list[tuple[str, str, FileRecord]] = []
    for rec in records:
        if not _matches_topic(rec, norm):
            continue
        d, src = _resolve_date(rec)
        matches.append((d, src, rec))
    matches.sort(key=lambda x: (x[0], type_order.get(x[2].type or "", 9), Path(x[2].path).name))
    for d, src, rec in matches:
        out.append(TimelineEntry(
            date=d, source=src,
            path=rec.path, project=rec.project, type=rec.type,
            state_label=rec.state_label, title=rec.title,
        ))
    return TimelineResult(topic=topic, entries=out)


def render_timeline(result: TimelineResult, *, fmt: str = "markdown") -> str:
    if fmt not in ("markdown", "plain", "json"):
        raise ValueError(f"未知の format: {fmt}")
    if fmt == "json":
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    lines: list[str] = []
    if fmt == "markdown":
        lines.append(f"# timeline: {result.topic}\n")
    else:
        lines.append(f"timeline: {result.topic}")
    if not result.entries:
        lines.append("（該当ファイルなし）")
        return "\n".join(lines)
    for e in result.entries:
        label = e.state_label or "[?]"
        title = f" — {e.title}" if e.title else ""
        if fmt == "markdown":
            lines.append(
                f"- {e.date} ({e.source}) {label} **{e.type or '?'}** "
                f"`{Path(e.path).name}`{title}"
            )
        else:
            lines.append(
                f"{e.date} ({e.source}) {label} {e.type or '?':7} "
                f"{Path(e.path).name}{title}"
            )
    return "\n".join(lines)
