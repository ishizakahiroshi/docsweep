import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from docsweep.config import load_config  # noqa: E402
from docsweep.server.app import create_app  # noqa: E402

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


def test_index_redirects_to_board(client):
    """plan_consolidate-to-board: / は看板へ 302 リダイレクト（旧 dashboard 廃止）。"""
    c, root, _ = client
    r = c.get(f"/?token={TOKEN}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/board")


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


def test_reveal_opens_folder(client, monkeypatch):
    c, root, _ = client
    calls = []
    monkeypatch.setattr("docsweep.server.app._reveal_in_file_manager", lambda p: calls.append(p))
    p = (root / "proj_a" / "plan_done.md").resolve().as_posix()
    r = c.post("/api/reveal", data={"token": TOKEN, "path": p})
    assert r.status_code == 200
    assert r.json()["revealed"].endswith("proj_a")   # 内包フォルダを返す
    assert calls and calls[0].name == "plan_done.md"  # 実起動はモック越しに 1 回


def test_reveal_rejects_path_outside_roots(client, monkeypatch):
    c, root, outside = client
    monkeypatch.setattr("docsweep.server.app._reveal_in_file_manager", lambda p: None)
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
    """manifest / 中央 guidance / グローバル config を tmp に隔離し、実 home を汚さない。"""
    monkeypatch.setattr("docsweep.inject.MANIFEST_PATH", tmp_path / "injected.json")
    monkeypatch.setattr("docsweep.inject.GUIDANCE_PATH", tmp_path / "guidance.md")
    monkeypatch.setattr("docsweep.inject.GLOBAL_CONFIG_PATH", tmp_path / "config.yaml")


def test_inject_project_preview_does_not_write(client, iso_inject):
    c, root, _ = client
    pdir = (root / "proj_a").resolve().as_posix()
    r = c.post("/api/inject", data={"token": TOKEN, "scope": "project", "project": pdir, "dry_run": "true"})
    assert r.status_code == 200
    pv = r.json()
    assert pv["scope"] == "project"
    assert "CLAUDE.md" in [b["file"] for b in pv["blocks"]]
    assert "docsweep:managed:start" in pv["blocks"][0]["text"]
    assert not (root / "proj_a" / "CLAUDE.md").exists()  # プレビューは書き込まない


