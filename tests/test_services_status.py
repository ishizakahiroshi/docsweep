"""services/status.py — update_status（H1 ラベル書き換え + postpone リセット）のテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep.config import load_config
from docsweep.services.status import StatusValidationError, update_status
from docsweep.state import get_postpone_count, increment_postpone


def _setup_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")
    return proj


def _cfg(tmp_path: Path):
    return load_config(
        explicit_roots=[str(tmp_path)],
        global_path=tmp_path / "no_global.yaml",
    )


def test_update_status_rewrites_h1_label(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("# [計画] テスト\n\n本文。\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    res = update_status(
        f, "in-progress",
        project_root=proj, config=cfg, file_type="plan",
    )
    assert res.new_label == "[実行中]"
    assert res.old_label == "[計画]"
    body = f.read_text(encoding="utf-8")
    assert body.startswith("# [実行中] テスト")
    assert "本文。" in body  # 本文は触らない


def test_update_status_resets_postpone_on_planned_to_inprogress(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("# [計画] テスト\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    # 先送り 3 回
    for _ in range(3):
        increment_postpone(proj, f, from_due=None, to_due="2026-07-01")
    assert get_postpone_count(proj, f) == 3
    res = update_status(
        f, "in-progress",
        project_root=proj, config=cfg, file_type="plan",
    )
    assert res.postpone_count_reset is True
    assert get_postpone_count(proj, f) == 0


def test_update_status_archive_triggered_for_done(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("# [様子見] テスト\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    res = update_status(
        f, "done",
        project_root=proj, config=cfg, file_type="plan",
    )
    assert res.archive_triggered is True
    # archive 自身は本サービスでは呼ばない（呼び出し側で archive_done を実行）


def test_update_status_archive_triggered_for_discarded(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("# [計画] テスト\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    res = update_status(
        f, "discarded",
        project_root=proj, config=cfg, file_type="plan",
    )
    assert res.archive_triggered is True


def test_update_status_rejects_active_on_plan(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("# [計画] テスト\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    with pytest.raises(StatusValidationError):
        update_status(
            f, "active",
            project_root=proj, config=cfg, file_type="plan",
        )


def test_update_status_rejects_planned_on_bugfix(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "bugfix_a.md"
    f.write_text("# [対応中] テスト\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    with pytest.raises(StatusValidationError):
        update_status(
            f, "planned",
            project_root=proj, config=cfg, file_type="bugfix",
        )


def test_update_status_allows_pending_on_bugfix(tmp_path: Path):
    """2026-06-23 改訂: bugfix にも [保留] を許可する。
    協議: docs/local/bugfix-return-target-design_review.html A1。
    """
    proj = _setup_project(tmp_path)
    f = proj / "bugfix_a.md"
    f.write_text("# [対応中] テスト\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    res = update_status(
        f, "pending",
        project_root=proj, config=cfg, file_type="bugfix",
    )
    assert res.new_state_key == "pending"
    assert "[保留]" in (proj / "bugfix_a.md").read_text(encoding="utf-8")


def test_update_status_pending_allows_pending_planned_discarded(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "pending_a.md"
    f.write_text("# [保留] テスト\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    # planned はOK
    update_status(f, "planned", project_root=proj, config=cfg, file_type="pending")
    # watching は pending には許可されていない
    with pytest.raises(StatusValidationError):
        update_status(f, "watching", project_root=proj, config=cfg, file_type="pending")


def test_update_status_rejects_unknown_state_key(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("# [計画] テスト\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    with pytest.raises(StatusValidationError):
        update_status(
            f, "nonexistent",
            project_root=proj, config=cfg, file_type="plan",
        )


def test_update_status_requires_h1(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "plan_a.md"
    f.write_text("本文だけで H1 がない\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    with pytest.raises(StatusValidationError):
        update_status(
            f, "in-progress",
            project_root=proj, config=cfg, file_type="plan",
        )
    # 元の内容が壊れていないこと
    assert f.read_text(encoding="utf-8") == "本文だけで H1 がない\n"


def test_update_status_loose_when_type_unknown(tmp_path: Path):
    proj = _setup_project(tmp_path)
    f = proj / "custom.md"
    f.write_text("# [計画] x\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    # type=None は緩く通る（ユーザー定義 type のエスケープハッチ）
    res = update_status(f, "in-progress", project_root=proj, config=cfg, file_type=None)
    assert res.new_label == "[実行中]"
