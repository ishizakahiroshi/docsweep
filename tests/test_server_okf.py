"""server/routes — C4 で追加した OKF frontmatter API の TestClient テスト。

- /api/cards/frontmatter で tags/owner/related/review_status を書き換えられる
- /api/cards/detail で OKF 値 + 逆参照が返る
- /api/cards/claim で owner が自動セット / unclaim で空になる
- /api/user/current が空でない文字列を返す
- カード HTML に tags/owner/related バッジが出る（後方互換: frontmatter 無しは出ない）
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from docsweep.config import load_config  # noqa: E402
from docsweep.server.app import create_app  # noqa: E402

TOKEN = "test-token-okf"


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def client(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj_o"
    proj.mkdir(parents=True)
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")

    # OKF frontmatter 付き
    _write(
        proj / "plan_okf.md",
        "---\ntype: plan\nstatus: planned\ntags: [ui, backend]\nowner: alice\n"
        "review_status: review\nrelated: [plan_other.md]\nlast_reviewed: 2026-06-29\n"
        "---\n# [計画] OKF 入り\n\n## 概要\n\nbody.\n",
    )
    # related の指し先
    _write(proj / "plan_other.md", "# [計画] 他のプラン\n\n## 概要\n\n参照される側。\n")
    # frontmatter 無し（後方互換確認用）
    _write(proj / "plan_legacy.md", "# [計画] レガシー\n\n## 概要\n\n旧フォーマット。\n")

    cfg = load_config(
        explicit_roots=[str(root)],
        global_path=tmp_path / "no_global.yaml",
    )
    app = create_app(cfg, token=TOKEN)
    return TestClient(app), root, proj


def test_card_html_shows_okf_badges(client):
    c, _, _ = client
    html = c.get(f"/board?token={TOKEN}").text
    # tags / owner / related バッジが出る
    assert "okf-tag" in html
    assert "#ui" in html
    assert "👤 alice" in html
    assert "okf-related" in html
    # legacy（frontmatter 無し）は okf 行が描画されない（後方互換）
    assert "[計画] レガシー" in html or "plan_legacy.md" in html


def test_detail_returns_okf_and_backrefs(client):
    c, _, proj = client
    target = (proj / "plan_other.md").resolve().as_posix()
    res = c.get(f"/api/cards/detail?token={TOKEN}&path={target}")
    assert res.status_code == 200
    body = res.json()
    assert body["found"] is True
    # plan_okf.md が related: [plan_other.md] と書いているので 1 件の逆参照が来る
    names = [b["name"] for b in body["backrefs"]]
    assert "plan_okf.md" in names


def test_detail_for_okf_target(client):
    c, _, proj = client
    target = (proj / "plan_okf.md").resolve().as_posix()
    body = c.get(f"/api/cards/detail?token={TOKEN}&path={target}").json()
    assert body["owner"] == "alice"
    assert body["tags"] == ["ui", "backend"]
    assert body["review_status"] == "review"
    assert body["related"] == ["plan_other.md"]


def test_post_frontmatter_updates_tags(client):
    c, _, proj = client
    target = (proj / "plan_okf.md").resolve().as_posix()
    res = c.post(
        "/api/cards/frontmatter",
        data={"token": TOKEN, "path": target, "field": "tags", "value": "ui, docs"},
    )
    assert res.status_code == 200, res.text
    text = Path(target).read_text(encoding="utf-8")
    assert "tags: [ui, docs]" in text
    assert "# [計画] OKF 入り" in text  # H1 温存


def test_post_frontmatter_rejects_unknown_field(client):
    c, _, proj = client
    target = (proj / "plan_okf.md").resolve().as_posix()
    res = c.post(
        "/api/cards/frontmatter",
        data={"token": TOKEN, "path": target, "field": "secret", "value": "x"},
    )
    assert res.status_code == 400


def test_claim_sets_owner(client):
    c, _, proj = client
    target = (proj / "plan_legacy.md").resolve().as_posix()
    res = c.post(
        "/api/cards/claim",
        data={"token": TOKEN, "path": target},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["owner"]
    text = Path(target).read_text(encoding="utf-8")
    assert "owner:" in text
    assert "# [計画] レガシー" in text  # H1 温存
    # frontmatter 無しから新設されたので先頭に --- が来ている
    assert text.startswith("---\n")


def test_unclaim_clears_owner(client):
    c, _, proj = client
    target = (proj / "plan_okf.md").resolve().as_posix()
    res = c.post(
        "/api/cards/claim",
        data={"token": TOKEN, "path": target, "unclaim": "true"},
    )
    assert res.status_code == 200
    text = Path(target).read_text(encoding="utf-8")
    assert "owner: alice" not in text
    assert "owner: " in text  # 行は残し値だけ空


def test_current_user_endpoint(client):
    c, _, _ = client
    res = c.get(f"/api/user/current?token={TOKEN}")
    assert res.status_code == 200
    name = res.json()["name"]
    assert isinstance(name, str) and name


def test_token_required_for_new_endpoints(client):
    c, _, proj = client
    target = (proj / "plan_okf.md").resolve().as_posix()
    assert c.get(f"/api/cards/detail?path={target}").status_code == 403
    assert c.post("/api/cards/frontmatter", data={"path": target, "field": "tags", "value": ""}).status_code == 403
    assert c.post("/api/cards/claim", data={"path": target}).status_code == 403
    assert c.get("/api/user/current").status_code == 403
