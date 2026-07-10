"""AI memory ファイルの簡易スキャン（UX W4 / P49 骨格）。

看板には混ぜない。``~/.claude/memory`` 等の既知パスを読み取り専用で列挙する。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_MEMORY_GLOBS = (
    "~/.claude/memory/**/*.md",
    "~/.claude/projects/**/memory/**/*.md",
)


@dataclass
class MemoryFile:
    path: str
    name: str
    age_days: int
    size: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryScanResult:
    roots_tried: list[str] = field(default_factory=list)
    files: list[MemoryFile] = field(default_factory=list)
    stale_over_days: int = 90

    def to_dict(self) -> dict[str, Any]:
        return {
            "roots_tried": list(self.roots_tried),
            "files": [f.to_dict() for f in self.files],
            "stale_over_days": self.stale_over_days,
            "stale_count": sum(1 for f in self.files if f.age_days >= self.stale_over_days),
        }


def scan_memory(
    *,
    paths: list[str] | None = None,
    stale_days: int = 90,
) -> MemoryScanResult:
    now = datetime.now(timezone.utc).timestamp()
    result = MemoryScanResult(stale_over_days=stale_days)
    candidates: list[Path] = []
    if paths:
        for p in paths:
            candidates.append(Path(p).expanduser())
    else:
        base = Path.home() / ".claude" / "memory"
        result.roots_tried.append(str(base))
        if base.is_dir():
            candidates.extend(sorted(base.rglob("*.md")))
        # MEMORY.md 単体
        mem = Path.home() / ".claude" / "MEMORY.md"
        if mem.is_file():
            candidates.append(mem)
            result.roots_tried.append(str(mem))

    seen: set[str] = set()
    for path in candidates:
        try:
            if not path.is_file():
                continue
            key = path.resolve().as_posix()
            if key in seen:
                continue
            seen.add(key)
            st = path.stat()
            age = max(0, int((now - st.st_mtime) / 86400))
            result.files.append(MemoryFile(
                path=key,
                name=path.name,
                age_days=age,
                size=st.st_size,
            ))
        except OSError:
            continue
    result.files.sort(key=lambda f: f.age_days, reverse=True)
    return result
