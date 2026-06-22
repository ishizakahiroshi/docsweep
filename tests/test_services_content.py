"""services/content.py — update_content（全置換 + 楽観ロック）のテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep.atomic import ConflictError
from docsweep.services.content import ContentValidationError, update_content


def _setup_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")
    return proj


def test_update_content_full_replace(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("# [計画] 旧\n旧本文\n", encoding="utf-8")
    new = "# [実行中] 新\n## 概要\n新本文\n"
    res = update_content(f, new)
    assert f.read_text(encoding="utf-8") == new
    assert res.new_sha256 != ""
    assert res.warnings == []


def test_update_content_rejects_empty(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("# [計画] a\n", encoding="utf-8")
    with pytest.raises(ContentValidationError):
        update_content(f, "")
    # 元のファイルが壊れていないこと
    assert f.read_text(encoding="utf-8") == "# [計画] a\n"


def test_update_content_warns_when_h1_missing(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("# [計画] a\n", encoding="utf-8")
    # H1 無しの本文（拒否はしない）
    res = update_content(f, "## 概要\n本文だけ\n")
    assert any("H1" in w for w in res.warnings)


def test_update_content_mtime_conflict(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("# [計画] a\n", encoding="utf-8")
    stale = f.stat().st_mtime - 1000
    with pytest.raises(ConflictError):
        update_content(f, "# [計画] new\n", expected_mtime=stale)


def test_update_content_mtime_match_passes(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("# [計画] a\n", encoding="utf-8")
    current = f.stat().st_mtime
    res = update_content(f, "# [計画] new\n", expected_mtime=current)
    assert "new" in f.read_text(encoding="utf-8")
    assert res.new_mtime > 0
