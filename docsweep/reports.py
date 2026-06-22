"""--report（人間向け週次レポート）と --summary（AI 要約 export・INDEX 圧縮）。"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .engine import run_scan
from .index import build_index


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


def slim_record(d: dict) -> dict:
    """CLI ``triage``/``summary`` / MCP / Web が共有する残作業 1 件のスリム表現。

    「あれ何すべきだっけ」に直結する最小フィールド（リポ＝project / 相対パス /
    H1 タイトル / ステータスラベル / 種別 / 経過日数）＋ AI が機械実行できる actions。
    """
    return {
        "project": d["project"],
        "rel": _rel_path(d),
        "title": d.get("title"),
        "state": d.get("state_label") or d.get("state"),
        "type": d.get("type"),
        "age_days": d["age_days"],
        "due": d.get("due"),
        "summary": d.get("summary"),
        "flags": d.get("flags") or [],
        "actions": d.get("allowed_actions") or [],
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


def _scoped_counts(idx, project: str | None) -> dict:
    """``project`` 指定時は当該プロジェクトのレコードだけで counts を再集計する。

    フィルタ後の items 数と counts がズレると AI/人間どちらにも混乱を招くため、
    items と counts は同じスコープで揃える（``projects`` は per-project スコープでは
    自明に 1 になるので 1 を返す）。
    """
    if not project:
        return idx.counts
    nd = _filter_by_project(idx.needs_decision, project)
    nf = _filter_by_project(idx.needs_fix, project)
    pn = _filter_by_project(idx.pending, project)
    ot = _filter_by_project(idx.overdue_todo, project)
    og = _filter_by_project(idx.overdue_graduate, project)
    total = sum(len(_filter_by_project(v, project)) for v in idx.by_state.values())
    archivable = sum(
        1 for recs in idx.by_state.values() for d in recs
        if d.get("project") == project and d.get("auto_movable") and d.get("archivable")
    )
    return {
        "total": total,
        "projects": 1 if total else 0,
        "needs_decision": len(nd),
        "needs_fix": len(nf),
        "pending": len(pn),
        "archivable": archivable,
        "overdue_todo": len(ot),
        "overdue_graduate": len(og),
    }


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
