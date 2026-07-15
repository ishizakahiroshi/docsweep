"""C3: brief 生成のコアロジック。CLI / Web / MCP がすべてここを呼ぶ。

brief は「今日 1 個だけやろう」を断定するための朝の入口。表示要素は 4 つ:

1. ``today_pick``: 「今日の 1 個」（最高スコア・必ず 1 件決まる）
2. ``co_running``: 併走中（in-progress 上位 ~3 件、today_pick を除く）
3. ``watchouts``: 要注意 stale（NEEDS_DECISION / OVERDUE_TODO 等を持つ古い未終端）
4. ``yesterday_done``: 昨日終わったこと（mtime が 24h 以内の done/discarded）

すべてプロジェクト粒度の概念で、``--project all`` 時は project_id ごとに同じ束を作る。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..config import Config
from ..engine import scan_records
from ..models import FileRecord, Flag
from .score import ScoreBreakdown, score_record, tiebreak_key


def _short_record(rec: FileRecord, score: ScoreBreakdown | None = None) -> dict:
    """brief 表示で使う slim 表現。冗長な path は basename を併記して人間にも AI にも読める形に。"""
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
        out["score"] = score.to_dict()
    return out


@dataclass
class ProjectBrief:
    """1 プロジェクト分の brief。``BriefResult.projects`` に複数並ぶ。"""

    project: str
    today_pick: dict | None
    co_running: list[dict] = field(default_factory=list)
    watchouts: list[dict] = field(default_factory=list)
    yesterday_done: list[dict] = field(default_factory=list)
    open_count: int = 0
    stale_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BriefResult:
    """brief の最終出力。CLI/Web/MCP 共通。"""

    mode: str  # "single" | "all"
    generated_at: str
    projects: list[ProjectBrief] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "generated_at": self.generated_at,
            "projects": [p.to_dict() for p in self.projects],
        }


def _is_open_state(rec: FileRecord) -> bool:
    """brief の主要対象（done/discarded 以外の未終端）。"""
    return rec.state in {"in-progress", "planned", "watching", "pending"}


def _yesterday_window(now: datetime) -> tuple[float, float]:
    """直近 24h ウィンドウの mtime 範囲（epoch 秒）。"""
    end = now.timestamp()
    start = (now - timedelta(hours=24)).timestamp()
    return start, end


def _build_for_project(
    records: list[FileRecord], project: str, *, today: date, now: datetime
) -> ProjectBrief:
    """1 プロジェクト分のレコードから ProjectBrief を組み立てる。"""
    open_recs = [r for r in records if _is_open_state(r)]
    scored: list[tuple[FileRecord, ScoreBreakdown]] = sorted(
        ((r, score_record(r, today=today)) for r in open_recs),
        key=lambda pair: (-pair[1].total, tiebreak_key(pair[0])),
    )

    today_pick: dict | None = None
    co_running: list[dict] = []
    if scored:
        head_rec, head_score = scored[0]
        today_pick = _short_record(head_rec, head_score)
        for rec, sc in scored[1:4]:
            if rec.state == "in-progress" or len(co_running) < 2:
                co_running.append(_short_record(rec, sc))
            if len(co_running) >= 3:
                break

    watchouts: list[dict] = []
    for rec in open_recs:
        flags = set(rec.flags or [])
        if not (flags & {
            Flag.NEEDS_DECISION.value,
            Flag.OVERDUE_TODO.value,
            Flag.OVERDUE_GRADUATE.value,
        }):
            continue
        if today_pick and rec.path == today_pick["path"]:
            continue
        watchouts.append(_short_record(rec, score_record(rec, today=today)))
    watchouts.sort(key=lambda d: -(d.get("score") or {}).get("total", 0.0))
    watchouts = watchouts[:5]

    start, end = _yesterday_window(now)
    yesterday: list[tuple[float, dict]] = []
    for rec in records:
        if rec.state not in {"done", "discarded"}:
            continue
        if rec.mtime and start <= rec.mtime <= end:
            yesterday.append((rec.mtime, _short_record(rec)))
    # 「昨日終わったこと」は新しい順に見せる意図。以前は age_days 降順（＝古い順）で
    # 直感と逆になっていた。24h ウィンドウ内では mtime 降順が最も自然。
    yesterday.sort(key=lambda pair: -pair[0])
    yesterday_dicts = [d for _, d in yesterday[:5]]

    stale_count = sum(1 for r in open_recs if Flag.STALE.value in (r.flags or []))

    return ProjectBrief(
        project=project,
        today_pick=today_pick,
        co_running=co_running,
        watchouts=watchouts,
        yesterday_done=yesterday_dicts,
        open_count=len(open_recs),
        stale_count=stale_count,
    )


def _resolve_target_projects(
    records: list[FileRecord],
    *,
    project: str | None,
    all_projects: bool,
    cwd_project: str | None,
) -> list[str]:
    if all_projects:
        names = sorted({r.project for r in records if r.project})
        return names
    if project:
        return [project]
    if cwd_project:
        return [cwd_project]
    # cwd 解決ができない時は records から先頭プロジェクトを 1 つ
    names = sorted({r.project for r in records if r.project})
    return names[:1]


def _detect_cwd_project(config: Config) -> str | None:
    """現在ディレクトリが含まれるプロジェクトを推測する（``cwd プロジェクト`` 既定）。

    まず ``config.roots`` 配下に cwd が含まれていれば、その root の name を返す。
    git remote が使える場合はそちら優先。失敗時は None（呼び出し側で他のフォールバック）。
    """
    import os
    import subprocess

    cwd = Path(os.getcwd()).resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            if url:
                tail = url.rstrip("/").split("/")[-1]
                if tail.endswith(".git"):
                    tail = tail[:-4]
                if tail:
                    return tail
    except (OSError, subprocess.SubprocessError):
        pass

    for root in config.roots:
        try:
            cwd.relative_to(Path(root).resolve())
            return Path(root).name
        except ValueError:
            continue
    return None


def build_brief(
    config: Config,
    *,
    project: str | None = None,
    all_projects: bool = False,
    today: date | None = None,
) -> BriefResult:
    """brief を 1 回ぶん組み立てて返す。

    Args:
        config: ロード済み Config
        project: 単一プロジェクト指定（``project_id`` 文字列）
        all_projects: True で search_paths の全プロジェクトを横並び要約
        today: テスト用の日付固定。未指定なら ``date.today()``
    """
    now = datetime.now(timezone.utc).astimezone()
    today_date = today or now.date()

    records = scan_records(config)

    cwd_proj = _detect_cwd_project(config) if not (project or all_projects) else None
    targets = _resolve_target_projects(
        records, project=project, all_projects=all_projects, cwd_project=cwd_proj,
    )

    by_project: dict[str, list[FileRecord]] = {}
    for r in records:
        if r.project:
            by_project.setdefault(r.project, []).append(r)

    projects: list[ProjectBrief] = []
    for name in targets:
        proj_recs = by_project.get(name, [])
        projects.append(_build_for_project(proj_recs, name, today=today_date, now=now))

    return BriefResult(
        mode="all" if all_projects else "single",
        generated_at=now.isoformat(),
        projects=projects,
    )
