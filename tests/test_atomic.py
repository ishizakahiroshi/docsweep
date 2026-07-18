"""atomic write helper のテスト — 楽観ロック・並行書き込み。"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep.atomic import (
    ConflictError,
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
    new_mtime = write_atomic(f, "# [計画] a\n")
    assert f.read_text(encoding="utf-8") == "# [計画] a\n"
    assert new_mtime > 0


def test_write_atomic_overwrites(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    f.write_text("old", encoding="utf-8")
    write_atomic(f, "new")
    assert f.read_text(encoding="utf-8") == "new"


def test_write_atomic_mtime_conflict(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    f.write_text("v1", encoding="utf-8")
    stale = f.stat().st_mtime - 1000  # わざと古い mtime を渡す
    with pytest.raises(ConflictError):
        write_atomic(f, "v2", expected_mtime=stale)
    # 内容が書き換わっていないこと
    assert f.read_text(encoding="utf-8") == "v1"


def test_write_atomic_mtime_match_passes(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    f.write_text("v1", encoding="utf-8")
    current = f.stat().st_mtime
    new_mtime = write_atomic(f, "v2", expected_mtime=current)
    assert f.read_text(encoding="utf-8") == "v2"
    assert new_mtime > 0


def test_update_line_changes_only_when_transform_differs(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    f.write_text("old\nbody\n", encoding="utf-8")
    pre_mtime = f.stat().st_mtime

    def _identity(text: str) -> str:
        return text

    new_mtime = update_line(f, transform=_identity)
    # 変化なしでも例外にならない
    assert new_mtime == pytest.approx(pre_mtime, rel=0, abs=0.01)
    assert f.read_text(encoding="utf-8") == "old\nbody\n"


def test_update_line_writes_when_transform_differs(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    f.write_text("old\n", encoding="utf-8")

    def _xform(text: str) -> str:
        return text.replace("old", "new")

    update_line(f, transform=_xform)
    assert f.read_text(encoding="utf-8") == "new\n"


def test_update_line_respects_mtime(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    f.write_text("v1\n", encoding="utf-8")
    stale = f.stat().st_mtime - 999

    def _xform(text: str) -> str:
        return text + "extra\n"

    with pytest.raises(ConflictError):
        update_line(f, transform=_xform, expected_mtime=stale)
    assert f.read_text(encoding="utf-8") == "v1\n"


def test_write_atomic_preserves_unicode(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "日本語.md"
    text = "# [計画] テスト\n日本語コンテンツ。\n"
    write_atomic(f, text)
    assert f.read_text(encoding="utf-8") == text


def test_write_atomic_no_tmpfile_leftover_on_success(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    write_atomic(f, "x")
    leftovers = [p for p in f.parent.iterdir() if p.name.startswith(".plan.md.")]
    assert leftovers == []


def test_write_atomic_does_not_create_docsweep_dir(tmp_path: Path):
    """回帰防止: 書き込みが .docsweep/ ディレクトリを勝手に作らない。

    v0.4 で backup 機構を撤去した。以前は初回書き込みで ``.docsweep/backup/`` が
    生成され、.gitignore し忘れの public リポで docs/local 由来の md が意図せず
    push される事故が発生していた。同じ穴を再導入しないための保険。
    """
    proj = _setup_project(tmp_path)
    f = proj / "plan.md"
    write_atomic(f, "content")

    def _xform(text: str) -> str:
        return text + "\n更新\n"

    update_line(f, transform=_xform)
    assert not (proj / ".docsweep").exists()
