"""--report（人間向け週次レポート）と --summary（AI 要約 export・INDEX 圧縮）。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .config import Config
from .engine import run_scan
from .index import build_index
from .models import Action
from .state import load as load_state


def render_report(config: Config) -> str:
    """人間が読む週次サマリー（テキスト）。"""
    idx = build_index(config)
    c = idx.counts
    lines = [
        "docsweep レポート",
        "=" * 40,
        f"プロジェクト数: {c['projects']}   総ファイル: {c['total']}",
        f"要判断(陳腐化): {c['needs_decision']}   要修正: {c['needs_fix']}   "
        f"保留: {c['pending']}   archive候補: {c['archivable']}",
        "",
    ]
    if idx.needs_decision:
        lines.append("■ いま判断が要るもの（古い順・上位10）")
        for d in idx.needs_decision[:10]:
            lines.append(f"  {d['state_label']} {d['project']}/{Path(d['path']).name}  {d['age_days']}d")
        lines.append("")
    if c["archivable"]:
        lines.append(f"■ archive へ運べる確定ファイル: {c['archivable']} 件（`python -m docsweep sweep` で移送）")
        lines.append("")
    return "\n".join(lines)


def _rel_path(d: dict) -> str:
    """project_root 起点の相対パス（無理なら basename）。AI が場所を掴みやすい表記。"""
    root = d.get("project_root") or ""
    p = Path(d["path"])
    if root:
        try:
            return p.relative_to(root).as_posix()
        except ValueError:
            pass
    return p.name


# 書き込み系アクション（C3 で追加）。AI / Web UI に「ここから何を呼べるか」を機械的に伝える。
# 親 plan C6 で確定した閉じた集合のうち、書き込みに関わるものをラベル状態から導出する。
_WRITE_ACTIONS_BY_STATE: dict[str | None, tuple[str, ...]] = {
    # 可動状態は全て update_due / update_content / relabel が許可される。
    # archive は state.archive を見て個別判定（done/discarded のみ）。
    "planned": ("update_due", "update_content"),
    "in-progress": ("update_due", "update_content"),
    "watching": ("update_due", "update_content"),
    "pending": ("update_due", "update_content"),
    "done": ("update_content",),  # 期日は意味を失うので update_due を出さない
    "discarded": ("update_content",),
    # 2026-06-23 改修: active を in-progress に統合。
}


def _augment_actions(d: dict) -> list[str]:
    """既存 allowed_actions（engine.classify が付与）に、書き込み系アクションを足す。

    `relabel` は engine 側で既に state が判明していれば付くので、ここでは update_due /
    update_content / archive のみ追加。archive は state.archive 由来の archivable フラグで判断する。
    """
    base = list(d.get("allowed_actions") or [])
    extras = list(_WRITE_ACTIONS_BY_STATE.get(d.get("state"), ()))
    if d.get("archivable"):
        extras.append(Action.DISCARD.value if d.get("state") == "discarded" else "archive")
        # state=done でも archive アクションを許可（明示移送）。
        if d.get("state") == "done" and "archive" not in extras:
            extras.append("archive")
    merged: list[str] = list(base)
    for e in extras:
        if e not in merged:
            merged.append(e)
    return merged


def _mtime_iso(d: dict) -> str | None:
    """epoch 秒 → ローカルタイムゾーン付き ISO8601。AI/Web 側の表示用。"""
    mtime = d.get("mtime")
    if not mtime:
        return None
    try:
        return datetime.fromtimestamp(float(mtime)).astimezone().isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return None


def _overdue_kind(d: dict, *, today: date | None = None) -> tuple[str | None, int | None]:
    """(overdue_kind, overdue_days) を返す。

    overdue_kind の値:
    - "overdue_todo": 計画/実行中/保留 で due 超過（やり忘れ）
    - "overdue_graduate": 様子見で due 超過（卒業判定どき）
    - "today": 今日が期日（state が可動なもの）
    - "future": 未来期日
    - "missing": 期日未設定（可動状態のみ）
    - None: 判定対象外（done / discarded / parse error）
    """
    state = d.get("state")
    if state in {"done", "discarded"} or state is None:
        return (None, None)
    if d.get("due_parse_error"):
        return (None, None)
    due_raw = d.get("due")
    if not due_raw:
        return ("missing", None)
    try:
        due_date = date.fromisoformat(due_raw)
    except (TypeError, ValueError):
        return (None, None)
    today = today or date.today()
    delta = (today - due_date).days
    if delta > 0:
        kind = "overdue_graduate" if state == "watching" else "overdue_todo"
        return (kind, delta)
    if delta == 0:
        return ("today", 0)
    return ("future", delta)  # delta は負値（残日数）


def _postpone_info(d: dict) -> tuple[int, int]:
    """(postpone_count, label_history_count) を state.json から取得する。

    project_root が無い / state.json が無い場合は (0, 0)。
    """
    project_root = d.get("project_root")
    path = d.get("path")
    if not project_root or not path:
        return (0, 0)
    try:
        proot = Path(project_root)
        sdoc = load_state(proot)
        # 相対キーは state._rel_key と同じ規約。
        try:
            key = Path(path).resolve().relative_to(proot.resolve()).as_posix()
        except ValueError:
            key = Path(path).as_posix()
        fs = sdoc.get(key)
        return (int(fs.postpone_count or 0), len(fs.label_history or []))
    except OSError:
        return (0, 0)


def slim_record(d: dict) -> dict:
    """CLI ``triage``/``summary`` / MCP / Web が共有する残作業 1 件のスリム表現。

    「あれ何すべきだっけ」に直結する最小フィールド（リポ＝project / 相対パス /
    H1 タイトル / ステータスラベル / 種別 / 経過日数）＋ AI が機械実行できる actions。

    C3 で due / overdue / postpone 系の機械処理用フィールドを追加（旧クライアントは
    無視するだけで動く・非破壊拡張）。
    """
    overdue_kind, overdue_days = _overdue_kind(d)
    postpone_count, label_history_count = _postpone_info(d)
    return {
        "project": d["project"],
        "rel": _rel_path(d),
        "title": d.get("title"),
        "state": d.get("state_label") or d.get("state"),
        "type": d.get("type"),
        "age_days": d["age_days"],
        "due": d.get("due"),
        "due_raw": d.get("due"),
        "due_parse_error": bool(d.get("due_parse_error")),
        "overdue_kind": overdue_kind,
        "overdue_days": overdue_days,
        "postpone_count": postpone_count,
        "label_history_count": label_history_count,
        "mtime_iso": _mtime_iso(d),
        "summary": d.get("summary"),
        "flags": d.get("flags") or [],
        "actions": _augment_actions(d),
        "path": d["path"],
    }


def _filter_by_project(items: list[dict], project: str | None) -> list[dict]:
    """``project`` 指定時に当該プロジェクト名のレコードへ絞り込む（後段フィルタ）。

    スキャンルートを動かさないのは ``auto_sweep`` / ``promote_state`` と同じ方針。
    個別プロジェクトの ``.gitignore`` で ``docs/local/`` が除外される問題を避ける。
    """
    if not project:
        return items
    return [d for d in items if d.get("project") == project]


def _due_axis_counts(records: list[dict]) -> dict:
    """due 軸の集計（today / future / missing_due）を records から作る。

    overdue_todo / overdue_graduate は engine.classify が flags に立てているが、
    today / future / missing_due は dashboard 表示用の派生集計なのでここで導出する。
    archive 済（done/discarded）は除外（軸 2 は可動状態のみ意味を持つ）。
    """
    today_n = future_n = missing_n = 0
    today_d = date.today()
    for d in records:
        if d.get("state") in {"done", "discarded"} or d.get("state") is None:
            continue
        if d.get("due_parse_error"):
            continue
        due_raw = d.get("due")
        if not due_raw:
            missing_n += 1
            continue
        try:
            dd = date.fromisoformat(due_raw)
        except (TypeError, ValueError):
            continue
        delta = (today_d - dd).days
        if delta == 0:
            today_n += 1
        elif delta < 0:
            future_n += 1
        # delta > 0 は overdue_* で別途数えられている
    return {"today": today_n, "future": future_n, "missing_due": missing_n}


def _scoped_counts(idx, project: str | None) -> dict:
    """``project`` 指定時は当該プロジェクトのレコードだけで counts を再集計する。

    フィルタ後の items 数と counts がズレると AI/人間どちらにも混乱を招くため、
    items と counts は同じスコープで揃える（``projects`` は per-project スコープでは
    自明に 1 になるので 1 を返す）。
    """
    all_records: list[dict] = [d for recs in idx.by_state.values() for d in recs]
    if project:
        all_records = _filter_by_project(all_records, project)
    due_counts = _due_axis_counts(all_records)

    if not project:
        base = dict(idx.counts)
        base.update(due_counts)
        return base
    nd = _filter_by_project(idx.needs_decision, project)
    nf = _filter_by_project(idx.needs_fix, project)
    pn = _filter_by_project(idx.pending, project)
    ot = _filter_by_project(idx.overdue_todo, project)
    og = _filter_by_project(idx.overdue_graduate, project)
    total = len(all_records)
    archivable = sum(
        1 for d in all_records
        if d.get("auto_movable") and d.get("archivable")
    )
    base = {
        "total": total,
        "projects": 1 if total else 0,
        "needs_decision": len(nd),
        "needs_fix": len(nf),
        "pending": len(pn),
        "archivable": archivable,
        "overdue_todo": len(ot),
        "overdue_graduate": len(og),
    }
    base.update(due_counts)
    return base


def build_triage(config: Config, *, project: str | None = None) -> dict:
    """AI が「次に何を続ければいいか」を判断するための残作業ビュー。

    summary が横断 INDEX 全体の俯瞰なのに対し、triage はそこから *いま着手すべき
    生きた残作業だけ* を行動可能な粒度に絞った入口。既定フィルタ＝要判断（陳腐化）＋
    保留、並び順は古い順（放置されたものを上に）。壊れたラベル（要修正）は別枠で添える。

    ``project`` を指定すると当該プロジェクト名に絞った subset を返す
    （``sweep`` / ``promote`` と同じ後段フィルタパターン）。
    """
    idx = build_index(config)
    # 要判断＋保留をマージ（同一ファイルが両方に出ても path で一意化）。
    seen: dict[str, dict] = {}
    for d in idx.needs_decision + idx.pending:
        seen[d["path"]] = d
    items = sorted(seen.values(), key=lambda d: d["age_days"], reverse=True)
    items = _filter_by_project(items, project)
    needs_fix = _filter_by_project(idx.needs_fix, project)
    return {
        "counts": _scoped_counts(idx, project),
        "items": [slim_record(d) for d in items],
        "needs_fix": [slim_record(d) for d in needs_fix],
    }


def render_summary(config: Config, *, project: str | None = None) -> str:
    """AI に渡す圧縮 JSON（INDEX 全体を要点だけに絞った俯瞰）。

    ``project`` を指定するとそのプロジェクトの subset 版を返す
    （他コマンドと引数を揃え、AI が当該プロジェクトを深掘りする際に使う）。
    """
    idx = build_index(config)
    payload = {
        "counts": _scoped_counts(idx, project),
        "needs_decision": [slim_record(d) for d in _filter_by_project(idx.needs_decision, project)],
        "pending": [slim_record(d) for d in _filter_by_project(idx.pending, project)],
        "needs_fix": [slim_record(d) for d in _filter_by_project(idx.needs_fix, project)],
        "overdue_todo": [slim_record(d) for d in _filter_by_project(idx.overdue_todo, project)],
        "overdue_graduate": [slim_record(d) for d in _filter_by_project(idx.overdue_graduate, project)],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
