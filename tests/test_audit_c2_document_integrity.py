"""監査 C2: 文書・データ整合の決定的回帰。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path

import pytest

import docsweep.engine as engine_module
import docsweep.related as related_module
from docsweep.archive import archive_file
from docsweep.config import load_config
from docsweep.day import _local_day_bounds
from docsweep.detect import detect_status
from docsweep.migrate import apply_migration
from docsweep.related import apply_fix_related
from docsweep.services.frontmatter import update_frontmatter_field
from docsweep.state import load as load_state
from docsweep.states import StateModel


def _cfg(root: Path):
    return load_config(explicit_roots=[str(root)], global_path=root / "no-global.yaml")


def _write_doc(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def test_archive_reserves_distinct_destinations_under_concurrent_collision(tmp_path: Path):
    root = tmp_path / "root"
    archive = root / "archive"
    archive.mkdir(parents=True)
    existing = archive / "same.md"
    existing.write_bytes(b"existing bytes")
    first = _write_doc(root / "in-1" / "same.md", "first")
    second = _write_doc(root / "in-2" / "same.md", "second")

    def move(src: Path) -> Path:
        return archive_file(
            src=src,
            project_dir=root,
            archive_dir="archive",
            root=root,
            project="root",
            status="done",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        destinations = list(pool.map(move, (first, second)))

    assert existing.read_bytes() == b"existing bytes"
    assert {p.name for p in destinations} == {"same_2.md", "same_3.md"}
    assert {p.read_text(encoding="utf-8") for p in destinations} == {"first", "second"}


def test_auto_sweep_continues_after_one_file_move_failure(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"
    first = _write_doc(root / "proj" / "docs" / "plan_done_a.md", "# [完了] a\n")
    second = _write_doc(root / "proj" / "docs" / "plan_done_b.md", "# [完了] b\n")
    cfg = _cfg(root)
    real_archive_file = engine_module.archive_file

    def fail_first(**kwargs):
        if Path(kwargs["src"]).name == first.name:
            raise FileNotFoundError("simulated race")
        return real_archive_file(**kwargs)

    monkeypatch.setattr(engine_module, "archive_file", fail_first)
    result = engine_module.auto_sweep(cfg)

    assert [Path(item.src).name for item in result] == [second.name]
    assert any(item["path"] == first.resolve().as_posix() for item in result.failed)
    assert first.is_file()
    assert not second.is_file()


def test_promote_continues_after_one_state_update_failure(tmp_path: Path, monkeypatch):
    root = tmp_path / "root"
    first = _write_doc(root / "proj" / "docs" / "plan_watch_a.md", "# [様子見] a\n")
    second = _write_doc(root / "proj" / "docs" / "plan_watch_b.md", "# [様子見] b\n")
    cfg = _cfg(root)
    real_update = engine_module._update_doc_state

    def fail_first(doc, target_key, config, **kwargs):
        if Path(doc.record.path).name == first.name:
            raise PermissionError("simulated read-only document")
        return real_update(doc, target_key, config, **kwargs)

    monkeypatch.setattr(engine_module, "_update_doc_state", fail_first)
    result = engine_module.promote_state(cfg)

    assert [Path(item.src).name for item in result] == [second.name]
    assert any(item["path"] == first.resolve().as_posix() for item in result.failed)
    assert first.is_file()
    assert not second.is_file()


def test_fix_related_collects_decode_failure_and_continues(tmp_path: Path):
    root = tmp_path / "root"
    _write_doc(
        root / "proj" / "plan_a.md",
        "---\ntype: plan\nstatus: planned\nrelated: [plan_b.md]\n---\n# [計画] a\n",
    )
    target = root / "proj" / "plan_b.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        "---\ntype: plan\nstatus: planned\nrelated: []\n---\n# [計画] b\n".encode()
        + b"\xff"
    )

    result = apply_fix_related(_cfg(root))

    assert target.resolve().as_posix() in {item["path"] for item in result.failed}
    assert target.resolve().as_posix() not in result.applied


def test_state_invalid_utf8_is_treated_as_empty_cache(tmp_path: Path):
    project = tmp_path / "project"
    state_path = project / ".docsweep" / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(b'{"files": "\xff"}')

    assert load_state(project).files == {}


def test_bom_and_non_bom_documents_have_same_state():
    sm = StateModel()
    plain = detect_status(
        text="# [完了] x\n", filename="plan_x.md", sm=sm,
    )
    bom = detect_status(
        text="\ufeff# [完了] x\n", filename="plan_x.md", sm=sm,
    )

    assert (bom.state_key, bom.source, bom.title) == (
        plain.state_key,
        plain.source,
        plain.title,
    )


def test_long_frontmatter_keeps_policy_after_head_limit():
    long_value = "x" * 8200
    text = (
        "---\n"
        "type: plan\n"
        "status: done\n"
        f"description: {long_value}\n"
        "docsweep_policy: never_archive\n"
        "---\n"
        "# [完了] retained\n"
    )
    detection = detect_status(text=text, filename="plan_retained.md", sm=StateModel())

    assert detection.docsweep_policy == "never_archive"
    assert detection.state_key == "done"


def test_malformed_frontmatter_does_not_treat_body_as_metadata():
    text = "---\nstatus: [broken\n---\n# [完了] body\n"
    detection = detect_status(text=text, filename="plan_broken.md", sm=StateModel())

    assert detection.docsweep_policy is None
    assert detection.source == "h1"
    assert detection.state_key == "done"


def test_frontmatter_update_preserves_bom_and_crlf(tmp_path: Path):
    path = tmp_path / "plan_crlf.md"
    path.write_bytes(
        "\ufeff---\r\ntype: plan\r\nowner: alice\r\n---\r\n# [計画] x\r\n".encode("utf-8")
    )

    update_frontmatter_field(path, "owner", "bob")
    data = path.read_bytes()

    assert data.startswith(b"\xef\xbb\xbf---\r\n")
    assert b"owner: bob\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")


def test_migrate_preserves_bom_and_crlf(tmp_path: Path):
    root = tmp_path / "root"
    path = root / "proj" / "plan_plain.md"
    path.parent.mkdir(parents=True)
    path.write_bytes("\ufeff# [計画] x\r\n本文\r\n".encode("utf-8"))

    result = apply_migration(_cfg(root), today="2026-08-23")
    data = path.read_bytes()

    assert path.resolve().as_posix() in result.applied
    assert data.startswith(b"\xef\xbb\xbf---\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")


def test_yaml_datetime_due_is_normalized_to_date():
    detection = detect_status(
        text="---\ndue: 2026-08-23T12:34:56\n---\n# [計画] x\n",
        filename="plan_due.md",
        sm=StateModel(),
    )

    assert detection.due == "2026-08-23"
    assert detection.due_parse_error is False


class _BoundaryTz(tzinfo):
    def __init__(self, *, spring: bool):
        self.spring = spring

    def utcoffset(self, dt: datetime | None) -> timedelta:
        if dt is None:
            return timedelta(hours=-5)
        if self.spring:
            return timedelta(hours=-4 if dt.date() >= date(2026, 3, 9) else -5)
        return timedelta(hours=-5 if dt.date() >= date(2026, 11, 2) else -4)

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)


@pytest.mark.parametrize(
    ("day", "tz", "expected_hours"),
    [
        (date(2026, 3, 8), _BoundaryTz(spring=True), 23),
        (date(2026, 11, 1), _BoundaryTz(spring=False), 25),
    ],
)
def test_local_day_bounds_follow_dst_transition(day, tz, expected_hours):
    start, end = _local_day_bounds(day, tz)

    assert end - start == expected_hours * 3600
