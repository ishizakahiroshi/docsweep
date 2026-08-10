from pathlib import Path

import pytest

from docsweep.config import DEFAULT_TYPES, load_config
from docsweep.engine import auto_sweep
from docsweep.scan import scan


def test_default_types_includes_manual_release():
    release_type = next(t for t in DEFAULT_TYPES if t.name == "manual_release")

    assert release_type.pattern == "manual_release-*.md"
    assert release_type.stale_days == 180
    assert release_type.archive_dir is None


def test_obsidian_artifacts_are_not_queue_types():
    recap_type = next(t for t in DEFAULT_TYPES if t.name == "recap")

    assert recap_type.pattern == "recap_*.md"
    assert not any(t.name in {"report_skill", "report_audit"} for t in DEFAULT_TYPES)


@pytest.fixture
def manual_release_record(tmp_path: Path):
    release = tmp_path / "manual_release-v0.1.0_2026-01-01.md"
    release.write_text("# [完了] v0.1.0 release\n", encoding="utf-8")
    (tmp_path / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive\n", encoding="utf-8"
    )
    config = load_config(
        explicit_roots=[str(tmp_path)],
        global_path=tmp_path / "no_global.yaml",
    )

    return tmp_path, config, scan(config)[0].record


def test_manual_release_type_recognized_in_scan(manual_release_record):
    _, _, record = manual_release_record

    assert record.type == "manual_release"


def test_manual_release_done_is_archivable(manual_release_record):
    root, config, record = manual_release_record

    assert record.archivable is True
    assert record.auto_movable is True

    moved = auto_sweep(config, dry_run=False)
    assert [Path(entry.src).name for entry in moved] == [
        "manual_release-v0.1.0_2026-01-01.md"
    ]
    assert (
        root / "docs" / "local" / "archive" / "manual_release-v0.1.0_2026-01-01.md"
    ).is_file()


def test_plan_release_uses_standard_plan_type(tmp_path: Path):
    release = tmp_path / "plan_release-v0.2.0_2026-08-09.md"
    release.write_text("# [完了] v0.2.0 release\n", encoding="utf-8")
    config = load_config(
        explicit_roots=[str(tmp_path)],
        global_path=tmp_path / "no_global.yaml",
    )

    records = scan(config)

    assert len(records) == 1
    assert records[0].record.type == "plan"
    assert records[0].record.archivable is True


def test_static_manual_and_reference_docs_are_not_work_records(tmp_path: Path):
    for name in ("manual_release.md", "manual_operator.md", "reference_api.md", "setup_local.md"):
        (tmp_path / name).write_text("# Stable reference\n", encoding="utf-8")
    config = load_config(
        explicit_roots=[str(tmp_path)],
        global_path=tmp_path / "no_global.yaml",
    )

    assert scan(config) == []
