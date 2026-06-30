"""C4 (bloat-mitigation): sync_index --prune-projects — 孤児プロジェクト掃除。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from docsweep import index as db
from docsweep.config import load_config
from docsweep.scan import sync_index


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def two_projects(tmp_path: Path) -> Path:
    """2 プロジェクトを持つワークスペース。"""
    root = tmp_path / "dev"
    _write(root / "alpha" / "pyproject.toml", "[project]\nname='alpha'\n")
    _write(root / "alpha" / "docs" / "local" / "plan_a.md",
           "# [計画] a\n\n## 概要\n\nx\n")
    _write(root / "beta" / "pyproject.toml", "[project]\nname='beta'\n")
    _write(root / "beta" / "docs" / "local" / "plan_b.md",
           "# [計画] b\n\n## 概要\n\ny\n")
    return root


def _cfg(root_glob: str, tmp_path: Path):
    cfg = load_config(global_path=tmp_path / "noexists.yaml")
    cfg.search_paths = [root_glob]
    return cfg


def test_prune_disabled_by_default_keeps_orphans(
    two_projects: Path, tmp_path: Path, monkeypatch
) -> None:
    """既定は孤児を残す（一時的な search_paths 変更で誤削除しない安全側）。"""
    db_file = tmp_path / "idx.db"
    monkeypatch.setenv("DOCSWEEP_INDEX_DB", str(db_file))

    # 両方走査して両 project を登録
    cfg_all = _cfg(str(two_projects / "*"), tmp_path)
    sync_index(cfg_all)
    with db.connect(db_file) as conn:
        ids = {r[0] for r in conn.execute("SELECT project_id FROM projects").fetchall()}
    assert ids == {"alpha", "beta"}

    # search_paths を絞って alpha だけにする（prune なし）
    cfg_alpha_only = _cfg(str(two_projects / "alpha"), tmp_path)
    stats = sync_index(cfg_alpha_only)

    assert stats.projects_removed == 0
    with db.connect(db_file) as conn:
        ids = {r[0] for r in conn.execute("SELECT project_id FROM projects").fetchall()}
    assert ids == {"alpha", "beta"}  # beta が残っている


def test_prune_projects_removes_orphans_and_cascades_files(
    two_projects: Path, tmp_path: Path, monkeypatch
) -> None:
    """--prune-projects 相当で孤児 projects と CASCADE で files が消える。"""
    db_file = tmp_path / "idx.db"
    monkeypatch.setenv("DOCSWEEP_INDEX_DB", str(db_file))

    cfg_all = _cfg(str(two_projects / "*"), tmp_path)
    sync_index(cfg_all)

    with db.connect(db_file) as conn:
        files_before = conn.execute(
            "SELECT COUNT(*) FROM files WHERE project_id='beta'"
        ).fetchone()[0]
    assert files_before > 0

    # alpha のみで再 sync + prune
    cfg_alpha_only = _cfg(str(two_projects / "alpha"), tmp_path)
    stats = sync_index(cfg_alpha_only, prune_projects=True)

    assert stats.projects_removed == 1
    with db.connect(db_file) as conn:
        ids = {r[0] for r in conn.execute("SELECT project_id FROM projects").fetchall()}
        files_beta = conn.execute(
            "SELECT COUNT(*) FROM files WHERE project_id='beta'"
        ).fetchone()[0]

    assert ids == {"alpha"}
    assert files_beta == 0  # CASCADE で連鎖削除


def test_prune_projects_noop_when_all_present(
    two_projects: Path, tmp_path: Path, monkeypatch
) -> None:
    """全 project が現存している時は prune しても何も削除しない。"""
    db_file = tmp_path / "idx.db"
    monkeypatch.setenv("DOCSWEEP_INDEX_DB", str(db_file))

    cfg_all = _cfg(str(two_projects / "*"), tmp_path)
    sync_index(cfg_all)
    stats = sync_index(cfg_all, prune_projects=True)

    assert stats.projects_removed == 0
