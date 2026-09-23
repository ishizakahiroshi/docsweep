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

from ..archive import _move_log_lock, _now_iso, append_move_log
from ..atomic import update_line
from ..config import Config
from ..detect import _H1_LABEL_RE, _H1_RE, mask_code_fences
from ..i18n import t
from ..models import MoveLogEntry
from ..okf import is_okf_lifecycle_status
from ..state import record_label_transition, should_reset_postpone
from .frontmatter import (
    _format_value,
    _read_current,
    _remove_field,
    _replace_or_insert,
    read_frontmatter,
)

# ファイル種別ごとに許可されるラベル（内部 state key）。命名規約と state モデルの直交化。
# - plan は [計画] からスタート可。bugfix は [計画] を持たない（事後記録のため）
# - bugfix / plan ともに「着手中」は [実行中] (in-progress) で共通化
#   （2026-06-23 改修: 旧 bugfix 専用の active=[対応中] を in-progress に統合・state モデル簡素化）
# - bugfix も [保留] (pending) を許可（修正後の中断・寝かせ前の一時停止を表現）
# - pending ファイルは [保留] / [計画] / [廃止] のみ
# 経緯: docs/local/kanban-card-ux-options/index.html、
#       docs/local/archive/v0.5.x/plan_state-tag-orthogonalization.md 改訂版
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
    # 書き換える前の frontmatter の値（行が無かったら None）。undo で書き戻すために持つ。
    old_frontmatter_value: str | None = None

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


def validate_state_transition(file_type: str | None, new_state_key: str) -> None:
    """ファイル種別と new_state_key の組み合わせを検証する。

    書き込みを伴わない下見（``promote --dry-run`` 等）からも呼べるように公開している。
    dry-run がこの検証を通らないと、**本実行では拒否される文書を移送予定として予告**し、
    予告と結果が食い違う（2026-09-11 に実測: 予告 9 件 / 実移送 7 件）。
    判定の正本は ``_ALLOWED_BY_TYPE`` ただ 1 つにする。
    """
    if file_type is None:
        return  # type 不明なら緩く通す
    allowed = _ALLOWED_BY_TYPE.get(file_type)
    if allowed is None:
        return  # ユーザー定義 type は緩く通す
    if new_state_key not in allowed:
        raise StatusValidationError(
            t(
                "services_status.state_not_allowed",
                file_type=file_type,
                state=new_state_key,
                allowed=sorted(allowed),
            )
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
        raise StatusValidationError(t("services_status.watching_days_invalid"))

    sm = config.state_model
    target = sm.by_key(new_state_key)
    if target is None:
        raise StatusValidationError(t("services_status.unknown_state", state=new_state_key))
    if watching_days is not None and new_state_key != "watching":
        raise StatusValidationError(t("services_status.watching_days_requires_watching"))
    validate_state_transition(file_type, new_state_key)

    # 旧ラベル抽出（書き換え前の text を読む）。
    text_before = Path(abs_path).open("r", encoding="utf-8", newline="").read()
    old_token, _title = _current_h1(text_before)
    old_state = sm.match(old_token) if old_token else None
    old_state_key = old_state.key if old_state else None
    old_label = f"[{old_token}]" if old_token else None

    # 新しいラベルは文書にすでにある表記の言語で書く。ラベルが無い・言語が分からない
    # ときは文書の言語（設定の lang、無ければ表示言語）。
    new_label_token = target.label(sm.label_lang(old_token) or config.document_lang())
    new_label = f"[{new_label_token}]"

    def _xform(text: str) -> str:
        m = _H1_RE.search(mask_code_fences(text))
        if not m:
            # H1 が無い場合、本ヘルパは H1 を新設しない（破壊しない方針）。
            raise StatusValidationError(t("services_status.h1_not_found", path=abs_path))
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
    old_frontmatter_value: str | None = None
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
        old_frontmatter_value = _read_current(text_before, frontmatter_field)

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
        old_frontmatter_value=old_frontmatter_value,
    )


