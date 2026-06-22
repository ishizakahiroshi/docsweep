"""services/archive.py — archive_done（[完了]/[廃止] のみ移送）のテスト。"""

from __future__ import annotations

from pathlib import Path

from docsweep.config import load_config
from docsweep.services.archive import archive_done


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _cfg(root: Path):
    return load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")


def test_archive_done_moves_done_and_discarded(tmp_path: Path):
    root = tmp_path / "dev"
    _write(root / "proj" / "docs" / "plan_done.md", "# [完了] 完了\n\n## 概要\n\na\n")
    _write(root / "proj" / "docs" / "plan_discarded.md", "# [廃止] 廃止\n\n## 概要\n\nb\n")
    _write(root / "proj" / "docs" / "plan_watch.md", "# [様子見] 寝かせ\n\n## 概要\n\nc\n")
    res = archive_done(config=_cfg(root), auto=True)
    moved_names = {Path(m.src).name for m in res.moved}
    assert moved_names == {"plan_done.md", "plan_discarded.md"}
    # 様子見は触れない
    assert (root / "proj" / "docs" / "plan_watch.md").exists()


def test_archive_done_rejects_watching_when_specified(tmp_path: Path):
    root = tmp_path / "dev"
    f = _write(root / "proj" / "docs" / "plan_watch.md", "# [様子見] 寝かせ\n\n## 概要\n\na\n")
    res = archive_done(config=_cfg(root), paths=[str(f)])
    assert res.moved == []
    assert any("not archivable" in s.reason for s in res.skipped)
    # ファイルは元のまま
    assert f.exists()


def test_archive_done_with_explicit_paths(tmp_path: Path):
    root = tmp_path / "dev"
    f1 = _write(root / "proj" / "docs" / "plan_done.md", "# [完了] 1\n\n## 概要\n\na\n")
    _write(root / "proj" / "docs" / "plan_other.md", "# [完了] 2\n\n## 概要\n\nb\n")
    res = archive_done(config=_cfg(root), paths=[str(f1)])
    moved_names = {Path(m.src).name for m in res.moved}
    assert moved_names == {"plan_done.md"}


def test_archive_done_empty_when_neither_paths_nor_auto(tmp_path: Path):
    root = tmp_path / "dev"
    _write(root / "proj" / "docs" / "plan_done.md", "# [完了] 1\n\n## 概要\n\na\n")
    # paths も auto も無いと何もしない（破壊安全）
    res = archive_done(config=_cfg(root))
    assert res.moved == []
    assert res.skipped == []


def test_archive_done_reports_unknown_path_in_skipped(tmp_path: Path):
    root = tmp_path / "dev"
    _write(root / "proj" / "docs" / "plan_done.md", "# [完了] 1\n\n## 概要\n\na\n")
    res = archive_done(config=_cfg(root), paths=[str(root / "proj" / "docs" / "nope.md")])
    assert any("not found" in s.reason for s in res.skipped)
