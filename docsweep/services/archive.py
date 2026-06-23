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
- restore エントリを ``moves.jsonl`` に追記し、二重 Undo を防ぐ
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..archive import _now_iso, append_move_log, move_log_path
from ..config import Config
from ..engine import archive_doc, run_scan
from ..models import MoveLogEntry

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

    def to_dict(self) -> dict:
        return {
            "moved": [
                {"from": m.src, "to": m.dst, "label": m.label, "state": m.state}
                for m in self.moved
            ],
            "skipped": [{"path": s.path, "reason": s.reason} for s in self.skipped],
        }


def archive_done(
    *,
    config: Config,
    paths: list[str] | None = None,
    auto: bool = False,
    dry_run: bool = False,
) -> ArchiveDoneResult:
    """``[完了]`` / ``[廃止]`` のファイルを archive へ移送する。

    Args:
        config: スキャン設定
        paths: 明示指定する絶対 or 相対パス（空 or None かつ auto=True で全件処理）
        auto: True 時は全プロジェクトの archive 可能ファイルを一括処理
        dry_run: True で実移送せず処理予定だけ返す
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
    batch_id = uuid.uuid4().hex[:12]

    for doc in scan_result.docs:
        rec = doc.record
        if target_paths is not None and rec.path not in target_paths:
            continue
        if rec.state not in _ARCHIVABLE_KEYS:
            if target_paths is not None and rec.path in target_paths:
                result.skipped.append(
                    ArchiveSkipEntry(
                        path=rec.path,
                        reason=f"label is {rec.state_label or '[?]'} — not archivable",
                    )
                )
            continue
        entry = archive_doc(doc, config, dry_run=dry_run, batch_id=batch_id)
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
                ArchiveSkipEntry(path=p, reason="path not found in scan result")
            )

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

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "restored": [
                {"from": e.dst, "to": e.src, "project": e.project, "state": e.state}
                for e in self.restored
            ],
            "failed": list(self.failed),
        }


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


def _find_latest_undoable_batch(entries: list[dict]) -> str | None:
    """最新の「まだ復元されていない」archive/promote バッチの batch_id を返す。

    既に restore エントリが書かれているバッチは飛ばし、次に古いバッチを返す。
    batch_id を持たない古いエントリは Undo 対象外（None を返す）。
    """
    restored_batches = {
        e.get("batch_id") for e in entries
        if e.get("op") == "restore" and e.get("batch_id")
    }
    for e in reversed(entries):
        if e.get("op") not in ("archive", "promote"):
            continue
        bid = e.get("batch_id")
        if not bid:
            continue
        if bid in restored_batches:
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
            if e.get("batch_id") == bid and e.get("op") in ("archive", "promote")
        ]
        result.batch_id = bid
        for entry in batch:
            src = Path(entry["src"])  # 元の場所
            dst = Path(entry.get("dst") or "")  # archive 配下
            if not dst.is_file():
                result.failed.append({"path": str(dst), "error": "archive 先ファイルが見つかりません"})
                continue
            if src.exists():
                result.failed.append({"path": str(src), "error": "復元先に既にファイルがあります"})
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
            result.restored.append(UndoEntry(
                src=str(src), dst=str(dst),
                project=entry.get("project") or "",
                state=entry.get("status"),
            ))
        # 最初に見つかった root で 1 バッチだけ Undo して終わる
        return result
    return result
