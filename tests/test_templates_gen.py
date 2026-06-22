"""docsweep new テンプレ生成: due 初期値挿入と既存挙動の非回帰テスト。"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from docsweep.templates_gen import _resolve_initial_due, new_doc


def _today_plus(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def test_new_plan_includes_default_due_frontmatter(tmp_path: Path):
    """plan は ``offset_days={'plan': 7}`` で frontmatter に ``due: today+7`` が入る。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc("plan", "auth-refactor", project_dir=proj, offset_days={"plan": 7})
    body = doc.path.read_text(encoding="utf-8")
    assert body.startswith("---\n")
    assert f"due: {_today_plus(7)}" in body
    assert "# [計画] auth-refactor" in body
    assert doc.due == _today_plus(7)


def test_new_pending_uses_pending_offset(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc(
        "pending", "wait-for-vendor",
        project_dir=proj, offset_days={"plan": 7, "pending": 14},
    )
    body = doc.path.read_text(encoding="utf-8")
    assert f"due: {_today_plus(14)}" in body
    assert "# [保留] wait-for-vendor" in body


def test_new_bugfix_does_not_include_due(tmp_path: Path):
    """bugfix は新規時に ``due:`` を付けない（[様子見] 遷移時に追記する設計）。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc(
        "bugfix", "login-500",
        project_dir=proj,
        offset_days={"plan": 7, "pending": 14, "bugfix_watching": 7},
    )
    body = doc.path.read_text(encoding="utf-8")
    assert "---" not in body.splitlines()[0]  # frontmatter なし
    assert "due:" not in body
    assert "# [対応中] login-500" in body
    assert doc.due is None


def test_explicit_due_overrides_offset(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc(
        "plan", "x", project_dir=proj,
        due="2030-01-01", offset_days={"plan": 7},
    )
    body = doc.path.read_text(encoding="utf-8")
    assert "due: 2030-01-01" in body
    assert doc.due == "2030-01-01"


def test_no_offset_no_due_does_not_add_frontmatter(tmp_path: Path):
    """``offset_days={}`` かつ ``due=None`` のとき frontmatter は付けない（既存挙動の非回帰）。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc("plan", "y", project_dir=proj, offset_days={})
    body = doc.path.read_text(encoding="utf-8")
    assert body.startswith("# [計画] y")
    assert doc.due is None


def test_resolve_initial_due_explicit_wins():
    out = _resolve_initial_due("plan", due="2026-12-31", offset_days={"plan": 7})
    assert out == "2026-12-31"


def test_resolve_initial_due_uses_offset():
    today = date(2026, 6, 23)
    out = _resolve_initial_due("plan", due=None, offset_days={"plan": 5}, today=today)
    assert out == "2026-06-28"


def test_resolve_initial_due_bugfix_returns_none():
    out = _resolve_initial_due(
        "bugfix", due=None, offset_days={"bugfix_watching": 7}, today=date(2026, 6, 23),
    )
    assert out is None  # 新規時 bugfix は明示 due が無ければ付けない


def test_topic_with_path_traversal_rejected(tmp_path: Path):
    """既存挙動: トポイックに .. を含めてもパス区切りで cut される（非回帰）。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc("plan", "../escape", project_dir=proj, offset_days={"plan": 7})
    # 生成ファイルは proj 配下に留まる
    assert proj in doc.path.parents or doc.path.parent == proj
    # トポイックは "escape" 部分のみ採用される
    assert "escape" in doc.path.name


def test_filename_collision_suffix(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    d1 = new_doc("plan", "topic", project_dir=proj, offset_days={"plan": 7})
    d2 = new_doc("plan", "topic", project_dir=proj, offset_days={"plan": 7})
    assert d1.path != d2.path
    assert re.search(r"plan_topic_2\.md$", d2.path.name)
