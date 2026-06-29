"""C5 (wings): linkcheck / auto-triage / graph の単体テスト。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from docsweep.auto_triage import apply_suggestions, suggest_transitions
from docsweep.config import load_config
from docsweep.graph import build_graph
from docsweep.linkcheck import (
    _extract_files_from_section,
    _extract_section,
    linkcheck,
)
from docsweep.models import Flag


# ===================================================================
# linkcheck の section / file 抽出
# ===================================================================


def test_extract_section_finds_target():
    text = """# [計画] X

## 概要
...

## 変更予定ファイル

- `docsweep/foo.py`
- `tests/test_foo.py`

## その他

other"""
    sec = _extract_section(text)
    assert sec is not None
    assert "foo.py" in sec
    assert "other" not in sec  # 次の ## までで止まる


def test_extract_section_returns_none_if_missing():
    text = "# [計画] X\n\n## 概要\nfoo\n"
    assert _extract_section(text) is None


def test_extract_files_picks_backticked():
    section = "## 変更予定ファイル\n- `docsweep/foo.py` — main\n- `tests/test_foo.py`\n"
    files = _extract_files_from_section(section)
    assert "docsweep/foo.py" in files
    assert "tests/test_foo.py" in files


def test_extract_files_dedupes():
    section = "- `foo.py`\n- `foo.py`\n"
    files = _extract_files_from_section(section)
    assert files == ["foo.py"]


# ===================================================================
# linkcheck end-to-end (ファイル fixture で)
# ===================================================================


@pytest.fixture
def linkcheck_workspace(tmp_path: Path):
    """plan が 1 つあり、変更予定ファイルが部分的に実在するワークスペース。"""
    root = tmp_path / "proj"
    (root / "docs" / "local").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='proj'\n")

    plan = root / "docs" / "local" / "plan_demo_2026-06-29.md"
    plan.write_text(
        """# [計画] demo

## 概要

x

## 変更予定ファイル

- `src/foo.py` — main
- `tests/test_foo.py`
- `docs/missing.md`
""",
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src" / "foo.py").write_text("# implemented\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_foo.py").write_text("# implemented\n")
    # docs/missing.md は意図的に作らない
    return root


def test_linkcheck_detects_implementation(linkcheck_workspace, tmp_path):
    cfg = load_config(explicit_roots=[str(linkcheck_workspace)], global_path=tmp_path / "no.yaml")
    results = linkcheck(cfg)
    assert len(results) == 1
    lc = results[0]
    assert lc.plan_name.startswith("plan_demo")
    files = {Path(f.path).name: f for f in lc.declared_files}
    assert files["foo.py"].exists is True
    assert files["test_foo.py"].exists is True
    assert files["missing.md"].exists is False


def test_linkcheck_progress_hint_partial(linkcheck_workspace, tmp_path):
    cfg = load_config(explicit_roots=[str(linkcheck_workspace)], global_path=tmp_path / "no.yaml")
    results = linkcheck(cfg)
    # 3 件中 2 件存在 + git log 無し → partial か not_started
    assert results[0].progress_hint in ("partial", "not_started", "implemented")


# ===================================================================
# auto-triage suggest (ruleset decider)
# ===================================================================


@pytest.fixture
def triage_workspace(tmp_path: Path):
    """様子見 + 古い計画 を含むワークスペース。"""
    root = tmp_path / "proj"
    (root / "docs" / "local").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='proj'\n")

    # 様子見 (age > 14 想定 → mtime 過去に)
    watch = root / "docs" / "local" / "plan_watch.md"
    watch.write_text("# [様子見] w\n\n## 概要\n\nx\n", encoding="utf-8")
    import os
    old = time.time() - 30 * 86400
    os.utime(watch, (old, old))

    # 普通の計画
    plan = root / "docs" / "local" / "plan_normal.md"
    plan.write_text("# [計画] n\n\n## 概要\n\ny\n", encoding="utf-8")

    return root


def test_suggest_transitions_promotes_old_watching(triage_workspace, tmp_path):
    cfg = load_config(explicit_roots=[str(triage_workspace)], global_path=tmp_path / "no.yaml")
    result = suggest_transitions(cfg)
    # 様子見 (age > 14) は promote 提案
    promotes = [s for s in result.suggestions if s.proposed_action == "promote"]
    assert any(s.path.endswith("plan_watch.md") for s in promotes)


def test_apply_suggestions_skip_action_recorded(triage_workspace, tmp_path):
    cfg = load_config(explicit_roots=[str(triage_workspace)], global_path=tmp_path / "no.yaml")
    # FileRecord.path は POSIX 形式（forward slash）。Windows 上でも揃える
    target_posix = (triage_workspace / "docs" / "local" / "plan_normal.md").resolve().as_posix()
    decisions = [{"path": target_posix, "action": "skip"}]
    r = apply_suggestions(cfg, decisions, dry_run=True)
    assert len(r.skipped) == 1
    assert r.applied == []
    assert r.failed == []


def test_apply_suggestions_unknown_path_fails(triage_workspace, tmp_path):
    cfg = load_config(explicit_roots=[str(triage_workspace)], global_path=tmp_path / "no.yaml")
    decisions = [
        {"path": "/nonexistent/plan_x.md", "action": "discard"},
    ]
    r = apply_suggestions(cfg, decisions, dry_run=True)
    assert len(r.failed) == 1


# ===================================================================
# graph
# ===================================================================


@pytest.fixture
def graph_workspace(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "docs" / "local").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='proj'\n")

    (root / "docs" / "local" / "plan_a.md").write_text(
        "---\nrelated:\n  - plan_b.md\n---\n# [計画] a\n\n## 概要\n\nx\n",
        encoding="utf-8",
    )
    (root / "docs" / "local" / "plan_b.md").write_text(
        "# [計画] b\n\n## 概要\n\ny\n",
        encoding="utf-8",
    )
    (root / "docs" / "local" / "plan_alone.md").write_text(
        "# [計画] alone\n\n## 概要\n\nz\n",
        encoding="utf-8",
    )
    return root


def test_build_graph_nodes_and_edges(graph_workspace, tmp_path):
    cfg = load_config(explicit_roots=[str(graph_workspace)], global_path=tmp_path / "no.yaml")
    g = build_graph(cfg)
    node_ids = {n.id for n in g.nodes}
    assert {"plan_a.md", "plan_b.md", "plan_alone.md"} <= node_ids
    # plan_a -> plan_b エッジが解決される
    resolved_edges = [e for e in g.edges if e.resolved]
    assert any(e.source == "plan_a.md" and e.target == "plan_b.md" for e in resolved_edges)


def test_build_graph_isolated_flag(graph_workspace, tmp_path):
    cfg = load_config(explicit_roots=[str(graph_workspace)], global_path=tmp_path / "no.yaml")
    g = build_graph(cfg)
    by_id = {n.id: n for n in g.nodes}
    assert by_id["plan_alone.md"].isolated is True
    assert by_id["plan_a.md"].isolated is False  # outgoing が 1
    assert by_id["plan_b.md"].isolated is False  # 被参照あり


def test_build_graph_to_dict_shape(graph_workspace, tmp_path):
    cfg = load_config(explicit_roots=[str(graph_workspace)], global_path=tmp_path / "no.yaml")
    d = build_graph(cfg).to_dict()
    assert "nodes" in d and "edges" in d
    assert all("id" in n and "label" in n for n in d["nodes"])
    assert all("source" in e and "target" in e for e in d["edges"])
