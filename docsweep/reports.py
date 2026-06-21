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


def build_triage(config: Config) -> dict:
    """AI が「次に何を続ければいいか」を判断するための残作業ビュー。

    summary が横断 INDEX 全体の俯瞰なのに対し、triage はそこから *いま着手すべき
    生きた残作業だけ* を行動可能な粒度に絞った入口。既定フィルタ＝要判断（陳腐化）＋
    保留、並び順は古い順（放置されたものを上に）。壊れたラベル（要修正）は別枠で添える。
    """
    idx = build_index(config)
    # 要判断＋保留をマージ（同一ファイルが両方に出ても path で一意化）。
    seen: dict[str, dict] = {}
    for d in idx.needs_decision + idx.pending:
        seen[d["path"]] = d
    items = sorted(seen.values(), key=lambda d: d["age_days"], reverse=True)
    return {
        "counts": idx.counts,
        "items": [slim_record(d) for d in items],
        "needs_fix": [slim_record(d) for d in idx.needs_fix],
    }


def render_summary(config: Config) -> str:
    """AI に渡す圧縮 JSON（INDEX 全体を要点だけに絞った俯瞰）。"""
    idx = build_index(config)
    payload = {
        "counts": idx.counts,
        "needs_decision": [slim_record(d) for d in idx.needs_decision],
        "pending": [slim_record(d) for d in idx.pending],
        "needs_fix": [slim_record(d) for d in idx.needs_fix],
        "overdue_todo": [slim_record(d) for d in idx.overdue_todo],
        "overdue_graduate": [slim_record(d) for d in idx.overdue_graduate],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
