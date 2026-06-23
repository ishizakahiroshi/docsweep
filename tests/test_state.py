"""state.json (postpone_count / due_history / label_history) のテスト。"""

from __future__ import annotations

from pathlib import Path

from docsweep.state import (
    FileState,
    StateDoc,
    get_postpone_count,
    increment_postpone,
    load,
    record_label_transition,
    save,
    should_reset_postpone,
    state_path,
)


def _setup_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")
    return proj


def test_load_returns_empty_when_missing(tmp_path: Path):
    proj = _setup_project(tmp_path)
    doc = load(proj)
    assert doc.files == {}
    assert doc.version == 1


def test_save_then_load_roundtrip(tmp_path: Path):
    proj = _setup_project(tmp_path)
    doc = StateDoc(files={"plan_a.md": FileState(postpone_count=3)})
    save(proj, doc)
    loaded = load(proj)
    assert loaded.get("plan_a.md").postpone_count == 3


def test_save_creates_docsweep_dir(tmp_path: Path):
    proj = _setup_project(tmp_path)
    save(proj, StateDoc())
    assert (proj / ".docsweep" / "state.json").is_file()


def test_load_corrupt_json_returns_empty(tmp_path: Path):
    proj = _setup_project(tmp_path)
    p = state_path(proj)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not valid json", encoding="utf-8")
    # 例外を上げず、空 doc が返る（MD は壊れない不変条件）
    doc = load(proj)
    assert doc.files == {}


def test_increment_postpone_counts_up(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("content", encoding="utf-8")
    count1 = increment_postpone(proj, f, from_due="2026-06-15", to_due="2026-06-22")
    count2 = increment_postpone(proj, f, from_due="2026-06-22", to_due="2026-06-29", reason="blocker")
    count3 = increment_postpone(proj, f, from_due="2026-06-29", to_due="2026-07-06")
    assert count1 == 1
    assert count2 == 2
    assert count3 == 3
    # due_history も 3 件記録されること
    doc = load(proj)
    assert len(doc.get("plan_a.md").due_history) == 3
    assert doc.get("plan_a.md").due_history[1]["reason"] == "blocker"


def test_get_postpone_count_returns_zero_when_unseen(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "new.md"
    f.write_text("c", encoding="utf-8")
    assert get_postpone_count(proj, f) == 0


def test_record_label_transition_resets_on_qualified_transition(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("c", encoding="utf-8")
    # 5 回先送り
    for _ in range(5):
        increment_postpone(proj, f, from_due=None, to_due="2026-06-29")
    assert get_postpone_count(proj, f) == 5
    # [計画] → [実行中] でリセット
    count = record_label_transition(
        proj, f, from_label="[計画]", to_label="[実行中]", reset_postpone=True,
    )
    assert count == 0
    assert get_postpone_count(proj, f) == 0
    # label_history に記録されること
    doc = load(proj)
    assert len(doc.get("plan_a.md").label_history) == 1


def test_record_label_transition_does_not_reset_for_done(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("c", encoding="utf-8")
    increment_postpone(proj, f, from_due=None, to_due="2026-06-29")
    # [様子見] → [完了] はリセット対象ではない
    record_label_transition(
        proj, f, from_label="[様子見]", to_label="[完了]", reset_postpone=False,
    )
    assert get_postpone_count(proj, f) == 1


def test_should_reset_postpone_planned_to_inprogress():
    assert should_reset_postpone(old_state_key="planned", new_state_key="in-progress")


def test_should_reset_postpone_inprogress_to_watching():
    assert should_reset_postpone(old_state_key="in-progress", new_state_key="watching")


def test_should_not_reset_for_obsolete_active_key():
    """2026-06-23 改修: active を in-progress に統合した結果、旧 active キーは
    _RESET_TRANSITIONS から消えた。エイリアス検出後は in-progress として扱われるため
    本テストは「廃止キーは直接マッチしない」確認に書き換え。"""
    assert not should_reset_postpone(old_state_key="active", new_state_key="watching")


def test_state_model_resolves_taiou_chu_alias_to_in_progress():
    """2026-06-23 改修: 旧 bugfix 専用ラベル ``[対応中]`` は ``in-progress`` の
    エイリアスとして検出される（既存 bugfix_*.md の書き換え不要・読み取り側互換）。"""
    from docsweep.states import StateModel

    sm = StateModel()
    st = sm.match("対応中")
    assert st is not None
    assert st.key == "in-progress"
    # 英語エイリアス "Active" も同様に in-progress に解決
    st_en = sm.match("Active")
    assert st_en is not None
    assert st_en.key == "in-progress"


def test_should_not_reset_for_done_or_discarded():
    # archive 行きへの遷移はリセット不要（state.json が archive 後に参照されないため）
    assert not should_reset_postpone(old_state_key="watching", new_state_key="done")
    assert not should_reset_postpone(old_state_key="planned", new_state_key="discarded")


def test_should_not_reset_when_state_unknown():
    assert not should_reset_postpone(old_state_key=None, new_state_key="in-progress")
    assert not should_reset_postpone(old_state_key="planned", new_state_key=None)


def test_state_path_in_project_root(tmp_path: Path):
    assert state_path(tmp_path) == tmp_path / ".docsweep" / "state.json"
