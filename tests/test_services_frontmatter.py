"""``docsweep.services.frontmatter`` の単体テスト（C4 OKF frontmatter 編集）。

H1 ラベル・本文・他フィールドが温存されること、list/scalar の正規化、frontmatter 無し
ファイルに対する新設挙動を検証する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep.services.frontmatter import (
    FrontmatterValidationError,
    current_owner,
    update_frontmatter_field,
)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_update_tags_list_inserts_into_existing_frontmatter(tmp_path: Path):
    p = tmp_path / "plan_x.md"
    p.write_text(
        "---\ntype: plan\nstatus: planned\n---\n# [計画] X\n\n## 概要\n\nbody.\n",
        encoding="utf-8",
    )
    res = update_frontmatter_field(p, "tags", ["ui", "backend"])
    assert res.new_value == ["ui", "backend"]
    text = _read(p)
    assert "tags: [ui, backend]" in text
    assert "# [計画] X" in text  # H1 ラベル温存
    assert "body." in text  # 本文温存


def test_update_owner_scalar_replaces_existing(tmp_path: Path):
    p = tmp_path / "plan_y.md"
    p.write_text(
        "---\ntype: plan\nowner: alice\n---\n# [計画] Y\n",
        encoding="utf-8",
    )
    update_frontmatter_field(p, "owner", "bob")
    assert "owner: bob" in _read(p)
    assert "owner: alice" not in _read(p)


def test_update_owner_empty_keeps_line(tmp_path: Path):
    p = tmp_path / "plan_z.md"
    p.write_text(
        "---\ntype: plan\nowner: alice\n---\n# [計画] Z\n",
        encoding="utf-8",
    )
    update_frontmatter_field(p, "owner", "")
    txt = _read(p)
    assert "owner: " in txt
    assert "owner: alice" not in txt


def test_creates_frontmatter_when_missing(tmp_path: Path):
    p = tmp_path / "plan_w.md"
    p.write_text("# [計画] W\n\nbody\n", encoding="utf-8")
    update_frontmatter_field(p, "tags", ["x"])
    txt = _read(p)
    assert txt.startswith("---\n")
    assert "tags: [x]" in txt
    assert "# [計画] W" in txt
    assert "body" in txt


def test_related_list_roundtrip(tmp_path: Path):
    p = tmp_path / "plan_rel.md"
    p.write_text("---\nstatus: planned\n---\n# [計画] R\n", encoding="utf-8")
    update_frontmatter_field(p, "related", ["plan_a.md", "bugfix_b.md"])
    assert "related: [plan_a.md, bugfix_b.md]" in _read(p)


def test_review_status_scalar(tmp_path: Path):
    p = tmp_path / "plan_rv.md"
    p.write_text("---\nstatus: planned\n---\n# [計画] RV\n", encoding="utf-8")
    update_frontmatter_field(p, "review_status", "review")
    assert "review_status: review" in _read(p)


def test_rejects_unknown_field(tmp_path: Path):
    p = tmp_path / "plan_u.md"
    p.write_text("---\nstatus: planned\n---\n# [計画] U\n", encoding="utf-8")
    with pytest.raises(FrontmatterValidationError):
        update_frontmatter_field(p, "secret", "x")


def test_rejects_dangerous_scalar_chars(tmp_path: Path):
    p = tmp_path / "plan_v.md"
    p.write_text("---\nstatus: planned\n---\n# [計画] V\n", encoding="utf-8")
    with pytest.raises(FrontmatterValidationError):
        update_frontmatter_field(p, "owner", "alice: extra")


def test_rejects_dangerous_list_item(tmp_path: Path):
    p = tmp_path / "plan_l.md"
    p.write_text("---\nstatus: planned\n---\n# [計画] L\n", encoding="utf-8")
    with pytest.raises(FrontmatterValidationError):
        update_frontmatter_field(p, "tags", ["a,b"])


def test_current_owner_returns_nonempty():
    # git config or OS login が無くても "unknown" を返す。
    name = current_owner()
    assert isinstance(name, str)
    assert name != ""
