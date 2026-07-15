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

from ..atomic import update_line
from ..detect import _FRONTMATTER_RE
from ..state import increment_postpone

# frontmatter ブロック内で `due:` 行を捉える正規表現。
# `due:\n`（値なし）でも 1 行として掴めるように値部分は `.*` で任意許容にする。
# 以前は `\S.*` を要求していたため、空 `due:` が残った md に対し `_replace_or_insert_due` が
# else 分岐に落ちて重複 `due:` 行を末尾追加していた（PyYAML の last-wins 依存になり fragile）。
# 空白は `\s` ではなく `[ \t]` に絞る（`\s` は `\n` を含み、MULTILINE でも直後の改行を
# 貪欲に取り込んでしまうため group 1 に `\n` が混入する）。
_DUE_LINE_RE = re.compile(r"^([ \t]*due[ \t]*:[ \t]*)(.*)$", re.MULTILINE)
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
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    inner = m.group(1)
    dm = _DUE_LINE_RE.search(inner)
    if not dm:
        return None
    # 新しい regex は空値の `due:` にもヒットするので、値部分（group 2）が空なら None を返す。
    value = (dm.group(2) or "").strip()
    return value or None


def _replace_or_insert_due(text: str, new_due: str) -> str:
    """frontmatter 内の ``due:`` 行を置換、無ければ frontmatter を新設・追記する。

    既存 frontmatter があれば末尾に `due: <値>` を追加。frontmatter 自体が無ければ
    `---\\ndue: <値>\\n---\\n` を最先頭に挿入する。
    """
    fm = _FRONTMATTER_RE.match(text)
    if fm:
        inner = fm.group(1)
        if _DUE_LINE_RE.search(inner):
            new_inner = _DUE_LINE_RE.sub(rf"\g<1>{new_due}", inner, count=1)
        else:
            # frontmatter 末尾に追加（既存行末の改行有無を保つ）。
            sep = "" if inner.endswith("\n") or not inner else "\n"
            new_inner = f"{inner}{sep}due: {new_due}\n"
        # _FRONTMATTER_RE は終端 `---\n` まで含むので、内側を差し替えて再構築する。
        head = "---\n"
        tail = "\n---\n" if not new_inner.endswith("\n") else "---\n"
        return head + new_inner + tail + text[fm.end():]
    # frontmatter 無し → 先頭に挿入。
    return f"---\ndue: {new_due}\n---\n{text}"


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

    def _xform(text: str) -> str:
        return _replace_or_insert_due(text, new_due)

    new_mtime = update_line(Path(abs_path), transform=_xform, expected_mtime=expected_mtime)
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
