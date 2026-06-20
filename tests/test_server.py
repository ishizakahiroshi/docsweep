import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from docSweep.config import load_config  # noqa: E402
from docSweep.server.app import create_app  # noqa: E402

TOKEN = "test-token-123"


def _write(p: Path, text: str, age_days: int = 0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if age_days:
        import os
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
    return p


@pytest.fixture
def client(tmp_path: Path):
    root = tmp_path / "dev"
    _write(root / "proj_a" / "plan_done.md", "# [完了] 終わった\n\n## 概要\n\n片付いた。\n")
    _write(root / "proj_a" / "plan_watch.md", "# [様子見] 寝かせ\n\n## 概要\n\n再発確認中。\n")
    _write(root / "proj_b" / "plan_stale.md", "# [計画] 古い\n\n## 概要\n\n放置。\n", age_days=200)
    # スキャンルートの外（プレビュー禁止対象）
    outside = tmp_path / "secret.md"
    outside.write_text("# [完了] 外部秘密\n", encoding="utf-8")

    cfg = load_config(explicit_roots=[str(root)], global_path=tmp_path / "no_global.yaml")
    app = create_app(cfg, token=TOKEN)
    return TestClient(app), root, outside


def test_index_requires_token(client):
    c, root, _ = client
    assert c.get("/").status_code == 403
    assert c.get(f"/?token={TOKEN}").status_code == 200


def test_index_lists_states(client):
    c, root, _ = client
    html = c.get(f"/?token={TOKEN}&filter=all").text
    # 既定 index は要判断フィルタだが counts は全件。stale plan は要判断に出る。
    assert "plan_stale.md" in c.get(f"/list?token={TOKEN}&filter=all").text


def test_preview_renders_markdown(client):
    c, root, _ = client
    p = (root / "proj_a" / "plan_done.md").resolve().as_posix()
    r = c.get("/preview", params={"token": TOKEN, "path": p})
    assert r.status_code == 200
    assert "終わった" in r.text          # 本文がレンダリングされる
    assert 'class="md"' in r.text         # プレビュー（詳細モーダルの中身）である
    assert "エディタで開く" in r.text     # 開くボタンを備える


def test_preview_rejects_path_outside_roots(client):
    c, root, outside = client
    r = c.get("/preview", params={"token": TOKEN, "path": str(outside)})
    assert r.status_code == 403


def test_preview_rejects_non_md(client):
    c, root, _ = client
    r = c.get("/preview", params={"token": TOKEN, "path": str(root / "proj_a")})
    assert r.status_code == 403


def test_apply_promote_via_api(client):
    c, root, _ = client
    p = (root / "proj_a" / "plan_watch.md").resolve().as_posix()
    r = c.post("/api/apply", data={"token": TOKEN, "path": p, "action": "promote"})
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert (root / "proj_a" / "archive" / "plan_watch.md").exists()


def test_apply_disallowed_returns_400(client):
    c, root, _ = client
    p = (root / "proj_a" / "plan_done.md").resolve().as_posix()
    r = c.post("/api/apply", data={"token": TOKEN, "path": p, "action": "discard"})
    assert r.status_code == 400


def test_dashboard_shows_archivable_list(client):
    c, root, _ = client
    html = c.get(f"/?token={TOKEN}").text
    # 完了ファイルが archive 可能セクションに一覧表示される（一括実行前に中身を確認できる）。
    assert 'id="archivable"' in html
    assert "archive 可能" in html
    assert "plan_done.md" in html


def test_reveal_opens_folder(client, monkeypatch):
    c, root, _ = client
    calls = []
    monkeypatch.setattr("docSweep.server.app._reveal_in_file_manager", lambda p: calls.append(p))
    p = (root / "proj_a" / "plan_done.md").resolve().as_posix()
    r = c.post("/api/reveal", data={"token": TOKEN, "path": p})
    assert r.status_code == 200
    assert r.json()["revealed"].endswith("proj_a")   # 内包フォルダを返す
    assert calls and calls[0].name == "plan_done.md"  # 実起動はモック越しに 1 回


def test_reveal_rejects_path_outside_roots(client, monkeypatch):
    c, root, outside = client
    monkeypatch.setattr("docSweep.server.app._reveal_in_file_manager", lambda p: None)
    r = c.post("/api/reveal", data={"token": TOKEN, "path": str(outside)})
    assert r.status_code == 403


def test_sweep_skips_watching(client):
    c, root, _ = client
    r = c.post("/api/sweep", data={"token": TOKEN, "dry_run": "true"})
    assert r.status_code == 200
    srcs = {Path(m["src"]).name for m in r.json()}
    assert "plan_done.md" in srcs
    assert "plan_watch.md" not in srcs


# ---- inject / eject via Web UI ----

@pytest.fixture
def iso_inject(tmp_path, monkeypatch):
    """manifest / 中央 guidance を tmp に隔離し、実 home を汚さない。"""
    monkeypatch.setattr("docSweep.inject.MANIFEST_PATH", tmp_path / "injected.json")
    monkeypatch.setattr("docSweep.inject.GUIDANCE_PATH", tmp_path / "guidance.md")


def test_inject_project_preview_does_not_write(client, iso_inject):
    c, root, _ = client
    pdir = (root / "proj_a").resolve().as_posix()
    r = c.post("/api/inject", data={"token": TOKEN, "scope": "project", "project": pdir, "dry_run": "true"})
    assert r.status_code == 200
    pv = r.json()
    assert pv["scope"] == "project"
    assert "CLAUDE.md" in [b["file"] for b in pv["blocks"]]
    assert "docSweep:managed:start" in pv["blocks"][0]["text"]
    assert not (root / "proj_a" / "CLAUDE.md").exists()  # プレビューは書き込まない


def test_inject_project_applies(client, iso_inject):
    c, root, _ = client
    pdir = (root / "proj_a").resolve().as_posix()
    r = c.post("/api/inject", data={"token": TOKEN, "scope": "project", "project": pdir, "dry_run": "false"})
    assert r.status_code == 200
    text = (root / "proj_a" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "docSweep:managed:start" in text
    assert "| 内部状態 |" in text


def test_inject_rejects_project_outside_roots(client, iso_inject):
    c, root, _ = client
    r = c.post("/api/inject", data={"token": TOKEN, "scope": "project", "project": str(root.parent), "dry_run": "false"})
    assert r.status_code == 403


def test_inject_global_preview_uses_import(client, iso_inject):
    c, root, _ = client
    r = c.post("/api/inject", data={"token": TOKEN, "scope": "global", "agent": "claude", "dry_run": "true"})
    assert r.status_code == 200
    pv = r.json()
    assert pv["scope"] == "global"
    assert "@~/.docSweep/guidance.md" in pv["blocks"][0]["text"]
    assert "残作業" in pv["guidance"]


def test_eject_project_removes_block(client, iso_inject):
    c, root, _ = client
    pdir = (root / "proj_a").resolve().as_posix()
    c.post("/api/inject", data={"token": TOKEN, "scope": "project", "project": pdir, "dry_run": "false"})
    r = c.post("/api/eject", data={"token": TOKEN, "scope": "project", "project": pdir, "dry_run": "false"})
    assert r.status_code == 200
    text = (root / "proj_a" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "docSweep:managed" not in text


def test_inject_unknown_preset_returns_400(client, iso_inject):
    c, root, _ = client
    pdir = (root / "proj_a").resolve().as_posix()
    r = c.post("/api/inject", data={"token": TOKEN, "scope": "project", "project": pdir,
                                    "preset": "nope", "dry_run": "false"})
    assert r.status_code == 400


def test_eject_project_purge_removes_yaml(client, iso_inject):
    c, root, _ = client
    pdir = (root / "proj_a").resolve().as_posix()
    c.post("/api/inject", data={"token": TOKEN, "scope": "project", "project": pdir,
                                "preset": "frontmatter", "dry_run": "false"})
    assert (root / "proj_a" / ".docSweep.yaml").is_file()
    r = c.post("/api/eject", data={"token": TOKEN, "scope": "project", "project": pdir,
                                   "purge": "true", "dry_run": "false"})
    assert r.status_code == 200
    assert r.json()["purged_yaml"] is True
    assert not (root / "proj_a" / ".docSweep.yaml").exists()
