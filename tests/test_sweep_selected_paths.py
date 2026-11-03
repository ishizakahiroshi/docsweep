"""Explicit sweep selection preserves the complete repo/family reference guards."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from docsweep.cli import main
from docsweep.cli.parser import build_parser
from docsweep.engine import auto_sweep
from test_archive_repo_refs import _cfg, _doc, _git, _put
from test_archive_linked_queue import _directory_link


@pytest.fixture()
def selected_repo(tmp_path):
    root = tmp_path / "repo"
    _put(root / ".docsweep.yaml", "archive_refs:\n  scope: repo\n")
    _put(root / ".gitignore", ".docsweep/\n")
    q = root / "docs/local"
    for name in ("plan_chosen.md", "plan_outside.md", "plan_dirty.md",
                 "plan_fam.md", "plan_fam_c1_ui.md", "plan_code.md"):
        _doc(q / name)
    _doc(q / "plan_active.md", state="in-progress")
    _put(root / "src/app.py", "# docs/local/plan_code.md\n")
    _put(root / "docs/notes.md", "[chosen](local/plan_chosen.md)\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")
    _put(q / "plan_dirty.md", (q / "plan_dirty.md").read_text() + "dirty\n")
    return root


def test_selected_move_preserves_outside_and_rewrites_refs(selected_repo, tmp_path):
    q = selected_repo / "docs/local"
    outside = (q / "plan_outside.md").read_bytes()
    result = auto_sweep(_cfg(selected_repo, tmp_path), paths=[q / "plan_chosen.md"],
                        ref_scope="repo")
    assert not result.failed
    assert {Path(m.src).name for m in result} == {"plan_chosen.md"}
    assert (q / "archive/plan_chosen.md").is_file()
    assert (q / "plan_outside.md").read_bytes() == outside
    assert "local/archive/plan_chosen.md" in (selected_repo / "docs/notes.md").read_text()
    assert result.verify["ok"]


@pytest.mark.parametrize("name,reason", [("plan_dirty.md", "uncommitted"),
    ("plan_code.md", "referenced_by_code"), ("plan_fam.md", "family_incomplete")])
def test_selected_guard_still_holds(selected_repo, tmp_path, name, reason):
    q = selected_repo / "docs/local"
    result = auto_sweep(_cfg(selected_repo, tmp_path), paths=[q / name], ref_scope="repo")
    assert not result
    assert any(h["reason"] == reason and Path(h["path"]).name == name for h in result.held)
    assert (q / name).is_file()


@pytest.mark.parametrize("bad", ["plan_missing.md", "plan_active.md", "duplicate", "blank", "alias"])
def test_bad_selection_aborts_whole_batch(selected_repo, tmp_path, bad):
    q = selected_repo / "docs/local"
    chosen = q / "plan_chosen.md"
    target = q / bad
    if bad == "duplicate":
        target = chosen
    elif bad == "blank":
        target = ""
    elif bad == "alias":
        target = q / "plan_alias.md"
        try:
            target.symlink_to(chosen)
        except OSError:
            pytest.skip("symlink unavailable")
    result = auto_sweep(_cfg(selected_repo, tmp_path), paths=[chosen, target], ref_scope="repo")
    assert not result
    assert result.failed
    assert chosen.is_file()
    assert not (q / "archive").exists()


def test_other_project_rejected(selected_repo, tmp_path):
    q = selected_repo / "docs/local"
    result = auto_sweep(_cfg(selected_repo, tmp_path), project="another-project",
                        paths=[q / "plan_chosen.md"], ref_scope="repo")
    assert not result and result.failed
    assert (q / "plan_chosen.md").is_file()


def test_cli_target_paths_do_not_become_scan_roots(selected_repo, tmp_path, capsys):
    q = selected_repo / "docs/local"
    cfg = tmp_path / "global.yaml"
    cfg.write_text("roots: []\n")
    args = build_parser().parse_args(["sweep", "--root", str(selected_repo),
                                     "--path", str(q / "plan_chosen.md")])
    assert args.paths == []
    assert args.roots == [str(selected_repo)]
    assert args.target_paths == [str(q / "plan_chosen.md")]
    code = main(["sweep", "--root", str(selected_repo), "--project-dir", str(selected_repo),
        "--config", str(cfg), "--path", str(q / "plan_chosen.md"), "--path",
        str(q / "plan_outside.md"), "--dry-run", "--json", "--ref-scope", "repo"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert {Path(m["src"]).name for m in payload["moved"]} == {"plan_chosen.md", "plan_outside.md"}
    assert (q / "plan_chosen.md").is_file()


def test_empty_selection_keeps_existing_default(selected_repo, tmp_path):
    result = auto_sweep(_cfg(selected_repo, tmp_path), paths=[], dry_run=True, ref_scope="repo")
    assert {Path(m.src).name for m in result} == {"plan_chosen.md", "plan_outside.md",
                                               "plan_fam.md", "plan_fam_c1_ui.md"}


@pytest.mark.parametrize("paths", ["", "plan_chosen.md", {}, [None]])
def test_invalid_api_path_container_cannot_fall_back_to_all(selected_repo, tmp_path, paths):
    result = auto_sweep(_cfg(selected_repo, tmp_path), paths=paths, ref_scope="repo")
    assert not result and result.failed
    assert (selected_repo / "docs/local/plan_chosen.md").is_file()


def test_cli_unknown_selection_moves_nothing(selected_repo, tmp_path, capsys):
    q = selected_repo / "docs/local"
    cfg = tmp_path / "global.yaml"
    cfg.write_text("roots: []\n")
    code = main(["sweep", "--root", str(selected_repo), "--project-dir", str(selected_repo),
        "--config", str(cfg), "--path", str(q / "plan_chosen.md"), "--path",
        str(q / "plan_missing.md"), "--json", "--ref-scope", "repo"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1 and payload["failed"] and not payload["moved"]
    assert (q / "plan_chosen.md").is_file()
    assert (q / "plan_outside.md").is_file()


@pytest.mark.parametrize("link_kind", ["junction", "symlink"])
@pytest.mark.parametrize("linked_root", [False, True])
def test_configured_queue_lexical_path_is_valid_and_duplicate_is_not(tmp_path, link_kind, linked_root):
    repo = tmp_path / "physical-repo"
    (repo / ".git").mkdir(parents=True)
    _put(repo / ".docsweep.yaml", "archive_refs:\n  scope: queue\n")
    if linked_root:
        physical = _doc(repo / "docs/local/plan_linked.md")
        root = tmp_path / "configured-root"
        _directory_link(root, repo, link_kind)
    else:
        root = repo
        physical = _doc(tmp_path / "external/local/plan_linked.md")
        _directory_link(repo / "docs/local", physical.parent, link_kind)
    lexical = root / "docs/local/plan_linked.md"
    cfg = _cfg(root, tmp_path)
    before = physical.read_bytes()
    result = auto_sweep(cfg, paths=[lexical], dry_run=True)
    assert not result.failed and len(result) == 1
    assert Path(result[0].src) == physical.resolve()
    duplicate = auto_sweep(cfg, paths=[lexical, physical], dry_run=True)
    assert not duplicate and duplicate.failed
    # A file alias inside the declared queue is not the queue junction itself.
    alias = lexical.parent / "plan_file_alias.md"
    try:
        alias.symlink_to(physical)
    except OSError:
        pytest.skip("file symlink unavailable")
    invalid = auto_sweep(cfg, paths=[alias], dry_run=True)
    assert not invalid and invalid.failed
    assert physical.read_bytes() == before
    assert not (physical.parent / "archive").exists()
