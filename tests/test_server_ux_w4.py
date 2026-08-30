"""UX W4（C4）で追加した HTTP API のテスト。

対象: P59 一括操作の 2 段階確認 / P21 snooze・pin / P20 board メトリクス。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from docsweep.config import load_config  # noqa: E402
from docsweep.server.app import create_app  # noqa: E402
from docsweep.state import get_view_state  # noqa: E402

TOKEN = "test-token-w4"


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _make(tmp_path: Path, *, n_plans: int = 3, threshold: int | None = None):
    root = tmp_path / "dev"
    proj = root / "proj"
    proj.mkdir(parents=True)
    conf = "" if threshold is None else f"bulk_confirm_threshold: {threshold}\n"
    (proj / ".docsweep.yaml").write_text(conf, encoding="utf-8")

    today = time.strftime("%Y-%m-%d")
    made = []
    for i in range(n_plans):
        made.append(
            _write(
                proj / f"plan_{i}.md",
                f"---\ndue: {today}\n---\n# [計画] p{i}\n\n## 概要\n\nP{i}\n",
            )
        )
    _write(proj / "plan_done.md", "# [完了] done\n\n## 概要\n\nd\n")

    cfg = load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")
    if threshold is not None:
        cfg.bulk_confirm_threshold = threshold
    app = create_app(cfg, token=TOKEN)
    return TestClient(app), root, proj, made


# ===== P59: 一括操作の 2 段階確認 ==============================================


def test_bulk_status_below_threshold_needs_no_confirmation(tmp_path: Path) -> None:
    c, _, proj, made = _make(tmp_path, n_plans=3, threshold=20)
    r = c.post(
        "/api/cards/bulk/status",
        data={
            "token": TOKEN,
            "paths": [p.as_posix() for p in made],
            "new_state": "in-progress",
        },
    )
    assert r.status_code == 200


def test_bulk_status_at_threshold_is_refused_without_the_phrase(tmp_path: Path) -> None:
    c, _, proj, made = _make(tmp_path, n_plans=3, threshold=3)
    r = c.post(
        "/api/cards/bulk/status",
        data={
            "token": TOKEN,
            "paths": [p.as_posix() for p in made],
            "new_state": "in-progress",
        },
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "confirm_required"
    assert body["phrase"] == "RELABEL"
    assert body["count"] == 3
    # 拒否されたときはファイルが書き換わっていない
    assert "[計画]" in made[0].read_text(encoding="utf-8")


def test_bulk_status_proceeds_with_the_phrase(tmp_path: Path) -> None:
    c, _, proj, made = _make(tmp_path, n_plans=3, threshold=3)
    r = c.post(
        "/api/cards/bulk/status",
        data={
            "token": TOKEN,
            "paths": [p.as_posix() for p in made],
            "new_state": "in-progress",
            "confirm": "RELABEL",
        },
    )
    assert r.status_code == 200
    assert "[実行中]" in made[0].read_text(encoding="utf-8")


def test_bulk_archive_is_gated_by_its_own_phrase(tmp_path: Path) -> None:
    c, _, proj, _ = _make(tmp_path, n_plans=1, threshold=1)
    done = (proj / "plan_done.md").as_posix()
    refused = c.post(
        "/api/cards/bulk/archive",
        data={"token": TOKEN, "paths": [done], "confirm": "RELABEL"},
    )
    assert refused.status_code == 409
    assert refused.json()["phrase"] == "ARCHIVE"
    assert (proj / "plan_done.md").is_file()

    ok = c.post(
        "/api/cards/bulk/archive",
        data={"token": TOKEN, "paths": [done], "confirm": "ARCHIVE"},
    )
    assert ok.status_code == 200


def test_bulk_archive_dry_run_never_asks_for_confirmation(tmp_path: Path) -> None:
    """下見は破壊しないので、確認で止めると使い勝手だけ落ちる。"""
    c, _, proj, _ = _make(tmp_path, n_plans=1, threshold=1)
    r = c.post(
        "/api/cards/bulk/archive",
        data={"token": TOKEN, "paths": [(proj / "plan_done.md").as_posix()], "dry_run": "true"},
    )
    assert r.status_code == 200
    assert (proj / "plan_done.md").is_file()


# ===== P21: snooze / pin ======================================================


def test_snooze_and_clear_round_trip(tmp_path: Path) -> None:
    c, root, proj, made = _make(tmp_path)
    target = made[0]
    r = c.post(
        "/api/cards/snooze",
        data={"token": TOKEN, "path": target.as_posix(), "until": "2099-12-31"},
    )
    assert r.status_code == 200
    assert r.json()["snoozed"] is True
    assert get_view_state(proj, target).snoozed_until == "2099-12-31"

    r2 = c.post("/api/cards/snooze/clear", data={"token": TOKEN, "path": target.as_posix()})
    assert r2.status_code == 200
    assert r2.json()["snoozed"] is False
    assert get_view_state(proj, target).snoozed_until is None


def test_snooze_defaults_to_today(tmp_path: Path) -> None:
    c, _, proj, made = _make(tmp_path)
    r = c.post("/api/cards/snooze", data={"token": TOKEN, "path": made[0].as_posix()})
    assert r.status_code == 200
    assert r.json()["snoozed_until"] == time.strftime("%Y-%m-%d")


def test_snooze_rejects_a_non_iso_date(tmp_path: Path) -> None:
    c, _, proj, made = _make(tmp_path)
    r = c.post(
        "/api/cards/snooze",
        data={"token": TOKEN, "path": made[0].as_posix(), "until": "明日"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_until"


def test_pin_round_trip(tmp_path: Path) -> None:
    c, _, proj, made = _make(tmp_path)
    r = c.post("/api/cards/pin", data={"token": TOKEN, "path": made[0].as_posix(), "pinned": "true"})
    assert r.status_code == 200
    assert r.json()["pinned"] is True
    assert get_view_state(proj, made[0]).pinned is True

    r2 = c.post(
        "/api/cards/pin", data={"token": TOKEN, "path": made[0].as_posix(), "pinned": "false"}
    )
    assert r2.json()["pinned"] is False


def test_snooze_and_pin_require_a_token(tmp_path: Path) -> None:
    c, _, proj, made = _make(tmp_path)
    for url, payload in (
        ("/api/cards/snooze", {"path": made[0].as_posix()}),
        ("/api/cards/snooze/clear", {"path": made[0].as_posix()}),
        ("/api/cards/pin", {"path": made[0].as_posix()}),
    ):
        assert c.post(url, data=payload).status_code == 403


def test_snooze_rejects_a_path_outside_the_roots(tmp_path: Path) -> None:
    c, _, _, _ = _make(tmp_path)
    outside = (tmp_path / "elsewhere" / "plan_x.md").as_posix()
    r = c.post("/api/cards/snooze", data={"token": TOKEN, "path": outside})
    assert r.status_code == 400


def test_read_only_mode_blocks_snooze_and_pin(tmp_path: Path) -> None:
    """serve --read-only（P58）が新しい書き込み API も止めること。"""
    root = tmp_path / "dev"
    proj = root / "proj"
    proj.mkdir(parents=True)
    _write(proj / "plan_a.md", "# [計画] a\n\n## 概要\n\nA\n")
    cfg = load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")
    app = create_app(cfg, token=TOKEN, read_only=True)
    c = TestClient(app)
    r = c.post(
        "/api/cards/pin",
        data={"token": TOKEN, "path": (proj / "plan_a.md").as_posix(), "pinned": "true"},
    )
    assert r.status_code == 403


# ===== P20: board メトリクス ==================================================


def test_board_exposes_metrics(tmp_path: Path) -> None:
    c, _, _, _ = _make(tmp_path)
    r = c.get("/board", params={"token": TOKEN})
    assert r.status_code == 200


def test_metrics_view_is_disabled_when_config_says_so(tmp_path: Path) -> None:
    from docsweep.server.routes.board import _metrics_view

    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no_global.yaml")
    cfg.metrics_enabled = False
    assert _metrics_view(cfg)["enabled"] is False
