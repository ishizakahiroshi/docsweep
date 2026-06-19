"""横断集約 INDEX（develop 全体を 1 か所で把握）— C5。

実ファイルは各プロジェクトに残したまま、スキャンルート直下に論理集約の INDEX を生成する。
- INDEX.json: AI エージェント連携用（ステータス別・pending 常設・要判断/要修正）
- INDEX.md: 人間がエディタで一覧
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Config
from .engine import ScanResult, run_scan
from .models import Flag

INDEX_DIRNAME = ".docsweep"


@dataclass
class IndexData:
    roots: list[str]
    counts: dict
    by_state: dict[str, list[dict]]
    pending: list[dict]
    needs_decision: list[dict]
    needs_fix: list[dict]
    overdue_todo: list[dict] = None  # type: ignore[assignment]
    overdue_graduate: list[dict] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.overdue_todo is None:
            self.overdue_todo = []
        if self.overdue_graduate is None:
            self.overdue_graduate = []

    def to_dict(self) -> dict:
        return asdict(self)


def build_index(config: Config, result: ScanResult | None = None) -> IndexData:
    result = result or run_scan(config)
    recs = result.records

    by_state: dict[str, list[dict]] = {}
    for r in recs:
        by_state.setdefault(r.state or "unknown", []).append(r.to_dict())
    for v in by_state.values():
        v.sort(key=lambda d: d["age_days"], reverse=True)

    pending = [r.to_dict() for r in recs if r.state == "pending"]
    needs_decision = sorted(
        [r.to_dict() for r in recs if Flag.NEEDS_DECISION.value in r.flags],
        key=lambda d: d["age_days"], reverse=True,
    )
    needs_fix = [r.to_dict() for r in recs if Flag.NEEDS_FIX.value in r.flags]
    overdue_todo = sorted(
        [r.to_dict() for r in recs if Flag.OVERDUE_TODO.value in r.flags],
        key=lambda d: d.get("due") or "",
    )
    overdue_graduate = sorted(
        [r.to_dict() for r in recs if Flag.OVERDUE_GRADUATE.value in r.flags],
        key=lambda d: d.get("due") or "",
    )

    counts = {
        "total": len(recs),
        "projects": len({r.project for r in recs}),
        "needs_decision": len(needs_decision),
        "needs_fix": len(needs_fix),
        "pending": len(pending),
        "archivable": sum(1 for r in recs if r.auto_movable and r.archivable),
        "overdue_todo": len(overdue_todo),
        "overdue_graduate": len(overdue_graduate),
    }
    return IndexData(
        roots=[str(r) for r in config.roots],
        counts=counts,
        by_state=by_state,
        pending=pending,
        needs_decision=needs_decision,
        needs_fix=needs_fix,
        overdue_todo=overdue_todo,
        overdue_graduate=overdue_graduate,
    )


def _render_row(d: dict) -> str:
    name = Path(d["path"]).name
    label = d.get("state_label") or "[?]"
    summary = f" — {d['summary']}" if d.get("summary") else ""
    flags = f" `{','.join(d['flags'])}`" if d.get("flags") else ""
    return f"- {label} **{d['project']}**/{name} · {d['age_days']}d{flags}{summary}"


def render_markdown(idx: IndexData, state_model) -> str:
    c = idx.counts
    lines: list[str] = [
        "# docsweep INDEX",
        "",
        f"> 横断集約: {c['projects']} プロジェクト / {c['total']} 件 ＝ "
        f"要判断 {c['needs_decision']} · 要修正 {c['needs_fix']} · 保留 {c['pending']} · archive候補 {c['archivable']}",
        "",
    ]
    if idx.needs_decision:
        lines += ["## ⚠ 要判断（陳腐化）", ""]
        lines += [_render_row(d) for d in idx.needs_decision]
        lines += [""]
    if idx.pending:
        lines += ["## 💤 保留（pending）", ""]
        lines += [_render_row(d) for d in idx.pending]
        lines += [""]
    if idx.needs_fix:
        lines += ["## 🔧 要修正（ラベル欠落・パース不能）", ""]
        lines += [_render_row(d) for d in idx.needs_fix]
        lines += [""]

    lines += ["## ステータス別", ""]
    for key in sorted(idx.by_state):
        recs = idx.by_state[key]
        st = state_model.by_key(key) if state_model else None
        label = f"[{st.label()}]" if st else f"[{key}]"
        lines += [f"### {label} ({len(recs)})", ""]
        lines += [_render_row(d) for d in recs]
        lines += [""]
    return "\n".join(lines).rstrip() + "\n"


def index_dir(config: Config) -> Path:
    base = config.roots[0] if config.roots else Path.cwd()
    return Path(base) / INDEX_DIRNAME


def write_index(config: Config, result: ScanResult | None = None) -> tuple[Path, Path]:
    """INDEX.json / INDEX.md をスキャンルート直下の .docsweep/ に書き出す。"""
    idx = build_index(config, result)
    out_dir = index_dir(config)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "INDEX.json"
    md_path = out_dir / "INDEX.md"
    json_path.write_text(json.dumps(idx.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(idx, config.state_model), encoding="utf-8")
    return json_path, md_path
