"""OKF（plan_okf-adoption_2026-06-29.md C1）frontmatter 併用パーサのテスト。

検出ロジックは ``detect.detect_status`` に集約されているので、ここでは
拡張フィールド（tags / owner / review_status / related / last_reviewed / type）と
type/status 矛盾 warn の挙動だけ確認する。基本の status 検出は ``test_detect.py``。
"""

from __future__ import annotations

from docsweep.config import DEFAULT_TYPES
from docsweep.detect import detect_status
from docsweep.states import StateModel

SM = StateModel()
PLAN_TYPE = next(t for t in DEFAULT_TYPES if t.name == "plan")
BUGFIX_TYPE = next(t for t in DEFAULT_TYPES if t.name == "bugfix")


def test_frontmatter_okf_fields_are_extracted():
    text = (
        "---\n"
        "type: plan\n"
        "status: planned\n"
        "tags: [auth, refactor]\n"
        "owner: ishiz\n"
        "review_status: draft\n"
        "related: [bugfix_login-500_2026-06-20.md]\n"
        "last_reviewed: 2026-06-29\n"
        "---\n"
        "# [計画] 認証リファクタ\n"
    )
    d = detect_status(text=text, filename="plan_auth.md", sm=SM, _type=PLAN_TYPE)
    assert d.state_key == "planned"
    assert d.source == "frontmatter"
    assert d.tags == ["auth", "refactor"]
    assert d.owner == "ishiz"
    assert d.review_status == "draft"
    assert d.related == ["bugfix_login-500_2026-06-20.md"]
    assert d.last_reviewed == "2026-06-29"
    assert d.frontmatter_type == "plan"
    assert d.type_conflict is False
    assert not d.frontmatter_warnings


def test_h1_fallback_when_no_frontmatter():
    """frontmatter なし → H1 ラベル + ファイル名にフォールバック（後方互換 100%）。"""
    text = "# [計画] 認証\n"
    d = detect_status(text=text, filename="plan_auth.md", sm=SM, _type=PLAN_TYPE)
    assert d.state_key == "planned"
    assert d.source == "h1"
    assert d.tags == []
    assert d.owner is None
    assert d.review_status is None
    assert d.related == []
    assert d.last_reviewed is None
    assert d.frontmatter_type is None
    assert d.type_conflict is False


def test_frontmatter_type_mismatch_emits_warning():
    """frontmatter type='plan' なのに filename が bugfix_*.md なら warn を出す（自動上書きしない）。"""
    text = "---\ntype: plan\nstatus: in-progress\n---\n# [実行中] x\n"
    d = detect_status(
        text=text, filename="bugfix_x_2026-06-29.md", sm=SM, _type=BUGFIX_TYPE
    )
    assert d.frontmatter_type == "plan"
    assert d.type_conflict is True
    # warn 文言は具体的な値（plan / bugfix）を含む
    joined = " | ".join(d.frontmatter_warnings)
    assert "plan" in joined and "bugfix" in joined
    # state_key は frontmatter の status を優先（H1 と一致するので conflict=False のはず）
    assert d.state_key == "in-progress"


def test_frontmatter_status_vs_h1_conflict_warning():
    """frontmatter status と H1 ラベルが食い違ったら conflict + warn を立てる。"""
    text = "---\nstatus: discarded\n---\n# [計画] タイトル\n"
    d = detect_status(text=text, filename="plan_x.md", sm=SM, _type=PLAN_TYPE)
    assert d.state_key == "discarded"  # frontmatter 優先
    assert d.conflict is True
    assert any("status=" in w for w in d.frontmatter_warnings)


def test_tags_scalar_is_promoted_to_list():
    """``tags: foo`` のスカラ表記も 1 要素のリストとして拾う。"""
    text = "---\ntype: plan\nstatus: planned\ntags: solo\n---\n# [計画] x\n"
    d = detect_status(text=text, filename="plan_x.md", sm=SM, _type=PLAN_TYPE)
    assert d.tags == ["solo"]


def test_related_yaml_block_form_is_extracted():
    text = (
        "---\n"
        "type: plan\n"
        "status: planned\n"
        "related:\n"
        "  - a.md\n"
        "  - b.md\n"
        "---\n"
        "# [計画] x\n"
    )
    d = detect_status(text=text, filename="plan_x.md", sm=SM, _type=PLAN_TYPE)
    assert d.related == ["a.md", "b.md"]


def test_last_reviewed_date_object_is_normalized_to_string():
    """YAML が ``YYYY-MM-DD`` を ``datetime.date`` に自動変換しても文字列で受ける。"""
    text = "---\ntype: plan\nstatus: planned\nlast_reviewed: 2026-06-29\n---\n# [計画] x\n"
    d = detect_status(text=text, filename="plan_x.md", sm=SM, _type=PLAN_TYPE)
    assert d.last_reviewed == "2026-06-29"


def test_no_warning_when_frontmatter_type_matches_filename():
    text = "---\ntype: plan\nstatus: planned\n---\n# [計画] x\n"
    d = detect_status(text=text, filename="plan_x.md", sm=SM, _type=PLAN_TYPE)
    assert d.type_conflict is False
    assert d.frontmatter_warnings == []
