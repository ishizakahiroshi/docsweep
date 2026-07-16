"""frontmatter ↔ H1 食い違い修理（UX W2 / P37）。

``prefer=h1``: H1 を正として frontmatter status を合わせる。
``prefer=frontmatter``: FM を正として H1 を合わせる。
``both`` は h1 と同じ（H1 優先）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .config import Config
from .engine import scan_records
from .interactive import _update_frontmatter_status
from .models import Flag
from .services.frontmatter import read_frontmatter
from .services.status import update_status

Prefer = Literal["h1", "frontmatter", "both"]


@dataclass
class ConflictFix:
    path: str
    fixed: bool
    detail: str
    old_h1: str | None = None
    old_fm: str | None = None
    new_value: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConflictFixResult:
    items: list[ConflictFix]

    def to_dict(self) -> dict[str, Any]:
        return {"items": [i.to_dict() for i in self.items]}


def list_conflicts(config: Config) -> list[dict]:
    records = scan_records(config)
    out = []
    for r in records:
        if Flag.CONFLICT.value not in (r.flags or []):
            continue
        out.append({
            "path": r.path,
            "project": r.project,
            "state": r.state,
            "state_label": r.state_label,
            "state_source": r.state_source,
            "title": r.title,
        })
    return out


def fix_conflicts(
    config: Config,
    *,
    prefer: Prefer = "h1",
    paths: list[str] | None = None,
    dry_run: bool = False,
) -> ConflictFixResult:
    """conflict フラグ付きファイルを修理する。"""
    records = scan_records(config)
    want = set(paths) if paths else None
    items: list[ConflictFix] = []
    prefer_h1 = prefer in ("h1", "both")

    for r in records:
        if Flag.CONFLICT.value not in (r.flags or []):
            continue
        if want is not None and r.path not in want:
            continue

        path = Path(r.path)
        if not path.is_file():
            items.append(ConflictFix(path=r.path, fixed=False, detail="file missing"))
            continue

        h1_label = r.state_label
        fm_status = None
        try:
            fm = read_frontmatter(path) or {}
            fm_status = fm.get("status")
        except Exception:  # noqa: BLE001
            fm_status = None

        project_root = Path(r.project_root) if r.project_root else path.parent

        if prefer_h1:
            if not r.state:
                items.append(ConflictFix(
                    path=r.path, fixed=False, detail="H1 state unknown",
                    old_h1=h1_label, old_fm=str(fm_status) if fm_status else None,
                ))
                continue
            if dry_run:
                items.append(ConflictFix(
                    path=r.path, fixed=True,
                    detail="dry-run: frontmatter status ← H1",
                    old_h1=h1_label, old_fm=str(fm_status) if fm_status else None,
                    new_value=r.state,
                ))
                continue
            ok = _update_frontmatter_status(path, r.state)
            items.append(ConflictFix(
                path=r.path, fixed=ok,
                detail="frontmatter status ← H1 state" if ok else "no frontmatter status line",
                old_h1=h1_label, old_fm=str(fm_status) if fm_status else None,
                new_value=r.state,
            ))
        else:
            # frontmatter の status を state key に解決して H1 を書き換え
            raw = str(fm_status) if fm_status is not None else ""
            matched = config.state_model.match(raw) if raw else None
            target_key = matched.key if matched else (raw or None)
            if not target_key:
                items.append(ConflictFix(
                    path=r.path, fixed=False, detail="no frontmatter status",
                    old_h1=h1_label, old_fm=None,
                ))
                continue
            if dry_run:
                items.append(ConflictFix(
                    path=r.path, fixed=True,
                    detail="dry-run: H1 ← frontmatter",
                    old_h1=h1_label, old_fm=str(fm_status),
                    new_value=target_key,
                ))
                continue
            try:
                update_status(
                    path, target_key,
                    project_root=project_root,
                    config=config,
                    file_type=r.type,
                )
                items.append(ConflictFix(
                    path=r.path, fixed=True,
                    detail="H1 ← frontmatter status",
                    old_h1=h1_label, old_fm=str(fm_status),
                    new_value=target_key,
                ))
            except Exception as e:  # noqa: BLE001
                items.append(ConflictFix(path=r.path, fixed=False, detail=str(e)))

    return ConflictFixResult(items=items)
