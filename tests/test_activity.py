"""plan_activity-summary.md C2 — activity: 日付フィルタ・軸振り分け・build_activity のテスト。"""

from __future__ import annotations

from datetime import date, datetime
from datetime import time as dtime
from datetime import timedelta
from pathlib import Path

import pytest

from docsweep.activity import (
    ActivityDateError,
    _axes_for_date,
    build_activity,
    resolve_target_dates,
)
from docsweep.config import load_config
from docsweep.models import FileRecord

TODAY = date(2026, 6, 25)
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
IN_2_DAYS = TODAY + timedelta(days=2)
FAR_FUTURE = TODAY + timedelta(days=10)
OLD_DATE = TODAY - timedelta(days=30)


def _epoch(d: date) -> float:
    """d の現地正午を epoch 秒に変換する（DST 境界を避けて日付を安定させる）。"""
    return datetime.combine(d, dtime(12, 0)).timestamp()


def _rec(**kw) -> FileRecord:
    defaults = dict(
        path="/a/docs/local/plan_x.md",
        project="alpha",
        project_root="/a",
        type="plan",
        state="planned",
        state_label="[計画]",
        state_source="h1",
        title="x",
        summary="s",
        mtime=_epoch(OLD_DATE),
        age_days=5,
        archivable=False,
        auto_movable=False,
    )
    defaults.update(kw)
    return FileRecord(**defaults)


# ===================================================================
# resolve_target_dates / _axes_for_date（純粋ロジック・monkeypatch 不要）
# ===================================================================


def test_resolve_target_dates_default_is_today_and_yesterday():
    assert resolve_target_dates(None, since=None, until=None, today=TODAY) == [
        YESTERDAY, TODAY,
    ]


def test_resolve_target_dates_date_tokens():
    got = resolve_target_dates(
        ["today", "yesterday", "tomorrow"], since=None, until=None, today=TODAY,
    )
    assert got == [YESTERDAY, TODAY, TOMORROW]


def test_resolve_target_dates_date_absolute_token():
    got = resolve_target_dates(["2026-07-01"], since=None, until=None, today=TODAY)
    assert got == [date(2026, 7, 1)]


def test_resolve_target_dates_since_until_absolute_range():
    got = resolve_target_dates(
        None, since=TODAY.isoformat(), until=IN_2_DAYS.isoformat(), today=TODAY,
    )
    assert got == [TODAY, TOMORROW, IN_2_DAYS]


def test_resolve_target_dates_since_until_relative_range():
    got = resolve_target_dates(None, since="+1d", until="+2d", today=TODAY)
    assert got == [TOMORROW, IN_2_DAYS]
    assert TODAY not in got


def test_resolve_target_dates_since_only_defaults_until_to_today():
    got = resolve_target_dates(None, since="-2d", until=None, today=TODAY)
    assert got == [TODAY - timedelta(days=2), YESTERDAY, TODAY]


def test_resolve_target_dates_date_and_since_until_union():
    got = resolve_target_dates(
        ["yesterday"], since="+1d", until="+1d", today=TODAY,
    )
    assert got == [YESTERDAY, TOMORROW]


def test_resolve_target_dates_invalid_date_token_raises():
    with pytest.raises(ActivityDateError):
        resolve_target_dates(["not-a-date"], since=None, until=None, today=TODAY)


def test_resolve_target_dates_invalid_since_raises():
    with pytest.raises(ActivityDateError):
        resolve_target_dates(None, since="bogus", until=None, today=TODAY)


def test_axes_for_date_past_is_mtime_only():
    assert _axes_for_date(YESTERDAY, today=TODAY) == (True, False)


def test_axes_for_date_future_is_due_only():
    assert _axes_for_date(TOMORROW, today=TODAY) == (False, True)


def test_axes_for_date_today_is_both():
    assert _axes_for_date(TODAY, today=TODAY) == (True, True)


# ===================================================================
# build_activity（scan_records を monkeypatch）
# ===================================================================


@pytest.fixture
def activity_records(monkeypatch):
    """alpha/beta 2 プロジェクトの混合データ。mtime/due の軸振り分けを検証する材料。"""
    records = [
        _rec(path="/a/docs/local/plan_touched_today.md", title="today touch",
             mtime=_epoch(TODAY)),
        _rec(path="/a/docs/local/plan_touched_yesterday.md", title="yesterday touch",
             mtime=_epoch(YESTERDAY)),
        _rec(path="/a/docs/local/plan_due_yesterday.md", title="due yesterday",
             due=YESTERDAY.isoformat()),
        _rec(path="/a/docs/local/plan_due_today.md", title="due today",
             due=TODAY.isoformat()),
        _rec(path="/a/docs/local/plan_due_tomorrow.md", title="due tomorrow",
             due=TOMORROW.isoformat()),
        _rec(path="/a/docs/local/plan_due_in2days.md", title="due in 2 days",
             due=IN_2_DAYS.isoformat()),
        _rec(path="/a/docs/local/plan_mtime_tomorrow_edge.md", title="future mtime edge",
             mtime=_epoch(TOMORROW)),
        _rec(path="/a/docs/local/plan_no_due.md", title="no due"),
        _rec(path="/b/docs/local/plan_beta_today.md", project="beta", project_root="/b",
             title="beta today", mtime=_epoch(TODAY)),
    ]

    def fake_scan(config):
        return list(records)

    monkeypatch.setattr("docsweep.activity.scan_records", fake_scan)
    return records


