"""C4 (wings): cross — service / build_cross / explain_score の単体テスト。"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pytest

from docsweep.config import load_config
from docsweep.cross.service import (
    FROZEN_AGE_DAYS,
    build_cross,
    explain_score,
)
from docsweep.models import FileRecord, Flag


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
        mtime=time.time(),
        age_days=5,
        archivable=False,
        auto_movable=False,
    )
    defaults.update(kw)
    return FileRecord(**defaults)


@pytest.fixture
def cross_records(monkeypatch):
    """3 プロジェクトの混合データ。"""
    records = [
        # alpha: スコア高い（urgency + NEEDS_DECISION）
        _rec(path="/a/plan_high.md", project="alpha",
             title="alpha 主役", state="in-progress", state_label="[実行中]",
             age_days=10, flags=[Flag.NEEDS_DECISION.value]),
        _rec(path="/a/plan_mid.md", project="alpha",
             state="planned", state_label="[計画]", age_days=2),
        # beta: 中程度
        _rec(path="/b/plan_beta.md", project="beta",
             title="beta plan", state="planned", state_label="[計画]",
             age_days=15),
        # gamma: 凍結（age 大・open・他 flag 無し）
        _rec(path="/c/plan_frozen.md", project="gamma",
             title="frozen", state="planned", state_label="[計画]",
             age_days=FROZEN_AGE_DAYS + 30),
        # gamma: done（cross 対象外）
        _rec(path="/c/plan_done.md", project="gamma",
             state="done", state_label="[完了]", age_days=5),
    ]

    def fake_scan(config, *, project=None):
        if project:
            return [r for r in records if r.project == project]
        return list(records)

    monkeypatch.setattr("docsweep.cross.service.scan_records", fake_scan)
    return records


def test_build_cross_picks_top_across_projects(cross_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = build_cross(cfg)
    assert result.top_pick is not None
    # alpha 主役 (in-progress + NEEDS_DECISION + 10d) が最高
    assert result.top_pick["rel"] == "plan_high.md"
    assert result.top_pick["project"] == "alpha"


def test_build_cross_runners_up_exclude_top(cross_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = build_cross(cfg)
    top_path = result.top_pick["path"]
    assert all(d["path"] != top_path for d in result.runners_up)


def test_build_cross_frozen_candidates_detect_old_low_score(cross_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = build_cross(cfg)
    rels = {d["rel"] for d in result.frozen_candidates}
    assert "plan_frozen.md" in rels
    # done は対象外
    assert "plan_done.md" not in rels


def test_build_cross_excludes_done_from_top_pick(cross_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = build_cross(cfg)
    # 全 runners_up + top_pick + frozen に done は出ない
    all_picked = (
        [result.top_pick] + list(result.runners_up) + list(result.frozen_candidates)
    )
    for d in all_picked:
        if d is None:
            continue
        assert d["state"] != "done"


def test_build_cross_project_filter(cross_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = build_cross(cfg, projects=["beta"])
    assert result.project_filter == ["beta"]
    assert result.total_projects == 1
    assert result.top_pick is not None
    assert result.top_pick["project"] == "beta"


def test_build_cross_project_summaries_show_all_projects(cross_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = build_cross(cfg)
    names = {p.project for p in result.project_summaries}
    assert names == {"alpha", "beta", "gamma"}
    # alpha: 2 open
    alpha = next(p for p in result.project_summaries if p.project == "alpha")
    assert alpha.open_count == 2
    # gamma: 1 open（done は除外）
    gamma = next(p for p in result.project_summaries if p.project == "gamma")
    assert gamma.open_count == 1


def test_build_cross_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("docsweep.cross.service.scan_records", lambda c, project=None: [])
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    result = build_cross(cfg)
    assert result.top_pick is None
    assert result.runners_up == []
    assert result.total_open == 0


def test_explain_score_returns_breakdown(cross_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    out = explain_score(cfg, "plan_high.md")
    assert out is not None
    assert out["rel"] == "plan_high.md"
    assert "score" in out and "total" in out["score"]


def test_explain_score_unknown_returns_none(cross_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    assert explain_score(cfg, "no-such.md") is None


def test_cross_result_to_dict_shape(cross_records, tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    d = build_cross(cfg).to_dict()
    for key in ("generated_at", "project_filter", "top_pick", "runners_up",
                "frozen_candidates", "project_summaries", "total_projects", "total_open"):
        assert key in d
