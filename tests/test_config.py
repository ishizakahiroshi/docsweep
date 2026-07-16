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
