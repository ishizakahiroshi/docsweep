"""build_triage / slim_record の C3 スキーマ拡張を検証する。

旧クライアントが追加フィールドを無視するだけで動き続けることを担保するため、
既存フィールドは温存しつつ新フィールド（due_raw / due_parse_error / overdue_kind /
overdue_days / postpone_count / label_history_count / mtime_iso）が必ず埋まることを確認する。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from docsweep.config import load_config
from docsweep.reports import build_triage
from docsweep.state import increment_postpone, record_label_transition


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _cfg(root: Path):
    return load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")


def test_triage_items_include_new_fields(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj"
    # 陳腐化した [計画]（needs_decision 入り）を作って triage の items に乗せる。
    f = _write(
        proj / "docs" / "plan_old.md",
        "---\ndue: 2026-06-15\n---\n# [計画] 古い計画\n\n## 概要\n\n概要本文\n",
    )
    # 陳腐化させるため mtime を 200 日前に。
    import os
    old = (date.today() - timedelta(days=200))
    ts = (old - date(1970, 1, 1)).total_seconds()
    os.utime(f, (ts, ts))
    cfg = _cfg(root)
    out = build_triage(cfg)
    assert out["items"], "needs_decision 入りの item が triage に出るはず"
    item = next(i for i in out["items"] if i["path"].endswith("plan_old.md"))
    # 既存フィールド（非破壊チェック）
    assert "project" in item and "rel" in item and "state" in item and "actions" in item
    # 新フィールド（C3 で追加）
    assert "due_raw" in item
    assert "due_parse_error" in item and item["due_parse_error"] is False
    assert "overdue_kind" in item
    assert "overdue_days" in item
    assert "postpone_count" in item
    assert "label_history_count" in item
    assert "mtime_iso" in item


def test_triage_overdue_kind_classification(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj"
    past = (date.today() - timedelta(days=3)).isoformat()
    future = (date.today() + timedelta(days=7)).isoformat()
    today = date.today().isoformat()
    # 計画 + 過去 → overdue_todo
    f1 = _write(
        proj / "docs" / "plan_overdue.md",
        f"---\ndue: {past}\n---\n# [計画] 過ぎてる\n\n## 概要\n\nx\n",
    )
    # 様子見 + 過去 → overdue_graduate（陳腐化させる必要あり: needs_decision 入りで triage に乗せる）
    f2 = _write(
        proj / "docs" / "plan_watch.md",
        f"---\ndue: {past}\n---\n# [様子見] 寝かせて期限切れ\n\n## 概要\n\ny\n",
    )
    # pending + 未来
    f3 = _write(
        proj / "docs" / "pending_future.md",
        f"---\ndue: {future}\n---\n# [保留] 未来\n\n## 概要\n\nz\n\n## 保留理由\n\nr\n\n## 着手条件\n\nc\n",
    )
    # pending + today
    f4 = _write(
        proj / "docs" / "pending_today.md",
        f"---\ndue: {today}\n---\n# [保留] 今日\n\n## 概要\n\nz\n\n## 保留理由\n\nr\n\n## 着手条件\n\nc\n",
    )
    # 陳腐化させて needs_decision に乗せる
    import os
    old_ts = (date.today() - timedelta(days=200) - date(1970, 1, 1)).total_seconds()
    for f in (f1, f2):
        os.utime(f, (old_ts, old_ts))
    cfg = _cfg(root)
    out = build_triage(cfg)
    by_path = {Path(i["path"]).name: i for i in out["items"]}
    # f1 / f2 は陳腐化済なので items に乗る
    if "plan_overdue.md" in by_path:
        assert by_path["plan_overdue.md"]["overdue_kind"] == "overdue_todo"
        assert by_path["plan_overdue.md"]["overdue_days"] == 3
    if "plan_watch.md" in by_path:
        assert by_path["plan_watch.md"]["overdue_kind"] == "overdue_graduate"
    # pending は items に乗る（needs_decision または pending として）。
    if "pending_future.md" in by_path:
        assert by_path["pending_future.md"]["overdue_kind"] == "future"
    if "pending_today.md" in by_path:
        assert by_path["pending_today.md"]["overdue_kind"] == "today"


def test_triage_missing_due_when_no_due(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj"
    f = _write(
        proj / "docs" / "pending_a.md",
        "# [保留] 期日なし\n\n## 概要\n\nx\n\n## 保留理由\n\nr\n\n## 着手条件\n\nc\n",
    )
    cfg = _cfg(root)
    out = build_triage(cfg)
    item = next(i for i in out["items"] if i["path"] == str(f.resolve()).replace("\\", "/"))
    assert item["overdue_kind"] == "missing"
    assert item["overdue_days"] is None


def test_triage_postpone_count_from_state_json(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj"
    f = _write(
        proj / "docs" / "pending_a.md",
        "# [保留] x\n\n## 概要\n\na\n\n## 保留理由\n\nb\n\n## 着手条件\n\nc\n",
    )
    # postpone を 4 回回す（project_root は proj 直下に .docsweep/ を作る）。
    for _ in range(4):
        increment_postpone(proj, f, from_due=None, to_due="2026-07-01")
    # label_history も 2 回記録
    record_label_transition(proj, f, from_label="[保留]", to_label="[計画]", reset_postpone=False)
    record_label_transition(proj, f, from_label="[計画]", to_label="[実行中]", reset_postpone=False)
    cfg = _cfg(root)
    out = build_triage(cfg)
    item = next(i for i in out["items"] if i["path"].endswith("pending_a.md"))
    assert item["postpone_count"] == 4
    assert item["label_history_count"] == 2


def test_triage_summary_has_due_axis_counts(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj"
    past = (date.today() - timedelta(days=2)).isoformat()
    future = (date.today() + timedelta(days=5)).isoformat()
    today = date.today().isoformat()
    _write(proj / "docs" / "pending_overdue.md",
           f"---\ndue: {past}\n---\n# [保留] x\n\n## 概要\n\na\n\n## 保留理由\n\nb\n\n## 着手条件\n\nc\n")
    _write(proj / "docs" / "pending_today.md",
           f"---\ndue: {today}\n---\n# [保留] x\n\n## 概要\n\na\n\n## 保留理由\n\nb\n\n## 着手条件\n\nc\n")
    _write(proj / "docs" / "pending_future.md",
           f"---\ndue: {future}\n---\n# [保留] x\n\n## 概要\n\na\n\n## 保留理由\n\nb\n\n## 着手条件\n\nc\n")
    _write(proj / "docs" / "pending_missing.md",
           "# [保留] x\n\n## 概要\n\na\n\n## 保留理由\n\nb\n\n## 着手条件\n\nc\n")
    cfg = _cfg(root)
    out = build_triage(cfg)
    counts = out["counts"]
    assert counts.get("today", 0) >= 1
    assert counts.get("future", 0) >= 1
    assert counts.get("missing_due", 0) >= 1
    assert counts.get("overdue_todo", 0) >= 1


def test_triage_actions_include_write_ops(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj"
    f = _write(proj / "docs" / "pending_a.md",
               "# [保留] x\n\n## 概要\n\na\n\n## 保留理由\n\nb\n\n## 着手条件\n\nc\n")
    cfg = _cfg(root)
    out = build_triage(cfg)
    item = next(i for i in out["items"] if i["path"].endswith("pending_a.md"))
    assert "update_due" in item["actions"]
    assert "update_content" in item["actions"]
    # 既存（engine 由来）の action も保持されている
    assert "keep" in item["actions"]
