"""コアエンジン: 分類（flags/allowed_actions 付与）・triage・apply（移送/ラベル書換）。

「ラベルを立てる＝判断」は人/AI、「archive へ運ぶ＝作業」は自動、と分離する。
--auto の自動移送対象は done＋discarded（auto_move=True）のみ。watching は絶対に触らない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .archive import _now_iso, append_move_log, archive_file
from .atomic import write_atomic
from .config import Config, project_archive_dir
from .detect import _H1_LABEL_RE, _H1_RE, mask_code_fences
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

    # never_archive は archive 対象状態でも移送されない（policy による保持）。
    # archivable/auto_movable を False に落として下流の sweep/apply_action で自動的に外れる形にする。
    if rec.docsweep_policy == "never_archive":
        rec.archivable = False
        rec.auto_movable = False

    # stale 判定（type 別 stale_days）。
    type_def = doc.type_def
    if type_def is not None and rec.age_days >= type_def.stale_days:
        flags.append(Flag.STALE.value)
        # 陳腐化した未終端ラベル（計画/実行中/様子見）は要判断。
        # 2026-06-23 改修: active を in-progress に統合。
        if rec.state in {"planned", "in-progress", "watching"}:
            flags.append(Flag.NEEDS_DECISION.value)

    # due 超過フラグ（archive 制御には絡めない — 第2軸は気づきのみ）。
    if rec.due_parse_error:
        flags.append(Flag.DUE_PARSE_ERROR.value)
    elif rec.due and rec.state not in {"done", "discarded"}:
        try:
            due_date = date.fromisoformat(rec.due)
            if date.today() > due_date:
                if rec.state == "watching":
                    flags.append(Flag.OVERDUE_GRADUATE.value)
                else:
                    flags.append(Flag.OVERDUE_TODO.value)
        except ValueError:
            flags.append(Flag.DUE_PARSE_ERROR.value)

    rec.flags = flags
    rec.allowed_actions = _allowed_actions(rec)


def _allowed_actions(rec: FileRecord) -> list[str]:
    actions: list[str] = [Action.KEEP.value]
    state = rec.state
    # 2026-06-23 改修: active を in-progress に統合。
    if state in {"planned", "in-progress", "watching", "pending"}:
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
    # UX W2 / P39: グローバル除外リスト
    try:
        from .excluded import filter_docs_by_excluded

        docs = filter_docs_by_excluded(docs)
    except Exception:
        pass
    return ScanResult(docs=docs)


def scan_records(config: Config, *, project: str | None = None) -> list[FileRecord]:
    """読み取り系コマンド用の高速版 ``run_scan``。``FileRecord`` のリストだけ返す。

    優先順位:
      1. SQLite 索引（``~/.docsweep/index.db``）に登録済みなら索引から復元（高速）
      2. 索引が空 / 無い / 例外 → ``run_scan(config).records`` にフォールバック（既存挙動）

    project: 指定すると索引クエリ時点で project_id で絞り込む（フォールバック時は呼び出し側
    で絞る）。
    """
    try:
        from .index import load_records_from_index
        from .excluded import filter_records_by_excluded

        recs = load_records_from_index(config, project_filter=project)
        if recs is not None:
            recs = filter_records_by_excluded(recs)
            return recs
    except Exception:
        # 索引が壊れていてもユーザー体験は止めない。run_scan へ落とす。
        pass
    result = run_scan(config)
    records = list(result.records)
    if project:
        records = [r for r in records if r.project == project]
    return records


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


def auto_sweep(
    config: Config, *, project: str | None = None, dry_run: bool = False,
) -> list[MoveLogEntry]:
    """--auto: auto_move 対象を各プロジェクトの archive/ へ移送。watching は触らない。

    ``project`` を指定すると、その名前のプロジェクトに属する対象だけを処理する
    （個別プロジェクトの gitignore で docs/local が除外される問題を回避するため、
    スキャンルートは config のまま・出力だけ後段で絞る）。
    """
    result = run_scan(config)
    moved: list[MoveLogEntry] = []
    for doc in result.auto_movable():
        rec = doc.record
        if project and rec.project != project:
            continue
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


def archive_doc(
    doc: ScannedDoc, config: Config, *, dry_run: bool = False, batch_id: str | None = None,
) -> MoveLogEntry:
    """1 ファイルを（ラベル書換なしで）そのまま archive へ移送する。"""
    rec = doc.record
    project_dir, root = _project_dir_for(doc, config)
    dst = archive_file(
        src=Path(rec.path), project_dir=project_dir, archive_dir=_archive_dir_for(doc, config),
        root=root, project=rec.project, status=rec.state, op="archive", dry_run=dry_run,
        batch_id=batch_id,
    )
    return MoveLogEntry(
        ts="", op="archive", project=rec.project, status=rec.state,
        src=rec.path, dst=dst.as_posix(), batch_id=batch_id,
    )


def promote_state(
    config: Config, *, from_state: str = "watching", to_state: str = "done",
    project: str | None = None, dry_run: bool = False,
) -> list[MoveLogEntry]:
    """release sweep: 溜まった from_state を to_state へ一括昇格し archive へ移送。"""
    result = run_scan(config)
    sm = config.state_model
    target = sm.by_key(to_state)
    # 未知の to_state（タイプミス等）だと relabel されないまま archive 移送され、
    # ラベルと配置が矛盾する。移送前に弾く。
    if target is None:
        raise ValueError(f"未知の to_state: {to_state}")
    moved: list[MoveLogEntry] = []
    for doc in result.docs:
        rec = doc.record
        if rec.state != from_state:
            continue
        if project and rec.project != project:
            continue
        # docsweep_policy: never_archive は昇格しても archive しない（policy による保持）。
        if rec.docsweep_policy == "never_archive":
            continue
        project_dir, root = _project_dir_for(doc, config)
        if not dry_run:
            if not relabel_file(Path(rec.path), f"[{target.label(config.lang)}]", config):
                raise ValueError(
                    f"H1 ラベルを書き換えられないため昇格を中止しました（H1 が無い/UTF-8 でない）: {rec.path}"
                )
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
    # sweep / promote は複数プロジェクト横断で動くため、起動時に読んだ単一 config ではなく
    # 対象プロジェクト自身の .docsweep.yaml（あれば）を優先する。これにより cwd や
    # --project-dir フラグに依存せず、どこから実行しても各プロジェクトの設定が効く。
    from_project = project_archive_dir(Path(doc.record.project_root))
    if from_project:
        return from_project
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
        # docsweep_policy: never_archive は明示 discard/promote でも archive しない。
        # 手動 apply でも policy を尊重する（Web UI の三点メニューからでも同じ挙動）。
        if rec.docsweep_policy == "never_archive":
            raise ValueError(
                f"docsweep_policy: never_archive のため archive 移送できません: {rec.path}"
            )
        target_key = "discarded" if action == Action.DISCARD.value else "done"
        st = sm.by_key(target_key)
        if st and not dry_run:
            # ラベルを書き換えられない（H1 が無い/読めない）まま移送すると、配置と
            # ラベルが矛盾した archive ファイルができる。書換失敗時は移送を中止する。
            if not relabel_file(path, f"[{st.label(config.lang)}]", config):
                raise ValueError(
                    f"H1 ラベルを書き換えられないため移送を中止しました（H1 が無い/UTF-8 でない）: {rec.path}"
                )
        dst = archive_file(
            src=path, project_dir=project_dir, archive_dir=_archive_dir_for(doc, config),
            root=root, project=rec.project, status=target_key, op=action, dry_run=dry_run,
        )
        return MoveLogEntry(ts="", op=action, project=rec.project, status=target_key, src=rec.path, dst=dst.as_posix())

    if action == Action.RESUME.value:
        # 2026-06-23 改修: active/対応中 を in-progress に統合。種別による振り分けは不要に。
        target_key = "in-progress"
        st = sm.by_key(target_key)
        if st and not dry_run:
            relabel_file(path, f"[{st.label(config.lang)}]", config)
            append_move_log(root, MoveLogEntry(ts=_now_iso(), op="resume", project=rec.project, status=target_key, src=rec.path, dst=None))
        return MoveLogEntry(ts="", op="resume", project=rec.project, status=target_key, src=rec.path, dst=None)

    if action == Action.RELABEL.value:
        if not to:
            raise ValueError("relabel には to（ラベル名）が必要です")
        st = sm.match(to)
        label = f"[{st.label(config.lang)}]" if st else (to if to.startswith("[") else f"[{to}]")
        if not dry_run:
            relabel_file(path, label, config)
            append_move_log(root, MoveLogEntry(ts=_now_iso(), op="relabel", project=rec.project, status=(st.key if st else None), src=rec.path, dst=None))
        return MoveLogEntry(ts="", op="relabel", project=rec.project, status=(st.key if st else None), src=rec.path, dst=None)

    raise ValueError(f"未知の action: {action}")


def relabel_file(path: Path, new_label: str, config: Config) -> bool:
    """H1 のラベルを new_label（例 "[完了]"）に書き換える。成功で True。

    非 UTF-8 の文書は errors="replace" で読むと U+FFFD 置換が書き戻されて原本破損するため、
    strict で読み、デコード不能なら何もせず False を返す（破壊しない）。コードフェンス内の
    ``# ...`` を H1 と誤認しないよう、位置特定はマスク版で行い書換は原文へ適用する。
    """
    try:
        # newline="" で読み込み、CRLF/LF を文字列にそのまま保持する（読み書きとも変換しない）。
        text = path.open("r", encoding="utf-8", newline="").read()
    except UnicodeDecodeError:
        return False
    # マスクは長さを保つので、マスク版で得た start/end を原文 text にそのまま使える。
    m = _H1_RE.search(mask_code_fences(text))
    if not m:
        return False
    captured = m.group(1)
    # MULTILINE の (.*) は CRLF の \r を取り込みうる。元の改行を完全保存するため退避→再付与する。
    cr = "\r" if captured.endswith("\r") else ""
    h1 = captured.rstrip("\r").strip()
    lm = _H1_LABEL_RE.match(h1)
    title = lm.group(2).strip() if lm else h1
    new_h1 = f"# {new_label} {title}".rstrip()
    # 元の改行コードを保ったまま H1 行だけ差し替える（OS 依存の CRLF 変換を避ける）。
    new_text = text[: m.start()] + new_h1 + cr + text[m.end():]
    # atomic.py 冒頭の宣言「全ての書き込み API はこのヘルパ経由で MD を更新する」に沿って
    # write_atomic 経由へ寄せる。これによりバックアップ・原子的差し替え（tmp → os.replace）が
    # 効き、Web UI 編集中の md を CLI/MCP 側から書き換える race が壊れにくくなる。
    write_atomic(path, new_text)
    return True
