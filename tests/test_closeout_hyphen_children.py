"""closeout-check が子の名前を ``archive_guard`` の系統の規則と同じ範囲で読むことを確かめる。

``archive_guard._FAMILY_RE`` は v0.7.0 で、旧い命名の ``plan_x-c<数字>.md``（ハイフン・接尾辞なし）も
``_c<数字>(_<名前>)?`` と同じ系統として読むようにした。closeout 側が ``_c<数字>`` のままだと、
``docsweep_parent`` を持たないハイフンの子は子として数えられず、``related`` が親を指していても
``generic_related_not_child`` の警告になるだけだった。

データはすべて合成値（``tmp_path`` の中だけ）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from docsweep import archive_guard
from docsweep.closeout import _legacy_child_name, check_closeout
from docsweep.config import load_config


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _plan(title: str, *, related: str = "[]", parent: str | None = None) -> str:
    parent_line = f"docsweep_parent: {parent}\n" if parent else ""
    return (
        "---\n"
        "type: plan\n"
        "status: draft\n"
        "docsweep_state: in-progress\n"
        f"{parent_line}"
        "review_status: draft\n"
        f"related: {related}\n"
        "---\n"
        f"# [実行中] {title}\n\n"
        "## 完了条件\n\n"
        "- [x] 実装を完了\n\n"
        "## 検証\n\n"
        "- [x] 自動確認: pytest -q 成功\n\n"
        "## 受入条件\n\n"
        "- [x] 手動確認: 完了\n"
    )


def _cfg(project: Path):
    return load_config(explicit_roots=[project.as_posix()], global_path=project / "missing.yaml")


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    return project, project / "docs" / "local"


def _codes(items: list[dict], code: str) -> list[dict]:
    return [item for item in items if item["code"] == code]


def _flagged_names(result, code: str) -> set[str]:
    return {Path(item["path"]).name for item in _codes(result.warnings, code)}


def test_hyphen_children_are_counted_as_inferred_children(tmp_path: Path) -> None:
    """``plan_x-c9.md`` と ``plan_x-c10.md`` は ``related`` が親を指していれば子として数える。"""
    project, local = _project(tmp_path)
    parent = _write(local / "plan_x.md", _plan("x"))
    _write(local / "plan_x-c9.md", _plan("nine", related="[plan_x.md]"))
    _write(local / "plan_x-c10.md", _plan("ten", related="[plan_x.md]"))

    result = check_closeout(parent, project_dir=project, config=_cfg(project))

    assert {item["name"]: item["relation"] for item in result.children} == {
        "plan_x-c9.md": "inferred",
        "plan_x-c10.md": "inferred",
    }
    assert result.verdict == "ready"
    # 子として扱えたので、子でない related の警告は出ない
    assert _flagged_names(result, "generic_related_not_child") == set()
    assert _flagged_names(result, "legacy_child_not_confirmed") == set()


def test_hyphen_child_that_does_not_point_to_the_parent_is_a_candidate_only(tmp_path: Path) -> None:
    """名前が子でも ``related`` が親を指さなければ、``_c`` の子と同じく確定させず警告に残す。"""
    project, local = _project(tmp_path)
    parent = _write(local / "plan_x.md", _plan("x"))
    _write(local / "plan_x-c1.md", _plan("one"))
    _write(local / "plan_x_c2.md", _plan("two"))

    result = check_closeout(parent, project_dir=project, config=_cfg(project))

    assert result.children == []
    assert _flagged_names(result, "legacy_child_not_confirmed") == {"plan_x-c1.md", "plan_x_c2.md"}


def test_underscore_children_are_still_counted(tmp_path: Path) -> None:
    """従来の ``_c<数字>`` の子（接尾辞あり・なし）は変わらず数える。"""
    project, local = _project(tmp_path)
    parent = _write(local / "plan_x.md", _plan("x"))
    _write(local / "plan_x_c1_backend.md", _plan("one", related="[plan_x.md]"))
    _write(local / "plan_x_c2.md", _plan("two", related="[plan_x.md]"))
    _write(local / "plan_x_C3_Upper.md", _plan("three", related="[plan_x.md]"))

    result = check_closeout(parent, project_dir=project, config=_cfg(project))

    assert {item["name"] for item in result.children} == {
        "plan_x_c1_backend.md",
        "plan_x_c2.md",
        "plan_x_C3_Upper.md",
    }
    assert {item["relation"] for item in result.children} == {"inferred"}


def test_underscore_and_hyphen_children_can_be_mixed(tmp_path: Path) -> None:
    project, local = _project(tmp_path)
    parent = _write(local / "plan_x.md", _plan("x"))
    _write(local / "plan_x_c1_backend.md", _plan("one", related="[plan_x.md]"))
    _write(local / "plan_x-c2.md", _plan("two", related="[plan_x.md]"))

    result = check_closeout(parent, project_dir=project, config=_cfg(project))

    assert {item["name"] for item in result.children} == {"plan_x_c1_backend.md", "plan_x-c2.md"}


@pytest.mark.parametrize(
    "name",
    [
        "plan_x-config.md",  # ``-c`` の後が数字でない
        "plan_x-cx.md",
        "plan_x-c1-extra.md",  # ハイフンの形は接尾辞を許さない（archive_guard も系統にしない）
        "plan_x-c1_extra.md",
        "plan_x-c1-c2.md",  # ``plan_x-c1`` の子であって ``plan_x`` の子ではない
        "plan_xy-c1.md",  # 別の親
        "plan_xy_c1.md",
        "plan_x_cx.md",
        "plan_xc1.md",
        "plan_x-c.md",
    ],
)
def test_names_outside_the_rule_are_not_counted_as_children(tmp_path: Path, name: str) -> None:
    project, local = _project(tmp_path)
    parent = _write(local / "plan_x.md", _plan("x"))
    other = _write(local / name, _plan("other", related="[plan_x.md]"))

    result = check_closeout(parent, project_dir=project, config=_cfg(project))

    assert result.children == []
    # 子の候補にもしない（legacy_child_not_confirmed を出さない）。従来どおり related だけの警告になる
    assert _flagged_names(result, "legacy_child_not_confirmed") == set()
    assert _flagged_names(result, "generic_related_not_child") == {other.name}


def test_explicit_parent_still_decides_regardless_of_the_name(tmp_path: Path) -> None:
    """``docsweep_parent`` がある文書は、名前に関わらず従来どおり ``docsweep_parent`` だけで決まる。"""
    project, local = _project(tmp_path)
    parent = _write(local / "plan_x.md", _plan("x"))
    _write(local / "plan_other.md", _plan("other"))
    # 名前は ``plan_x`` の子でも、親が別と明示されていれば ``plan_x`` の子ではない
    _write(
        local / "plan_x-c1.md",
        _plan("hyphen child of other", related="[plan_x.md]", parent="docs/local/plan_other.md"),
    )
    # 名前が子でなくても、親が明示されていれば子
    _write(
        local / "plan_unrelated_name.md",
        _plan("explicit child", parent="docs/local/plan_x.md"),
    )
    # 名前も ``docsweep_parent`` も ``plan_x`` の子
    _write(
        local / "plan_x-c2.md",
        _plan("hyphen child with parent", parent="docs/local/plan_x.md"),
    )

    result = check_closeout(parent, project_dir=project, config=_cfg(project))

    assert {item["name"]: item["relation"] for item in result.children} == {
        "plan_unrelated_name.md": "explicit",
        "plan_x-c2.md": "explicit",
    }
    # 明示の親を持つ文書は名前の推測に回らない
    assert _flagged_names(result, "legacy_child_not_confirmed") == set()


# 親 ``plan_x`` から見た名前の判定が archive_guard の系統の規則と食い違わないこと。
# ``_legacy_child_name`` は親の名前を前置きにする（親も子になりうる入れ子を扱うため）ので、
# 親が系統の根（名前に ``_c<数字>`` / ``-c<数字>`` を持たない）の場合に限って比べる。
_EQUIVALENCE_NAMES = [
    "plan_x_c1",
    "plan_x_c10",
    "plan_x_c1_backend",
    "plan_x_c1_",
    "plan_x_c1backend",
    "plan_x_c1-backend",
    "plan_x_c1_c2",
    "plan_x_C1_Upper",
    "plan_x-c1",
    "plan_x-c9",
    "plan_x-c10",
    "plan_x-C2",
    "plan_x-c1-extra",
    "plan_x-c1_extra",
    "plan_x-c1-c2",
    "plan_x-c",
    "plan_x-cx",
    "plan_x-config",
    "plan_x_cx",
    "plan_xy-c1",
    "plan_xy_c1",
    "plan_xc1",
    "plan_x.v2-c1",
    "plan_x_c1.v2",
    "other-c1",
]


@pytest.mark.parametrize("child", _EQUIVALENCE_NAMES)
def test_child_name_rule_agrees_with_archive_guard_family(child: str) -> None:
    parent_stem = "plan_x"
    in_family = archive_guard._family_base(f"{child}.md") == parent_stem
    parent_doc = SimpleNamespace(path=Path(f"{parent_stem}.md"))
    child_doc = SimpleNamespace(path=Path(f"{child}.md"))

    assert _legacy_child_name(child_doc, parent_doc) is in_family  # type: ignore[arg-type]


def test_child_name_rule_escapes_the_parent_name() -> None:
    """親の名前の ``.`` を任意の 1 文字として読まない。"""
    parent_doc = SimpleNamespace(path=Path("plan_a.b.md"))

    assert _legacy_child_name(SimpleNamespace(path=Path("plan_a.b-c1.md")), parent_doc) is True  # type: ignore[arg-type]
    assert _legacy_child_name(SimpleNamespace(path=Path("plan_aXb-c1.md")), parent_doc) is False  # type: ignore[arg-type]
