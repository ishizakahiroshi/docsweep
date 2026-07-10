"""グローバル除外リスト（UX W2 / P39 · plan_per-project-toggle）。

``~/.docsweep/excluded.json`` にプロジェクト root の絶対パス（posix）を持つ。
デフォルトは全 ON。将来 ``.docsweep.yaml`` の ``enabled: false`` は
``is_excluded`` を OR 結合で拡張する。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

EXCLUDED_PATH = Path.home() / ".docsweep" / "excluded.json"


def _norm(p: str | Path) -> str:
    try:
        return Path(os.path.realpath(str(p))).resolve().as_posix()
    except OSError:
        return Path(str(p)).as_posix().replace("\\", "/")


def excluded_path(override: Path | None = None) -> Path:
    return Path(override) if override is not None else EXCLUDED_PATH


def load_excluded(*, path: Path | None = None) -> set[str]:
    p = excluded_path(path)
    if not p.is_file():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, dict):
        return set()
    raw = data.get("excluded") or []
    if not isinstance(raw, list):
        return set()
    return {_norm(x) for x in raw if x}


def save_excluded(excluded: Iterable[str], *, path: Path | None = None) -> Path:
    p = excluded_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "excluded": sorted({_norm(x) for x in excluded if x}),
    }
    p.write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return p


def is_excluded(project_root: str | Path, *, path: Path | None = None) -> bool:
    return _norm(project_root) in load_excluded(path=path)


def disable_project(project_root: str | Path, *, path: Path | None = None) -> set[str]:
    s = load_excluded(path=path)
    s.add(_norm(project_root))
    save_excluded(s, path=path)
    return s


def enable_project(project_root: str | Path, *, path: Path | None = None) -> set[str]:
    s = load_excluded(path=path)
    s.discard(_norm(project_root))
    save_excluded(s, path=path)
    return s


def filter_docs_by_excluded(docs: list, *, path: Path | None = None) -> list:
    """ScannedDoc リストから除外プロジェクトを落とす。"""
    excl = load_excluded(path=path)
    if not excl:
        return docs
    out = []
    for d in docs:
        root = getattr(getattr(d, "record", None), "project_root", None) or ""
        if _norm(root) in excl:
            continue
        out.append(d)
    return out


def filter_records_by_excluded(records: list, *, path: Path | None = None) -> list:
    excl = load_excluded(path=path)
    if not excl:
        return records
    return [r for r in records if _norm(getattr(r, "project_root", "") or "") not in excl]


def list_known_projects(config) -> list[dict]:
    """スキャンで見えるプロジェクト + 除外状態（生 scan・除外適用前）。"""
    from .scan import scan as raw_scan

    docs = raw_scan(config)
    by_root: dict[str, dict] = {}
    excl = load_excluded()
    for d in docs:
        root = d.record.project_root
        key = _norm(root)
        if key not in by_root:
            by_root[key] = {
                "name": d.record.project,
                "root": key,
                "enabled": key not in excl,
                "open_approx": 0,
            }
        if d.record.state not in {"done", "discarded"}:
            by_root[key]["open_approx"] += 1
    for e in excl:
        if e not in by_root:
            by_root[e] = {
                "name": Path(e).name,
                "root": e,
                "enabled": False,
                "open_approx": 0,
            }
    return sorted(by_root.values(), key=lambda x: x["name"].lower())
