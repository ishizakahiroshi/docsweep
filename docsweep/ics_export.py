"""due 付き open カードを .ics に export（UX W4 / P54）。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from .config import Config
from .engine import scan_records


def _ics_escape(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def build_ics(config: Config) -> str:
    records = scan_records(config)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//docsweep//UX//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for r in records:
        if r.state in {"done", "discarded"}:
            continue
        if not r.due:
            continue
        try:
            d = date.fromisoformat(r.due)
        except ValueError:
            continue
        uid = f"docsweep-{abs(hash(r.path))}@local"
        summary = _ics_escape(f"{r.state_label or ''} {r.title or Path(r.path).name}".strip())
        desc = _ics_escape(r.path)
        day = d.strftime("%Y%m%d")
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{day}",
            f"DTEND;VALUE=DATE:{day}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{desc}",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def write_ics(config: Config, out: Path) -> Path:
    out = Path(out)
    out.write_text(build_ics(config), encoding="utf-8", newline="")
    return out
