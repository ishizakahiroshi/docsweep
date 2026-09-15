from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from docsweep.config import load_config
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
