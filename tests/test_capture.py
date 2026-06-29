"""C2 (wings): capture — heuristics / Mock LLM / save flow の単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep.capture import extract_drafts, save_drafts
from docsweep.capture.heuristics import extract_drafts_heuristic
from docsweep.capture.llm import MockLLM, LLMRequest, get_llm
from docsweep.capture.models import Draft, DraftKind
from docsweep.config import load_config


# ===================================================================
# heuristics
# ===================================================================


def test_heuristic_detects_plan_marker():
    text = "明日決定された方針として、SQLite を採用してインデックスを作る。\n\n別の話題。"
    drafts = extract_drafts_heuristic(text)
    assert len(drafts) >= 1
    assert drafts[0].kind == DraftKind.PLAN.value


def test_heuristic_detects_bugfix_marker():
    text = "ログイン画面でバグが出ている。エラーは X 行目"
    drafts = extract_drafts_heuristic(text)
    assert drafts[0].kind == DraftKind.BUGFIX.value


def test_heuristic_detects_pending_marker():
    text = "これは保留しておく。あとで検討する。"
    drafts = extract_drafts_heuristic(text)
    assert drafts[0].kind == DraftKind.PENDING.value


def test_heuristic_skips_irrelevant():
    text = "今日の天気は晴れ。\n\nコーヒーが美味しい。"
    drafts = extract_drafts_heuristic(text)
    assert drafts == []


def test_heuristic_respects_max_drafts():
    paras = "\n\n".join(f"これを決定する: 案 {i}" for i in range(20))
    drafts = extract_drafts_heuristic(paras, max_drafts=3)
    assert len(drafts) == 3


def test_heuristic_draft_has_required_sections():
    text = "TODO: API キャッシュを実装する"
    drafts = extract_drafts_heuristic(text)
    body = drafts[0].body
    assert "# [計画]" in body
    assert "## context配分" in body
    assert "## 概要" in body


def test_heuristic_bugfix_has_full_sections():
    text = "ログイン画面でバグが出ている"
    drafts = extract_drafts_heuristic(text)
    body = drafts[0].body
    assert "# [対応中]" in body
    for sec in ("## 症状", "## 根本原因", "## 修正内容", "## 変更ファイル", "## 検証", "## 備忘"):
        assert sec in body


def test_heuristic_bugfix_filename_has_date():
    drafts = extract_drafts_heuristic("ログイン画面でバグが出ている")
    fname = drafts[0].suggested_filename
    assert fname.startswith("bugfix_")
    assert fname.endswith(".md")
    # YYYY-MM-DD 形式が含まれる
    import re
    assert re.search(r"\d{4}-\d{2}-\d{2}", fname)


# ===================================================================
# LLM provider 抽象
# ===================================================================


def test_mock_llm_returns_drafts():
    client = MockLLM()
    drafts = client.extract(LLMRequest(conversation="決定された: 機能 X を実装する"))
    assert len(drafts) >= 1
    assert drafts[0].source_hint == "llm:mock"


def test_get_llm_default_is_mock():
    client = get_llm(None)
    assert isinstance(client, MockLLM)


def test_get_llm_real_providers_not_implemented():
    with pytest.raises(NotImplementedError):
        get_llm("openai")
    with pytest.raises(NotImplementedError):
        get_llm("anthropic")


def test_get_llm_unknown_provider_raises():
    with pytest.raises(ValueError):
        get_llm("totally-unknown-provider")


# ===================================================================
# extract_drafts (service)
# ===================================================================


def test_extract_drafts_default_heuristic(tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    drafts = extract_drafts("決定: 索引を再構築する", config=cfg)
    assert all(d.source_hint == "heuristic" for d in drafts)


def test_extract_drafts_with_llm(tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    drafts = extract_drafts("決定: 索引を再構築する", config=cfg, use_llm=True)
    assert all(d.source_hint == "llm:mock" for d in drafts)


# ===================================================================
# save_drafts
# ===================================================================


def test_save_drafts_writes_files(tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    drafts = [
        Draft(id="draft-001", kind="plan", title="t1", body="# [計画] t1\n",
              suggested_filename="plan_t1.md"),
        Draft(id="draft-002", kind="bugfix", title="t2", body="# [対応中] t2\n",
              suggested_filename="bugfix_t2_2026-06-29.md"),
    ]
    saved = save_drafts(drafts, config=cfg, target_dir=tmp_path / "out")
    assert len(saved) == 2
    assert (tmp_path / "out" / "plan_t1.md").is_file()
    assert (tmp_path / "out" / "bugfix_t2_2026-06-29.md").is_file()


def test_save_drafts_no_overwrite_appends_suffix(tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    target = tmp_path / "out"
    target.mkdir()
    (target / "plan_x.md").write_text("# existing", encoding="utf-8")

    drafts = [Draft(id="d1", kind="plan", title="x", body="new",
                    suggested_filename="plan_x.md")]
    saved = save_drafts(drafts, config=cfg, target_dir=target)
    assert len(saved) == 1
    # 既存は温存され、_2 等で重複回避
    assert (target / "plan_x.md").read_text(encoding="utf-8") == "# existing"
    assert saved[0].name != "plan_x.md"
    assert saved[0].read_text(encoding="utf-8") == "new"


def test_save_drafts_overwrite_replaces(tmp_path):
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    target = tmp_path / "out"
    target.mkdir()
    (target / "plan_x.md").write_text("old", encoding="utf-8")

    drafts = [Draft(id="d1", kind="plan", title="x", body="new",
                    suggested_filename="plan_x.md")]
    save_drafts(drafts, config=cfg, target_dir=target, overwrite=True)
    assert (target / "plan_x.md").read_text(encoding="utf-8") == "new"


# ===================================================================
# config 連携
# ===================================================================


def test_config_reads_llm_provider(tmp_path):
    g = tmp_path / "global.yaml"
    g.write_text("llm:\n  provider: mock\n  model: gpt-4\n", encoding="utf-8")
    cfg = load_config(global_path=g)
    assert cfg.capture_llm_provider == "mock"
    assert cfg.capture_llm_model == "gpt-4"


def test_config_llm_defaults_to_mock(tmp_path):
    cfg = load_config(global_path=tmp_path / "absent.yaml")
    assert cfg.capture_llm_provider == "mock"
    assert cfg.capture_llm_model is None
