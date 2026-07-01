"""``docsweep activity`` — 過去に触ったもの／今後期限のものを日付でまとめる。

新規永続化は一切行わない。``scan_records()`` が既に返す ``mtime``（過去日軸）と
``due``（未来日軸）だけを使い、日付ごとにグルーピングする薄い読み取り専用ロジック。
today 自身は両軸を出す。plan_activity-summary.md C1 の主要 deliverable。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .brief.service import _detect_cwd_project, _resolve_target_projects
from .config import Config
from .engine import scan_records
from .models import FileRecord
from .services.due import DueParseError, resolve_relative_offset


class ActivityDateError(ValueError):
    """``--date``/``--since``/``--until`` の指定が解釈できないときに発生。"""


def _resolve_date_token(token: str, *, today: date) -> date:
    """``--date`` の 1 トークンを解決する（today/yesterday/tomorrow/YYYY-MM-DD）。"""
    t = token.strip().lower()
    if t == "today":
        return today
    if t == "yesterday":
        return today - timedelta(days=1)
    if t == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(token.strip())
    except ValueError as e:
        raise ActivityDateError(f"--date を解釈できません: {token!r}") from e


def resolve_target_dates(
    dates: list[str] | None,
    *,
    since: str | None,
    until: str | None,
    today: date,
) -> list[date]:
    """CLI 引数から対象日付の一覧（昇順・重複無し）を組み立てる。

    ``--since``/``--until`` と ``--date`` は和集合（両方指定されたら両方を含める）。
    どちらも未指定なら既定（today + yesterday）。
    """
    out: set[date] = set()
    if dates:
        for tok in dates:
            out.add(_resolve_date_token(tok, today=today))
    if since or until:
        try:
            start = resolve_relative_offset(since, today=today) if since else today
            end = resolve_relative_offset(until, today=today) if until else today
        except DueParseError as e:
            raise ActivityDateError(str(e)) from e
        if start > end:
            start, end = end, start
        cur = start
        while cur <= end:
            out.add(cur)
            cur += timedelta(days=1)
    if not out:
        out = {today, today - timedelta(days=1)}
    return sorted(out)


def _axes_for_date(d: date, *, today: date) -> tuple[bool, bool]:
    """(mtime 軸を出すか, due 軸を出すか) を返す。today 自身は両方 True。"""
    if d < today:
        return (True, False)
    if d > today:
        return (False, True)
    return (True, True)


def _short_record(rec: FileRecord) -> dict:
    return {
        "path": rec.path,
        "rel": Path(rec.path).name,
        "project": rec.project,
        "type": rec.type,
        "state": rec.state,
        "state_label": rec.state_label,
        "title": rec.title,
        "due": rec.due,
        "age_days": rec.age_days,
    }


@dataclass
class DateBucket:
    """1 日分のグルーピング結果。"""

    touched: list[dict] = field(default_factory=list)  # mtime 軸（触ったもの）
    due: list[dict] = field(default_factory=list)  # due 軸（期限のもの）

    def to_dict(self) -> dict:
        return {"touched": list(self.touched), "due": list(self.due)}


@dataclass
class ActivityResult:
    """activity の最終出力。CLI/JSON 共通。"""

    generated_at: str
    today: str
    dates: dict[str, DateBucket] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "today": self.today,
            "dates": {k: v.to_dict() for k, v in self.dates.items()},
        }


def build_activity(
    config: Config,
    *,
    dates: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    project: str | None = None,
    all_projects: bool = False,
    today: date | None = None,
) -> ActivityResult:
    """activity を 1 回ぶん組み立てて返す。

    Args:
        config: ロード済み Config
        dates: ``--date`` の複数指定（today/yesterday/tomorrow/YYYY-MM-DD）
        since: ``--since``（絶対 or 符号付き相対オフセット）
        until: ``--until``（同上）
        project: 単一プロジェクト指定（``project_id`` 文字列）
        all_projects: True で search_paths の全プロジェクトを束ねる
        today: テスト用の日付固定。未指定なら ``date.today()``
    """
    now = datetime.now(timezone.utc).astimezone()
    today_date = today or now.date()

    target_dates = resolve_target_dates(dates, since=since, until=until, today=today_date)

    records = scan_records(config)
    cwd_proj = _detect_cwd_project(config) if not (project or all_projects) else None
    targets = _resolve_target_projects(
        records, project=project, all_projects=all_projects, cwd_project=cwd_proj,
    )
    scoped = [r for r in records if r.project in targets]

    buckets: dict[str, DateBucket] = {}
    for d in target_dates:
        want_mtime, want_due = _axes_for_date(d, today=today_date)
        bucket = DateBucket()
        if want_mtime:
            bucket.touched = [
                _short_record(r) for r in scoped
                if r.mtime and datetime.fromtimestamp(r.mtime).astimezone().date() == d
            ]
            bucket.touched.sort(key=lambda x: (x["project"] or "", x["rel"]))
        if want_due:
            for r in scoped:
                if not r.due:
                    continue
                try:
                    due_date = date.fromisoformat(r.due)
                except ValueError:
                    continue
                if due_date == d:
                    bucket.due.append(_short_record(r))
            bucket.due.sort(key=lambda x: (x["project"] or "", x["rel"]))
        buckets[d.isoformat()] = bucket

    return ActivityResult(
        generated_at=now.isoformat(),
        today=today_date.isoformat(),
        dates=buckets,
    )