# ---- undo: archive の直前に書き換えた状態の記録と書き戻し -------------------------------
#
# promote / discard / Web の [完了] などは、状態を書き換えてから archive へ移す。場所だけ戻すと
# 文書は [完了] のまま queue に戻り、次の sweep でまた移ってしまうので、書き換えた H1 のラベルと
# frontmatter の値を移動ログ（``state_rewrite``）に残し、同じバッチの undo で書き戻す。
# 状態の遷移で期日（due）が変わるのは様子見へ移すときだけなので、archive へ向かう遷移では
# この 2 つを戻せば元の状態になる。state.json の延期回数（postpone_count）は戻さない。

STATE_REWRITE_OP = "state_rewrite"
STATE_RESTORE_OP = "state_restore"
H1_FIELD = "h1"


def _label_token(label: str | None) -> str | None:
    if label and label.startswith("[") and label.endswith("]"):
        return label[1:-1]
    return label


def state_rewrites(result: UpdateStatusResult) -> list[tuple[str, str | None, str | None]]:
    """``update_status`` が書き換えた ``(項目, 前, 後)``。値が変わらなかった項目は含めない。"""
    changes = [(H1_FIELD, _label_token(result.old_label), _label_token(result.new_label))]
    if result.frontmatter_field is not None:
        changes.append(
            (result.frontmatter_field, result.old_frontmatter_value, result.new_state_key)
        )
    return [change for change in changes if change[1] != change[2]]


def log_state_rewrite(
    root: Path, project: str, result: UpdateStatusResult, *, batch_id: str | None
) -> None:
    """archive の直前に書き換えた状態を、移送と同じ batch_id で移動ログへ残す。"""
    if batch_id is None:
        return
    with _move_log_lock(root):
        for field_name, before, after in state_rewrites(result):
            append_move_log(
                root,
                MoveLogEntry(
                    ts=_now_iso(), op=STATE_REWRITE_OP, project=project,
                    status=result.new_state_key, src=result.path, dst=None,
                    batch_id=batch_id, field=field_name, before=before, after=after,
                ),
            )


def log_state_restore(root: Path, project: str, entry: dict, *, batch_id: str) -> None:
    """``state_rewrite`` を書き戻したことを残す（同じ書き戻しを二度しない）。"""
    with _move_log_lock(root):
        append_move_log(
            root,
            MoveLogEntry(
                ts=_now_iso(), op=STATE_RESTORE_OP, project=project, status=None,
                src=str(entry.get("src") or ""), dst=None, batch_id=batch_id,
                field=entry.get("field"), before=entry.get("before"), after=entry.get("after"),
            ),
        )


def revert_state_rewrite(entry: dict) -> None:
    """``state_rewrite`` の 1 行を書き戻す。

    archive の後に人がラベルや値を変えていたら（今の値が書き換えた後の値と違ったら）、
    上書きせずに例外で止める。frontmatter の値は書き換える前の行の右辺をそのまま戻し、
    行が無かった場合は消す。
    """
    path = Path(str(entry.get("src") or ""))
    field_name = str(entry.get("field") or "")
    before = entry.get("before")
    after = entry.get("after")
    if not path.is_file():
        raise FileNotFoundError(t("services_status.restore_target_missing", path=path))
    mtime = path.stat().st_mtime
    text = path.open("r", encoding="utf-8", newline="").read()

    if field_name == H1_FIELD:
        current, _title = _current_h1(text)
        if current != after:
            raise ValueError(t("services_status.label_changed", path=path))

        def _xform(source: str) -> str:
            m = _H1_RE.search(mask_code_fences(source))
            if not m:
                raise StatusValidationError(t("services_status.h1_not_found", path=path))
            captured = m.group(1)
            cr = "\r" if captured.endswith("\r") else ""
            h1 = captured.rstrip("\r").strip()
            lm = _H1_LABEL_RE.match(h1)
            title = lm.group(2).strip() if lm else h1
            new_h1 = f"# [{before}] {title}" if before else f"# {title}"
            return source[: m.start()] + new_h1.rstrip() + cr + source[m.end():]
    else:
        if _read_current(text, field_name) != after:
            raise ValueError(
                t("services_status.state_field_changed", path=path, field=field_name)
            )

        def _xform(source: str) -> str:
            if before is None:
                return _remove_field(source, field_name)
            return _replace_or_insert(source, field_name, f"{field_name}: {before}")

    update_line(path, transform=_xform, expected_mtime=mtime)
