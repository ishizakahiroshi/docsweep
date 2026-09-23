"""``archive_done`` — ``[完了]`` / ``[廃止]`` のみ archive 移送する閉じた口 + Undo。

不変条件:
- ``[様子見]`` は明示指定でも拒否（寝かせを守る）
- ``[完了]`` / ``[廃止]`` 以外は absolutely 移送しない
- 物理削除は持たない（最悪 archive 止まり）
- 同名衝突は連番 ``_2``（archive.dedupe_path 経由）

呼び出し側:
- MCP: ``archive_done(paths=[...]|auto=True)``
- Web UI: ``POST /api/cards/<path>/archive``
- 内部: ``update_status`` で ``[完了]`` / ``[廃止]`` 指定時の自動連携

Undo:
- ``archive_done`` は実行ごとに ``batch_id`` を生成し、全エントリにマーク
- ``undo_last_batch`` は最新の未復元バッチを逆操作（dst → src へ shutil.move）
- 移送に伴う参照の書き換え（``ref_rewrite``）と、移送の直前に書き換えた状態
  （``state_rewrite``: H1 のラベルと frontmatter の値）も同じバッチで元へ戻す
- provenance 台帳の ``work_path`` も元の場所へ付け替える（移送時の付け替えの逆）
- restore エントリを ``moves.jsonl`` に追記し、二重 Undo を防ぐ
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from .. import move_refs
from ..archive import _now_iso, append_move_log, move_log_path, new_batch_id
from ..atomic import ConflictError
from ..config import Config
from ..engine import _project_dir_for, archive_doc, run_scan
from ..i18n import t
from ..models import MoveLogEntry
from ..provenance import follow_move_safely
from ..scan import detect_project_root, work_dir_aliases
from .status import (
    STATE_RESTORE_OP,
    STATE_REWRITE_OP,
    UpdateStatusResult,
    log_state_restore,
    log_state_rewrite,
    revert_state_rewrite,
)

# archive 可能な内部状態キー。新しい状態を増やすときは states.archive 属性と整合させる。
_ARCHIVABLE_KEYS: frozenset[str] = frozenset({"done", "discarded"})


@dataclass
class ArchiveMoveEntry:
    src: str
    dst: str
    label: str | None
    state: str | None


@dataclass
class ArchiveSkipEntry:
    path: str
    reason: str


@dataclass
class ArchiveDoneResult:
    moved: list[ArchiveMoveEntry] = field(default_factory=list)
    skipped: list[ArchiveSkipEntry] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    ref_updates: list[dict] = field(default_factory=list)
    ref_failed: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = {
            "moved": [
                {"from": m.src, "to": m.dst, "label": m.label, "state": m.state}
                for m in self.moved
            ],
            "skipped": [{"path": s.path, "reason": s.reason} for s in self.skipped],
            "failed": list(self.failed),
            "ref_updates": list(self.ref_updates),
        }
        if self.ref_failed:
            data["ref_failed"] = list(self.ref_failed)
        return data


def archive_done(
    *,
    config: Config,
    paths: list[str] | None = None,
    auto: bool = False,
    dry_run: bool = False,
    state_changes: Mapping[str, UpdateStatusResult] | None = None,
) -> ArchiveDoneResult:
    """``[完了]`` / ``[廃止]`` のファイルを archive へ移送する。

    Args:
        config: スキャン設定
        paths: 明示指定する絶対 or 相対パス（空 or None かつ auto=True で全件処理）
        auto: True 時は全プロジェクトの archive 可能ファイルを一括処理
        dry_run: True で実移送せず処理予定だけ返す
        state_changes: 移送の直前にラベルを書き換えた文書（``update_status`` の結果・パスで引く）。
            Web / MCP で ``[完了]`` にして続けて移すときに渡すと、undo でラベルも元に戻る。
    """
    result = ArchiveDoneResult()
    scan_result = run_scan(config)
    target_paths: set[str] | None = None
    if paths:
        target_paths = {Path(p).resolve().as_posix() for p in paths}
    elif not auto:
        # paths も auto も指定なしは何もしない（破壊安全側）。
        return result

    # 同一実行を識別する batch_id（Undo で逆引きするため）。dry_run でも一応振っておく。
    batch_id = new_batch_id()
    ref_moves: list[tuple[move_refs.Move, str, Path, str]] = []

    for doc in scan_result.docs:
        rec = doc.record
        if target_paths is not None and rec.path not in target_paths:
            continue
        if rec.state not in _ARCHIVABLE_KEYS:
            if target_paths is not None and rec.path in target_paths:
                result.skipped.append(
                    ArchiveSkipEntry(
                        path=rec.path,
                        reason=t(
                            "services_archive.skip_not_archivable",
                            label=rec.state_label or "[?]",
                        ),
                    )
                )
            continue
        try:
            _project_dir, root = _project_dir_for(doc, config)
            entry = archive_doc(
                doc, config, dry_run=dry_run, batch_id=batch_id, rewrite_refs=False
            )
        except (OSError, UnicodeError, ValueError) as exc:
            result.failed.append({"path": rec.path, "error": str(exc)})
            continue
        change = (state_changes or {}).get(rec.path)
        if change is not None and not dry_run:
            log_state_rewrite(root, rec.project, change, batch_id=batch_id)
        ref_moves.append(
            (move_refs.Move(Path(entry.src), Path(entry.dst or "")), rec.project_root, root, rec.project)
        )
        result.moved.append(
            ArchiveMoveEntry(
                src=entry.src,
                dst=entry.dst or "",
                label=rec.state_label,
                state=rec.state,
            )
        )

    if target_paths is not None:
        seen = {m.src for m in result.moved} | {s.path for s in result.skipped}
        for p in target_paths - seen:
            result.skipped.append(
                ArchiveSkipEntry(path=p, reason=t("services_archive.skip_not_found"))
            )

    # 親と子を同じ実行で移しても子の参照が新しい場所を向くよう、移し終えてから 1 回で書き換える。
    refs = move_refs.rewrite_refs_by_project(
        ref_moves, config=config, batch_id=batch_id, dry_run=dry_run
    )
    result.ref_updates = [u.to_dict() for u in refs.updates]
    result.ref_failed = refs.failed
    return result


@dataclass
class UndoEntry:
    src: str   # 復元先（元の場所）
    dst: str   # 復元元（archive 配下）
    project: str
    state: str | None


@dataclass
class UndoResult:
    batch_id: str | None
    restored: list[UndoEntry] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    refs_restored: list[dict] = field(default_factory=list)
    states_restored: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "restored": [
                {"from": e.dst, "to": e.src, "project": e.project, "state": e.state}
                for e in self.restored
            ],
            "failed": list(self.failed),
            "refs_restored": list(self.refs_restored),
            "states_restored": list(self.states_restored),
        }


# ファイルの移動として Undo できる op。ref_rewrite はこれらに付随して戻す。
# discard は ``apply --action discard`` / ``triage --review`` の廃止で書かれる。
_UNDOABLE_MOVE_OPS: frozenset[str] = frozenset({"archive", "promote", "discard", "move"})


def _read_log(root: Path) -> list[dict]:
    p = move_log_path(root)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _path_key(value: str) -> str:
    """Normalize a move-log path for matching archive and restore entries."""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(value)))
    except (OSError, TypeError, ValueError):
        return os.path.normcase(os.path.normpath(str(value)))


def _undo_key(entry: dict) -> tuple[str, str] | None:
    """Return ``(archive_path, original_path)`` for a move-log entry."""
    src = entry.get("src")
    dst = entry.get("dst")
    if not src or not dst:
        return None
    # archive/promote: src=original, dst=archive
    # restore: src=archive, dst=original
    if entry.get("op") == "restore":
        return _path_key(str(src)), _path_key(str(dst))
    return _path_key(str(dst)), _path_key(str(src))


def _ref_key(entry: dict) -> tuple[str, str, str, str, str]:
    """``ref_rewrite`` と、それを取り消した ``ref_restore`` を突き合わせるキー。"""
    return (
        _path_key(str(entry.get("src") or "")),
        str(entry.get("field") or ""),
        json.dumps(entry.get("before"), ensure_ascii=False),
        json.dumps(entry.get("after"), ensure_ascii=False),
        json.dumps(entry.get("offsets")),
    )


def _restore_ref_rewrites(
    root: Path, entries: list[dict], bid: str, result: UndoResult
) -> None:
    """バッチの参照書き換えを新しい順に戻す（ファイルを元の場所へ戻す前に行う）。"""
    restored = {
        _ref_key(e) for e in entries
        if e.get("op") == "ref_restore" and e.get("batch_id") == bid
    }
    rewrites = [
        e for e in entries
        if e.get("op") == "ref_rewrite" and e.get("batch_id") == bid
    ]
    for entry in reversed(rewrites):
        if _ref_key(entry) in restored:
            continue
        try:
            undone = move_refs.revert_ref_rewrite(entry)
        except (OSError, UnicodeError, ValueError, ConflictError) as exc:
            result.failed.append({
                "path": str(entry.get("src") or ""),
                "field": entry.get("field"),
                "error": str(exc),
            })
            continue
        move_refs.log_ref_restore(root, str(entry.get("project") or ""), undone, batch_id=bid)
        result.refs_restored.append({
            "path": undone.path,
            "field": undone.field,
            "from": undone.after,
            "to": undone.before,
        })


def _state_key(entry: dict) -> tuple[str, str, str, str]:
    """``state_rewrite`` と、それを書き戻した ``state_restore`` を突き合わせるキー。"""
    return (
        _path_key(str(entry.get("src") or "")),
        str(entry.get("field") or ""),
        json.dumps(entry.get("before"), ensure_ascii=False),
        json.dumps(entry.get("after"), ensure_ascii=False),
    )


def _restore_state_rewrites(
    root: Path, entries: list[dict], bid: str, result: UndoResult, *, still_archived: set[str]
) -> None:
    """移送の直前に書き換えたラベルを書き戻す（ファイルを元の場所へ戻した後に行う）。

    ``still_archived`` は今回も元へ戻せなかった文書の元の場所。その場所に別の文書が
    あるかもしれないので、書き戻さない（移送を戻せた次の undo で書き戻す）。
    """
    restored = {
        _state_key(e) for e in entries
        if e.get("op") == STATE_RESTORE_OP and e.get("batch_id") == bid
    }
    rewrites = [
        e for e in entries
        if e.get("op") == STATE_REWRITE_OP and e.get("batch_id") == bid
    ]
    for entry in reversed(rewrites):
        if _state_key(entry) in restored:
            continue
        if _path_key(str(entry.get("src") or "")) in still_archived:
            continue
        try:
            revert_state_rewrite(entry)
        except (OSError, UnicodeError, ValueError, ConflictError) as exc:
            result.failed.append({
                "path": str(entry.get("src") or ""),
                "field": entry.get("field"),
                "error": str(exc),
            })
            continue
        log_state_restore(root, str(entry.get("project") or ""), entry, batch_id=bid)
        result.states_restored.append({
            "path": str(entry.get("src") or ""),
            "field": entry.get("field"),
            "from": entry.get("after"),
            "to": entry.get("before"),
        })


def _restored_project_dir(path: Path, root: Path, config: Config) -> Path | None:
    """元へ戻した文書の project root（scan と同じ判定）。特定できなければ None。

    移動ログのパスは実体側なので、queue が junction なら repo の外を指す。marker を上へ辿る前に
    work_dir の実体 → 宣言元 project の対応表で引き戻す（``detect_project_root`` の順序）。
    対応表を作るには root 配下の走査が要るので、provenance を使っている文書のときだけ呼ばれる
    （``follow_move_safely`` に関数で渡す）。スキャンルートの外で見つかった marker は別物なので採らない。
    """
    try:
        scan_root = root.resolve()
        project = detect_project_root(
            path.parent, scan_root, config.project_markers, {}, work_dir_aliases(scan_root, config)
        )
        project.relative_to(scan_root)
    except (OSError, ValueError):
        return None
    return project


def _find_latest_undoable_batch(entries: list[dict]) -> str | None:
    """最新の「まだ復元されていない」archive/promote/move バッチの batch_id を返す。

    部分復元されたバッチは残りの項目があれば選び直す。全項目が restore
    済みなら飛ばし、次に古いバッチを返す。batch_id を持たない古いエントリは
    Undo 対象外（None を返す）。
    """
    restored_items = {
        key for e in entries
        if e.get("op") == "restore"
        for key in [_undo_key(e)]
        if key is not None
    }
    for e in reversed(entries):
        if e.get("op") not in _UNDOABLE_MOVE_OPS:
            continue
        bid = e.get("batch_id")
        if not bid:
            continue
        if _undo_key(e) in restored_items:
            continue
        return bid
    return None


def undo_last_batch(*, config: Config) -> UndoResult:
    """直近の archive バッチ（最新の未復元 batch_id）を逆操作で元の場所へ戻す。

    各 archive ルートを順番に走査し、最初に見つかった「Undo 対象バッチ」だけを処理する
    （複数 root に跨る同時 archive は実運用で稀なので単純化）。restore エントリを追記して
    二重 Undo を防ぐ。
    """
    result = UndoResult(batch_id=None)
    for root in config.roots:
        entries = _read_log(root)
        bid = _find_latest_undoable_batch(entries)
        if not bid:
            continue
        batch = [
            e for e in entries
            if e.get("batch_id") == bid and e.get("op") in _UNDOABLE_MOVE_OPS
        ]
        restored_items = {
            key for e in entries
            if e.get("op") == "restore"
            for key in [_undo_key(e)]
            if key is not None
        }
        # A previous undo may have restored only some entries in a batch.  Do
        # not attempt to move those files a second time; continue with the
        # remaining items in the same batch.
        batch = [e for e in batch if _undo_key(e) not in restored_items]
        result.batch_id = bid
        # 参照の書き換えは移動の後に行ったので、先に戻す（書き換えた文書が同じバッチで
        # 動いていても、記録した場所にあるうちに戻せる）。
        _restore_ref_rewrites(root, entries, bid, result)
        still_archived: set[str] = set()
        for entry in batch:
            still_archived.add(_path_key(str(entry["src"])))
            src = Path(entry["src"])  # 元の場所
            dst = Path(entry.get("dst") or "")  # archive 配下
            if not dst.is_file():
                result.failed.append(
                    {"path": str(dst), "error": t("services_archive.undo_source_missing")}
                )
                continue
            if src.exists():
                result.failed.append(
                    {"path": str(src), "error": t("services_archive.undo_destination_exists")}
                )
                continue
            try:
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst), str(src))
            except OSError as e:
                result.failed.append({"path": str(dst), "error": str(e)})
                continue
            # restore エントリを追記（同じ batch_id を付けて二重 Undo を防ぐ）
            append_move_log(
                root,
                MoveLogEntry(
                    ts=_now_iso(), op="restore",
                    project=entry.get("project") or "",
                    status=entry.get("status"),
                    src=str(dst), dst=str(src),
                    batch_id=bid,
                ),
            )
            # provenance 台帳の work_path も戻した場所へ付け替える（失敗しても復元は戻さない）。
            follow_move_safely(
                dst, src, project_dir=partial(_restored_project_dir, src, root, config)
            )
            result.restored.append(UndoEntry(
                src=str(src), dst=str(dst),
                project=entry.get("project") or "",
                state=entry.get("status"),
            ))
            still_archived.discard(_path_key(str(src)))
        # 移送の直前に書き換えたラベルは、ファイルを元の場所へ戻してから書き戻す。
        _restore_state_rewrites(root, entries, bid, result, still_archived=still_archived)
        # 最初に見つかった root で 1 バッチだけ Undo して終わる
        return result
    return result
