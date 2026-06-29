"""``docsweep export --okf`` の単体テスト（C3 OKF 採用 Phase 3）。

zip が生成され ``okf-manifest.json`` が同梱されること、frontmatter 込みで md が
バイトレベル保全されること、archive 包含オプションが効くことを確認する。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from docsweep.config import load_config
from docsweep.export import (
    OKF_REVIEW_STATUS_VOCABULARY,
    OKF_STATUS_VOCABULARY,
    OKF_TYPE_VOCABULARY,
    run_export,
)


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "dev"
    proj = root / "demo"
    _write(
        proj / "docs" / "local" / "plan_alpha.md",
        "---\n"
        "type: plan\n"
        "status: planned\n"
        "tags: [auth]\n"
        "owner: alice\n"
        "review_status: draft\n"
        "related: []\n"
        "last_reviewed: 2026-06-29\n"
        "---\n"
        "# [計画] α 計画\n\n## 概要\n\nαの計画。\n",
    )
    _write(
        proj / "docs" / "local" / "bugfix_x_2026-06-01.md",
        "# [完了] X 修正\n\n## 症状\n\n発生していた。\n",
    )
    _write(
        proj / "docs" / "local" / "pending_y.md",
        "# [保留] Y 案件\n\n## 概要\n\n保留。\n",
    )
    # archive 配下
    _write(
        proj / "archive" / "plan" / "plan_old.md",
        "# [完了] old plan\n",
    )
    return root


def _cfg(root: Path):
    return load_config(
        explicit_roots=[str(root)], global_path=root / "no_global.yaml"
    )


def test_export_creates_zip_with_manifest(workspace: Path, tmp_path: Path):
    """zip が生成され okf-manifest.json が同梱される。"""
    out = tmp_path / "out.zip"
    cfg = _cfg(workspace)
    result = run_export(cfg, out=out)
    assert out.is_file()
    assert result.file_count >= 3  # plan + bugfix + pending
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "okf-manifest.json" in names
        manifest = json.loads(zf.read("okf-manifest.json").decode("utf-8"))
    assert manifest["format"] == "okf"
    assert manifest["type_vocabulary"] == OKF_TYPE_VOCABULARY
    assert manifest["status_vocabulary"] == OKF_STATUS_VOCABULARY
    assert manifest["review_status_vocabulary"] == OKF_REVIEW_STATUS_VOCABULARY
    assert manifest["file_count"] == result.file_count
    assert manifest["include_archive"] is False


def test_export_preserves_frontmatter_bytes(workspace: Path, tmp_path: Path):
    """frontmatter 込みで md が元のバイト列のまま zip に入る。"""
    out = tmp_path / "out.zip"
    cfg = _cfg(workspace)
    run_export(cfg, out=out)
    plan_src = (workspace / "demo" / "docs" / "local" / "plan_alpha.md").read_bytes()
    with zipfile.ZipFile(out) as zf:
        match = [n for n in zf.namelist() if n.endswith("plan_alpha.md")]
        assert match, "plan_alpha.md が zip に入っていない"
        assert zf.read(match[0]) == plan_src


def test_export_excludes_archive_by_default(workspace: Path, tmp_path: Path):
    """既定では archive/ 配下は含まれない。"""
    out = tmp_path / "out.zip"
    cfg = _cfg(workspace)
    run_export(cfg, out=out)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert not any("plan_old.md" in n for n in names)


def test_export_include_archive_picks_up_archive_files(
    workspace: Path, tmp_path: Path
):
    """--include-archive で archive/ 配下も _archive/ 名前空間で同梱される。"""
    out = tmp_path / "out.zip"
    cfg = _cfg(workspace)
    result = run_export(cfg, out=out, include_archive=True)
    assert result.include_archive is True
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("okf-manifest.json").decode("utf-8"))
    assert any("plan_old.md" in n and n.startswith("_archive/") for n in names)
    assert manifest["include_archive"] is True


def test_export_status_vocabulary_converts_to_okf(workspace: Path, tmp_path: Path):
    """manifest の files[].status が OKF 互換語彙に丸められている。"""
    out = tmp_path / "out.zip"
    cfg = _cfg(workspace)
    run_export(cfg, out=out)
    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("okf-manifest.json").decode("utf-8"))
    statuses = {f["status"] for f in manifest["files"] if f["status"]}
    # docsweep 内部 state key（planned 等）ではなく OKF 互換値（draft 等）が並ぶ。
    assert "draft" in statuses or "done" in statuses or "deferred" in statuses


def test_export_project_filter(workspace: Path, tmp_path: Path):
    """--project で 1 プロジェクトに絞れる。"""
    out = tmp_path / "out.zip"
    cfg = _cfg(workspace)
    result = run_export(cfg, out=out, project="demo")
    assert result.file_count >= 1
    # 存在しないプロジェクト指定なら 0 件
    out2 = tmp_path / "empty.zip"
    result2 = run_export(cfg, out=out2, project="not-a-project")
    assert result2.file_count == 0
