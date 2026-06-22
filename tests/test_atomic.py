"""atomic write helper のテスト — 楽観ロック・並行書き込み・バックアップ。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from docsweep.atomic import (
    BACKUP_RETENTION_SECONDS,
    ConflictError,
    backup,
    backup_dir_for,
    update_line,
    write_atomic,
)


def _setup_project(tmp_path: Path) -> Path:
    """project marker を 1 つ作ってプロジェクト境界を確定させる。"""
    proj = tmp_path / "proj"
    (proj / ".docsweep.yaml").parent.mkdir(parents=True, exist_ok=True)
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")
    return proj


def test_write_atomic_creates_file(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "docs" / "plan_a.md"
    new_mtime = write_atomic(f, "# [計画] a\n", take_backup=False)
    assert f.read_text(encoding="utf-8") == "# [計画] a\n"
    assert new_mtime > 0


def test_write_atomic_overwrites(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    f.write_text("old", encoding="utf-8")
    write_atomic(f, "new", take_backup=False)
    assert f.read_text(encoding="utf-8") == "new"


def test_write_atomic_mtime_conflict(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    f.write_text("v1", encoding="utf-8")
    stale = f.stat().st_mtime - 1000  # わざと古い mtime を渡す
    with pytest.raises(ConflictError):
        write_atomic(f, "v2", expected_mtime=stale, take_backup=False)
    # 内容が書き換わっていないこと
    assert f.read_text(encoding="utf-8") == "v1"


def test_write_atomic_mtime_match_passes(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    f.write_text("v1", encoding="utf-8")
    current = f.stat().st_mtime
    new_mtime = write_atomic(f, "v2", expected_mtime=current, take_backup=False)
    assert f.read_text(encoding="utf-8") == "v2"
    assert new_mtime > 0


def test_backup_copies_to_docsweep_backup(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "docs" / "plan.md"
    f.parent.mkdir(parents=True)
    f.write_text("content", encoding="utf-8")
    dst = backup(f)
    assert dst is not None
    assert dst.exists()
    assert dst.read_text(encoding="utf-8") == "content"
    # .docsweep/backup/ 配下に置かれているか（プロジェクト境界判定の確認）
    assert backup_dir_for(f) == proj / ".docsweep" / "backup"
    assert dst.parent == backup_dir_for(f)


def test_backup_returns_none_for_missing_file(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "missing.md"
    assert backup(f) is None


def test_backup_cleanup_removes_old_entries(tmp_path: Path):
    proj = _setup_project(tmp_path)
    bd = backup_dir_for(proj / "x.md")
    bd.mkdir(parents=True)
    old = bd / "x.md.1"
    old.write_text("old", encoding="utf-8")
    # 30 日 + 1 日前に mtime を巻き戻す
    old_ts = time.time() - BACKUP_RETENTION_SECONDS - 86400
    import os
    os.utime(old, (old_ts, old_ts))
    # 新規バックアップで cleanup が走り、古いものが消える
    f = proj / "y.md"
    f.write_text("c", encoding="utf-8")
    backup(f)
    assert not old.exists()


def test_update_line_changes_only_when_transform_differs(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    f.write_text("old\nbody\n", encoding="utf-8")
    pre_mtime = f.stat().st_mtime

    def _identity(text: str) -> str:
        return text

    new_mtime = update_line(f, transform=_identity, take_backup=False)
    # 変化なしでも例外にならない
    assert new_mtime == pytest.approx(pre_mtime, rel=0, abs=0.01)
    assert f.read_text(encoding="utf-8") == "old\nbody\n"


def test_update_line_writes_when_transform_differs(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    f.write_text("old\n", encoding="utf-8")

    def _xform(text: str) -> str:
        return text.replace("old", "new")

    update_line(f, transform=_xform, take_backup=False)
    assert f.read_text(encoding="utf-8") == "new\n"


def test_update_line_respects_mtime(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    f.write_text("v1\n", encoding="utf-8")
    stale = f.stat().st_mtime - 999

    def _xform(text: str) -> str:
        return text + "extra\n"

    with pytest.raises(ConflictError):
        update_line(f, transform=_xform, expected_mtime=stale, take_backup=False)
    assert f.read_text(encoding="utf-8") == "v1\n"


def test_write_atomic_preserves_unicode(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "日本語.md"
    text = "# [計画] テスト\n日本語コンテンツ。\n"
    write_atomic(f, text, take_backup=False)
    assert f.read_text(encoding="utf-8") == text


def test_write_atomic_no_tmpfile_leftover_on_success(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    write_atomic(f, "x", take_backup=False)
    leftovers = [p for p in f.parent.iterdir() if p.name.startswith(".plan.md.")]
    assert leftovers == []
