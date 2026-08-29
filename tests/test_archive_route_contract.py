"""archive 移送先の決定契約（bugfix_closeout-evidence-classifier-and-archive-route-contract C2）。

2026-08-29 の変更点は 1 つ。**設定を書いていない project の既定移送先を、repo 直下
``archive/`` から ``<work_dir>/archive`` へ変えた。**

変えた理由は既定値の組み合わせにある。``work_dir=docs/local`` /
``work_policy=private`` / ``archive_dir=archive`` が既定なので、設定を書いていない
project は **private な作業文書を git 追跡され得る repo 直下へ移送していた**。
元 bugfix が最低限の不変条件として挙げた「private queue の文書を Git 追跡され得る
場所へ無警告で移さない」を、既定のまま満たしていなかった。

明示設定の優先順位と、``work_policy: shared`` の repo 直下互換は変えていない。
既存の repo 直下 ``archive/`` を使っている project には警告を出す（黙って変えない）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep.config import archive_route_for_project, load_config
from docsweep.engine import auto_sweep


def _project(tmp_path: Path, *, project_yaml: str | None = None) -> Path:
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    (project / "docs" / "local").mkdir(parents=True)
    if project_yaml is not None:
        (project / ".docsweep.yaml").write_text(project_yaml, encoding="utf-8")
    return project


def _cfg(tmp_path: Path, *, global_yaml: str = "roots: []\n"):
    global_path = tmp_path / "global.yaml"
    global_path.write_text(global_yaml, encoding="utf-8")
    return load_config(explicit_roots=[str(tmp_path)], global_path=global_path)


# ---- destination matrix ------------------------------------------------------


def test_config_less_project_archives_inside_the_private_queue(tmp_path: Path) -> None:
    route = archive_route_for_project(_project(tmp_path), _cfg(tmp_path))

    assert route.archive_dir == "docs/local/archive"
    assert route.source == "private_queue"
    assert route.legacy_root is None


def test_explicit_project_archive_dir_always_wins(tmp_path: Path) -> None:
    project = _project(tmp_path, project_yaml="archive_dir: custom/arc\n")
    config = _cfg(tmp_path, global_yaml="roots: []\narchive_dir: other/arc\n")

    route = archive_route_for_project(project, config)

    assert route.archive_dir == "custom/arc"
    assert route.source == "explicit_project"


def test_explicit_global_archive_dir_wins_over_the_queue_default(tmp_path: Path) -> None:
    route = archive_route_for_project(
        _project(tmp_path), _cfg(tmp_path, global_yaml="roots: []\narchive_dir: custom/arc\n")
    )

    assert route.archive_dir == "custom/arc"
    assert route.source == "explicit_global"


def test_shared_queue_keeps_the_repository_root_archive(tmp_path: Path) -> None:
    route = archive_route_for_project(
        _project(tmp_path, project_yaml="work_policy: shared\n"), _cfg(tmp_path)
    )

    assert route.archive_dir == "archive"
    assert route.source == "shared_root"


def test_custom_work_dir_moves_the_archive_with_it(tmp_path: Path) -> None:
    route = archive_route_for_project(
        _project(tmp_path, project_yaml="work_dir: docs/work\n"), _cfg(tmp_path)
    )

    assert route.archive_dir == "docs/work/archive"
    assert route.source == "private_queue"


def test_existing_root_archive_is_reported_instead_of_being_switched_silently(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    legacy = project / "archive"
    legacy.mkdir()
    (legacy / "plan_old.md").write_text("# [完了] 昔の\n", encoding="utf-8")

    route = archive_route_for_project(project, _cfg(tmp_path))

    assert route.archive_dir == "docs/local/archive"
    assert route.legacy_root == "archive"


def test_empty_root_archive_is_not_reported(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "archive").mkdir()

    assert archive_route_for_project(project, _cfg(tmp_path)).legacy_root is None


# ---- dry-run と実移送が同じ結果を使う ------------------------------------------


def _done_plan(project: Path, name: str = "plan_done.md") -> Path:
    path = project / "docs" / "local" / name
    path.write_text("# [完了] x\n\n## 概要\n\nd\n", encoding="utf-8")
    return path


def test_dry_run_and_real_move_resolve_to_the_same_destination(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _done_plan(project)
    config = _cfg(tmp_path)

    preview = auto_sweep(config, dry_run=True)
    assert [entry.dst for entry in preview]

    actual = auto_sweep(config, dry_run=False)

    assert [entry.dst for entry in preview] == [entry.dst for entry in actual]
    assert (project / "docs" / "local" / "archive" / "plan_done.md").is_file()


def test_dry_run_reports_the_route_and_its_reason(tmp_path: Path) -> None:
    """移送候補が無くても、どこへ archive するのかを dry-run だけで確かめられる。"""
    project = _project(tmp_path)
    (project / "docs" / "local" / "plan_open.md").write_text(
        "# [計画] x\n\n## 概要\n\nd\n", encoding="utf-8"
    )

    result = auto_sweep(_cfg(tmp_path), dry_run=True)

    assert list(result) == []
    assert len(result.routes) == 1
    route = result.routes[0]
    assert route["archive_dir"] == "docs/local/archive"
    assert route["source"] == "private_queue"


def test_dry_run_warns_about_an_existing_root_archive(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _done_plan(project)
    legacy = project / "archive"
    legacy.mkdir()
    (legacy / "plan_old.md").write_text("# [完了] 昔の\n", encoding="utf-8")

    result = auto_sweep(_cfg(tmp_path), dry_run=True)

    route = next(r for r in result.routes if r["project"] == project.name)
    assert route["legacy_root"] == "archive"
    assert "archive_dir: archive" in route["warning"]
    # 既存 archive の中身は dry-run では動かない。
    assert (legacy / "plan_old.md").is_file()


def test_declaring_the_legacy_root_keeps_the_old_destination(tmp_path: Path) -> None:
    """警告に従って ``archive_dir: archive`` を書けば、従来どおり repo 直下へ移送する。"""
    project = _project(tmp_path, project_yaml="archive_dir: archive\n")
    _done_plan(project)

    auto_sweep(_cfg(tmp_path), dry_run=False)

    assert (project / "archive" / "plan_done.md").is_file()


@pytest.mark.parametrize("policy", ["private", "shared"])
def test_the_document_never_leaves_its_project(tmp_path: Path, policy: str) -> None:
    project = _project(tmp_path, project_yaml=f"work_policy: {policy}\n")
    _done_plan(project)

    moved = auto_sweep(_cfg(tmp_path), dry_run=False)

    for entry in moved:
        assert Path(entry.dst).resolve().is_relative_to(project.resolve())
