"""``update_due`` — frontmatter ``due:`` の書き換え + postpone_count インクリメント。

- 行単位の正規表現置換で本文を触らない（atomic.update_line 経由）
- frontmatter が無いファイルには YAML frontmatter ブロックを新規挿入
- 相対指定（'today' / '+1d' / '+1w' / '+1m'）も解釈
- 過去日を指定された場合は警告のみ・拒否はしない（「やり忘れ」列に居続けるだけ）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ..state import increment_postpone
from .frontmatter import read_frontmatter_text, update_frontmatter_field

_RELATIVE_RE = re.compile(r"^\s*\+\s*(\d+)\s*([dwmy])\s*$", re.IGNORECASE)
_ABSOLUTE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*$")
# plan_activity-summary.md C1: --since/--until は過去方向（-3d 等）も受けるため符号付き。
_RELATIVE_SIGNED_RE = re.compile(r"^\s*([+-])\s*(\d+)\s*([dwmy])\s*$", re.IGNORECASE)


class DueParseError(ValueError):
    """new_due 文字列が解釈不能なときに発生。"""


@dataclass
class UpdateDueResult:
    path: str
    old_due: str | None
    new_due: str
    postpone_count: int
    warning: str | None
    new_mtime: float

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "old_due": self.old_due,
            "new_due": self.new_due,
            "postpone_count": self.postpone_count,
            "warning": self.warning,
            "new_mtime": self.new_mtime,
        }


def resolve_due(spec: str, *, today: date | None = None) -> date:
    """``spec`` から ``date`` を返す。``today`` / ``+1d`` / ``+1w`` / ``+1m`` / 絶対指定対応。

    ``+1m`` は単純に 30 日加算（カレンダー月計算しない）。``+1y`` は 365 日加算。
    """
    today = today or date.today()
    s = spec.strip()
    if s.lower() == "today":
        return today
    m_abs = _ABSOLUTE_RE.match(s)
    if m_abs:
        return date.fromisoformat(m_abs.group(1))
    m_rel = _RELATIVE_RE.match(s)
    if m_rel:
        n = int(m_rel.group(1))
        unit = m_rel.group(2).lower()
        days = {"d": n, "w": n * 7, "m": n * 30, "y": n * 365}[unit]
        return today + timedelta(days=days)
    raise DueParseError(f"new_due を解釈できません: {spec!r}")


def resolve_relative_offset(spec: str, *, today: date | None = None) -> date:
    """``resolve_due`` の姉妹関数。符号付き相対オフセット（``+Nd``/``-Nd``/``+Nw``/``-Nw`` 等）と
    ``today`` / 絶対指定（``YYYY-MM-DD``）を解釈する。

    ``resolve_due`` は due 更新（未来日が基本）用に正のオフセットのみを受けるため、
    ``activity --since``/``--until`` の過去方向レンジ指定用にこちらを新設する。
    ``update_due`` からは呼ばれず、``resolve_due`` 自体の挙動には影響しない。
    """
    today = today or date.today()
    s = spec.strip()
    if s.lower() == "today":
        return today
    m_abs = _ABSOLUTE_RE.match(s)
    if m_abs:
        return date.fromisoformat(m_abs.group(1))
    m_rel = _RELATIVE_SIGNED_RE.match(s)
    if m_rel:
        sign = -1 if m_rel.group(1) == "-" else 1
        n = int(m_rel.group(2))
        unit = m_rel.group(3).lower()
        days = {"d": n, "w": n * 7, "m": n * 30, "y": n * 365}[unit]
        return today + timedelta(days=sign * days)
    raise DueParseError(f"日付指定を解釈できません: {spec!r}")


def _read_current_due(text: str) -> str | None:
    """frontmatter から現在の due 値を抽出（無ければ / 空値なら None）。"""
    data, _body = read_frontmatter_text(text)
    raw = data.get("due")
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def update_due(
    abs_path: Path,
    new_due_spec: str,
    *,
    project_root: Path,
    reason: str | None = None,
    expected_mtime: float | None = None,
    warn_threshold: int = 3,
    alert_threshold: int = 5,
) -> UpdateDueResult:
    """frontmatter の due を書き換え、postpone_count を 1 増やす。

    Args:
        abs_path: 書き込み対象 MD の絶対パス（呼び出し側でスコープ境界検証済み前提）
        new_due_spec: ``YYYY-MM-DD`` または ``today`` / ``+1d`` / ``+1w`` / ``+1m``
        project_root: state.json の置き場（プロジェクト境界）
        reason: 任意・先送り理由（due_history に記録）
        expected_mtime: 楽観ロック用（None で強制上書き）
        warn_threshold / alert_threshold: warning メッセージのしきい値
    """
    target = resolve_due(new_due_spec)
    new_due = target.isoformat()
    text_before = Path(abs_path).read_text(encoding="utf-8", newline="")
    old_due = _read_current_due(text_before)

    updated = update_frontmatter_field(
        Path(abs_path), "due", new_due, expected_mtime=expected_mtime
    )
    new_mtime = updated.new_mtime
    count = increment_postpone(
        Path(project_root), Path(abs_path),
        from_due=old_due, to_due=new_due, reason=reason,
    )

    warning: str | None = None
    if count >= alert_threshold:
        warning = (
            f"postpone_count={count} は廃止候補しきい値（{alert_threshold}）に達しました。"
        )
    elif count >= warn_threshold:
        warning = (
            f"postpone_count={count} は警告しきい値（{warn_threshold}）に達しました。"
        )
    if target < date.today():
        past_msg = "指定日付は過去日です（やり忘れ列に残ります）。"
        warning = f"{warning} {past_msg}".strip() if warning else past_msg

    return UpdateDueResult(
        path=Path(abs_path).resolve().as_posix(),
        old_due=old_due,
        new_due=new_due,
        postpone_count=count,
        warning=warning,
        new_mtime=new_mtime,
    )
