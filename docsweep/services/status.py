"""``update_status`` — H1 ラベル書き換え + postpone_count 自動リセット + archive 連携。

- 行単位の正規表現置換で本文を触らない（atomic.update_line 経由）
- 軸 1 のラベル遷移時に postpone_count をリセット（state.should_reset_postpone）
- ``[完了]`` / ``[廃止]`` 指定で archive_doc を内部呼び出し
- ファイル種別と無効ラベル組み合わせはバリデーション拒否
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..atomic import update_line
from ..config import Config
from ..detect import _H1_LABEL_RE, _H1_RE, mask_code_fences
from ..state import record_label_transition, should_reset_postpone

# ファイル種別ごとに許可されるラベル（内部 state key）。命名規約と state モデルの直交化。
# - plan は [対応中] を持たない（bugfix 専用）
# - bugfix は [計画] / [実行中] を持たない（plan 専用）
# - pending は [保留] / [計画] / [廃止] のみ
_ALLOWED_BY_TYPE: dict[str, frozenset[str]] = {
    "plan": frozenset({"planned", "in-progress", "watching", "pending", "done", "discarded"}),
    "bugfix": frozenset({"active", "watching", "done", "discarded"}),
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


def update_status(
    abs_path: Path,
    new_state_key: str,
    *,
    project_root: Path,
    config: Config,
    file_type: str | None = None,
    expected_mtime: float | None = None,
) -> UpdateStatusResult:
    """H1 ラベルを ``new_state_key`` に書き換える。

    Args:
        abs_path: 書き込み対象 MD の絶対パス（呼び出し側でスコープ境界検証済み前提）
        new_state_key: 内部状態キー（"planned" / "in-progress" / "watching" / "done" / "discarded" / "pending" / "active"）
        project_root: state.json の置き場
        config: state_model（ラベル文字列の解決）と lang を持つ
        file_type: "plan" / "bugfix" / "pending"（バリデーション用・None で緩判定）
        expected_mtime: 楽観ロック用
    """
    sm = config.state_model
    target = sm.by_key(new_state_key)
    if target is None:
        raise StatusValidationError(f"未知の state key: {new_state_key}")
    _validate_for_type(file_type, new_state_key)

    new_label_token = target.label(config.lang)
    new_label = f"[{new_label_token}]"

    # 旧ラベル抽出（書き換え前の text を読む）。
    text_before = Path(abs_path).read_text(encoding="utf-8", newline="")
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

    new_mtime = update_line(Path(abs_path), transform=_xform, expected_mtime=expected_mtime)

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
        new_mtime=new_mtime,
        old_state_key=old_state_key,
        new_state_key=new_state_key,
        postpone_count_reset=reset,
        archive_triggered=archive_triggered,
    )