def test_inject_project_applies(client, iso_inject):
    c, root, _ = client
    pdir = (root / "proj_a").resolve().as_posix()
    r = c.post("/api/inject", data={"token": TOKEN, "scope": "project", "project": pdir, "dry_run": "false"})
    assert r.status_code == 200
    text = (root / "proj_a" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "docsweep:managed:start" in text
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
    assert "@~/.docsweep/guidance.md" in pv["blocks"][0]["text"]
    assert "残作業" in pv["guidance"]


def test_eject_project_removes_block(client, iso_inject):
    c, root, _ = client
    pdir = (root / "proj_a").resolve().as_posix()
    c.post("/api/inject", data={"token": TOKEN, "scope": "project", "project": pdir, "dry_run": "false"})
    r = c.post("/api/eject", data={"token": TOKEN, "scope": "project", "project": pdir, "dry_run": "false"})
    assert r.status_code == 200
    text = (root / "proj_a" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "docsweep:managed" not in text


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
    assert (root / "proj_a" / ".docsweep.yaml").is_file()
    r = c.post("/api/eject", data={"token": TOKEN, "scope": "project", "project": pdir,
                                   "purge": "true", "dry_run": "false"})
    assert r.status_code == 200
    assert r.json()["purged_yaml"] is True
    assert not (root / "proj_a" / ".docsweep.yaml").exists()


# ---- Web UI i18n（plan_v0.2.0-english-support） ----

def test_board_lang_query_switches_to_english(client):
    """?lang=en で看板の UI 文言が英語になる。"""
    c, root, _ = client
    r = c.get("/board", params={"token": TOKEN, "lang": "en"})
    assert r.status_code == 200
    assert 'lang="en"' in r.text
    assert "Overdue" in r.text          # 列名
    assert "Rescan" in r.text           # トップバー
    assert "やり忘れ" not in r.text
    assert "再スキャン" not in r.text


def test_board_default_lang_is_japanese(client):
    """既定（config.lang=ja・cookie 無し）は従来どおり日本語。"""
    c, root, _ = client
    r = c.get("/board", params={"token": TOKEN})
    assert r.status_code == 200
    assert 'lang="ja"' in r.text
    assert "やり忘れ" in r.text


def test_board_lang_cookie_persists_english(client):
    """cookie docsweep_lang=en が ?lang= 無しでも効く（設定モーダルのトグル永続化）。"""
    c, root, _ = client
    c.cookies.set("docsweep_lang", "en")
    r = c.get("/board", params={"token": TOKEN})
    assert r.status_code == 200
    assert 'lang="en"' in r.text
    assert "Overdue" in r.text


def test_change_picker_labels_follow_lang(client):
    """ピッカーの状態ラベルが states の二言語辞書から lang 解決される。"""
    c, root, _ = client
    c.cookies.set("docsweep_lang", "en")
    r = c.get("/board/_partial/change_picker", params={"token": TOKEN})
    assert r.status_code == 200
    assert "[Planned]" in r.text
    assert "[計画]" not in r.text
    c.cookies.set("docsweep_lang", "ja")
    r = c.get("/board/_partial/change_picker", params={"token": TOKEN})
    assert "[計画]" in r.text


def test_settings_partial_has_lang_toggle(client):
    """設定モーダルに言語トグル（日本語 / English）が出る。"""
    c, root, _ = client
    r = c.get("/board/_partial/settings", params={"token": TOKEN})
    assert r.status_code == 200
    assert 'data-action="settings-set-lang"' in r.text
    assert "English" in r.text


def test_settings_partial_has_about_section(client):
    """設定モーダルに About & Licenses（自ライセンス + 同梱/CDN OSS 表記）が出る。"""
    c, root, _ = client
    r = c.get("/board/_partial/settings", params={"token": TOKEN})
    assert r.status_code == 200
    assert "htmx 1.9.12" in r.text
    assert "cytoscape.js 3.30.0" in r.text
    assert "Hiroshi Ishizaka" in r.text
    from docsweep import __version__
    assert f"v{__version__}" in r.text
    # 英語でも同じ節が出る
    c.cookies.set("docsweep_lang", "en")
    r = c.get("/board/_partial/settings", params={"token": TOKEN})
    assert "Included Open Source Software" in r.text


def test_subpages_render_in_english(client):
    """サブページ（brief / cross / resurrect）も cookie で英語表示になる。"""
    c, root, _ = client
    c.cookies.set("docsweep_lang", "en")
    r = c.get("/brief", params={"token": TOKEN})
    assert r.status_code == 200
    assert 'lang="en"' in r.text
    r = c.get("/cross", params={"token": TOKEN})
    assert r.status_code == 200
    assert 'lang="en"' in r.text
    r = c.get("/resurrect", params={"token": TOKEN})
    assert r.status_code == 200
    assert "Recompute" in r.text


# ---- スキャンルート管理 API（plan_web-roots-management） ----

@pytest.fixture
def iso_roots(tmp_path, monkeypatch):
    """config.yaml 書き込み先を tmp に隔離（実ユーザー設定を守る）。"""
    from docsweep.server import config_write as CW
    gpath = tmp_path / "docsweep-config.yaml"
    monkeypatch.setattr(CW, "GLOBAL_CONFIG_PATH", gpath)
    return gpath


def test_roots_add_reflects_and_persists(client, iso_roots, tmp_path):
    c, root, _ = client
    extra = tmp_path / "extra-project"
    (extra / "docs").mkdir(parents=True)
    r = c.post("/api/config/roots", data={"token": TOKEN, "op": "add", "path": str(extra)})
    assert r.status_code == 200
    body = r.json()
    assert body["persisted"] is True
    assert any(p.endswith("extra-project") for p in body["roots"])
    # 永続化: roots: ブロックが config.yaml に書かれる
    text = iso_roots.read_text(encoding="utf-8")
    assert "roots:" in text and "extra-project" in text
    # 設定モーダルにも出る
    r2 = c.get("/board/_partial/settings", params={"token": TOKEN})
    assert "extra-project" in r2.text


def test_roots_add_rejects_missing_dir(client, iso_roots, tmp_path):
    c, root, _ = client
    r = c.post("/api/config/roots", data={"token": TOKEN, "op": "add",
                                          "path": str(tmp_path / "no-such-dir")})
    assert r.status_code == 400


def test_roots_remove_and_last_root_guard(client, iso_roots, tmp_path):
    c, root, _ = client
    extra = tmp_path / "extra2"
    extra.mkdir()
    c.post("/api/config/roots", data={"token": TOKEN, "op": "add", "path": str(extra)})
    # 追加分を削除できる
    r = c.post("/api/config/roots", data={"token": TOKEN, "op": "remove", "path": str(extra)})
    assert r.status_code == 200
    # 最後の 1 個（元の root）は削除拒否
    r = c.post("/api/config/roots", data={"token": TOKEN, "op": "remove", "path": str(root)})
    assert r.status_code == 400


def test_update_global_roots_preserves_other_keys(tmp_path):
    """roots: だけ差し替え、他キーとコメントを温存する（surgical 置換）。"""
    from docsweep.server.config_write import update_global_roots
    gpath = tmp_path / "config.yaml"
    gpath.write_text(
        "# 手書きコメント\n"
        "lang: en\n"
        "roots:\n"
        "  - C:/old/root\n"
        "# due の説明コメント\n"
        "due:\n"
        "  postpone_warn_threshold: 2\n",
        encoding="utf-8",
    )
    update_global_roots([tmp_path / "new-root"], config_path=gpath)
    text = gpath.read_text(encoding="utf-8")
    assert "# 手書きコメント" in text
    assert "lang: en" in text
    assert "postpone_warn_threshold: 2" in text
    assert "new-root" in text
    assert "C:/old/root" not in text


def test_board_subtitle_has_no_kanban(client):
    """トップバーから「看板（カンバン）/ Kanban」表記を撤去した。"""
    c, root, _ = client
    for lang in ("ja", "en"):
        r = c.get("/board", params={"token": TOKEN, "lang": lang})
        assert r.status_code == 200
        assert "Kanban" not in r.text
        assert "カンバン" not in r.text
