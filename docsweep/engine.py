"""コアエンジン: 分類（flags/allowed_actions 付与）・triage・apply（移送/ラベル書換）。

「ラベルを立てる＝判断」は人/AI、「archive へ運ぶ＝作業」は自動、と分離する。
--auto の自動移送対象は done＋discarded（auto_move=True）のみ。watching は絶対に触らない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .archive import archive_file, append_move_log
from .config import Config
from .detect import _H1_LABEL_RE, _H1_RE
from .models import Action, Flag, FileRecord, MoveLogEntry
from .scan import ScannedDoc, scan


def classify(doc: ScannedDoc, config: Config) -> None:
    """ScannedDoc.record に flags と allowed_actions を埋める（in-place）。"""
    rec = doc.record
    det = doc.detection
    flags: list[str] = []

    if det.parse_error or rec.state is None:
        flags.append(Flag.NEEDS_FIX.value)
    if det.conflict:
        flags.append(Flag.CONFLICT.value)

    # stale 判定（type 別 stale_days）。
    type_def = doc.type_def
    if type_def is not None and rec.age_days >= type_def.stale_days:
        flags.append(Flag.STALE.value)
        # 陳腐化した未終端ラベル（計画/実行中/対応中/様子見）は要判断。
        if rec.state in {"planned", "in-progress", "active", "watching"}:
            flags.append(Flag.NEEDS_DECISION.value)

    rec.flags = flags
    rec.allowed_actions = _allowed_actions(rec)


def _allowed_actions(rec: FileRecord) -> list[str]:
    actions: list[str] = [Action.KEEP.value]
    state = rec.state
    if state in {"planned", "in-progress", "active", "watching", "pending"}:
        actions.append(Action.DISCARD.value)
    if state in {"watching", "discarded"}:
        actions.append(Action.RESUME.value)
    if state == "watching":
        actions.append(Action.PROMOTE.value)
    if state is not None:
        actions.append(Action.RELABEL.value)
    return actions


@dataclass
class ScanResult:
    docs: list[ScannedDoc]

    @property
    def records(self) -> list[FileRecord]:
        return [d.record for d in self.docs]

    def auto_movable(self) -> list[ScannedDoc]:
        """--auto の対象（auto_move=True かつ archivable）。watching は除外される。"""
        return [d for d in self.docs if d.record.auto_movable and d.record.archivable]

    def needs_decision(self) -> list[ScannedDoc]:
        return [d for d in self.docs if Flag.NEEDS_DECISION.value in d.record.flags]

    def needs_fix(self) -> list[ScannedDoc]:
        return [d for d in self.docs if Flag.NEEDS_FIX.value in d.record.flags]


def run_scan(config: Config) -> ScanResult:
    docs = scan(config)
    for d in docs:
        classify(d, config)
    return ScanResult(docs=docs)


def _project_dir_for(doc: ScannedDoc, config: Config) -> tuple[Path, Path]:
    """(project_dir, scan_root) を返す。project_dir は archive を置く基準＝検出済みプロジェクト境界。"""
    project_dir = Path(doc.record.project_root)
    for root in config.roots:
        root = root.resolve()
        try:
            project_dir.relative_to(root)
            return (project_dir, root)
        except ValueError:
            continue
    # フォールバック: スキャンルートが特定できなければプロジェクト境界自身を root 扱い。
    return (project_dir, project_dir)


def auto_sweep(config: Config, *, dry_run: bool = False) -> list[MoveLogEntry]:
    """--auto: auto_move 対象を各プロジェクトの archive/ へ移送。watching は触らない。"""
    result = run_scan(config)
    moved: list[MoveLogEntry] = []
    for doc in result.auto_movable():
        rec = doc.record
        project_dir, root = _project_dir_for(doc, config)
        archive_dir = _archive_dir_for(doc, config)
        dst = archive_file(
            src=Path(rec.path), project_dir=project_dir, archive_dir=archive_dir,
            root=root, project=rec.project, status=rec.state, dry_run=dry_run,
        )
        moved.append(MoveLogEntry(
            ts="(dry-run)" if dry_run else "", op="archive", project=rec.project,
            status=rec.state, src=rec.path, dst=dst.as_posix(),
        ))
    return moved


def archive_doc(doc: ScannedDoc, config: Config, *, dry_run: bool = False) -> MoveLogEntry:
    """1 ファイルを（ラベル書換なしで）そのまま archive へ移送する。"""
    rec = doc.record
    project_dir, root = _project_dir_for(doc, config)
    dst = archive_file(
        src=Path(rec.path), project_dir=project_dir, archive_dir=_archive_dir_for(doc, config),
        root=root, project=rec.project, status=rec.state, op="archive", dry_run=dry_run,
    )
    return MoveLogEntry(ts="", op="archive", project=rec.project, status=rec.state, src=rec.path, dst=dst.as_posix())


def promote_state(
    config: Config, *, from_state: str = "watching", to_state: str = "done",
    project: str | None = None, dry_run: bool = False,
) -> list[MoveLogEntry]:
    """release sweep: 溜まった from_state を to_state へ一括昇格し archive へ移送。"""
    result = run_scan(config)
    sm = config.state_model
    target = sm.by_key(to_state)
    moved: list[MoveLogEntry] = []
    for doc in result.docs:
        rec = doc.record
        if rec.state != from_state:
            continue
        if project and rec.project != project:
            continue
        project_dir, root = _project_dir_for(doc, config)
        if target and not dry_run:
            relabel_file(Path(rec.path), f"[{target.label(config.lang)}]", config)
        dst = archive_file(
            src=Path(rec.path), project_dir=project_dir, archive_dir=_archive_dir_for(doc, config),
            root=root, project=rec.project, status=to_state, op="promote", dry_run=dry_run,
        )
        moved.append(MoveLogEntry(
            ts="(dry-run)" if dry_run else "", op="promote", project=rec.project,
            status=to_state, src=rec.path, dst=dst.as_posix(),
        ))
    return moved


def _archive_dir_for(doc: ScannedDoc, config: Config) -> str:
    if doc.type_def and doc.type_def.archive_dir:
        return doc.type_def.archive_dir
    return config.archive_dir


def apply_action(
    doc: ScannedDoc, action: str, config: Config, *, to: str | None = None, dry_run: bool = False
) -> MoveLogEntry:
    """triage の閉じた action を 1 ファイルへ機械実行する。"""
    rec = doc.record
    project_dir, root = _project_dir_for(doc, config)
    sm = config.state_model
    path = Path(rec.path)

    if action not in rec.allowed_actions:
        raise ValueError(f"action '{action}' は {rec.path} に許可されていません（{rec.allowed_actions}）")

    if action == Action.KEEP.value:
        return MoveLogEntry(ts="", op="keep", project=rec.project, status=rec.state, src=rec.path, dst=None)

    if action in (Action.DISCARD.value, Action.PROMOTE.value):
        target_key = "discarded" if action == Action.DISCARD.value else "done"
        st = sm.by_key(target_key)
        if st and not dry_run:
            relabel_file(path, f"[{st.label(config.lang)}]", config)
        dst = archive_file(
            src=path, project_dir=project_dir, archive_dir=_archive_dir_for(doc, config),
            root=root, project=rec.project, status=target_key, op=action, dry_run=dry_run,
        )
        return MoveLogEntry(ts="", op=action, project=rec.project, status=target_key, src=rec.path, dst=dst.as_posix())

    if action == Action.RESUME.value:
        target_key = "active" if rec.type == "bugfix" else "in-progress"
        st = sm.by_key(target_key)
        if st and not dry_run:
            relabel_file(path, f"[{st.label(config.lang)}]", config)
            append_move_log(root, MoveLogEntry(ts="", op="resume", project=rec.project, status=target_key, src=rec.path, dst=None))
        return MoveLogEntry(ts="", op="resume", project=rec.project, status=target_key, src=rec.path, dst=None)

    if action == Action.RELABEL.value:
        if not to:
            raise ValueError("relabel には to（ラベル名）が必要です")
        st = sm.match(to)
        label = f"[{st.label(config.lang)}]" if st else (to if to.startswith("[") else f"[{to}]")
        if not dry_run:
            relabel_file(path, label, config)
            append_move_log(root, MoveLogEntry(ts="", op="relabel", project=rec.project, status=(st.key if st else None), src=rec.path, dst=None))
        return MoveLogEntry(ts="", op="relabel", project=rec.project, status=(st.key if st else None), src=rec.path, dst=None)

    raise ValueError(f"未知の action: {action}")


def relabel_file(path: Path, new_label: str, config: Config) -> bool:
    """H1 のラベルを new_label（例 "[完了]"）に書き換える。成功で True。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = _H1_RE.search(text)
    if not m:
        return False
    h1 = m.group(1).strip()
    lm = _H1_LABEL_RE.match(h1)
    title = lm.group(2).strip() if lm else h1
    new_h1 = f"# {new_label} {title}".rstrip()
    new_text = text[: m.start()] + new_h1 + text[m.end():]
    path.write_text(new_text, encoding="utf-8")
    return True
