"""C3 (wings): brief — score / service / build_brief の単体テスト。"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from docsweep.brief.score import (
    _STATE_BASE,
    W_DEP_CHAIN,
    ScoreBreakdown,
    score_record,
    tiebreak_key,
)
from docsweep.brief.service import build_brief
from docsweep.config import load_config
from docsweep.models import FileRecord, Flag


def _rec(**kw) -> FileRecord:
    defaults = dict(
        path="/x/docs/local/plan_x.md",
        project="alpha",
        project_root="/x",
        type="plan",
        state="planned",
        state_label="[計画]",
        state_source="h1",
        title="x",
        summary="s",
        mtime=time.time(),
        age_days=5,
        archivable=False,
        auto_movable=False,
    )
    defaults.update(kw)
    return FileRecord(**defaults)


# ===================================================================
# score.py
# ===================================================================


def test_score_done_state_returns_zero() -> None:
    rec = _rec(state="done", state_label="[完了]")
    sb = score_record(rec)
    assert sb.total == 0.0


def test_score_in_progress_higher_than_pending() -> None:
    ip = score_record(_rec(state="in-progress", state_label="[実行中]"))
    pe = score_record(_rec(state="pending", state_label="[保留]"))
    assert ip.total > pe.total


def test_score_needs_decision_boosts_urgency() -> None:
    rec_plain = _rec(state="planned", age_days=10)
    rec_decision = _rec(state="planned", age_days=10, flags=[Flag.NEEDS_DECISION.value])
    assert score_record(rec_decision).total > score_record(rec_plain).total


def test_score_dep_chain_adds_weight() -> None:
    rec_alone = _rec(state="planned")
    rec_linked = _rec(state="planned", related=["a.md", "b.md", "c.md"])
    s_alone = score_record(rec_alone).total
    s_linked = score_record(rec_linked).total
    assert s_linked == pytest.approx(s_alone + 3 * W_DEP_CHAIN)


def test_score_breakdown_to_dict_roundtrip() -> None:
    sb = ScoreBreakdown(total=10.5, state_base=10, urgency=0, stale=0.5,
                       review_status=0, touched_decay=0, dep_chain=0)
    d = sb.to_dict()
    assert d["total"] == 10.5 and d["state_base"] == 10


def test_tiebreak_key_is_deterministic() -> None:
    a = _rec(path="/a/plan_a.md")
    b = _rec(path="/a/plan_b.md")
    assert tiebreak_key(a) < tiebreak_key(b)


# ===================================================================
# service.build_brief
# ===================================================================


@pytest.fixture
def fake_records(monkeypatch):
    """scan_records をモック化し、固定 FileRecord 群を返す。"""
    now = time.time()
    yesterday = now - 18 * 3600  # 18 時間前

    records = [
        # 今日の 1 個候補（urgency 高い）
        _rec(path="/alpha/docs/local/plan_high.md", project="alpha",
             title="高優先プラン", state="in-progress", state_label="[実行中]",
             age_days=15, flags=[Flag.NEEDS_DECISION.value]),
        # 併走（in-progress だがスコア低い）
        _rec(path="/alpha/docs/local/plan_mid.md", project="alpha",
             title="併走中", state="in-progress", state_label="[実行中]",
             age_days=3),
        # 要注意（OVERDUE_TODO）
        _rec(path="/alpha/docs/local/plan_overdue.md", project="alpha",
             title="期限切れ", state="planned", state_label="[計画]",
             age_days=20, flags=[Flag.OVERDUE_TODO.value]),
        # 昨日終わったこと
        _rec(path="/alpha/docs/local/plan_done.md", project="alpha",
             title="昨日完了", state="done", state_label="[完了]",
             mtime=yesterday, age_days=1),
        # 別プロジェクトに 1 件
        _rec(path="/beta/docs/local/plan_beta.md", project="beta",
             title="beta plan", state="planned", state_label="[計画]",
             age_days=10),
    ]

    def fake_scan(config, *, project=None):
        if project:
            return [r for r in records if r.project == project]
        return list(records)

    monkeypatch.setattr("docsweep.brief.service.scan_records", fake_scan)
    return records


def test_build_brief_single_project_picks_today_one(fake_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = build_brief(cfg, project="alpha")
    assert result.mode == "single"
    assert len(result.projects) == 1
    proj = result.projects[0]
    assert proj.project == "alpha"
    assert proj.today_pick is not None
    # 高優先プラン (in-progress + NEEDS_DECISION + 15 日) がトップに来る
    assert proj.today_pick["rel"] == "plan_high.md"


def test_build_brief_co_running_excludes_today_pick(fake_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = build_brief(cfg, project="alpha")
    proj = result.projects[0]
    co_paths = {d["path"] for d in proj.co_running}
    assert proj.today_pick["path"] not in co_paths


def test_build_brief_watchouts_includes_overdue(fake_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = build_brief(cfg, project="alpha")
    proj = result.projects[0]
    rels = {d["rel"] for d in proj.watchouts}
    assert "plan_overdue.md" in rels


def test_build_brief_yesterday_done_window(fake_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = build_brief(cfg, project="alpha")
    proj = result.projects[0]
    rels = {d["rel"] for d in proj.yesterday_done}
    assert "plan_done.md" in rels


def test_build_brief_all_projects(fake_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = build_brief(cfg, all_projects=True)
    assert result.mode == "all"
    names = {p.project for p in result.projects}
    assert names == {"alpha", "beta"}


def test_build_brief_empty_project_returns_no_today_pick(monkeypatch, tmp_path):
    def empty_scan(config, *, project=None):
        return []
    monkeypatch.setattr("docsweep.brief.service.scan_records", empty_scan)
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = build_brief(cfg, project="ghost")
    assert len(result.projects) == 1
    assert result.projects[0].today_pick is None


def test_brief_result_to_dict_shape(fake_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    d = build_brief(cfg, project="alpha").to_dict()
    assert d["mode"] == "single"
    assert "generated_at" in d
    assert isinstance(d["projects"], list)
    proj = d["projects"][0]
    for key in ("project", "today_pick", "co_running", "watchouts",
                "yesterday_done", "open_count", "stale_count"):
        assert key in proj
