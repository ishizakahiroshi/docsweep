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
        lines.append(f"■ archive へ運べる確定ファイル: {c['archivable']} 件（`docsweep sweep` で移送）")
        lines.append("")
    return "\n".join(lines)


def render_summary(config: Config) -> str:
    """AI に渡す圧縮 JSON（INDEX を要点だけに絞る）。"""
    idx = build_index(config)

    def slim(d: dict) -> dict:
        return {
            "project": d["project"],
            "name": Path(d["path"]).name,
            "state": d.get("state"),
            "age_days": d["age_days"],
            "summary": d.get("summary"),
            "flags": d.get("flags") or [],
            "allowed_actions": d.get("allowed_actions") or [],
            "path": d["path"],
        }

    payload = {
        "counts": idx.counts,
        "needs_decision": [slim(d) for d in idx.needs_decision],
        "pending": [slim(d) for d in idx.pending],
        "needs_fix": [slim(d) for d in idx.needs_fix],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
