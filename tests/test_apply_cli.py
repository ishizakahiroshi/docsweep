"""Explicit apply targets must not scan the workspace before each operation."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from docsweep.cli import main
from docsweep import scan as scan_module


@pytest.fixture
def project(tmp_path, monkeypatch):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / ".docsweep.yaml").write_text("archive_dir: docs/local/archive\n", encoding="utf-8")
    (repo / ".gitignore").write_text("docs/local/\n", encoding="utf-8")
    (repo / "docs" / "local").mkdir(parents=True)
    monkeypatch.chdir(repo)
    return repo


def _document(path):
    path.write_text(
        "---\ntype: plan\nstatus: stable\ndocsweep_state: planned\n---\n"
        "# [計画] Sample\n\nKeep this body.\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("dry_run", [True, False])
def test_apply_reads_only_named_document(project, tmp_path, monkeypatch, capsys, dry_run):
    target = _document(project / "docs/local/plan_target.md")
    unrelated = _document(project / "docs/local/plan_unrelated.md")
    other = tmp_path / "other-project"
    other.mkdir()
    _document(other / "plan_other.md")
    config = tmp_path / "global.yaml"
    config.write_text(f"roots: {json.dumps([str(project), str(other)])}\n", encoding="utf-8")
    before = target.read_bytes()
    unrelated_before = unrelated.read_bytes()
    loaded = []
    build_doc = scan_module._build_doc

    def observe(path, *args, **kwargs):
        loaded.append(path.resolve())
        return build_doc(path, *args, **kwargs)

    def reject_scan(*args, **kwargs):
        pytest.fail("single-file apply scanned the workspace")

    monkeypatch.setattr("docsweep.engine.scan", reject_scan)
    # doc_for_path imports the helper into engine; instrument both entry points.
    monkeypatch.setattr("docsweep.engine._build_doc", observe)
    monkeypatch.setattr(scan_module, "_build_doc", observe)
    args = ["apply", "--config", str(config), "--project-dir", str(project),
            "--path", "docs/local/plan_target.md", "--action", "relabel",
            "--to", "watching", "--watching-days", "5"]
    if dry_run:
        args.append("--dry-run")
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project"] == project.name
    assert loaded == [target.resolve()]
    assert unrelated.read_bytes() == unrelated_before
    if dry_run:
        assert target.read_bytes() == before
    else:
        text = target.read_text(encoding="utf-8")
        assert "# [様子見] Sample" in text
        assert "docsweep_state: watching" in text
        assert "status: stable" in text
        assert f"due: {date.today() + timedelta(days=5)}" in text
        assert "Keep this body." in text


@pytest.mark.parametrize("case", ["outside", "missing", "unmanaged", "invalid-state", "never-archive"])
def test_apply_rejects_invalid_targets_without_writing(project, tmp_path, capsys, case):
    target = _document(project / "docs/local/plan_target.md")
    action, to = "relabel", "watching"
    if case == "outside":
        target = _document(tmp_path / "plan_outside.md")
    elif case == "missing":
        target = project / "docs/local/plan_missing.md"
    elif case == "unmanaged":
        target = _document(project / "README.md")
    elif case == "invalid-state":
        to = "not-a-state"
    else:
        target.write_text(target.read_text(encoding="utf-8").replace(
            "status: stable", "status: stable\ndocsweep_policy: never_archive"), encoding="utf-8")
        action = "discard"
    before = target.read_bytes() if target.exists() else None
    assert main(["apply", "--root", str(project), "--path", str(target),
                 "--action", action, "--to", to]) == 2
    assert not capsys.readouterr().out
    assert (target.read_bytes() if target.exists() else None) == before


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("state, action, to, target_state", [
    ("pending", "relabel", "done", "done"),
    ("discarded", "resume", None, "in-progress"),
])
def test_apply_refuses_a_disallowed_type_transition_in_dry_run_too(
    project, capsys, dry_run, state, action, to, target_state,
):
    """Dry-run refuses a state the file type does not allow, like the real run.

    2026-10-29: ``relabel --to done --dry-run`` on a pending file printed
    ``"status": "done"`` and exited 0, while the real run exited 2. The type/state
    check lived only inside ``_update_doc_state()``, which dry-run skips. The same
    gap in promote was fixed on 2026-09-11 (test_engine).
    """
    label = {"pending": "保留", "discarded": "廃止"}[state]
    target = project / "docs/local/pending_target.md"
    target.write_text(
        f"---\ntype: pending\nstatus: draft\ndocsweep_state: {state}\n---\n"
        f"# [{label}] Sample\n\nKeep this body.\n",
        encoding="utf-8",
    )
    before = target.read_bytes()
    args = ["apply", "--root", str(project), "--path", str(target), "--action", action]
    if to:
        args += ["--to", to]
    if dry_run:
        args.append("--dry-run")
    assert main(args) == 2
    captured = capsys.readouterr()
    assert not captured.out
    # The pending type allows pending / planned / discarded only.
    assert f"'{target_state}'" in captured.err
    assert "'planned'" in captured.err
    assert target.read_bytes() == before


def test_apply_linked_queue_keeps_project_and_due_settings(project, tmp_path, capsys):
    backing = tmp_path / "backing"
    backing.mkdir()
    target = _document(backing / "plan_linked.md")
    link = project / "linked-queue"
    try:
        link.symlink_to(backing, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    (project / ".docsweep.yaml").write_text(
        "work_dir: linked-queue\ndue:\n  default_offset_days:\n    plan_watching: 9\n",
        encoding="utf-8",
    )
    assert main(["apply", "--root", str(project), "--project-dir", str(project),
                 "--path", str(link / target.name), "--action", "relabel", "--to", "watching"]) == 0
    assert json.loads(capsys.readouterr().out)["project"] == project.name
    assert f"due: {date.today() + timedelta(days=9)}" in target.read_text(encoding="utf-8")
