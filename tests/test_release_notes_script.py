"""scripts/release_notes.py（publish.yml が GitHub Release の本文を CHANGELOG から取り出す）。"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_notes.py"

_spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
assert _spec is not None and _spec.loader is not None
release_notes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_notes)

CHANGELOG = """# Changelog

## [Unreleased]

### Added

- next

## [0.2.0] - 2026-01-02

### Added

- feature two

### Fixed

- fix two

## [0.1.0] - 2026-01-01

- first
"""


def test_section_returns_only_that_version():
    assert release_notes.section(CHANGELOG, "v0.2.0") == (
        "### Added\n\n- feature two\n\n### Fixed\n\n- fix two"
    )
    assert release_notes.section(CHANGELOG, "0.1.0") == "- first"


def test_section_does_not_match_a_longer_version():
    text = "## [0.2.10] - 2026-01-03\n\n- ten\n"
    assert release_notes.section(text, "v0.2.1") is None


def test_missing_or_empty_section_is_none():
    assert release_notes.section(CHANGELOG, "v9.9.9") is None
    assert release_notes.section("## [1.0.0] - 2026-01-01\n\n## [0.9.0]\n- x\n", "v1.0.0") is None


def test_cli_writes_notes_and_fails_without_a_section(tmp_path: Path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(CHANGELOG, encoding="utf-8")

    ok = subprocess.run([sys.executable, str(SCRIPT), "v0.2.0", str(path)],
                        capture_output=True, text=True, encoding="utf-8")
    missing = subprocess.run([sys.executable, str(SCRIPT), "v9.9.9", str(path)],
                             capture_output=True, text=True, encoding="utf-8")

    assert ok.returncode == 0
    assert ok.stdout.startswith("### Added\n\n- feature two")
    assert missing.returncode == 1
    assert missing.stdout == ""


def test_every_released_version_in_the_real_changelog_has_notes():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for tag in ("v0.6.0", "v0.7.0", "v0.8.0"):
        notes = release_notes.section(text, tag)
        assert notes, tag
        assert "## [" not in notes
