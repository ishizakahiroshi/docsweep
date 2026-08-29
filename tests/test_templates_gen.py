"""docsweep new テンプレ生成: due 初期値挿入と既存挙動の非回帰テスト。"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from docsweep.config import Config, TemplateSection
from docsweep.templates_gen import _resolve_initial_due, new_doc, new_split_plans


def _today_plus(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def test_new_plan_includes_default_due_frontmatter(tmp_path: Path):
    """plan は ``offset_days={'plan': 7}`` で frontmatter に ``due: today+7`` が入る。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc("plan", "auth-refactor", project_dir=proj, offset_days={"plan": 7})
    body = doc.path.read_text(encoding="utf-8")
    assert body.startswith("---\n")
    assert f"due: {_today_plus(7)}" in body
    assert "# [計画] auth-refactor" in body
    assert doc.due == _today_plus(7)


def test_new_pending_uses_pending_offset(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc(
        "pending", "wait-for-vendor",
        project_dir=proj, offset_days={"plan": 7, "pending": 14},
    )
    body = doc.path.read_text(encoding="utf-8")
    assert f"due: {_today_plus(14)}" in body
    assert "# [保留] wait-for-vendor" in body


def test_new_bugfix_does_not_include_due(tmp_path: Path):
    """bugfix は新規時に ``due:`` を付けない（[様子見] 遷移時に追記する設計）。

    OKF 採用後（plan_okf-adoption_2026-06-29.md C1）も bugfix の due 付与方針は不変。
    frontmatter ブロック自体は常時付くようになったが、その中に ``due:`` 行は出さない。
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc(
        "bugfix", "login-500",
        project_dir=proj,
        offset_days={"plan": 7, "pending": 14, "bugfix_watching": 7},
    )
    body = doc.path.read_text(encoding="utf-8")
    assert "due:" not in body
    # OKF 採用: frontmatter の必須フィールドが入る
    assert body.startswith("---\n")
    assert "type: bugfix" in body
    assert "status: draft" in body
    assert "docsweep_state: in-progress" in body
    assert "review_status: draft" in body
    # 2026-06-23 改修: 新規 bugfix は [対応中] でなく [実行中] を書く（active 統合）
    assert "# [実行中] login-500" in body
    assert doc.due is None


def test_no_offset_no_due_still_emits_okf_frontmatter(tmp_path: Path):
    """``offset_days={}`` でも OKF frontmatter は常に付く（due 行だけ落ちる）。

    旧来は frontmatter 自体を省略していたが、OKF 採用後は type/status/tags/owner/
    review_status/related/last_reviewed を常に出すよう仕様変更（後方互換は parser 側で吸収）。
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc("plan", "no-due", project_dir=proj, offset_days={})
    body = doc.path.read_text(encoding="utf-8")
    assert body.startswith("---\n")
    assert "type: plan" in body
    assert "status: draft" in body
    assert "docsweep_state: planned" in body
    assert "tags: []" in body
    assert "review_status: draft" in body
    assert "related: []" in body
    assert "last_reviewed:" in body
    assert "due:" not in body
    assert "# [計画] no-due" in body
    assert doc.due is None


def test_explicit_due_overrides_offset(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc(
        "plan", "x", project_dir=proj,
        due="2030-01-01", offset_days={"plan": 7},
    )
    body = doc.path.read_text(encoding="utf-8")
    assert "due: 2030-01-01" in body
    assert doc.due == "2030-01-01"


def test_no_offset_no_due_legacy_behavior_replaced_by_okf(tmp_path: Path):
    """OKF 採用前は frontmatter を完全省略していたが、現仕様では常時付ける。

    `test_no_offset_no_due_still_emits_okf_frontmatter` が新しい期待値を保証する。
    旧アサート（``# [計画] y`` で始まる）は OKF 採用で意味を失ったので置き換え。
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc("plan", "y", project_dir=proj, offset_days={})
    body = doc.path.read_text(encoding="utf-8")
    assert "# [計画] y" in body
    assert body.startswith("---\n")  # OKF frontmatter は必ず付く
    assert doc.due is None


def test_resolve_initial_due_explicit_wins():
    out = _resolve_initial_due("plan", due="2026-12-31", offset_days={"plan": 7})
    assert out == "2026-12-31"


def test_resolve_initial_due_uses_offset():
    today = date(2026, 6, 23)
    out = _resolve_initial_due("plan", due=None, offset_days={"plan": 5}, today=today)
    assert out == "2026-06-28"


def test_resolve_initial_due_bugfix_returns_none():
    out = _resolve_initial_due(
        "bugfix", due=None, offset_days={"bugfix_watching": 7}, today=date(2026, 6, 23),
    )
    assert out is None  # 新規時 bugfix は明示 due が無ければ付けない


def test_topic_with_path_traversal_rejected(tmp_path: Path):
    """既存挙動: トポイックに .. を含めてもパス区切りで cut される（非回帰）。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc("plan", "../escape", project_dir=proj, offset_days={"plan": 7})
    # 生成ファイルは proj 配下に留まる
    assert proj in doc.path.parents or doc.path.parent == proj
    # トポイックは "escape" 部分のみ採用される
    assert "escape" in doc.path.name


def test_filename_collision_suffix(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    d1 = new_doc("plan", "topic", project_dir=proj, offset_days={"plan": 7})
    d2 = new_doc("plan", "topic", project_dir=proj, offset_days={"plan": 7})
    assert d1.path != d2.path
    assert re.search(r"plan_topic_2\.md$", d2.path.name)


CONTEXT_HEADER = "| C | 種別 | 内容 | 備考/注意点 |"


def test_plan_context_table_column_order(tmp_path: Path):
    """context配分 表は C → 種別 → 内容 → 備考/注意点 の順で生成する（2026-08-27 統一）。

    状態を左端寄りに置いて、表を縦に眺めたときに残作業が読めるようにするための順。
    provenance は列名で `AI実行` を解決するので、この並び替えは記録側に影響しない。
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc("plan", "column-order", project_dir=proj, offset_days={"plan": 7})
    body = doc.path.read_text(encoding="utf-8")
    assert CONTEXT_HEADER in body
    assert "|---|---|---|---|" in body
    assert "| C1 | planned | <TODO> | — |" in body


def test_bugfix_has_context_table_too(tmp_path: Path):
    """bugfix にも同じ列順の context配分 表を持たせる（2026-08-27 変更）。

    表が無いと `provenance start --context C1` が使えないため。ラベル（[実行中] 等）は
    表から自動導出しない点は従来どおり。
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = new_doc("bugfix", "column-order", project_dir=proj, offset_days={"plan": 7})
    body = doc.path.read_text(encoding="utf-8")
    assert CONTEXT_HEADER in body
    # 表は H1 直下（症状より前）に置く
    assert body.index("## context配分") < body.index("## 症状")


DELEGATION_HEADINGS = (
    "#### 目的",
    "#### 調査で確認した現在の実装",
    "#### 作業内容",
    "#### 変更予定ファイル",
    "#### 維持する仕様",
    "#### スコープ外",
    "#### 検証方法",
    "#### 完了条件",
)


def test_delegate_plan_includes_delegation_frontmatter_and_detail_skeleton(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()

    doc = new_doc("plan", "delegate", project_dir=proj, offset_days={}, delegate=True)
    body = doc.path.read_text(encoding="utf-8")

    assert "docsweep_delegation: external" in body
    assert "## C 詳細" in body
    assert "### C1 <TODO: 短い目的>" in body
    assert all(heading in body for heading in DELEGATION_HEADINGS)
    assert body.index("## 概要") < body.index("## C 詳細")


def test_delegate_split_adds_detail_to_parent_and_children(tmp_path: Path):
    project = tmp_path / "project"

    created = new_split_plans(
        "split-delegate",
        n=2,
        project_dir=project,
        offset_days={},
        delegate=True,
    )

    assert len(created) == 3
    parent = created[0].path
    assert parent.read_text(encoding="utf-8").count("docsweep_delegation: external") == 1
    for child in created[1:]:
        body = child.path.read_text(encoding="utf-8")
        assert "docsweep_delegation: external" in body
        assert "## C 詳細" in body
        assert "### C1 <TODO: 短い目的>" in body
        assert "docsweep_parent: docs/local/plan_split-delegate.md" in body


def test_unconfigured_generation_is_byte_for_byte_unchanged(tmp_path: Path):
    for doc_type in ("plan", "bugfix", "pending"):
        plain_project = tmp_path / f"plain-{doc_type}"
        configured_project = tmp_path / f"configured-{doc_type}"
        plain_project.mkdir()
        configured_project.mkdir()

        plain = new_doc(doc_type, "same-output", project_dir=plain_project, offset_days={})
        configured = new_doc(
            doc_type,
            "same-output",
            project_dir=configured_project,
            config=Config(),
            offset_days={},
        )

        assert plain.path.read_bytes() == configured.path.read_bytes()


def test_configured_sections_are_appended_only_to_matching_type(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    config = Config(
        template_sections={
            "plan": (
                TemplateSection(heading="顧客への説明", body="<TODO: 伝達範囲>"),
            ),
        }
    )

    plan = new_doc("plan", "with-section", project_dir=project, config=config, offset_days={})
    bugfix = new_doc("bugfix", "without-section", project_dir=project, config=config, offset_days={})

    plan_body = plan.path.read_text(encoding="utf-8")
    bugfix_body = bugfix.path.read_text(encoding="utf-8")
    assert plan_body.endswith("## 顧客への説明\n\n<TODO: 伝達範囲>\n")
    assert "## 顧客への説明" not in bugfix_body


# --- --split の子ファイル名が親子命名規約と一致すること -------------------------
#
# 生成器は長く `plan_<親topic>-c1.md`（ハイフン）を作っていたが、規約と既存の親子 plan 群、
# そして `docsweep/closeout.py` の子判定フォールバック `^<親stem>_c\d+(?:_|$)` は
# いずれもアンダースコアを期待している。生成直後に手でリネームする運用が常態化していた。


def test_split_children_use_the_underscore_child_convention(tmp_path: Path) -> None:
    created = new_split_plans("release-v0.5.0", n=3, project_dir=tmp_path, offset_days={})

    names = [doc.path.name for doc in created]
    assert names == [
        "plan_release-v0.5.0.md",
        "plan_release-v0.5.0_c1.md",
        "plan_release-v0.5.0_c2.md",
        "plan_release-v0.5.0_c3.md",
    ]


def test_split_children_match_the_closeout_legacy_child_pattern(tmp_path: Path) -> None:
    """生成物が、同じリポジトリ内の子判定フォールバックに実際に一致すること。"""
    import re

    from docsweep.closeout import _LEGACY_CHILD_RE_TEMPLATE

    created = new_split_plans("alpha", n=2, project_dir=tmp_path, offset_days={})
    parent_stem = created[0].path.stem
    pattern = re.compile(_LEGACY_CHILD_RE_TEMPLATE.format(parent=re.escape(parent_stem)))

    for child in created[1:]:
        assert pattern.match(child.path.stem), child.path.name


def test_split_titles_put_the_role_into_the_filename(tmp_path: Path) -> None:
    created = new_split_plans(
        "auth-refactor",
        n=3,
        project_dir=tmp_path,
        offset_days={},
        child_titles=["backend", "frontend", "DB migration"],
    )

    assert [doc.path.name for doc in created[1:]] == [
        "plan_auth-refactor_c1_backend.md",
        "plan_auth-refactor_c2_frontend.md",
        "plan_auth-refactor_c3_db-migration.md",
    ]
    # H1 にも担当名が入る。
    assert "C1: backend" in created[1].path.read_text(encoding="utf-8")


def test_split_titles_count_must_match(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="同じ件数"):
        new_split_plans(
            "mismatch", n=3, project_dir=tmp_path, offset_days={}, child_titles=["a", "b"]
        )
