"""操作履歴（moves.jsonl を人が読む）（UX W4 / P57）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .services.archive import move_log_path


@dataclass
class HistoryEntry:
    ts: str
    op: str
    project: str
    src: str
    dst: str
    batch_id: str | None = None
    status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HistoryResult:
    entries: list[HistoryEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [e.to_dict() for e in self.entries]}


def read_history(config: Config, *, limit: int = 50) -> HistoryResult:
    entries: list[HistoryEntry] = []
    for root in config.roots:
        p = move_log_path(root)
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries.append(HistoryEntry(
                ts=str(d.get("ts") or ""),
                op=str(d.get("op") or ""),
                project=str(d.get("project") or ""),
                src=str(d.get("src") or ""),
                dst=str(d.get("dst") or ""),
                batch_id=d.get("batch_id"),
                status=d.get("status"),
            ))
    entries.sort(key=lambda e: e.ts, reverse=True)
    return HistoryResult(entries=entries[: max(1, limit)])
