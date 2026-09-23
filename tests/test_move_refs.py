"""移動に伴う参照の書き換え（docsweep_parent / パス形式の related）と、その undo。"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import date
from pathlib import Path

import pytest

from docsweep.closeout import check_closeout
from docsweep.config import load_config
from docsweep.engine import auto_sweep, doc_for_path, run_scan
from docsweep.models import MoveLogEntry
from docsweep.related import backref_records
from docsweep.services.archive import archive_done, undo_last_batch
from docsweep.services.frontmatter import read_frontmatter


def _project(tmp_path: Path, *, project_yaml: str | None = None) -> Path:
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    (project / "docs" / "local").mkdir(parents=True)
    if project_yaml is not None:
        (project / ".docsweep.yaml").write_text(project_yaml, encoding="utf-8")
    return project


def _cfg(tmp_path: Path):
    global_path = tmp_path / "global.yaml"
    if not global_path.exists():
        global_path.write_text("roots: []\n", encoding="utf-8")
    return load_config(explicit_roots=[str(tmp_path)], global_path=global_path)


_H1 = {"done": "完了", "in-progress": "実行中", "planned": "計画"}


def _doc(
    path: Path,
    *,
    state: str = "in-progress",
    parent: str | None = None,
    related: str = "[]",
    body: str = "",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: plan", "status: draft", f"docsweep_state: {state}"]
    if parent is not None:
        lines.append(f"docsweep_parent: {parent}")
    lines += [f"related: {related}", "---", f"# [{_H1[state]}] {path.stem}", "", "## 概要", "", body or "本文", ""]
    path.write_text("\n".join(lines), encoding="utf-8", newline="")
    return path


def _fm(path: Path) -> dict:
    data = read_frontmatter(path)
    assert data is not None
    return data


# ---- docsweep_parent ------------------------------------------------------------


def test_archiving_the_parent_rewrites_the_child_parent_ref(tmp_path: Path) -> None:
    project = _project(tmp_path)
    queue = project / "docs" / "local"
    _doc(queue / "plan_parent.md", state="done")
    child = _doc(queue / "plan_parent_c1_x.md", parent="docs/local/plan_parent.md")
    body_before = child.read_text(encoding="utf-8").split("---", 2)[2]

    result = auto_sweep(_cfg(tmp_path))

    assert _fm(child)["docsweep_parent"] == "docs/local/archive/plan_parent.md"
    assert result.ref_updates == [
        {
            "path": child.resolve().as_posix(),
            "field": "docsweep_parent",
            "before": "docs/local/plan_parent.md",
            "after": "docs/local/archive/plan_parent.md",
        }
    ]
    assert result.ref_failed == []
    # 本文には触れない。
    assert child.read_text(encoding="utf-8").split("---", 2)[2] == body_before

    closeout = check_closeout(
        queue / "archive" / "plan_parent.md", project_dir=project, config=_cfg(tmp_path)
    )
    assert "parent_moved" not in {w.get("code") for w in closeout.warnings}
    assert [c["relation"] for c in closeout.children] == ["explicit"]


def test_mirror_layout_rewrites_to_the_nested_archive_path(tmp_path: Path) -> None:
    project = _project(tmp_path, project_yaml="archive_layout: mirror\n")
    queue = project / "docs" / "local"
    _doc(queue / "app-a" / "plan_parent.md", state="done")
    child = _doc(queue / "app-a" / "plan_child.md", parent="docs/local/app-a/plan_parent.md")

    auto_sweep(_cfg(tmp_path))

    assert _fm(child)["docsweep_parent"] == "docs/local/archive/app-a/plan_parent.md"


def test_parent_and_child_moved_together_keep_pointing_at_each_other(tmp_path: Path) -> None:
    """同じ操作で親子とも archive へ移すと、archive 側の子も archive 側の親を指す。"""
    project = _project(tmp_path)
    queue = project / "docs" / "local"
    _doc(queue / "plan_parent.md", state="done")
    _doc(queue / "plan_child.md", state="done", parent="docs/local/plan_parent.md")

    auto_sweep(_cfg(tmp_path))

    archived_child = queue / "archive" / "plan_child.md"
    assert _fm(archived_child)["docsweep_parent"] == "docs/local/archive/plan_parent.md"


def test_unrelated_parent_refs_are_left_alone(tmp_path: Path) -> None:
    project = _project(tmp_path)
    queue = project / "docs" / "local"
    _doc(queue / "plan_parent.md", state="done")
    other = _doc(queue / "plan_other.md", parent="docs/local/plan_elsewhere.md")
    before = other.read_bytes()

    auto_sweep(_cfg(tmp_path))

    assert other.read_bytes() == before


# ---- related --------------------------------------------------------------------


def test_only_path_form_related_values_are_rewritten(tmp_path: Path) -> None:
    project = _project(tmp_path)
    queue = project / "docs" / "local"
    moved = _doc(queue / "plan_target.md", state="done")
    absolute = (queue / "plan_target.md").as_posix()
    referrer = _doc(
        queue / "plan_referrer.md",
        related=f"[plan_target.md, docs/local/plan_target.md, {absolute}, ../local/plan_target.md]",
    )
    cfg = _cfg(tmp_path)
    target_before = doc_for_path(moved, cfg)
    assert target_before is not None
    backrefs_before = len(backref_records(target_before.record, run_scan(cfg).records))

    auto_sweep(cfg)

    assert _fm(referrer)["related"] == [
        "plan_target.md",
        "docs/local/archive/plan_target.md",
        (project.resolve() / "docs" / "local" / "archive" / "plan_target.md").as_posix(),
        "../local/plan_target.md",
    ]
    target_after = doc_for_path(queue / "archive" / "plan_target.md", cfg)
    assert target_after is not None
    assert len(backref_records(target_after.record, run_scan(cfg).records)) == backrefs_before


# ---- dry-run --------------------------------------------------------------------


def test_dry_run_reports_planned_rewrites_without_writing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    queue = project / "docs" / "local"
    parent = _doc(queue / "plan_parent.md", state="done")
    child = _doc(
        queue / "plan_child.md",
        parent="docs/local/plan_parent.md",
        related="[docs/local/plan_parent.md]",
    )
    before = child.read_bytes()

    result = auto_sweep(_cfg(tmp_path), dry_run=True)

    assert parent.is_file()
    assert child.read_bytes() == before
    assert {(u["field"], json.dumps(u["after"])) for u in result.ref_updates} == {
        ("docsweep_parent", json.dumps("docs/local/archive/plan_parent.md")),
        ("related", json.dumps(["docs/local/archive/plan_parent.md"])),
    }
    assert not (project.parent / ".docsweep" / "moves.jsonl").exists()


# ---- junction -------------------------------------------------------------------


def _directory_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlink creation is unavailable")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
    )
    if result.returncode != 0:
        pytest.skip("directory junction creation is unavailable")


def test_linked_queue_keeps_repo_relative_parent_refs(tmp_path: Path) -> None:
    """queue が repo の外の実体でも、書き換え後の値は repo 相対（実体パスを書かない）。"""
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    (project / "docs").mkdir()
    external = tmp_path / "external" / "local"
    external.mkdir(parents=True)
    _directory_link(project / "docs" / "local", external)
    _doc(external / "plan_parent.md", state="done")
    child = _doc(external / "plan_child.md", parent="docs/local/plan_parent.md")

    auto_sweep(_cfg(tmp_path))

    assert _fm(child)["docsweep_parent"] == "docs/local/archive/plan_parent.md"


# ---- undo -----------------------------------------------------------------------


def test_undo_restores_the_file_and_the_parent_ref(tmp_path: Path) -> None:
    project = _project(tmp_path)
    queue = project / "docs" / "local"
    parent = _doc(queue / "plan_parent.md", state="done")
    child = _doc(queue / "plan_child.md", parent="docs/local/plan_parent.md")
    child_before = child.read_bytes()
    cfg = _cfg(tmp_path)

    archived = archive_done(config=cfg, paths=[str(parent)])
    assert archived.to_dict()["ref_updates"][0]["field"] == "docsweep_parent"
    assert _fm(child)["docsweep_parent"] == "docs/local/archive/plan_parent.md"

    undone = undo_last_batch(config=cfg)

    assert parent.is_file()
    assert child.read_bytes() == child_before
    assert undone.failed == []
    assert undone.refs_restored == [
        {
            "path": child.resolve().as_posix(),
            "field": "docsweep_parent",
            "from": "docs/local/archive/plan_parent.md",
            "to": "docs/local/plan_parent.md",
        }
    ]
    # 2 回目は何も戻さない（参照も二重に戻さない）。
    again = undo_last_batch(config=cfg)
    assert again.batch_id is None
    assert again.refs_restored == []


def test_undo_restores_everything_moved_by_one_sweep(tmp_path: Path) -> None:
    """CLI / MCP / Web の一括 sweep（auto_sweep）も、1 回の sweep を 1 バッチとして戻せる。"""
    project = _project(tmp_path)
    queue = project / "docs" / "local"
    first = _doc(queue / "plan_first.md", state="done")
    second = _doc(queue / "plan_second.md", state="done")
    child = _doc(queue / "plan_child.md", parent="docs/local/plan_first.md")
    child_before = child.read_bytes()
    cfg = _cfg(tmp_path)

    swept = auto_sweep(cfg)

    assert not first.exists() and not second.exists()
    batch_ids = {entry.batch_id for entry in swept}
    assert len(swept) == 2 and len(batch_ids) == 1 and None not in batch_ids

    undone = undo_last_batch(config=cfg)

    assert undone.batch_id in batch_ids
    assert undone.failed == []
    assert first.is_file() and second.is_file()
    assert child.read_bytes() == child_before


def test_sweep_dry_run_records_nothing_to_undo(tmp_path: Path) -> None:
    project = _project(tmp_path)
    queue = project / "docs" / "local"
    done = _doc(queue / "plan_done.md", state="done")
    cfg = _cfg(tmp_path)

    preview = auto_sweep(cfg, dry_run=True)

    assert [entry.batch_id for entry in preview] == [None]
    assert done.is_file()
    assert undo_last_batch(config=cfg).batch_id is None


def test_undo_does_not_overwrite_a_ref_changed_after_the_move(tmp_path: Path) -> None:
    project = _project(tmp_path)
    queue = project / "docs" / "local"
    parent = _doc(queue / "plan_parent.md", state="done")
    child = _doc(queue / "plan_child.md", parent="docs/local/plan_parent.md")
    cfg = _cfg(tmp_path)
    archive_done(config=cfg, paths=[str(parent)])
    text = child.read_text(encoding="utf-8").replace(
        "docs/local/archive/plan_parent.md", "docs/local/plan_manual.md"
    )
    child.write_text(text, encoding="utf-8", newline="")

    undone = undo_last_batch(config=cfg)

    assert parent.is_file()
    assert _fm(child)["docsweep_parent"] == "docs/local/plan_manual.md"
    assert [f.get("field") for f in undone.failed] == ["docsweep_parent"]


# ---- 既存の読み手を壊さない ----------------------------------------------------------


def test_existing_move_log_lines_keep_their_shape() -> None:
    entry = MoveLogEntry(ts="t", op="archive", project="p", status="done", src="a", dst="b")

    assert set(entry.to_dict()) == {"ts", "op", "project", "status", "src", "dst", "batch_id"}


def test_ref_rewrites_are_not_counted_as_archives_this_week(tmp_path: Path) -> None:
    from docsweep.streak import archived_this_week

    project = _project(tmp_path)
    queue = project / "docs" / "local"
    _doc(queue / "plan_parent.md", state="done")
    _doc(queue / "plan_child.md", parent="docs/local/plan_parent.md")
    cfg = _cfg(tmp_path)
    before = archived_this_week(cfg, today=date.today())

    auto_sweep(cfg)

    log = (tmp_path / ".docsweep" / "moves.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["op"] for line in log] == ["archive", "ref_rewrite"]
    assert archived_this_week(cfg, today=date.today()) == before
