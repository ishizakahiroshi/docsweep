"""new / capture 直前の類似ガード（UX W3 / P45 MVP）。

title/topic の部分一致で現役 open を探す（embedding 不要）。
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .engine import scan_records


def find_similar_open(
    config: Config,
    *,
    topic: str,
    limit: int = 5,
) -> list[dict]:
    needle = (topic or "").strip().lower().replace("_", "-").replace(" ", "-")
    if not needle:
        return []
    hits: list[dict] = []
    for r in scan_records(config):
        if r.state in {"done", "discarded"}:
            continue
        name = Path(r.path).stem.lower()
        title = (r.title or "").lower()
        hay = f"{name} {title}"
        if needle in hay or any(part and part in hay for part in needle.split("-") if len(part) > 3):
            hits.append({
                "path": r.path,
                "title": r.title,
                "state_label": r.state_label,
                "project": r.project,
            })
        if len(hits) >= limit:
            break
    return hits
