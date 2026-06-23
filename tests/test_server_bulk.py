"""/api/cards/bulk/* — 一括編集 API のテスト。

正常系・部分失敗・スコープ外混在・操作不適合（validation）・mtime conflict 部分発生を網羅。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from docsweep.config import load_config  # noqa: E402
from docsweep.server.app import create_app  # noqa: E402
from docsweep.state import get_postpone_count  # noqa: E402

TOKEN = "test-token-bulk"


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def client(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj"
    proj.mkdir(parents=True)
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")

    today = time.strftime("%Y-%m-%d")
    _write(proj / "plan_a.md", f"---\ndue: {today}\n---\n# [計画] a\n\n## 概要\n\nA\n")
    _write(proj / "plan_b.md", f"---\ndue: {today}\n---\n# [計画] b\n\n## 概要\n\nB\n")
    _write(proj / "plan_c.md", f"---\ndue: {today}\n---\n# [計画] c\n\n## 概要\n\nC\n")
    _write(proj / "plan_done.md", "# [完了] done\n\n## 概要\n\nd\n")
    _write(proj / "plan_discarded.md", "# [廃止] gone\n\n## 概要\n\ne\n")
    _write(proj / "bugfix_x_2026-01-01.md", "# [対応中] x\n\n## 症状\n\ns\n")

    cfg = load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")
    app = create_app(cfg, token=TOKEN)
    return TestClient(app), root, proj


# ===== bulk/due ===========================================================

def test_bulk_due_updates_all(client):
    c, _, proj = client
    paths = [(proj / n).as_posix() for n in ("plan_a.md", "plan_b.md", "plan_c.md")]
    r = c.post(
        "/api/cards/bulk/due",
        data={"token": TOKEN, "paths": paths, "new_due": "+1w"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["ok"]) == 3
    assert body["failed"] == []
    # 各ファイルに postpone_count=1 が記録される
    for n in ("plan_a.md", "plan_b.md", "plan_c.md"):
        assert get_postpone_count(proj, proj / n) == 1


def test_bulk_due_partial_failure_on_scope_violation(client, tmp_path):
    """スコープ外パスを混ぜても、有効な path は処理される。"""
    c, _, proj = client
    outside = tmp_path / "outside.md"
    outside.write_text("---\ndue: 2026-06-01\n---\n# [計画] x\n", encoding="utf-8")
    paths = [(proj / "plan_a.md").as_posix(), outside.as_posix()]
    r = c.post(
        "/api/cards/bulk/due",
        data={"token": TOKEN, "paths": paths, "new_due": "+1w"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["ok"]) == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["kind"] == "path_scope"


def test_bulk_due_invalid_new_due_returns_400(client):
    """new_due 自体が parse 不能なら全件失敗で 400。"""
    c, _, proj = client
    paths = [(proj / "plan_a.md").as_posix()]
    r = c.post(
        "/api/cards/bulk/due",
        data={"token": TOKEN, "paths": paths, "new_due": "tomorrow"},
    )
    assert r.status_code == 400


def test_bulk_due_requires_token(client):
    c, _, proj = client
    paths = [(proj / "plan_a.md").as_posix()]
    r = c.post("/api/cards/bulk/due", data={"paths": paths, "new_due": "+1w"})
    assert r.status_code == 403


# ===== bulk/status ========================================================

def test_bulk_status_relabels_all(client):
    c, _, proj = client
    paths = [(proj / n).as_posix() for n in ("plan_a.md", "plan_b.md", "plan_c.md")]
    r = c.post(
        "/api/cards/bulk/status",
        data={"token": TOKEN, "paths": paths, "new_state": "in-progress"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["ok"]) == 3
    assert body["failed"] == []
    assert body["archive"] is None  # in-progress は archive_triggered ではない
    # 各ファイルの H1 が書き換わっている
    for n in ("plan_a.md", "plan_b.md", "plan_c.md"):
        assert (proj / n).read_text(encoding="utf-8").splitlines()[3].startswith("# [実行中]")


def test_bulk_status_done_triggers_archive(client):
    """[完了] 一括指定で archive 移送が連動する。"""
    c, _, proj = client
    paths = [(proj / n).as_posix() for n in ("plan_a.md", "plan_b.md")]
    r = c.post(
        "/api/cards/bulk/status",
        data={"token": TOKEN, "paths": paths, "new_state": "done"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["ok"]) == 2
    assert body["archive"] is not None
    assert len(body["archive"]["moved"]) == 2
    # 元ファイルは消えて archive 配下へ
    assert not (proj / "plan_a.md").exists()
    assert (proj / "archive" / "plan_a.md").exists()


def test_bulk_status_partial_validation_failure(client):
    """bugfix に [計画] は不可。plan と混ぜると plan だけ通る（種別違反の振り分け確認）。

    2026-06-23 改修: 旧 [対応中] は [実行中] に統合されたため、plan 混在テストは
    「bugfix に [計画] 不可」というパターンに変更。
    """
    c, _, proj = client
    paths = [
        (proj / "plan_a.md").as_posix(),
        (proj / "bugfix_x_2026-01-01.md").as_posix(),
    ]
    r = c.post(
        "/api/cards/bulk/status",
        data={"token": TOKEN, "paths": paths, "new_state": "planned"},
    )
    assert r.status_code == 200
    body = r.json()
    # bugfix は planned が validation 違反、plan は planned で通る
    assert len(body["failed"]) == 1
    assert body["failed"][0]["kind"] == "validation"
    assert "bugfix_x" in body["failed"][0]["path"]
    assert len(body["ok"]) == 1


# ===== bulk/archive ========================================================

def test_bulk_archive_moves_done_and_discarded(client):
    c, _, proj = client
    paths = [(proj / n).as_posix() for n in ("plan_done.md", "plan_discarded.md")]
    r = c.post(
        "/api/cards/bulk/archive",
        data={"token": TOKEN, "paths": paths},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["moved"]) == 2
    assert body["failed_validation"] == []


def test_bulk_archive_skips_non_archivable(client):
    """[計画] のファイルを指定しても archive されない（services 層が skipped[] に振る）。"""
    c, _, proj = client
    paths = [(proj / n).as_posix() for n in ("plan_done.md", "plan_a.md")]
    r = c.post(
        "/api/cards/bulk/archive",
        data={"token": TOKEN, "paths": paths},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["moved"]) == 1  # plan_done.md のみ
    assert len(body["skipped"]) == 1
    assert "not archivable" in body["skipped"][0]["reason"]
    # 元ファイル plan_a.md は残る
    assert (proj / "plan_a.md").exists()


def test_bulk_archive_scope_violation_in_failed_validation(client, tmp_path):
    c, _, proj = client
    outside = tmp_path / "outside.md"
    outside.write_text("# [完了] x\n", encoding="utf-8")
    paths = [(proj / "plan_done.md").as_posix(), outside.as_posix()]
    r = c.post(
        "/api/cards/bulk/archive",
        data={"token": TOKEN, "paths": paths},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["moved"]) == 1
    assert len(body["failed_validation"]) == 1
    assert body["failed_validation"][0]["kind"] == "path_scope"


def test_bulk_archive_dry_run_does_not_move(client):
    c, _, proj = client
    paths = [(proj / "plan_done.md").as_posix()]
    r = c.post(
        "/api/cards/bulk/archive",
        data={"token": TOKEN, "paths": paths, "dry_run": "true"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["moved"]) == 1
    # dry_run なので実ファイルは移動していない
    assert (proj / "plan_done.md").exists()
