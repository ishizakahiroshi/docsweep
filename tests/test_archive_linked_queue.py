"""作業 queue をリポジトリ外への junction / symlink にした構成で、archive を探す処理が中を見ること。

export（``--include-archive``）と resurrect は、archive ディレクトリを ``rglob`` で探す。
``rglob`` の ``**`` は既定でディレクトリ symlink の中へ降りない（Windows の junction は
symlink 扱いされないので降りる）。以前は Linux / macOS で queue（``docs/local``）を
スキャンルートの外への symlink にすると、その中の ``archive/`` を export も resurrect も
1 件も拾わなかった（scan 側の同じ不具合は 2026-09-24 に直した）。

辿るのは scan と同じく「設定済み queue の link そのもの」だけ。
"""

from __future__ import annotations

import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from docsweep.config import load_config
from docsweep.export import _gather_archive_files, run_export
from docsweep.resurrect import find_candidates
from docsweep.resurrect.service import _walk_archive

OLD_PLAN = "# [廃止] old\n\n## 概要\n\nSQLite 索引導入を検討したが見送り。\n"
NEW_PLAN = "# [計画] new\n\n## 概要\n\nSQLite 索引導入を再検討して採用。\n"


def _directory_link(link: Path, target: Path, kind: str) -> None:
    """``link`` を ``target`` へのディレクトリリンクにする。junction は Windows だけ。"""
    link.parent.mkdir(parents=True, exist_ok=True)
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("junction は Windows だけ")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
        )
        if result.returncode != 0:
            pytest.skip("directory junction creation is unavailable")
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink creation is unavailable")


def _workspace(tmp_path: Path, link_kind: str) -> tuple[Path, Path]:
    """スキャンルート ``dev`` の下の repo と、その queue の実体（ルートの外）を作る。"""
    root = tmp_path / "dev"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    external = tmp_path / "external" / "local"
    (external / "archive").mkdir(parents=True)
    (external / "archive" / "plan_old.md").write_text(OLD_PLAN, encoding="utf-8")
    _directory_link(repo / "docs" / "local", external, link_kind)
    return root, external


def _cfg(root: Path, tmp_path: Path):
    return load_config(explicit_roots=[str(root)], global_path=tmp_path / "no-such-global.yaml")


def _resolved(paths) -> list[Path]:
    return sorted(Path(p).resolve() for p in paths)


@pytest.mark.parametrize("link_kind", ["junction", "symlink"])
def test_export_gathers_archive_inside_linked_queue(tmp_path: Path, link_kind: str) -> None:
    root, external = _workspace(tmp_path, link_kind)

    entries = _gather_archive_files(_cfg(root, tmp_path))

    assert _resolved(abs_path for _entry, abs_path, _proj, _root in entries) == [
        (external / "archive" / "plan_old.md").resolve()
    ]


@pytest.mark.parametrize("link_kind", ["junction", "symlink"])
def test_export_bundle_includes_archive_inside_linked_queue(tmp_path: Path, link_kind: str) -> None:
    root, _external = _workspace(tmp_path, link_kind)
    out = tmp_path / "out.zip"

    run_export(_cfg(root, tmp_path), out=out, include_archive=True, allow_sensitive=True)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert [n for n in names if n.endswith("plan_old.md")], names


@pytest.mark.parametrize("link_kind", ["junction", "symlink"])
def test_resurrect_walks_archive_inside_linked_queue(tmp_path: Path, link_kind: str) -> None:
    root, external = _workspace(tmp_path, link_kind)

    found = _walk_archive(_cfg(root, tmp_path))

    assert _resolved(path for path, _text in found) == [
        (external / "archive" / "plan_old.md").resolve()
    ]


@pytest.mark.parametrize("link_kind", ["junction", "symlink"])
def test_resurrect_pairs_archive_and_active_plan_in_linked_queue(
    tmp_path: Path, link_kind: str
) -> None:
    root, external = _workspace(tmp_path, link_kind)
    (external / "plan_new.md").write_text(NEW_PLAN, encoding="utf-8")

    result = find_candidates(_cfg(root, tmp_path), threshold=0.1, use_embedding=False)

    pairs = {(Path(c.archive_path).name, Path(c.related_path).name) for c in result.candidates}
    assert ("plan_old.md", "plan_new.md") in pairs


def test_symlink_other_than_work_dir_is_not_followed(tmp_path: Path) -> None:
    """辿るのは設定された queue の link だけ。ほかのディレクトリ symlink は今までどおり辿らない。"""
    root = tmp_path / "dev"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "docs" / "local").mkdir(parents=True)
    external = tmp_path / "external" / "other"
    (external / "archive").mkdir(parents=True)
    (external / "archive" / "plan_old.md").write_text(OLD_PLAN, encoding="utf-8")
    _directory_link(repo / "docs" / "other", external, "symlink")
    cfg = _cfg(root, tmp_path)

    assert _gather_archive_files(cfg) == []
    assert _walk_archive(cfg) == []


def test_work_dir_symlink_to_its_own_project_does_not_loop(tmp_path: Path) -> None:
    """queue の link が自分の祖先を指していても、循環せず、同じ文書を 2 回拾わない。"""
    root = tmp_path / "dev"
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "archive").mkdir()
    (repo / "archive" / "plan_old.md").write_text(OLD_PLAN, encoding="utf-8")
    _directory_link(repo / "docs" / "local", repo, "symlink")
    cfg = _cfg(root, tmp_path)

    expected = [(repo / "archive" / "plan_old.md").resolve()]
    assert _resolved(abs_path for _e, abs_path, _p, _r in _gather_archive_files(cfg)) == expected
    assert _resolved(path for path, _text in _walk_archive(cfg)) == expected
