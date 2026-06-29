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
    """bugfix は新規時に ``due:`` を付けない（[様子見] 遷移時に追記する設計）。

    OKF 採用後（plan_okf-adoption_2026-06-29.md C1）も bugfix の due 付与方針は不変。
    frontmatter ブロック自体は常時付くようになったが、その中に ``due:`` 行は出さない。
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc(
        "bugfix", "login-500",
        project_dir=proj,
        offset_days={"plan": 7, "pending": 14, "bugfix_watching": 7},
    )
    body = doc.path.read_text(encoding="utf-8")
    assert "due:" not in body
    # OKF 採用: frontmatter の必須フィールドが入る
    assert body.startswith("---\n")
    assert "type: bugfix" in body
    assert "status: in-progress" in body
    assert "review_status: draft" in body
    # 2026-06-23 改修: 新規 bugfix は [対応中] でなく [実行中] を書く（active 統合）
    assert "# [実行中] login-500" in body
    assert doc.due is None


def test_no_offset_no_due_still_emits_okf_frontmatter(tmp_path: Path):
    """``offset_days={}`` でも OKF frontmatter は常に付く（due 行だけ落ちる）。

    旧来は frontmatter 自体を省略していたが、OKF 採用後は type/status/tags/owner/
    review_status/related/last_reviewed を常に出すよう仕様変更（後方互換は parser 側で吸収）。
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc("plan", "no-due", project_dir=proj, offset_days={})
    body = doc.path.read_text(encoding="utf-8")
    assert body.startswith("---\n")
    assert "type: plan" in body
    assert "status: planned" in body
    assert "tags: []" in body
    assert "review_status: draft" in body
    assert "related: []" in body
    assert "last_reviewed:" in body
    assert "due:" not in body
    assert "# [計画] no-due" in body
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


def test_no_offset_no_due_legacy_behavior_replaced_by_okf(tmp_path: Path):
    """OKF 採用前は frontmatter を完全省略していたが、現仕様では常時付ける。

    `test_no_offset_no_due_still_emits_okf_frontmatter` が新しい期待値を保証する。
    旧アサート（``# [計画] y`` で始まる）は OKF 採用で意味を失ったので置き換え。
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc("plan", "y", project_dir=proj, offset_days={})
    body = doc.path.read_text(encoding="utf-8")
    assert "# [計画] y" in body
    assert body.startswith("---\n")  # OKF frontmatter は必ず付く
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
