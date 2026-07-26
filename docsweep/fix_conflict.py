"""frontmatter ↔ H1 食い違い修理（UX W2 / P37）。

``prefer=h1``: H1 を正として frontmatter status を合わせる。
``prefer=frontmatter``: FM を正として H1 を合わせる。
``both`` は h1 と同じ（H1 優先）。
"""

from __future__ import annotations

import sys
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


def _norm_path(p: str) -> str:
    """パス比較用の正規化キーを返す。

    ``FileRecord.path`` は ``resolve()`` 済みのスラッシュ区切り posix 絶対パスだが、
    Windows のユーザーが ``--path`` に渡すのはバックスラッシュ区切りや相対パスが自然。
    両辺を同じ ``resolve().as_posix()`` に通して表記ゆれを吸収する（Windows では
    ``resolve()`` が実ファイルの大文字小文字も正規化する）。

    存在しないパスや解決できないパスでも例外にせず、素の posix 表記へ落とす
    （不一致は呼び出し側が警告として報告する）。
    """
    try:
        return Path(p).expanduser().resolve().as_posix()
    except (OSError, ValueError, RuntimeError):
        return Path(p).as_posix()


def fix_conflicts(
    config: Config,
    *,
    prefer: Prefer = "h1",
    paths: list[str] | None = None,
    dry_run: bool = False,
) -> ConflictFixResult:
    """conflict フラグ付きファイルを修理する。

    ``paths`` を渡すと、そこに含まれるファイルだけを対象にする（表記ゆれは
    ``_norm_path`` で吸収）。1 件も conflict に一致しなかった指定は stderr に警告を
    出す — 黙って「修理対象なし」で終わると誤指定に気づけないため。
    """
    records = scan_records(config)
    # 正規化キー -> ユーザーが指定した元の表記（警告で元の表記を見せるため）
    want: dict[str, str] | None = None
    if paths:
        want = {}
        for p in paths:
            want.setdefault(_norm_path(p), p)
    matched_keys: set[str] = set()
    items: list[ConflictFix] = []
    prefer_h1 = prefer in ("h1", "both")

    for r in records:
        if Flag.CONFLICT.value not in (r.flags or []):
            continue
        if want is not None:
            key = _norm_path(r.path)
            if key not in want:
                continue
            matched_keys.add(key)

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

    if want is not None:
        unmatched = [orig for key, orig in want.items() if key not in matched_keys]
        if unmatched:
            print(
                f"warning: --path の指定 {len(unmatched)} 件は conflict 一覧に一致しませんでした"
                "（パス誤り、またはそのファイルに frontmatter ↔ H1 の食い違いが無い）:",
                file=sys.stderr,
            )
            for orig in unmatched:
                print(f"  - {orig}", file=sys.stderr)

    return ConflictFixResult(items=items)
