"""T-05 (plan_audit-followup_2026-07-16): FastAPI ルータ拡張の smoke テスト。

`/api/graph` / `/api/resurrect` / `/api/brief` / `/api/cross` / `/api/capture/extract`
の 5 エンドポイントに対し 200 / 401 / エラー（400 or 4xx/5xx）の 3 経路を通す。
既存 test_server.py の board 中心テストで薄かった各 route の HTTP 経路を補強し、
`_check_token` の 401 分岐を各 route ファイルで踏むためのカバレッジ。

（既存 test_server*.py には手を入れず、本ファイルだけを追加する。）
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from docsweep.config import load_config  # noqa: E402
from docsweep.server.app import create_app  # noqa: E402

TOKEN = "test-token-routes-extras"


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _make_client(
    tmp_path: Path,
    *,
    raise_server_exceptions: bool = True,
    allow_root_mutation: bool = False,
) -> TestClient:
    """テスト用の TestClient を組み立てる。

    500 を assert したいテストでは raise_server_exceptions=False にして
    サーバ例外を httpx 側でレスポンス化させる。
    """
    root = tmp_path / "dev"
    _write(root / "proj_x" / "plan_planned.md",
           "# [計画] 予定\n\n## 概要\n\n本文。\n")
    _write(root / "proj_x" / "plan_done.md",
           "# [完了] 済み\n\n## 概要\n\n本文。\n")
    cfg = load_config(
        explicit_roots=[str(root)],
        global_path=tmp_path / "no_global.yaml",
    )
    app = create_app(
        cfg,
        token=TOKEN,
        allow_root_mutation=allow_root_mutation,
    )
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _client_swallow(tmp_path: Path) -> TestClient:
    return _make_client(tmp_path, raise_server_exceptions=False)


@pytest.fixture
def app_client(tmp_path: Path):
    """最小の scan ルート + テスト用 token でアプリを組み立てる。

    scan 対象を空にすると build_brief / build_cross 側で ZeroDivision 等の
    エッジケースを踏みかねないため、planned / done を最低 1 本ずつ置いておく。
    """
    return _make_client(tmp_path)


# ---------------- /api/graph ----------------


def test_api_graph_ok(app_client: TestClient):
    r = app_client.get("/api/graph", params={"token": TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert "nodes" in body and "edges" in body


def test_api_graph_requires_token(app_client: TestClient):
    r = app_client.get("/api/graph")
    assert r.status_code == 401


def test_api_graph_server_error_on_backend_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """build_graph が投げた例外は FastAPI 既定の 500 で表面化する（route の骨格確認）。

    TestClient(raise_server_exceptions=False) で例外を握って 500 レスポンスに落とす。
    """

    def _boom(*_a, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("docsweep.server.routes.graph.build_graph", _boom)
    client = _client_swallow(tmp_path)
    r = client.get("/api/graph", params={"token": TOKEN})
    assert r.status_code == 500


# ---------------- /api/resurrect ----------------


def test_api_resurrect_ok(app_client: TestClient):
    r = app_client.get(
        "/api/resurrect",
        params={"token": TOKEN, "no_embedding": "true"},
    )
    assert r.status_code == 200
    body = r.json()
    # find_candidates の to_dict 出力: candidates / total 等が入っている。
    assert isinstance(body, dict)


def test_api_resurrect_requires_token(app_client: TestClient):
    r = app_client.get("/api/resurrect")
    assert r.status_code == 401


def test_api_resurrect_rejects_bad_threshold(app_client: TestClient):
    """threshold は float。文字列が来たら FastAPI の型バリデーションで 4xx。"""
    r = app_client.get(
        "/api/resurrect",
        params={"token": TOKEN, "threshold": "not-a-float"},
    )
    assert r.status_code in (400, 422)


# ---------------- /api/brief ----------------


def test_api_brief_ok(app_client: TestClient):
    r = app_client.get("/api/brief", params={"token": TOKEN})
    assert r.status_code == 200
    body = r.json()
    # BriefResult.to_dict() は today_pick / projects 等を含む。
    assert isinstance(body, dict)


def test_api_brief_requires_token(app_client: TestClient):
    r = app_client.get("/api/brief")
    assert r.status_code == 401


def test_api_brief_rejects_bad_all_flag(app_client: TestClient):
    """all は bool。無効値は型バリデーションで 4xx。"""
    r = app_client.get(
        "/api/brief",
        params={"token": TOKEN, "all": "not-a-bool"},
    )
    assert r.status_code in (400, 422)


# ---------------- /api/cross ----------------


def test_api_cross_ok(app_client: TestClient):
    r = app_client.get("/api/cross", params={"token": TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)


def test_api_cross_requires_token(app_client: TestClient):
    r = app_client.get("/api/cross")
    assert r.status_code == 401


def test_api_cross_server_error_on_backend_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    def _boom(*_a, **_kw):
        raise RuntimeError("cross boom")

    monkeypatch.setattr("docsweep.server.routes.cross.build_cross", _boom)
    client = _client_swallow(tmp_path)
    r = client.get("/api/cross", params={"token": TOKEN})
    assert r.status_code == 500


# ---------------- /capture (HTML) + /api/capture/extract (POST) ----------------


def test_capture_html_ok(app_client: TestClient):
    r = app_client.get("/capture", params={"token": TOKEN})
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_capture_html_requires_token(app_client: TestClient):
    r = app_client.get("/capture")
    assert r.status_code == 401


def test_api_capture_extract_ok(app_client: TestClient):
    """heuristic 経路（use_llm=False）で ok を確認。抽出 0 件でも 200。"""
    r = app_client.post(
        "/api/capture/extract",
        params={"token": TOKEN},
        json={"text": "本文サンプル: 何か plan 相当の会話。", "use_llm": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert "drafts" in body
    assert isinstance(body["drafts"], list)


def test_api_capture_extract_requires_token(app_client: TestClient):
    r = app_client.post(
        "/api/capture/extract",
        json={"text": "hello"},
    )
    assert r.status_code == 401


def test_api_capture_extract_rejects_empty_text(app_client: TestClient):
    """空 text は capture route 自身が 400 で弾く（HTTPException 明示分岐）。"""
    r = app_client.post(
        "/api/capture/extract",
        params={"token": TOKEN},
        json={"text": ""},
    )
    assert r.status_code == 400


# ---------------- /api/config/roots ----------------


def test_add_root_rejected_without_allow_flag(tmp_path: Path):
    extra = tmp_path / "extra-root"
    extra.mkdir()
    client = _make_client(tmp_path)

    r = client.post(
        "/api/config/roots",
        data={"token": TOKEN, "op": "add", "path": str(extra)},
    )

    assert r.status_code == 403
    assert r.json()["detail"] == "roots 追加は --allow-root-mutation 起動時のみ許可"


def test_add_root_accepted_with_allow_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from docsweep.server import config_write

    monkeypatch.setattr(
        config_write, "GLOBAL_CONFIG_PATH", tmp_path / "global-config.yaml",
    )
    extra = tmp_path / "extra-root"
    extra.mkdir()
    client = _make_client(tmp_path, allow_root_mutation=True)

    r = client.post(
        "/api/config/roots",
        data={"token": TOKEN, "op": "add", "path": str(extra)},
    )

    assert r.status_code == 200
    assert extra.resolve().as_posix() in r.json()["roots"]


@pytest.mark.parametrize("protected_path", ["/", "C:/", "~"])
def test_add_system_root_always_rejected(tmp_path: Path, protected_path: str):
    client = _make_client(tmp_path, allow_root_mutation=True)

    r = client.post(
        "/api/config/roots",
        data={"token": TOKEN, "op": "add", "path": protected_path},
    )

    assert r.status_code == 400
    assert r.json()["detail"] == "システム root / HOME 直下の追加は禁止"


def test_remove_root_still_works_without_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from docsweep.server import config_write

    monkeypatch.setattr(
        config_write, "GLOBAL_CONFIG_PATH", tmp_path / "global-config.yaml",
    )
    primary = tmp_path / "roots" / "primary"
    removable = tmp_path / "roots" / "removable"
    primary.mkdir(parents=True)
    removable.mkdir()
    cfg = load_config(
        explicit_roots=[str(primary), str(removable)],
        global_path=tmp_path / "no_global.yaml",
    )
    client = TestClient(create_app(cfg, token=TOKEN))

    r = client.post(
        "/api/config/roots",
        data={"token": TOKEN, "op": "remove", "path": str(removable)},
    )

    assert r.status_code == 200
    assert removable.resolve().as_posix() not in r.json()["roots"]
