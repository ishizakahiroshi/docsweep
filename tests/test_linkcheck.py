"""委譲 plan の C 詳細から linkcheck の宣言を収集するテスト。"""

from __future__ import annotations

from docsweep.linkcheck import (
    _extract_declared_files,
    _extract_files_from_section,
    _extract_section,
)


def test_extracts_files_from_c_detail_h4_section():
    text = (
        "## 概要\n\n概要。\n\n"
        "## C 詳細\n\n"
        "### C1 生成\n\n"
        "#### 変更予定ファイル\n\n"
        "- `docsweep/templates_gen.py`\n"
    )

    section = _extract_section(text)

    assert section is not None
    assert _extract_files_from_section(section) == ["docsweep/templates_gen.py"]


def test_unions_h2_and_h4_files_without_duplicates():
    text = (
        "## 変更予定ファイル\n\n"
        "- `docsweep/linkcheck.py`\n\n"
        "## C 詳細\n\n"
        "### C1 収集\n\n"
        "#### 変更予定ファイル\n\n"
        "- `docsweep/linkcheck.py`\n"
        "- `tests/test_linkcheck.py`\n"
    )

    section = _extract_section(text)

    assert section is not None
    assert _extract_files_from_section(section) == [
        "docsweep/linkcheck.py",
        "tests/test_linkcheck.py",
    ]


def test_unions_mixed_backtick_and_plain_path_notations():
    text = (
        "## 変更予定ファイル\n\n"
        "- docsweep/linkcheck.py\n\n"
        "## C 詳細\n\n"
        "### C1 収集\n\n"
        "#### 変更予定ファイル\n\n"
        "- `tests/test_linkcheck.py`\n"
    )

    assert _extract_declared_files(text) == [
        "docsweep/linkcheck.py",
        "tests/test_linkcheck.py",
    ]


def test_no_declaration_keeps_no_section_result():
    assert _extract_section("## 概要\n\n本文。\n") is None


def test_h2_extraction_keeps_legacy_first_section_behavior():
    text = (
        "## 変更予定ファイル\n\n"
        "- `docsweep/first.py`\n\n"
        "## 変更予定ファイル\n\n"
        "- `docsweep/second.py`\n"
    )

    assert _extract_declared_files(text) == ["docsweep/first.py"]
