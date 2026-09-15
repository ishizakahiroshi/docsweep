from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from docsweep.config import load_config
from docsweep.cli import main
from docsweep.engine import auto_sweep
from docsweep.release import (
    ReleaseTrackingError,
    archive_bucket_for_tag,
    close_release,
    parse_release_tag,
    release_archive_root,
    release_bucket_for_record,
    release_tracking_diagnostic,
    validate_git_tag,
    validate_release_label,
)
from docsweep.scan import scan
from docsweep import archive as archive_module
from docsweep import workspace_migration
from docsweep.services import frontmatter as frontmatter_service
from docsweep.workspace_migration import apply_manifest, build_manifest


def _git_repo(path: Path, *, tag: str | None = None) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "docsweep-test"], check=True)
    marker = path / "README.md"
    marker.write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)
    if tag:
        subprocess.run(["git", "-C", str(path), "tag", tag], check=True)


def _directory_link(link: Path, target: Path) -> None:
    """Create a directory symlink, with a Windows junction fallback."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlink creation is unavailable")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("directory junction creation is unavailable")


def _config(project: Path, *, global_yaml: str = ""):
    global_path = project / "global.yaml"
    global_path.write_text(global_yaml, encoding="utf-8")
    return load_config(
        project_dir=project,
        explicit_roots=[str(project)],
        global_path=global_path,
    )


def _plan(path: Path, *, state: str = "done", target: str | None = None, policy: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: plan", f"status: {state}"]
    if target is not None:
        lines.append(f"target_release: {target}")
    if policy is not None:
        lines.append(f"docsweep_policy: {policy}")
    lines.extend(["---", f"# [{state}] test", "", "## 概要", "", "body\n"])
    path.write_text("\n".join(lines), encoding="utf-8")


def test_config_release_tracking_is_tristate_and_project_override(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repo(project)
    (project / ".docsweep.yaml").write_text(
        "archive_partition: release\n"
        "release_tracking:\n"
        "  mode: disabled\n",
        encoding="utf-8",
    )
    cfg = _config(
        project,
        global_yaml=(
            "release_tracking:\n"
            "  mode: enabled\n"
            "  archive_group_by: major\n"
        ),
    )
    assert cfg.release_tracking.mode == "disabled"
    assert cfg.release_tracking.archive_group_by == "major"
    assert cfg.archive_partition == "release"


def test_target_labels_are_preserved_and_dangerous_values_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repo(project)
    (project / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n",
        encoding="utf-8",
    )
    for index, label in enumerate(("v0.9.x", "v0.9.1", "2026-Q3")):
        _plan(project / "docs" / "local" / f"plan_{index}.md", target=label)
    cfg = _config(project)
    records = [item.record for item in scan(cfg)]
    assert {record.target_release for record in records} == {"v0.9.x", "v0.9.1", "2026-Q3"}
    with pytest.raises(ReleaseTrackingError):
        validate_release_label("../outside")
    with pytest.raises(ReleaseTrackingError):
        validate_release_label("v0.9.x\\outside")


def test_release_tag_prefix_prerelease_and_four_component_rules() -> None:
    from docsweep.config import ReleaseTrackingConfig

    no_prefix = ReleaseTrackingConfig(mode="enabled", tag_prefix="none")
    optional_prefix = ReleaseTrackingConfig(mode="enabled", tag_prefix="optional")
    assert parse_release_tag("1.2.3", no_prefix) is not None
    assert parse_release_tag("v1.2.3", no_prefix) is None
    assert parse_release_tag("1.2.3", optional_prefix) is not None
    assert parse_release_tag("v1.2.3", optional_prefix) is not None
    assert parse_release_tag("v1.2.3-rc.1", optional_prefix) is not None
    assert parse_release_tag("v1.2.3-01", optional_prefix) is None
    assert parse_release_tag("v1.2.3.4", optional_prefix) is None


def test_custom_tag_pattern_normalizes_unmatched_optional_prefix() -> None:
    from docsweep.config import ReleaseTrackingConfig

    tracking = ReleaseTrackingConfig(
        mode="enabled",
        tag_pattern=(
            r"(?P<prefix>v)?(?P<major>0|[1-9]\d*)\."
            r"(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
        ),
    )

    parsed = parse_release_tag("1.2.3", tracking)

    assert parsed is not None
    assert parsed.prefix == ""
    assert archive_bucket_for_tag(parsed, "minor") == "1.2.x"


def test_archive_bucket_grouping_and_released_in_precedence(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repo(project, tag="v2.3.4")
    (project / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n  archive_group_by: minor\n",
        encoding="utf-8",
    )
    cfg = _config(project)
    parsed = parse_release_tag("v2.3.4", cfg.release_tracking)
    assert parsed is not None
    assert archive_bucket_for_tag(parsed, "patch") == "v2.3.4"
    assert archive_bucket_for_tag(parsed, "minor") == "v2.3.x"
    assert archive_bucket_for_tag(parsed, "major") == "v2.x"
    _plan(project / "docs" / "local" / "plan_done.md", target="2026-Q3")
    record = scan(cfg)[0].record
    record.released_in = "v2.3.4"
    assert release_bucket_for_record(record, cfg, project_dir=project) == "v2.3.x"
    assert release_archive_root("docs/local/archive/v0.9.x") == "docs/local/archive"
    assert release_archive_root("v0.9.x") == "."


def test_release_scan_prunes_legacy_archive_parent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repo(project)
    (project / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive/v0.9.x\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n",
        encoding="utf-8",
    )
    _plan(project / "docs" / "local" / "plan_active.md", target="v0.9.x")
    _plan(
        project / "docs" / "local" / "archive" / "v0.8.x" / "plan_old.md",
        target="v0.8.x",
    )
    cfg = _config(project)
    records = [item.record.path for item in scan(cfg)]
    assert records == [(project / "docs" / "local" / "plan_active.md").resolve().as_posix()]


def test_release_close_uses_same_dry_run_candidates_and_moves_only_eligible(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repo(project, tag="v2.3.4")
    (project / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n  archive_group_by: minor\n",
        encoding="utf-8",
    )
    queue = project / "docs" / "local"
    _plan(queue / "plan_done.md", target="v2.3.x")
    _plan(queue / "plan_watch.md", state="watching", target="v2.3.x")
    _plan(queue / "plan_open.md", state="planned", target="v2.3.x")
    _plan(queue / "plan_missing.md")
    _plan(queue / "plan_never.md", target="v2.3.x", policy="never_archive")
    _plan(queue / "plan_other.md", target="v1.0.x")
    cfg = _config(project)

    preview = close_release(cfg, "v2.3.4", dry_run=True)
    assert len(preview.movable) == 1
    assert len(preview.watching) == 1
    assert len(preview.incomplete) == 1
    assert len(preview.target_unset) == 1
    assert len(preview.never_archive) == 1
    assert len(preview.target_mismatch) == 1
    assert not (queue / "archive" / "v2.3.x" / "plan_done.md").exists()

    applied = close_release(cfg, "v2.3.4")
    assert len(applied.moved) == 1
    moved = queue / "archive" / "v2.3.x" / "plan_done.md"
    assert moved.is_file()
    assert "released_in: v2.3.4" in moved.read_text(encoding="utf-8")


def test_release_archive_missing_target_fails_closed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repo(project)
    (project / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n",
        encoding="utf-8",
    )
    path = project / "docs" / "local" / "plan_done.md"
    _plan(path)
    cfg = _config(project)
    result = auto_sweep(cfg, dry_run=True)
    assert result == []
    assert result.failed
    assert "target_release" in result.failed[0]["error"]


def test_non_semver_hierarchical_git_tag_is_diagnosed_not_treated_as_path(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repo(project, tag="release/2026-09")
    (project / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n",
        encoding="utf-8",
    )
    _plan(project / "docs" / "local" / "plan_release.md", target="release/2026-09")
    cfg = _config(project)

    assert validate_git_tag("release/2026-09") == "release/2026-09"
    result = close_release(cfg, "release/2026-09", dry_run=True)
    assert len(result.tag_missing) == 1
    assert result.tag_missing[0]["reason"] == "tag_not_recognized"
    diagnostic = release_tracking_diagnostic(cfg, project_dir=project)
    assert diagnostic["ignored_tag_count"] == 1


def test_release_close_stops_on_exact_destination_collision(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repo(project, tag="v2.3.4")
    (project / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n  archive_group_by: minor\n",
        encoding="utf-8",
    )
    source = project / "docs" / "local" / "plan_done.md"
    _plan(source, target="v2.3.x")
    destination = project / "docs" / "local" / "archive" / "v2.3.x" / source.name
    destination.parent.mkdir(parents=True)
    destination.write_text("sentinel\n", encoding="utf-8")
    cfg = _config(project)

    preview = close_release(cfg, "v2.3.4", dry_run=True)
    assert not preview.movable
    assert len(preview.collision) == 1
    applied = close_release(cfg, "v2.3.4")
    assert len(applied.collision) == 1
    assert source.is_file()
    assert destination.read_text(encoding="utf-8") == "sentinel\n"
    assert "released_in" not in source.read_text(encoding="utf-8")


def test_release_close_rolls_back_move_and_metadata_when_move_log_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repo(project, tag="v2.3.4")
    (project / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n  archive_group_by: minor\n",
        encoding="utf-8",
    )
    source = project / "docs" / "local" / "plan_log_failure.md"
    _plan(source, target="v2.3.x")
    cfg = _config(project)

    def fail_move_log(root: Path, entry: object) -> None:
        raise OSError("injected move log failure")

    monkeypatch.setattr(archive_module, "append_move_log", fail_move_log)
    result = close_release(cfg, "v2.3.4")
    destination = project / "docs" / "local" / "archive" / "v2.3.x" / source.name

    assert not result.moved
    assert len(result.failed) == 1
    assert source.is_file()
    assert not destination.exists()
    assert "released_in" not in source.read_text(encoding="utf-8")
    assert result.failed[0]["reason"] == "move_log_failed"
    assert result.failed[0]["source_exists"] is True
    assert result.failed[0]["destination_exists"] is False


def test_release_close_reports_unlogged_move_when_rollback_destination_collides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repo(project, tag="v2.3.4")
    (project / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n  archive_group_by: minor\n",
        encoding="utf-8",
    )
    source = project / "docs" / "local" / "plan_log_collision.md"
    _plan(source, target="v2.3.x")
    cfg = _config(project)

    def fail_move_log(_root: Path, _entry: object) -> None:
        source.write_text("external replacement\n", encoding="utf-8")
        raise OSError("injected move log failure")

    monkeypatch.setattr(archive_module, "append_move_log", fail_move_log)
    result = close_release(cfg, "v2.3.4")
    destination = project / "docs" / "local" / "archive" / "v2.3.x" / source.name

    assert not result.moved
    assert len(result.failed) == 1
    assert result.failed[0]["reason"] == "rollback_failed"
    assert result.failed[0]["source_exists"] is True
    assert result.failed[0]["destination_exists"] is True
    assert source.read_text(encoding="utf-8") == "external replacement\n"
    assert destination.is_file()
    assert "released_in: v2.3.4" in destination.read_text(encoding="utf-8")


def test_release_close_reports_frontmatter_rollback_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repo(project, tag="v2.3.4")
    (project / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n  archive_group_by: minor\n",
        encoding="utf-8",
    )
    source = project / "docs" / "local" / "plan_frontmatter_failure.md"
    _plan(source, target="v2.3.x")
    cfg = _config(project)
    real_update = frontmatter_service.update_frontmatter_field
    calls = 0

    def fail_rollback(
        path: Path,
        field: str,
        value: object,
        *,
        expected_mtime: float | None = None,
    ) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_update(path, field, value, expected_mtime=expected_mtime)
        raise OSError("injected frontmatter rollback failure")

    def fail_move_log(_root: Path, _entry: object) -> None:
        raise OSError("injected move log failure")

    monkeypatch.setattr(frontmatter_service, "update_frontmatter_field", fail_rollback)
    monkeypatch.setattr(archive_module, "append_move_log", fail_move_log)
    result = close_release(cfg, "v2.3.4")

    assert not result.moved
    assert len(result.failed) == 1
    assert result.failed[0]["reason"] == "move_log_failed"
    assert "frontmatter rollback failure" in result.failed[0]["released_in_rollback_error"]
    assert source.is_file()
    assert "released_in: v2.3.4" in source.read_text(encoding="utf-8")


def test_archive_log_rollback_preserves_later_worker_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    first = project / "plan_first.md"
    second = project / "plan_second.md"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    real_append = archive_module.append_move_log
    first_in_append = threading.Event()
    second_started = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    def controlled_append(root: Path, entry: object) -> None:
        nonlocal call_count
        with call_lock:
            call_count += 1
            current = call_count
        if current == 1:
            first_in_append.set()
            assert second_started.wait(timeout=5)
            raise OSError("injected first-worker log failure")
        real_append(root, entry)  # type: ignore[arg-type]

    monkeypatch.setattr(archive_module, "append_move_log", controlled_append)
    errors: list[Exception] = []

    def archive_first() -> None:
        try:
            archive_module.archive_file(
                src=first,
                project_dir=project,
                archive_dir="archive",
                root=tmp_path,
                project="project",
                status="done",
            )
        except Exception as exc:  # expected injected transaction failure
            errors.append(exc)

    def archive_second() -> None:
        second_started.set()
        archive_module.archive_file(
            src=second,
            project_dir=project,
            archive_dir="archive",
            root=tmp_path,
            project="project",
            status="done",
        )

    first_thread = threading.Thread(target=archive_first)
    second_thread = threading.Thread(target=archive_second)
    first_thread.start()
    assert first_in_append.wait(timeout=5)
    second_thread.start()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert len(errors) == 1
    assert first.is_file()
    assert not second.is_file()
    entries = [
        json.loads(line)
        for line in archive_module.move_log_path(tmp_path)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(entries) == 1
    assert entries[0]["src"].endswith("plan_second.md")


def test_workspace_review_enable_persists_settings_and_applies_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    doc = repo / "docs" / "local" / "plan_first-review.md"
    _plan(doc)
    global_config = tmp_path / "global.yaml"
    global_config.write_text("", encoding="utf-8")
    journal = tmp_path / "review-journal.json"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    rc = main(
        [
            "workspace",
            "migrate-release-tracking",
            "--root",
            str(workspace),
            "--config",
            str(global_config),
            "--default-target",
            "v1.2.x",
            "--review",
            "--journal",
            str(journal),
        ]
    )
    capsys.readouterr()

    assert rc == 0
    project_config = (repo / ".docsweep.yaml").read_text(encoding="utf-8")
    assert "mode: enabled" in project_config
    assert "default_target: v1.2.x" in project_config
    assert "archive_group_by: minor" in project_config
    assert "archive_dir: docs/local/archive" in project_config
    assert "target_release: v1.2.x" in doc.read_text(encoding="utf-8")
    assert journal.is_file()


def test_workspace_review_skip_persists_disabled_and_is_not_prompted_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    doc = repo / "docs" / "local" / "plan_first-skip.md"
    _plan(doc)
    global_config = tmp_path / "global.yaml"
    global_config.write_text("", encoding="utf-8")
    journal = tmp_path / "skip-journal.json"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["n", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    first = main(
        [
            "workspace",
            "migrate-release-tracking",
            "--root",
            str(workspace),
            "--config",
            str(global_config),
            "--review",
            "--journal",
            str(journal),
        ]
    )
    capsys.readouterr()
    assert first == 0
    assert "mode: disabled" in (repo / ".docsweep.yaml").read_text(encoding="utf-8")
    assert "target_release:" not in doc.read_text(encoding="utf-8")

    def fail_prompt(_prompt: str) -> str:
        raise AssertionError("disabled repositories must not be prompted again")

    monkeypatch.setattr("builtins.input", fail_prompt)
    second = main(
        [
            "workspace",
            "migrate-release-tracking",
            "--root",
            str(workspace),
            "--config",
            str(global_config),
            "--review",
        ]
    )
    capsys.readouterr()
    assert second == 0
    assert json.loads(journal.read_text(encoding="utf-8"))["counts"]["applied"] == 1


def test_workspace_review_cancel_is_side_effect_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    doc = repo / "docs" / "local" / "plan_cancel-review.md"
    _plan(doc)
    global_config = tmp_path / "global.yaml"
    global_config.write_text("", encoding="utf-8")
    journal = tmp_path / "cancel-journal.json"
    manifest_path = tmp_path / "cancel-manifest.json"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "q")

    rc = main(
        [
            "workspace",
            "migrate-release-tracking",
            "--root",
            str(workspace),
            "--config",
            str(global_config),
            "--review",
            "--manifest",
            str(manifest_path),
            "--journal",
            str(journal),
        ]
    )
    capsys.readouterr()

    assert rc == 0
    assert not (repo / ".docsweep.yaml").exists()
    assert "target_release:" not in doc.read_text(encoding="utf-8")
    assert not journal.exists()
    assert not manifest_path.exists()


def test_workspace_review_rejects_blank_target_before_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    doc = repo / "docs" / "local" / "plan_blank-target.md"
    _plan(doc)
    global_config = tmp_path / "global.yaml"
    global_config.write_text("", encoding="utf-8")
    journal = tmp_path / "blank-target-journal.json"
    answers = iter(["y", "", "q"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    rc = main(
        [
            "workspace",
            "migrate-release-tracking",
            "--root",
            str(workspace),
            "--config",
            str(global_config),
            "--review",
            "--journal",
            str(journal),
        ]
    )
    output = capsys.readouterr()

    assert rc == 0
    assert "default target_release は必須" in output.err
    assert not (repo / ".docsweep.yaml").exists()
    assert "target_release:" not in doc.read_text(encoding="utf-8")
    assert not journal.exists()


def test_workspace_review_shows_final_actions_and_requires_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    doc = repo / "docs" / "local" / "plan_confirm.md"
    _plan(doc)
    global_config = tmp_path / "global.yaml"
    global_config.write_text("", encoding="utf-8")
    answers = iter(["y", "n"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    rc = main(
        [
            "workspace",
            "migrate-release-tracking",
            "--root",
            str(workspace),
            "--config",
            str(global_config),
            "--default-target",
            "v1.2.x",
            "--review",
        ]
    )
    output = capsys.readouterr().out

    assert rc == 0
    assert "適用前の棚卸し" in output
    assert "最終的に適用する内容" in output
    assert "action: target_release=v1.2.x" in output
    assert not (repo / ".docsweep.yaml").exists()
    assert "target_release:" not in doc.read_text(encoding="utf-8")


def test_workspace_json_non_tty_never_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    _plan(repo / "docs" / "local" / "plan_json-review.md")
    global_config = tmp_path / "global.yaml"
    global_config.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO())

    def fail_prompt(_prompt: str) -> str:
        raise AssertionError("JSON/non-TTY workspace migration must not prompt")

    monkeypatch.setattr("builtins.input", fail_prompt)
    rc = main(
        [
            "workspace",
            "migrate-release-tracking",
            "--root",
            str(workspace),
            "--config",
            str(global_config),
            "--review",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["mode"] == "dry-run"
    assert payload["manifest"]["repositories"][0]["status"] == "needs_review"
    assert not (repo / ".docsweep.yaml").exists()


@pytest.mark.parametrize("use_ci", [False, True])
def test_workspace_auto_and_ci_review_never_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    use_ci: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    _plan(repo / "docs" / "local" / "plan_auto-review.md")
    global_config = tmp_path / "global.yaml"
    global_config.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    if use_ci:
        monkeypatch.setenv("CI", "1")
    else:
        monkeypatch.delenv("CI", raising=False)

    def fail_prompt(_prompt: str) -> str:
        raise AssertionError("auto/CI review must not prompt")

    monkeypatch.setattr("builtins.input", fail_prompt)
    args = [
        "workspace",
        "migrate-release-tracking",
        "--root",
        str(workspace),
        "--config",
        str(global_config),
        "--review",
    ]
    if not use_ci:
        args.append("--auto")
    rc = main(args)
    capsys.readouterr()

    assert rc == 0
    assert not (repo / ".docsweep.yaml").exists()


def test_workspace_manifest_infers_release_bucket_and_reports_queue_outlier(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    (repo / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive/v0.8.x\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n",
        encoding="utf-8",
    )
    archived = repo / "docs" / "local" / "archive" / "v0.8.x" / "plan_old.md"
    archived.parent.mkdir(parents=True)
    archived.write_text("# [完了] old\n\n## 概要\n\nold\n", encoding="utf-8")
    _plan(repo / "docs" / "plan_outside.md", target="v0.8.x")

    manifest = build_manifest([workspace], default_target="v0.9.x")
    entry = manifest["repositories"][0]
    assert entry["inventory"]["archived_documents"] == 1
    assert any(
        action["reason"] == "archive_bucket_inference"
        for action in entry["actions"]
    )
    assert any(
        item.get("reason") == "document_outside_configured_work_dir"
        for item in entry["review_items"]
    )
    journal = tmp_path / "journal.json"
    result = apply_manifest(manifest, journal_path=journal)
    assert result["counts"]["applied"] == 1
    assert "target_release: v0.8.x" in archived.read_text(encoding="utf-8")


def test_workspace_manifest_is_metadata_only_and_apply_is_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    doc = repo / "docs" / "local" / "plan_secret.md"
    _plan(doc)
    doc.write_text(doc.read_text(encoding="utf-8") + "PASSWORD=do-not-copy\n", encoding="utf-8")

    excluded_repo = workspace / "excluded" / "nested"
    excluded_repo.mkdir(parents=True)
    _git_repo(excluded_repo)
    _plan(excluded_repo / "docs" / "local" / "plan_skip.md")

    manifest = build_manifest(
        [workspace], excludes=["excluded"], default_target="v1.2.x"
    )
    serialized = __import__("json").dumps(manifest, ensure_ascii=False)
    assert "PASSWORD=do-not-copy" not in serialized
    assert [item["root"] for item in manifest["repositories"]] == [repo.as_posix()]
    assert manifest["repositories"][0]["actions"]
    assert any("excluded" in item["path"] for item in manifest["excluded"])

    journal = tmp_path / "journal.json"
    first = apply_manifest(manifest, journal_path=journal)
    assert first["counts"]["applied"] == 1
    assert "target_release: v1.2.x" in doc.read_text(encoding="utf-8")
    assert "release_tracking:" in (repo / ".docsweep.yaml").read_text(encoding="utf-8")
    second = apply_manifest(manifest, journal_path=journal)
    assert second["counts"]["skipped"] == 1
    assert second["repositories"][0]["changed"] == 0


def test_workspace_manifest_scans_configured_directory_link_queue(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    physical_queue = tmp_path / "queue-data"
    physical_queue.mkdir()
    _plan(physical_queue / "plan_junction.md")
    (repo / "docs").mkdir()
    _directory_link(repo / "docs" / "local", physical_queue)
    (repo / ".docsweep.yaml").write_text(
        "work_dir: docs/local\n"
        "archive_dir: docs/local/archive\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n",
        encoding="utf-8",
    )

    manifest = build_manifest([workspace], default_target="v1.2.x")
    entry = manifest["repositories"][0]

    assert entry["status"] == "ready"
    assert entry["inventory"]["active_documents"] == 1
    assert entry["inventory"]["target_release_missing"] == 1
    assert entry["actions"]
    assert entry["actions"][0]["path"] == (
        repo / "docs" / "local" / "plan_junction.md"
    ).as_posix()
    assert not any(
        item.get("path") == (repo / "docs" / "local").as_posix()
        for item in manifest["excluded"]
    )


def test_workspace_manifest_rejects_nested_directory_link_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    physical_queue = tmp_path / "queue-data"
    physical_queue.mkdir()
    escape_target = tmp_path / "escape-data"
    escape_target.mkdir()
    _plan(escape_target / "plan_escape.md")
    _directory_link(physical_queue / "nested", escape_target)
    (repo / "docs").mkdir()
    _directory_link(repo / "docs" / "local", physical_queue)
    (repo / ".docsweep.yaml").write_text(
        "work_dir: docs/local\n"
        "release_tracking:\n  mode: enabled\n",
        encoding="utf-8",
    )

    manifest = build_manifest([workspace], default_target="v1.2.x")
    entry = manifest["repositories"][0]

    assert entry["status"] == "needs_review"
    assert not any("plan_escape.md" in action["path"] for action in entry["actions"])
    assert any(
        item.get("reason") == "nested_reparse_point"
        for item in entry["review_items"]
    )


def test_workspace_manifest_reports_queue_outside_docs_without_actions(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    queue = repo / "docs" / "local"
    queue.mkdir(parents=True)
    _plan(repo / "docs" / "plan_outside.md")
    (repo / ".docsweep.yaml").write_text(
        "work_dir: docs/local\n"
        "release_tracking:\n  mode: enabled\n",
        encoding="utf-8",
    )

    manifest = build_manifest([workspace], default_target="v1.2.x")
    entry = manifest["repositories"][0]

    assert entry["inventory"]["active_documents"] == 0
    assert not entry["actions"]
    assert any(
        item.get("reason") == "document_outside_configured_work_dir"
        for item in entry["review_items"]
    )


def test_workspace_apply_accepts_configured_archive_outside_work_queue(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    (repo / "docs" / "local").mkdir(parents=True)
    archived = repo / "archive" / "v1.2.x" / "plan_old.md"
    _plan(archived)
    (repo / ".docsweep.yaml").write_text(
        "work_dir: docs/local\n"
        "archive_dir: archive\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n",
        encoding="utf-8",
    )

    manifest = build_manifest([workspace], default_target="v1.3.x")
    entry = manifest["repositories"][0]
    assert entry["status"] == "ready"
    assert entry["actions"][0]["scope"] == "archive"

    result = apply_manifest(manifest, journal_path=tmp_path / "journal.json")

    assert result["counts"]["applied"] == 1
    assert "target_release: v1.2.x" in archived.read_text(encoding="utf-8")


def test_workspace_manifest_rejects_queue_outside_action_without_override(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    (repo / "docs" / "local").mkdir(parents=True)
    outside = repo / "docs" / "plan_outside.md"
    _plan(outside)
    (repo / ".docsweep.yaml").write_text(
        "work_dir: docs/local\n"
        "release_tracking:\n  mode: enabled\n",
        encoding="utf-8",
    )

    manifest = build_manifest([workspace])
    entry = manifest["repositories"][0]
    entry["actions"].append(
        {
            "path": outside.as_posix(),
            "field": "target_release",
            "value": "v1.2.x",
        }
    )
    manifest["manifest_sha256"] = workspace_migration._manifest_fingerprint(manifest)
    result = apply_manifest(manifest, journal_path=tmp_path / "journal.json")

    assert result["counts"]["failed"] == 1
    assert "target_release" not in outside.read_text(encoding="utf-8")


def test_workspace_manifest_needs_review_when_configured_queue_is_unavailable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    (repo / "docs").mkdir()
    (repo / ".docsweep.yaml").write_text(
        "work_dir: docs/missing\n"
        "release_tracking:\n  mode: enabled\n",
        encoding="utf-8",
    )

    manifest = build_manifest([workspace], default_target="v1.2.x")
    entry = manifest["repositories"][0]

    assert entry["status"] == "needs_review"
    assert not entry["actions"]
    assert any(
        item.get("reason") == "configured_work_queue_unavailable"
        for item in entry["review_items"]
    )


def test_workspace_apply_rejects_stale_document_before_any_repo_write(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    queue = repo / "docs" / "local"
    queue.mkdir(parents=True)
    doc = queue / "plan_stale.md"
    _plan(doc)
    config = repo / ".docsweep.yaml"
    config.write_text("release_tracking:\n  mode: enabled\n", encoding="utf-8")

    manifest = build_manifest([workspace], default_target="v1.2.x")
    original_config = config.read_text(encoding="utf-8")
    _plan(doc, target="v9.9.x")

    result = apply_manifest(manifest, journal_path=tmp_path / "journal.json")

    assert result["counts"]["needs_review"] == 1
    assert result["repositories"][0]["reason"] == "stale_manifest"
    assert "target_release: v9.9.x" in doc.read_text(encoding="utf-8")
    assert config.read_text(encoding="utf-8") == original_config


def test_workspace_apply_rejects_stale_project_config(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    queue = repo / "docs" / "local"
    queue.mkdir(parents=True)
    doc = queue / "plan_config.md"
    _plan(doc)
    config = repo / ".docsweep.yaml"
    config.write_text("release_tracking:\n  mode: enabled\n", encoding="utf-8")

    manifest = build_manifest([workspace], default_target="v1.2.x")
    config.write_text(
        "release_tracking:\n  mode: enabled\n  archive_group_by: major\n",
        encoding="utf-8",
    )

    result = apply_manifest(manifest, journal_path=tmp_path / "journal.json")

    assert result["counts"]["needs_review"] == 1
    assert result["repositories"][0]["reason"] == "stale_manifest"
    assert "target_release" not in doc.read_text(encoding="utf-8")


def test_workspace_apply_journal_preserves_applied_state_across_retries(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    queue = repo / "docs" / "local"
    queue.mkdir(parents=True)
    doc = queue / "plan_resume.md"
    _plan(doc)
    (repo / ".docsweep.yaml").write_text(
        "release_tracking:\n  mode: enabled\n", encoding="utf-8"
    )
    manifest = build_manifest([workspace], default_target="v1.2.x")
    journal = tmp_path / "journal.json"

    first = apply_manifest(manifest, journal_path=journal)
    second = apply_manifest(manifest, journal_path=journal)
    doc.write_text(
        doc.read_text(encoding="utf-8") + "external edit\n", encoding="utf-8"
    )
    third = apply_manifest(manifest, journal_path=journal)
    journal_data = __import__("json").loads(journal.read_text(encoding="utf-8"))

    assert first["counts"]["applied"] == 1
    assert second["repositories"][0]["reason"] == "already_applied"
    assert third["repositories"][0]["reason"] == "already_applied"
    assert "external edit" in doc.read_text(encoding="utf-8")
    assert journal_data["cumulative"]["repositories"][repo.as_posix()]["status"] == "applied"
    assert len(journal_data["attempts"]) == 3


def test_workspace_apply_rejects_manifest_content_change_even_with_same_id(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    queue = repo / "docs" / "local"
    queue.mkdir(parents=True)
    _plan(queue / "plan_identity.md")
    (repo / ".docsweep.yaml").write_text(
        "release_tracking:\n  mode: enabled\n", encoding="utf-8"
    )
    manifest = build_manifest([workspace], default_target="v1.2.x")
    manifest["default_target"] = "v9.9.x"

    with pytest.raises(ValueError, match="fingerprint"):
        apply_manifest(manifest, journal_path=tmp_path / "journal.json")


def test_workspace_apply_rolls_back_repo_when_a_later_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    queue = repo / "docs" / "local"
    queue.mkdir(parents=True)
    doc = queue / "plan_rollback.md"
    _plan(doc)
    config = repo / ".docsweep.yaml"
    config.write_text("release_tracking:\n  mode: enabled\n", encoding="utf-8")
    manifest = build_manifest([workspace], default_target="v1.2.x")
    original_config = config.read_text(encoding="utf-8")
    original_doc = doc.read_text(encoding="utf-8")
    real_write_atomic = workspace_migration.write_atomic
    calls = 0

    def fail_document_write(path: Path, text: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected document write failure")
        real_write_atomic(path, text)

    monkeypatch.setattr(workspace_migration, "write_atomic", fail_document_write)
    result = apply_manifest(manifest, journal_path=tmp_path / "journal.json")

    repository = result["repositories"][0]
    assert result["counts"]["failed"] == 1
    assert repository["reason"] == "apply_failed"
    assert repository["rolled_back"] is True
    assert config.read_text(encoding="utf-8") == original_config
    assert doc.read_text(encoding="utf-8") == original_doc


def test_workspace_apply_reports_rollback_failure_and_changed_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "repo"
    repo.mkdir()
    _git_repo(repo)
    queue = repo / "docs" / "local"
    queue.mkdir(parents=True)
    doc = queue / "plan_rollback_failure.md"
    _plan(doc)
    config = repo / ".docsweep.yaml"
    config.write_text("release_tracking:\n  mode: enabled\n", encoding="utf-8")
    manifest = build_manifest([workspace], default_target="v1.2.x")
    real_write_atomic = workspace_migration.write_atomic
    calls = 0

    def fail_write_and_rollback(path: Path, text: str) -> None:
        nonlocal calls
        calls += 1
        if calls in {2, 3}:
            raise OSError("injected write and rollback failure")
        real_write_atomic(path, text)

    monkeypatch.setattr(workspace_migration, "write_atomic", fail_write_and_rollback)
    result = apply_manifest(manifest, journal_path=tmp_path / "journal.json")

    repository = result["repositories"][0]
    assert result["counts"]["failed"] == 1
    assert repository["reason"] == "rollback_failed"
    assert repository["rolled_back"] is False
    assert config.as_posix() in repository["changed_paths"]
    assert "archive_partition: release" in config.read_text(encoding="utf-8")
