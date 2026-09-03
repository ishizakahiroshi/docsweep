"""``update_status`` — H1 と docsweep 作業状態の同期 + postpone_count 自動リセット。

- 行単位の正規表現置換で本文を触らない（atomic.update_line 経由）
- 軸 1 のラベル遷移時に postpone_count をリセット（state.should_reset_postpone）
- ``[完了]`` / ``[廃止]`` は terminal 状態として返すが、archive は呼び出し側の責務
- plan / bugfix を ``[様子見]`` へ寝かせるとき、``due.default_offset_days`` の
  ``plan_watching`` / ``bugfix_watching`` から卒業判定期限を設定する
- ファイル種別と無効ラベル組み合わせはバリデーション拒否
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ..atomic import update_line
from ..config import Config
from ..detect import _H1_LABEL_RE, _H1_RE, mask_code_fences
from ..okf import is_okf_lifecycle_status
from ..state import record_label_transition, should_reset_postpone
from .frontmatter import (
    _format_value,
    _replace_or_insert,
    read_frontmatter,
)

# ファイル種別ごとに許可されるラベル（内部 state key）。命名規約と state モデルの直交化。
# - plan は [計画] からスタート可。bugfix は [計画] を持たない（事後記録のため）
# - bugfix / plan ともに「着手中」は [実行中] (in-progress) で共通化
#   （2026-06-23 改修: 旧 bugfix 専用の active=[対応中] を in-progress に統合・state モデル簡素化）
# - bugfix も [保留] (pending) を許可（修正後の中断・寝かせ前の一時停止を表現）
# - pending ファイルは [保留] / [計画] / [廃止] のみ
# 経緯: docs/local/kanban-card-ux-options/index.html、plan_state-tag-orthogonalization.md 改訂版
_ALLOWED_BY_TYPE: dict[str, frozenset[str]] = {
    "plan": frozenset({"planned", "in-progress", "watching", "pending", "done", "discarded"}),
    "bugfix": frozenset({"in-progress", "watching", "pending", "done", "discarded"}),
    "pending": frozenset({"pending", "planned", "discarded"}),
}


class StatusValidationError(ValueError):
    """ファイル種別と new_status の組み合わせが規約違反のときに発生。"""


@dataclass
class UpdateStatusResult:
    path: str
    old_label: str | None
    new_label: str
    new_mtime: float
    old_state_key: str | None
    new_state_key: str
    postpone_count_reset: bool
    archive_triggered: bool
    frontmatter_field: str | None = None
    due_set: str | None = None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "old_label": self.old_label,
            "new_label": self.new_label,
            "new_mtime": self.new_mtime,
            "old_state_key": self.old_state_key,
            "new_state_key": self.new_state_key,
            "postpone_count_reset": self.postpone_count_reset,
            "archive_triggered": self.archive_triggered,
            "frontmatter_field": self.frontmatter_field,
            "due_set": self.due_set,
        }


def _current_h1(text: str) -> tuple[str | None, str | None]:
    """(label_token, title) を H1 から抽出。ラベル無 H1 / H1 無は (None, title|None)。"""
    m = _H1_RE.search(mask_code_fences(text))
    if not m:
        return None, None
    captured = m.group(1)
    h1 = captured.rstrip("\r").strip()
    lm = _H1_LABEL_RE.match(h1)
    if not lm:
        return None, (h1 or None)
    return lm.group(1).strip(), (lm.group(2).strip() or None)


def _validate_for_type(file_type: str | None, new_state_key: str) -> None:
    """ファイル種別と new_state_key の組み合わせを検証する。"""
    if file_type is None:
        return  # type 不明なら緩く通す
    allowed = _ALLOWED_BY_TYPE.get(file_type)
    if allowed is None:
        return  # ユーザー定義 type は緩く通す
    if new_state_key not in allowed:
        raise StatusValidationError(
            f"{file_type} ファイルに状態 '{new_state_key}' は許可されていません "
            f"（許可: {sorted(allowed)}）"
        )


def _watching_due_type(file_type: str | None, abs_path: Path) -> str | None:
    """様子見の卒業期限を自動設定する対象種別を返す。"""
    if file_type in {"plan", "bugfix"}:
        return file_type
    if file_type is None:
        filename = abs_path.name
        if filename.startswith("plan_"):
            return "plan"
        if filename.startswith("bugfix_"):
            return "bugfix"
    return None


def update_status(
    abs_path: Path,
    new_state_key: str,
    *,
    project_root: Path,
    config: Config,
    file_type: str | None = None,
    expected_mtime: float | None = None,
    watching_days: int | None = None,
) -> UpdateStatusResult:
    """H1 ラベルを ``new_state_key`` に書き換える。

    Args:
        abs_path: 書き込み対象 MD の絶対パス（呼び出し側でスコープ境界検証済み前提）
        new_state_key: 内部状態キー（"planned" / "in-progress" / "watching" / "done" / "discarded" / "pending"）
        project_root: state.json の置き場
        config: state_model（ラベル文字列の解決）と lang を持つ
        file_type: "plan" / "bugfix" / "pending"（バリデーション用・None で緩判定）
        expected_mtime: 楽観ロック用
        watching_days: 様子見への遷移時だけ使う一回限りの due 日数上書き（0 以上の整数）
    """
    if (
        watching_days is not None
        and (
            isinstance(watching_days, bool)
            or not isinstance(watching_days, int)
            or watching_days < 0
        )
    ):
        raise StatusValidationError("watching_days は 0 以上の整数で指定してください")

    sm = config.state_model
    target = sm.by_key(new_state_key)
    if target is None:
        raise StatusValidationError(f"未知の state key: {new_state_key}")
    if watching_days is not None and new_state_key != "watching":
        raise StatusValidationError("watching_days は様子見への遷移と組み合わせてください")
    _validate_for_type(file_type, new_state_key)

    new_label_token = target.label(config.lang)
    new_label = f"[{new_label_token}]"

    # 旧ラベル抽出（書き換え前の text を読む）。
    text_before = Path(abs_path).open("r", encoding="utf-8", newline="").read()
    old_token, _title = _current_h1(text_before)
    old_state = sm.match(old_token) if old_token else None
    old_state_key = old_state.key if old_state else None
    old_label = f"[{old_token}]" if old_token else None

    def _xform(text: str) -> str:
        m = _H1_RE.search(mask_code_fences(text))
        if not m:
            # H1 が無い場合、本ヘルパは H1 を新設しない（破壊しない方針）。
            raise StatusValidationError(f"H1 が見つかりません: {abs_path}")
        captured = m.group(1)
        cr = "\r" if captured.endswith("\r") else ""
        h1 = captured.rstrip("\r").strip()
        lm = _H1_LABEL_RE.match(h1)
        title = lm.group(2).strip() if lm else h1
        new_h1 = f"# {new_label} {title}".rstrip()
        return text[: m.start()] + new_h1 + cr + text[m.end():]

    # Keep the human-facing H1 and the machine-readable work state in sync.
    # New OKF documents use docsweep_state; legacy documents whose status is a
    # docsweep state keep receiving the old status update for compatibility.
    frontmatter_field: str | None = None
    frontmatter = read_frontmatter(Path(abs_path))
    if frontmatter is not None:
        frontmatter_field = "docsweep_state"
        if "docsweep_state" not in frontmatter:
            raw_status = frontmatter.get("status")
            if (
                raw_status is not None
                and not is_okf_lifecycle_status(raw_status)
                and config.state_model.match(str(raw_status)) is not None
            ):
                frontmatter_field = "status"

    # plan / bugfix を [様子見] へ移すときは、前の状態の due を卒業期限へ張り替える。
    # - 状態ごとに due の意味が違うため、実際の状態遷移時は既存 due も更新する
    # - 同じ状態への再指定では、人が更新した due を上書きしない
    # - frontmatter が無い legacy ファイルには新設しない（本ヘルパは frontmatter を作らない）
    # - file_type が None（緩判定）でも plan / bugfix のファイル名接頭辞を拾う。呼び出し口に
    #   よって期限が付いたり付かなかったりする方が事故になるため。
    due_to_set: str | None = None
    watching_type = _watching_due_type(file_type, Path(abs_path))
    if (
        watching_type is not None
        and new_state_key == "watching"
        and old_state_key != "watching"
        and frontmatter is not None
    ):
        if watching_days is not None:
            due_to_set = (date.today() + timedelta(days=watching_days)).isoformat()
        else:
            offset = config.due_default_offset_days.get(f"{watching_type}_watching")
            if offset is not None and int(offset) > 0:
                due_to_set = (date.today() + timedelta(days=int(offset))).isoformat()

    def _combined_xform(text: str) -> str:
        updated = _xform(text)
        if frontmatter_field is not None:
            updated = _replace_or_insert(
                updated,
                frontmatter_field,
                _format_value(frontmatter_field, new_state_key),
            )
        if due_to_set is not None:
            updated = _replace_or_insert(updated, "due", _format_value("due", due_to_set))
        return updated

    # H1 and frontmatter are one atomic replacement.  This prevents a malformed
    # frontmatter field or a concurrent edit from leaving the two state sources
    # half-updated.
    new_mtime = update_line(
        Path(abs_path), transform=_combined_xform, expected_mtime=expected_mtime
    )

    reset = should_reset_postpone(
        old_state_key=old_state_key, new_state_key=new_state_key,
    )
    record_label_transition(
        Path(project_root), Path(abs_path),
        from_label=old_label, to_label=new_label, reset_postpone=reset,
    )

    archive_triggered = new_state_key in {"done", "discarded"}

    return UpdateStatusResult(
        path=Path(abs_path).resolve().as_posix(),
        old_label=old_label,
        new_label=new_label,
        due_set=due_to_set,
        new_mtime=new_mtime,
        old_state_key=old_state_key,
        new_state_key=new_state_key,
        postpone_count_reset=reset,
        archive_triggered=archive_triggered,
        frontmatter_field=frontmatter_field,
    )
