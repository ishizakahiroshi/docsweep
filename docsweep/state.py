"""``.docsweep/state.json`` — 付随情報（postpone_count / due_history / label_history）。

- 正本は MD ファイル本体。state.json は破損しても MD は壊れない（再構築可能）。
- 各プロジェクト直下の ``.docsweep/state.json`` に置く（複数 PC 同期問題回避）。
- 不正 JSON は警告のみ・空 state で初期化（実害なし）。
- `version` フィールドで前方互換性管理（v1 → v2 マイグレーションは将来）。

書き込みは ``atomic.write_atomic`` を経由してアトミック。MD のロックとは独立。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .atomic import write_atomic

STATE_DIR_NAME = ".docsweep"
STATE_FILE_NAME = "state.json"
STATE_SCHEMA_VERSION = 1


def state_path(project_root: Path) -> Path:
    return Path(project_root) / STATE_DIR_NAME / STATE_FILE_NAME


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class FileState:
    """1 ファイルの付随情報。postpone_count・due_history・label_history を保持。"""

    postpone_count: int = 0
    due_history: list[dict] = field(default_factory=list)
    label_history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "postpone_count": self.postpone_count,
            "due_history": list(self.due_history),
            "label_history": list(self.label_history),
        }

    @classmethod
    def from_dict(cls, data: Any) -> FileState:
        if not isinstance(data, dict):
            return cls()
        return cls(
            postpone_count=int(data.get("postpone_count") or 0),
            due_history=list(data.get("due_history") or []),
            label_history=list(data.get("label_history") or []),
        )


@dataclass
class StateDoc:
    """1 プロジェクト分の state.json をメモリ表現したもの。key は **プロジェクト相対 POSIX パス**。"""

    version: int = STATE_SCHEMA_VERSION
    files: dict[str, FileState] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "files": {k: v.to_dict() for k, v in self.files.items()},
        }

    @classmethod
    def from_dict(cls, data: Any) -> StateDoc:
        if not isinstance(data, dict):
            return cls()
        files_raw = data.get("files") or {}
        files: dict[str, FileState] = {}
        if isinstance(files_raw, dict):
            for k, v in files_raw.items():
                if isinstance(k, str):
                    files[k] = FileState.from_dict(v)
        return cls(version=int(data.get("version") or STATE_SCHEMA_VERSION), files=files)

    def get(self, rel_path: str) -> FileState:
        return self.files.get(rel_path, FileState())

    def upsert(self, rel_path: str, fs: FileState) -> None:
        self.files[rel_path] = fs


def load(project_root: Path) -> StateDoc:
    """``.docsweep/state.json`` を読む。存在しない/壊れていれば空 StateDoc を返す。"""
    p = state_path(project_root)
    if not p.is_file():
        return StateDoc()
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        # 警告のみ・空で初期化（MD 正本主義）。
        return StateDoc()
    return StateDoc.from_dict(data)


def save(project_root: Path, doc: StateDoc) -> None:
    """``.docsweep/state.json`` をアトミックに書き出す。"""
    p = state_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(doc.to_dict(), ensure_ascii=False, indent=2) + "\n"
    write_atomic(p, content)


def _rel_key(project_root: Path, abs_path: Path) -> str:
    """state.json のキーに使うプロジェクト相対 POSIX パス。"""
    try:
        return Path(abs_path).resolve().relative_to(Path(project_root).resolve()).as_posix()
    except ValueError:
        # スコープ外（通常は起きない・呼び出し側が事前検証する想定）。
        return Path(abs_path).as_posix()


def increment_postpone(
    project_root: Path,
    abs_path: Path,
    *,
    from_due: str | None,
    to_due: str | None,
    reason: str | None = None,
) -> int:
    """``update_due`` 経由でカウントを 1 増やし、due_history に append する。

    Returns:
        更新後の postpone_count（呼び出し側で警告判定に使う）。
    """
    doc = load(project_root)
    key = _rel_key(project_root, abs_path)
    fs = doc.get(key)
    fs.postpone_count += 1
    fs.due_history.append(
        {"from": from_due, "to": to_due, "at": _now_iso(), "reason": reason}
    )
    doc.upsert(key, fs)
    save(project_root, doc)
    return fs.postpone_count


def record_label_transition(
    project_root: Path,
    abs_path: Path,
    *,
    from_label: str | None,
    to_label: str | None,
    reset_postpone: bool,
) -> int:
    """``update_status`` 経由でラベル遷移を記録。``reset_postpone=True`` でカウンタを 0 に戻す。

    Returns:
        更新後の postpone_count（リセットされたら 0）。
    """
    doc = load(project_root)
    key = _rel_key(project_root, abs_path)
    fs = doc.get(key)
    if reset_postpone:
        fs.postpone_count = 0
    fs.label_history.append(
        {"from": from_label, "to": to_label, "at": _now_iso()}
    )
    doc.upsert(key, fs)
    save(project_root, doc)
    return fs.postpone_count


def get_postpone_count(project_root: Path, abs_path: Path) -> int:
    """state.json に記録された postpone_count を取得（無ければ 0）。"""
    doc = load(project_root)
    key = _rel_key(project_root, abs_path)
    return doc.get(key).postpone_count


# 軸 1（ラベル）の遷移でカウンタをリセットする境界条件。
# - [計画] → [実行中]: ようやく着手したサイン
# - [実行中] → [様子見]: 直し終わったサイン（plan / bugfix 共通）
# - 上記以外（[完了]/[廃止] への遷移など）は archive 行きなのでリセット不要
# 2026-06-23 改修: 旧 active=[対応中] を in-progress に統合したため ("active", "watching") を撤去。
_RESET_TRANSITIONS: frozenset[tuple[str, str]] = frozenset({
    ("planned", "in-progress"),
    ("in-progress", "watching"),
    ("pending", "planned"),
    ("pending", "in-progress"),
})


def should_reset_postpone(*, old_state_key: str | None, new_state_key: str | None) -> bool:
    """ラベル遷移がカウンタリセット対象かを判定する純粋関数（テスト容易）。"""
    if old_state_key is None or new_state_key is None:
        return False
    return (old_state_key, new_state_key) in _RESET_TRANSITIONS
