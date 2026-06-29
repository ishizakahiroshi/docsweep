"""C4: cross — 全プロジェクト束ねた俯瞰ロジック。

設計:
- 入力: ``search_paths`` 全体の FileRecord（``scan_records(config)`` 経由）
- ``top_pick``: 全プロジェクト束ねた最高スコア 1 件（決定性: tiebreak_key で安定化）
- ``runners_up``: 次点 3 件（top_pick 除く、プロジェクト跨ぎ）
- ``frozen_candidates``: 凍結予備軍（open だが長期未更新・スコア低い）
- ``project_summaries``: 各プロジェクトの open/stale 件数と今日の 1 件
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from ..config import Config
from ..engine import scan_records
from ..models import FileRecord, Flag
from ..brief.score import _urgency_score, score_record, tiebreak_key

# 凍結予備軍の閾値: open 状態だがこの日数以上動きが無いもの。
# done/discarded は対象外（archive 系で別途処理される）。
FROZEN_AGE_DAYS = 90


def _short_record(rec: FileRecord, *, score: float | None = None) -> dict:
    out = {
        "path": rec.path,
        "rel": Path(rec.path).name,
        "project": rec.project,
        "type": rec.type,
        "state": rec.state,
        "state_label": rec.state_label,
        "title": rec.title,
        "summary": rec.summary,
        "age_days": rec.age_days,
        "due": rec.due,
        "owner": rec.owner,
        "flags": list(rec.flags or []),
        "tags": list(rec.tags or []),
    }
    if score is not None:
        out["score"] = round(score, 2)
    return out


@dataclass
class ProjectSummary:
    project: str
    open_count: int
    stale_count: int
    today_one: dict | None  # そのプロジェクトでの最高スコア 1 件

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CrossResult:
    generated_at: str
    project_filter: list[str]  # 絞り込まれたプロジェクト名 (空 = 全プロジェクト)
    top_pick: dict | None
    runners_up: list[dict] = field(default_factory=list)
    frozen_candidates: list[dict] = field(default_factory=list)
    project_summaries: list[ProjectSummary] = field(default_factory=list)
    total_projects: int = 0
    total_open: int = 0

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "project_filter": list(self.project_filter),
            "top_pick": self.top_pick,
            "runners_up": list(self.runners_up),
            "frozen_candidates": list(self.frozen_candidates),
            "project_summaries": [p.to_dict() for p in self.project_summaries],
            "total_projects": self.total_projects,
            "total_open": self.total_open,
        }


def _is_open_state(rec: FileRecord) -> bool:
    return rec.state in {"in-progress", "planned", "watching", "pending"}


def build_cross(
    config: Config,
    *,
    projects: list[str] | None = None,
    today: date | None = None,
) -> CrossResult:
    """全プロジェクト横断ビューを 1 件分組み立てる。

    Args:
        config: ロード済み Config
        projects: 絞り込むプロジェクト名のリスト（``--project a,b,c``）。None で全体。
        today: テスト用日付固定。

    Returns:
        ``CrossResult``。top_pick が None の場合は対象 open 0 件を意味する。
    """
    now = datetime.now(timezone.utc).astimezone()
    today_date = today or now.date()

    all_records = scan_records(config)
    project_filter = list(projects or [])
    if project_filter:
        all_records = [r for r in all_records if r.project in project_filter]

    open_records = [r for r in all_records if _is_open_state(r)]

    # 全プロジェクト束ねたスコア順
    scored: list[tuple[FileRecord, float]] = []
    for rec in open_records:
        sb = score_record(rec, today=today_date)
        scored.append((rec, sb.total))
    scored.sort(key=lambda pair: (-pair[1], tiebreak_key(pair[0])))

    top_pick: dict | None = None
    runners_up: list[dict] = []
    if scored:
        head, head_score = scored[0]
        top_pick = _short_record(head, score=head_score)
        for rec, sc in scored[1:4]:
            runners_up.append(_short_record(rec, score=sc))

    # 凍結予備軍: open かつ age >= FROZEN_AGE_DAYS かつ urgency シグナル無し
    # （NEEDS_DECISION / OVERDUE は active なので除外。純粋に「長期間動いていない」もの）
    frozen: list[dict] = []
    for rec, sc in scored:
        if rec.age_days < FROZEN_AGE_DAYS:
            continue
        if _urgency_score(rec, today=today_date) > 0:
            continue  # urgency 立ってる = まだ active
        frozen.append(_short_record(rec, score=sc))
        if len(frozen) >= 10:
            break

    # project_summaries: プロジェクトごとに「そのプロジェクト内の最高スコア 1 件」
    by_project: dict[str, list[tuple[FileRecord, float]]] = {}
    for rec, sc in scored:
        if rec.project:
            by_project.setdefault(rec.project, []).append((rec, sc))

    open_by_project: dict[str, int] = {}
    stale_by_project: dict[str, int] = {}
    for rec in open_records:
        open_by_project[rec.project] = open_by_project.get(rec.project, 0) + 1
        if Flag.STALE.value in (rec.flags or []):
            stale_by_project[rec.project] = stale_by_project.get(rec.project, 0) + 1

    summaries: list[ProjectSummary] = []
    all_project_names = sorted({r.project for r in all_records if r.project})
    for name in all_project_names:
        top_one: dict | None = None
        if name in by_project and by_project[name]:
            rec, sc = by_project[name][0]
            top_one = _short_record(rec, score=sc)
        summaries.append(ProjectSummary(
            project=name,
            open_count=open_by_project.get(name, 0),
            stale_count=stale_by_project.get(name, 0),
            today_one=top_one,
        ))

    return CrossResult(
        generated_at=now.isoformat(),
        project_filter=project_filter,
        top_pick=top_pick,
        runners_up=runners_up,
        frozen_candidates=frozen,
        project_summaries=summaries,
        total_projects=len(all_project_names),
        total_open=len(open_records),
    )


def explain_score(config: Config, file_id_or_rel: str, *, today: date | None = None) -> dict | None:
    """``cross --explain <rel|path>`` 用。指定ファイルのスコア内訳を返す。

    Args:
        config: ロード済み Config
        file_id_or_rel: 絶対パス / 相対パス / basename いずれか
        today: テスト用日付固定

    Returns:
        スコア内訳の dict。該当ファイル未検出時は None。
    """
    today_date = today or datetime.now().date()
    records = scan_records(config)
    target = Path(file_id_or_rel)
    candidates = [
        r for r in records
        if r.path == str(target) or Path(r.path).name == target.name
    ]
    if not candidates:
        return None
    rec = candidates[0]
    sb = score_record(rec, today=today_date)
    return {
        "path": rec.path,
        "rel": Path(rec.path).name,
        "project": rec.project,
        "state": rec.state,
        "score": sb.to_dict(),
    }
