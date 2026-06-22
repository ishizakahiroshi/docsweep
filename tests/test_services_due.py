"""services/due.py — update_due（frontmatter 書き換え + postpone カウント）のテスト。"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from docsweep.services.due import (
    DueParseError,
    resolve_due,
    update_due,
)
from docsweep.state import get_postpone_count


def _setup_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")
    return proj


def test_resolve_due_absolute():
    assert resolve_due("2026-07-01") == date(2026, 7, 1)


def test_resolve_due_today():
    assert resolve_due("today", today=date(2026, 6, 23)) == date(2026, 6, 23)


def test_resolve_due_relative_days():
    assert resolve_due("+3d", today=date(2026, 6, 23)) == date(2026, 6, 26)


def test_resolve_due_relative_weeks():
    assert resolve_due("+1w", today=date(2026, 6, 23)) == date(2026, 6, 30)


def test_resolve_due_relative_months():
    assert resolve_due("+1m", today=date(2026, 6, 23)) == date(2026, 7, 23)


def test_resolve_due_invalid_raises():
    with pytest.raises(DueParseError):
        resolve_due("tomorrow")
    with pytest.raises(DueParseError):
        resolve_due("xyz")


def test_update_due_replaces_existing_due(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("---\ndue: 2026-06-15\nstatus: planned\n---\n# [計画] a\n", encoding="utf-8")
    res = update_due(f, "2026-06-29", project_root=proj)
    assert res.new_due == "2026-06-29"
    assert res.old_due == "2026-06-15"
    body = f.read_text(encoding="utf-8")
    assert "due: 2026-06-29" in body
    assert "status: planned" in body  # 他の frontmatter は温存
    assert "# [計画] a" in body  # 本文は触らない


def test_update_due_appends_when_due_missing_in_frontmatter(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("---\nstatus: planned\n---\n# [計画] a\n", encoding="utf-8")
    update_due(f, "2026-07-01", project_root=proj)
    body = f.read_text(encoding="utf-8")
    assert "due: 2026-07-01" in body
    assert "status: planned" in body


def test_update_due_inserts_frontmatter_when_absent(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("# [計画] a\n本文。\n", encoding="utf-8")
    update_due(f, "2026-07-01", project_root=proj)
    body = f.read_text(encoding="utf-8")
    assert body.startswith("---\ndue: 2026-07-01\n---\n")
    assert "# [計画] a" in body


def test_update_due_increments_postpone_count(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("---\ndue: 2026-06-15\n---\n# [計画] a\n", encoding="utf-8")
    res1 = update_due(f, "2026-06-22", project_root=proj)
    res2 = update_due(f, "2026-06-29", project_root=proj)
    assert res1.postpone_count == 1
    assert res2.postpone_count == 2
    assert get_postpone_count(proj, f) == 2


def test_update_due_warning_on_threshold(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("---\ndue: 2026-06-15\n---\n# [計画] a\n", encoding="utf-8")
    for _ in range(3):
        res = update_due(f, "+1w", project_root=proj, warn_threshold=3, alert_threshold=5)
    assert "警告しきい値" in (res.warning or "")


def test_update_due_alert_on_threshold(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("---\ndue: 2026-06-15\n---\n# [計画] a\n", encoding="utf-8")
    for _ in range(5):
        res = update_due(f, "+1w", project_root=proj, warn_threshold=3, alert_threshold=5)
    assert "廃止候補" in (res.warning or "")


def test_update_due_warns_for_past_date(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("---\ndue: 2030-01-01\n---\n# [計画] a\n", encoding="utf-8")
    past = (date.today() - timedelta(days=1)).isoformat()
    res = update_due(f, past, project_root=proj)
    assert "過去日" in (res.warning or "")
    # 書き込み自体は成功
    assert f"due: {past}" in f.read_text(encoding="utf-8")


def test_update_due_returns_new_mtime(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("---\ndue: 2026-06-15\n---\n# [計画] a\n", encoding="utf-8")
    res = update_due(f, "2026-06-22", project_root=proj)
    assert res.new_mtime == pytest.approx(f.stat().st_mtime, rel=0, abs=0.01)
