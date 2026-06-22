"""``archive_done`` — ``[完了]`` / ``[廃止]`` のみ archive 移送する閉じた口。

不変条件:
- ``[様子見]`` は明示指定でも拒否（寝かせを守る）
- ``[完了]`` / ``[廃止]`` 以外は absolutely 移送しない
- 物理削除は持たない（最悪 archive 止まり）
- 同名衝突は連番 ``_2``（archive.dedupe_path 経由）

呼び出し側:
- MCP: ``archive_done(paths=[...]|auto=True)``
- Web UI: ``POST /api/cards/<path>/archive``
- 内部: ``update_status`` で ``[完了]`` / ``[廃止]`` 指定時の自動連携
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..engine import archive_doc, run_scan

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
        entry = archive_doc(doc, config, dry_run=dry_run)
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
