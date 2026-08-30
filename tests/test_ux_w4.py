"""UX W4（C4）で追加した機能の回帰テスト。

対象: P70 demo / P71 doc_links / P59 bulk_confirm / P21 snooze・pin / P20 streak。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from docsweep import state, streak
from docsweep.bulk_confirm import BulkConfirmRequired, evaluate, phrase_for, require
from docsweep.demo import build_demo
from docsweep.doc_links import LINKS, doc_hint, known_ids


# ===== P70: demo =============================================================


def test_demo_creates_a_self_contained_project(tmp_path: Path) -> None:
    result = build_demo(tmp_path / "demo", today=date(2026, 8, 30))
    assert (result.root / ".docsweep.yaml").is_file()
    assert len(result.files) == 8
    for f in result.files:
        assert f.is_file()
        assert f.parent == result.root / "docs" / "local"


def test_demo_frontmatter_type_matches_the_filename_prefix(tmp_path: Path) -> None:
    """type が filename 由来と食い違うと scan が warning を出す（実際に出していた）。"""
    result = build_demo(tmp_path / "demo", today=date(2026, 8, 30))
    for f in result.files:
        prefix = f.name.split("_", 1)[0]
        assert "type: " + prefix + "\n" in f.read_text(encoding="utf-8")


def test_demo_spreads_documents_across_due_buckets(tmp_path: Path) -> None:
    """overdue / today / future / 期日なし が全部そろっていないとデモにならない。"""
    today = date(2026, 8, 30)
    result = build_demo(tmp_path / "demo", today=today)
    kinds = {"overdue": 0, "today": 0, "future": 0, "none": 0}
    for f in result.files:
        text = f.read_text(encoding="utf-8")
        due_lines = [ln for ln in text.splitlines() if ln.startswith("due: ")]
        if not due_lines:
            kinds["none"] += 1
            continue
        d = date.fromisoformat(due_lines[0][len("due: "):].strip())
        if d < today:
            kinds["overdue"] += 1
        elif d == today:
            kinds["today"] += 1
        else:
            kinds["future"] += 1
    for key, n in kinds.items():
        assert n > 0, key + " のカードが 0 件"


def test_demo_only_writes_inside_the_generated_project(tmp_path: Path) -> None:
    """デモ生成が生成先の外（グローバル manifest 等）へ書かないこと。"""
    result = build_demo(tmp_path / "demo", today=date(2026, 8, 30))
    produced = {
        p.relative_to(result.root).as_posix()
        for p in result.root.rglob("*")
        if p.is_file()
    }
    expected = {".docsweep.yaml"} | {"docs/local/" + f.name for f in result.files}
    assert produced == expected


# ===== P71: doc_links ========================================================


def test_every_doc_link_points_at_a_file_that_exists() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for help_id in known_ids():
        doc = LINKS[help_id].doc.partition("#")[0]
        assert (repo_root / doc).is_file(), help_id + " -> " + doc + " が無い"


def test_every_doc_link_anchor_exists_in_the_target() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for help_id in known_ids():
        doc, _, anchor = LINKS[help_id].doc.partition("#")
        if not anchor:
            continue
        text = (repo_root / doc).read_text(encoding="utf-8")
        anchors = set()
        for line in text.splitlines():
            if line.startswith("#"):
                slug = line.lstrip("#").strip().lower().replace(" ", "-")
                anchors.add("".join(c for c in slug if c.isalnum() or c in "-_"))
        assert anchor.lower() in anchors, help_id + " -> #" + anchor + " が " + doc + " に無い"


def test_doc_hint_can_be_disabled() -> None:
    assert doc_hint("cli.unknown_command") is not None
    assert doc_hint("cli.unknown_command", enabled=False) is None
    assert doc_hint("no.such.id") is None


def test_doc_hint_carries_the_help_id_for_support() -> None:
    line = doc_hint("config.yaml_parse")
    assert line is not None
    assert "help id: config.yaml_parse" in line
    assert line.startswith("hint: ")


# ===== P59: bulk_confirm =====================================================


@pytest.mark.parametrize(
    ("count", "threshold", "expected"),
    [
        (0, 20, False),
        (1, 20, False),
        (19, 20, False),
        (20, 20, True),
        (50, 20, True),
        (1, 0, True),
    ],
)
def test_confirm_is_required_only_at_or_above_the_threshold(
    count: int, threshold: int, expected: bool
) -> None:
    assert evaluate("promote", count, threshold).required is expected


def test_zero_targets_never_require_confirmation() -> None:
    """0 件で確認を求めると、何も起きない操作で打ち込みを強いることになる。"""
    assert evaluate("promote", 0, 0).required is False


def test_require_accepts_only_the_exact_phrase() -> None:
    require("promote", 25, 20, "PROMOTE")
    require("promote", 25, 20, " PROMOTE ")  # 前後の空白は許す
    for wrong in (None, "", "promote", "ARCHIVE", "yes"):
        with pytest.raises(BulkConfirmRequired):
            require("promote", 25, 20, wrong)


def test_each_operation_has_its_own_phrase() -> None:
    """惰性で同じ語を打ち込めないよう、操作ごとにフレーズを変える。"""
    phrases = {phrase_for(op) for op in ("promote", "bulk_archive", "bulk_status")}
    assert len(phrases) == 3


def test_confirm_error_payload_tells_the_client_what_to_type() -> None:
    with pytest.raises(BulkConfirmRequired) as e:
        require("bulk_archive", 30, 20, None)
    payload = e.value.to_dict()
    assert payload["error"] == "confirm_required"
    assert payload["phrase"] == "ARCHIVE"
    assert payload["count"] == 30
    assert payload["threshold"] == 20


# ===== P21: snooze / pin =====================================================


def _work_file(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "proj"
    f = root / "docs" / "local" / "plan_x.md"
    f.parent.mkdir(parents=True)
    f.write_text("# [計画] x\n", encoding="utf-8")
    return root, f


def test_snooze_expires_on_its_own(tmp_path: Path) -> None:
    root, f = _work_file(tmp_path)
    state.set_snooze(root, f, "2026-08-30")
    assert state.snoozed_paths(root, "2026-08-30") == {"docs/local/plan_x.md"}
    assert state.snoozed_paths(root, "2026-08-31") == set()


def test_snooze_can_be_cleared(tmp_path: Path) -> None:
    root, f = _work_file(tmp_path)
    state.set_snooze(root, f, "2026-12-31")
    state.set_snooze(root, f, None)
    assert state.get_view_state(root, f).snoozed_until is None


def test_pin_round_trips(tmp_path: Path) -> None:
    root, f = _work_file(tmp_path)
    state.set_pinned(root, f, True)
    assert state.pinned_paths(root) == {"docs/local/plan_x.md"}
    state.set_pinned(root, f, False)
    assert state.pinned_paths(root) == set()


def test_snooze_and_pin_do_not_touch_the_markdown(tmp_path: Path) -> None:
    """正本は MD。見え方の情報を本文へ書き戻さない。"""
    root, f = _work_file(tmp_path)
    before = f.read_text(encoding="utf-8")
    state.set_snooze(root, f, "2026-12-31")
    state.set_pinned(root, f, True)
    assert f.read_text(encoding="utf-8") == before


def test_snooze_and_pin_preserve_existing_state_fields(tmp_path: Path) -> None:
    root, f = _work_file(tmp_path)
    state.increment_postpone(root, f, from_due=None, to_due="2026-09-01")
    state.set_pinned(root, f, True)
    state.set_snooze(root, f, "2026-12-31")
    view = state.get_view_state(root, f)
    assert view.postpone_count == 1
    assert len(view.due_history) == 1
    assert view.pinned is True
    assert view.snoozed_until == "2026-12-31"


def test_state_json_without_the_new_keys_still_loads(tmp_path: Path) -> None:
    """v0.4.0 以前の state.json（snooze/pin が無い）を読んでも落ちない。"""
    root, f = _work_file(tmp_path)
    p = state.state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"version": 1, "files": {"docs/local/plan_x.md": {"postpone_count": 2}}}),
        encoding="utf-8",
    )
    view = state.get_view_state(root, f)
    assert view.postpone_count == 2
    assert view.pinned is False
    assert view.snoozed_until is None


# ===== P20: streak ===========================================================


def test_streak_counts_consecutive_days(tmp_path: Path) -> None:
    p = tmp_path / "streak.json"
    today = date(2026, 8, 30)
    for off in (0, 1, 2):
        streak.record_open(today=today - timedelta(days=off), path=p)
    assert streak.current_streak(today=today, path=p) == 3


def test_streak_breaks_on_a_gap(tmp_path: Path) -> None:
    p = tmp_path / "streak.json"
    today = date(2026, 8, 30)
    for off in (0, 1, 3, 4):
        streak.record_open(today=today - timedelta(days=off), path=p)
    assert streak.current_streak(today=today, path=p) == 2


def test_streak_survives_not_having_opened_today_yet(tmp_path: Path) -> None:
    """朝まだ開いていないだけで昨日までの連続を 0 にしない。"""
    p = tmp_path / "streak.json"
    today = date(2026, 8, 30)
    for off in (1, 2):
        streak.record_open(today=today - timedelta(days=off), path=p)
    assert streak.current_streak(today=today, path=p) == 2


def test_streak_is_zero_without_any_record(tmp_path: Path) -> None:
    assert streak.current_streak(today=date(2026, 8, 30), path=tmp_path / "none.json") == 0


def test_recording_twice_in_a_day_does_not_inflate_the_streak(tmp_path: Path) -> None:
    p = tmp_path / "streak.json"
    today = date(2026, 8, 30)
    for _ in range(5):
        streak.record_open(today=today, path=p)
    assert streak.current_streak(today=today, path=p) == 1


def test_streak_file_records_only_dates(tmp_path: Path) -> None:
    """何を見たかは残さない（日付だけ）という設計を固定する。"""
    p = tmp_path / "streak.json"
    streak.record_open(today=date(2026, 8, 30), path=p)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert set(data) == {"version", "days"}
    assert data["days"] == ["2026-08-30"]


def test_corrupt_streak_file_falls_back_to_empty(tmp_path: Path) -> None:
    p = tmp_path / "streak.json"
    p.write_text("{{{ not json", encoding="utf-8")
    assert streak.current_streak(today=date(2026, 8, 30), path=p) == 0


def test_metrics_can_be_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCSWEEP_METRICS", "0")
    assert streak.metrics_enabled() is False
    monkeypatch.setenv("DOCSWEEP_METRICS", "1")
    assert streak.metrics_enabled() is True