def _cfg(tmp_path: Path):
    return load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")


def test_build_activity_default_dates_and_axes(activity_records, tmp_path):
    result = build_activity(_cfg(tmp_path), project="alpha", today=TODAY)
    assert set(result.dates) == {YESTERDAY.isoformat(), TODAY.isoformat()}

    yesterday_bucket = result.dates[YESTERDAY.isoformat()]
    today_bucket = result.dates[TODAY.isoformat()]

    assert {d["rel"] for d in yesterday_bucket.touched} == {"plan_touched_yesterday.md"}
    # 過去日は due 軸を出さない（due=yesterday の record があっても抑制される）
    assert yesterday_bucket.due == []

    assert {d["rel"] for d in today_bucket.touched} == {"plan_touched_today.md"}
    assert {d["rel"] for d in today_bucket.due} == {"plan_due_today.md"}


def test_build_activity_future_date_shows_due_axis_only(activity_records, tmp_path):
    result = build_activity(
        _cfg(tmp_path), project="alpha", dates=["tomorrow"], today=TODAY,
    )
    assert set(result.dates) == {TOMORROW.isoformat()}
    bucket = result.dates[TOMORROW.isoformat()]
    assert {d["rel"] for d in bucket.due} == {"plan_due_tomorrow.md"}
    # mtime が tomorrow と一致する record があっても未来日は mtime 軸を出さない
    assert bucket.touched == []


def test_build_activity_since_until_absolute(activity_records, tmp_path):
    result = build_activity(
        _cfg(tmp_path), project="alpha",
        since=TODAY.isoformat(), until=IN_2_DAYS.isoformat(), today=TODAY,
    )
    assert set(result.dates) == {TODAY.isoformat(), TOMORROW.isoformat(), IN_2_DAYS.isoformat()}
    assert {d["rel"] for d in result.dates[IN_2_DAYS.isoformat()].due} == {
        "plan_due_in2days.md",
    }


def test_build_activity_since_until_relative(activity_records, tmp_path):
    result = build_activity(
        _cfg(tmp_path), project="alpha", since="+1d", until="+2d", today=TODAY,
    )
    assert set(result.dates) == {TOMORROW.isoformat(), IN_2_DAYS.isoformat()}
    assert TODAY.isoformat() not in result.dates


def test_build_activity_due_none_excluded_from_due_axis(activity_records, tmp_path):
    result = build_activity(
        _cfg(tmp_path), project="alpha", since="-40d", until="+40d", today=TODAY,
    )
    due_rels = {d["rel"] for bucket in result.dates.values() for d in bucket.due}
    assert "plan_no_due.md" not in due_rels
    assert "plan_due_today.md" in due_rels  # sanity: due 軸自体は機能している


def test_build_activity_single_project_excludes_other_project(activity_records, tmp_path):
    result = build_activity(_cfg(tmp_path), project="alpha", today=TODAY)
    all_rels = {
        d["rel"]
        for bucket in result.dates.values()
        for d in bucket.touched + bucket.due
    }
    assert "plan_beta_today.md" not in all_rels


def test_build_activity_all_projects_bundles(activity_records, tmp_path):
    result = build_activity(
        _cfg(tmp_path), all_projects=True, dates=["today"], today=TODAY,
    )
    rels = {d["rel"] for d in result.dates[TODAY.isoformat()].touched}
    assert "plan_touched_today.md" in rels
    assert "plan_beta_today.md" in rels


def test_build_activity_invalid_date_raises(activity_records, tmp_path):
    with pytest.raises(ActivityDateError):
        build_activity(_cfg(tmp_path), project="alpha", dates=["nope"], today=TODAY)


def test_build_activity_to_dict_shape(activity_records, tmp_path):
    result = build_activity(
        _cfg(tmp_path), project="alpha", dates=["today"], today=TODAY,
    )
    d = result.to_dict()
    assert set(d) == {"generated_at", "today", "dates"}
    assert d["today"] == TODAY.isoformat()

    day = d["dates"][TODAY.isoformat()]
    assert set(day) == {"touched", "due"}
    assert isinstance(day["touched"], list)
    assert isinstance(day["due"], list)

    entry = day["touched"][0]
    for key in ("path", "rel", "project", "type", "state", "state_label",
                "title", "due", "age_days"):
        assert key in entry
