"""C6 (wings): resurrect — Jaccard フォールバック経路 + 類似度関数の単体テスト。

embedding (sentence-transformers) は opt-in で重い依存。CI/常設テストでは Jaccard 経路のみ。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep.config import load_config
from docsweep.resurrect import find_candidates
from docsweep.resurrect.embedding import EmbeddingUnavailable
from docsweep.resurrect.service import _extract_summary, _extract_title, _is_dismissed
from docsweep.resurrect.similarity import cosine_similarity, jaccard_similarity


# ===================================================================
# similarity 単体
# ===================================================================


def test_jaccard_identical_is_one():
    assert jaccard_similarity("foo bar baz", "foo bar baz") == 1.0


def test_jaccard_disjoint_is_zero():
    assert jaccard_similarity("foo bar", "alpha beta") == 0.0


def test_jaccard_partial_overlap():
    sim = jaccard_similarity("plan で SQLite を採用する", "SQLite を採用する 別の plan")
    assert 0.0 < sim < 1.0


def test_jaccard_empty_inputs():
    assert jaccard_similarity("", "anything") == 0.0


def test_cosine_orthogonal_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_parallel_is_one():
    assert cosine_similarity([1.0, 2.0], [2.0, 4.0]) == pytest.approx(1.0)


def test_cosine_handles_empty():
    assert cosine_similarity([], [1.0]) == 0.0


# ===================================================================
# service ヘルパ
# ===================================================================


def test_extract_title_strips_label():
    text = "# [完了] my plan\n\n## 概要\n\nx\n"
    assert _extract_title(text) == "my plan"


def test_extract_summary_grabs_section():
    text = "# [計画] x\n\n## 概要\n\nimportant detail\n\n## 関連\n\nother\n"
    summary = _extract_summary(text)
    assert "important detail" in summary
    assert "other" not in summary


def test_is_dismissed_detects_marker():
    text = "---\nresurrect_dismissed: true\n---\n# [廃止] x\n"
    assert _is_dismissed(text) is True


def test_is_dismissed_without_marker():
    text = "---\ntags:\n  - foo\n---\n# [計画] x\n"
    assert _is_dismissed(text) is False


# ===================================================================
# find_candidates (Jaccard 経路)
# ===================================================================


@pytest.fixture
def resurrect_workspace(tmp_path: Path):
    """archive と現役 plan が類似テキストを持つワークスペース。"""
    root = tmp_path / "proj"
    (root / "docs" / "local").mkdir(parents=True)
    (root / "archive").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='proj'\n")

    # archive: 「SQLite 索引導入」を 1 年前に検討して廃止していた
    (root / "archive" / "plan_old_sqlite.md").write_text(
        "# [廃止] old\n\n## 概要\n\nSQLite 索引導入を検討したが見送り。\n",
        encoding="utf-8",
    )
    # 現役: 同じ SQLite 索引を再検討
    (root / "docs" / "local" / "plan_new_sqlite.md").write_text(
        "# [計画] new\n\n## 概要\n\nSQLite 索引導入を再検討して採用。\n",
        encoding="utf-8",
    )
    # 関連無し
    (root / "docs" / "local" / "plan_unrelated.md").write_text(
        "# [計画] unrelated\n\n## 概要\n\n全く違う話題。\n",
        encoding="utf-8",
    )
    # 廃止確認済（再浮上しないはず）
    (root / "archive" / "plan_dismissed.md").write_text(
        "---\nresurrect_dismissed: true\n---\n# [廃止] dismissed\n\n## 概要\n\nSQLite 索引を別の理由で。\n",
        encoding="utf-8",
    )
    return root


def test_find_candidates_jaccard_finds_similar_pair(resurrect_workspace, tmp_path):
    cfg = load_config(explicit_roots=[str(resurrect_workspace)], global_path=tmp_path / "no.yaml")
    result = find_candidates(cfg, threshold=0.1, use_embedding=False)
    assert result.mode == "jaccard"
    # archive plan_old_sqlite.md と現役 plan_new_sqlite.md がペアになる
    paths = {(Path(c.archive_path).name, Path(c.related_path).name) for c in result.candidates}
    assert ("plan_old_sqlite.md", "plan_new_sqlite.md") in paths


def test_find_candidates_skips_dismissed(resurrect_workspace, tmp_path):
    cfg = load_config(explicit_roots=[str(resurrect_workspace)], global_path=tmp_path / "no.yaml")
    result = find_candidates(cfg, threshold=0.0, use_embedding=False)
    archive_names = {Path(c.archive_path).name for c in result.candidates}
    assert "plan_dismissed.md" not in archive_names


def test_find_candidates_respects_threshold(resurrect_workspace, tmp_path):
    cfg = load_config(explicit_roots=[str(resurrect_workspace)], global_path=tmp_path / "no.yaml")
    # 極端に高い閾値だと候補無し
    result = find_candidates(cfg, threshold=0.99, use_embedding=False)
    assert result.candidates == []


def test_find_candidates_falls_back_when_embedding_missing(monkeypatch, resurrect_workspace, tmp_path):
    """sentence-transformers が無い環境を模擬して、Jaccard モードに落ちることを確認。"""

    def boom(texts):
        raise EmbeddingUnavailable("test")

    monkeypatch.setattr("docsweep.resurrect.service.encode", boom)
    cfg = load_config(explicit_roots=[str(resurrect_workspace)], global_path=tmp_path / "no.yaml")
    result = find_candidates(cfg, threshold=0.1, use_embedding=True)
    assert result.mode == "jaccard"
    assert any(Path(c.archive_path).name == "plan_old_sqlite.md" for c in result.candidates)


def test_find_candidates_empty_archive(tmp_path):
    """archive が空のワークスペースは candidates 0 件。"""
    root = tmp_path / "proj"
    (root / "docs" / "local").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='proj'\n")
    (root / "docs" / "local" / "plan_x.md").write_text("# [計画] x\n\n## 概要\n\nfoo\n", encoding="utf-8")
    cfg = load_config(explicit_roots=[str(root)], global_path=tmp_path / "no.yaml")
    result = find_candidates(cfg, use_embedding=False)
    assert result.candidates == []
