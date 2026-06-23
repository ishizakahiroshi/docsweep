"""server/routes/board.py + cards.py — 看板（カンバン）ボードと書き込み口の TestClient テスト。

既存 test_server.py の pattern（TestClient + temp project + token）を踏襲する。
最低限カバーする項目:
- /board が 200 を返し 3 列とカードがレンダリングされる
- /api/board/triage が JSON で counts/columns を返す
- /api/cards/status でラベル変更ができる（postpone がリセットされる）
- /api/cards/status で [完了] 指定すると archive 移送が連動する
- /api/cards/due で期日変更 + postpone +1 になる
- /api/cards/content で本文を書き換えられる
- /api/cards/content で mtime 不一致なら 409 を返す
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from docsweep.config import load_config  # noqa: E402
from docsweep.server.app import create_app  # noqa: E402
from docsweep.state import get_postpone_count, increment_postpone  # noqa: E402

TOKEN = "test-token-board"


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
    proj = root / "proj_a"
    proj.mkdir(parents=True)
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")

    # やり忘れ列: due が過去日 + planned
    _write(
        proj / "plan_overdue.md",
        "---\ndue: 2024-01-01\n---\n# [計画] やり残し\n\n## 概要\n\n放置中。\n",
    )
    # 今日列: due == today
    today = time.strftime("%Y-%m-%d")
    _write(
        proj / "plan_today.md",
        f"---\ndue: {today}\n---\n# [計画] 今日やる\n\n## 概要\n\n本日対応。\n",
    )
    # 実行中列: in-progress + 未来期日
    _write(
        proj / "plan_active.md",
        "---\ndue: 2099-12-31\n---\n# [実行中] 進行中\n\n## 概要\n\n継続作業。\n",
    )
    # archive 候補
    _write(proj / "plan_done.md", "# [完了] 終わった\n\n## 概要\n\n片付いた。\n")
    # 様子見 + 期日超過 = 卒業判定
    _write(
        proj / "plan_graduate.md",
        "---\ndue: 2024-01-01\n---\n# [様子見] 卒業待ち\n\n## 概要\n\n再発確認中。\n",
    )
    # bugfix 対応中: カード左ボタンが「様子見に戻す」になることの検証用
    _write(
        proj / "bugfix_inprogress_2026-06-23.md",
        "---\ndue: 2099-12-31\n---\n# [対応中] 対応中のバグ\n\n## 症状\n\n調査中。\n",
    )

    cfg = load_config(
        explicit_roots=[str(root)],
        global_path=tmp_path / "no_global.yaml",
    )
    app = create_app(cfg, token=TOKEN)
    return TestClient(app), root, proj


# ===== /board / /api/board/triage =====================================


def test_board_requires_token(client):
    c, _, _ = client
    assert c.get("/board").status_code == 403
    assert c.get(f"/board?token={TOKEN}").status_code == 200


def test_board_renders_three_columns(client):
    c, _, _ = client
    html = c.get(f"/board?token={TOKEN}").text
    # 3 列ヘッダが出る
    assert "やり忘れ" in html
    assert "今日" in html
    assert "実行中" in html
    # 折りたたみセクションも出る
    assert "卒業判定" in html
    assert "archive 候補" in html
    # カードのファイル名が描画される
    assert "plan_overdue.md" in html
    assert "plan_today.md" in html
    assert "plan_active.md" in html


def test_board_fragment_returns_partial(client):
    c, _, _ = client
    r = c.get(f"/board/fragment?token={TOKEN}")
    assert r.status_code == 200
    # フラグメントは <body> を含まず col-cards を含む。
    assert "col-cards" in r.text
    assert "<body" not in r.text


def test_board_triage_json_groups_by_column(client):
    c, _, _ = client
    r = c.get(f"/api/board/triage?token={TOKEN}")
    assert r.status_code == 200
    body = r.json()
    assert "counts" in body and "columns" in body
    # overdue 列に plan_overdue.md がいる
    names_overdue = {Path(c["path"]).name for c in body["columns"]["overdue"]}
    assert "plan_overdue.md" in names_overdue
    # graduate 列に plan_graduate.md がいる
    names_grad = {Path(c["path"]).name for c in body["columns"]["graduate"]}
    assert "plan_graduate.md" in names_grad
    # archivable に plan_done.md がいる
    names_arc = {Path(c["path"]).name for c in body["columns"]["archivable"]}
    assert "plan_done.md" in names_arc


# ===== /api/cards/status =============================================


def test_cards_status_relabels_h1(client):
    c, _, proj = client
    p = (proj / "plan_overdue.md").resolve().as_posix()
    r = c.post(
        "/api/cards/status",
        data={"token": TOKEN, "path": p, "new_state": "in-progress"},
    )
    assert r.status_code == 200, r.text
    body = (proj / "plan_overdue.md").read_text(encoding="utf-8")
    assert "# [実行中] やり残し" in body


def test_cards_status_resets_postpone_on_inprogress(client):
    c, _, proj = client
    f = proj / "plan_overdue.md"
    # 先送り 2 回を仕込む
    for _ in range(2):
        increment_postpone(proj, f, from_due=None, to_due="2025-01-01")
    assert get_postpone_count(proj, f) == 2

    p = f.resolve().as_posix()
    r = c.post(
        "/api/cards/status",
        data={"token": TOKEN, "path": p, "new_state": "in-progress"},
    )
    assert r.status_code == 200
    assert r.json()["postpone_count_reset"] is True
    assert get_postpone_count(proj, f) == 0


def test_cards_status_done_triggers_archive(client):
    c, _, proj = client
    f = proj / "plan_active.md"
    p = f.resolve().as_posix()
    r = c.post(
        "/api/cards/status",
        data={"token": TOKEN, "path": p, "new_state": "done"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archive_triggered"] is True
    assert "archive" in body
    # 実ファイルが archive へ移送される
    assert not f.exists()
    assert (proj / "archive" / "plan_active.md").exists()


def test_cards_status_validation_rejects_bad_combo(client):
    c, _, proj = client
    # 2026-06-23 改修: active を in-progress に統合したため、plan に active 違反テストは消滅。
    # 代わりに「bugfix に [計画] (planned) は不可」のバリデーション違反で確認する。
    p = (proj / "bugfix_inprogress_2026-06-23.md").resolve().as_posix()
    r = c.post(
        "/api/cards/status",
        data={"token": TOKEN, "path": p, "new_state": "planned"},
    )
    assert r.status_code == 400


def test_cards_status_rejects_path_outside(client, tmp_path):
    c, _, _ = client
    outside = tmp_path / "outside.md"
    outside.write_text("# [計画] 外", encoding="utf-8")
    r = c.post(
        "/api/cards/status",
        data={"token": TOKEN, "path": str(outside), "new_state": "watching"},
    )
    assert r.status_code == 403


# ===== /api/cards/due ===============================================


def test_cards_due_updates_frontmatter_and_increments_postpone(client):
    c, _, proj = client
    f = proj / "plan_overdue.md"
    p = f.resolve().as_posix()
    r = c.post(
        "/api/cards/due",
        data={"token": TOKEN, "path": p, "new_due": "2099-01-01"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_due"] == "2099-01-01"
    assert body["postpone_count"] == 1
    text = f.read_text(encoding="utf-8")
    assert "due: 2099-01-01" in text


def test_cards_due_parse_error_returns_400(client):
    c, _, proj = client
    p = (proj / "plan_overdue.md").resolve().as_posix()
    r = c.post(
        "/api/cards/due",
        data={"token": TOKEN, "path": p, "new_due": "not-a-date"},
    )
    assert r.status_code == 400


# ===== /api/cards/content ===========================================


def test_cards_content_replaces_body(client):
    c, _, proj = client
    f = proj / "plan_active.md"
    new = "# [実行中] 進行中\n\n## 概要\n\n書き換えた。\n"
    r = c.post(
        "/api/cards/content",
        data={"token": TOKEN, "path": f.resolve().as_posix(), "content": new},
    )
    assert r.status_code == 200, r.text
    assert f.read_text(encoding="utf-8") == new


def test_cards_content_conflict_returns_409(client):
    c, _, proj = client
    f = proj / "plan_active.md"
    p = f.resolve().as_posix()
    # 古い mtime を渡して 409 を誘発
    r = c.post(
        "/api/cards/content",
        data={
            "token": TOKEN,
            "path": p,
            "content": "# [実行中] 進行中\n\n別バージョン\n",
            "expected_mtime": "1.0",
        },
    )
    assert r.status_code == 409


def test_cards_content_rejects_empty(client):
    c, _, proj = client
    f = proj / "plan_active.md"
    r = c.post(
        "/api/cards/content",
        data={"token": TOKEN, "path": f.resolve().as_posix(), "content": ""},
    )
    assert r.status_code == 400


# ===== /api/cards/archive ===========================================


def test_cards_archive_moves_done_file(client):
    c, _, proj = client
    f = proj / "plan_done.md"
    r = c.post(
        "/api/cards/archive",
        data={"token": TOKEN, "path": f.resolve().as_posix()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["moved"], body
    assert (proj / "archive" / "plan_done.md").exists()


def test_cards_archive_skips_watching(client):
    c, _, proj = client
    f = proj / "plan_graduate.md"  # [様子見]
    r = c.post(
        "/api/cards/archive",
        data={"token": TOKEN, "path": f.resolve().as_posix()},
    )
    assert r.status_code == 200
    body = r.json()
    assert not body["moved"]
    assert body["skipped"]
    # 様子見は寝かせを守る不変条件
    assert f.exists()


# ===== picker partials ==============================================


def test_label_picker_partial(client):
    c, _, _ = client
    r = c.get(f"/board/_partial/label_picker?token={TOKEN}")
    assert r.status_code == 200
    assert "label-picker" in r.text
    assert "data-new-state=\"in-progress\"" in r.text


def test_change_picker_partial_plan(client):
    """状態変更ピッカー（plan）は [計画]/[実行中]/[様子見]/[保留]/[完了]/[廃止] の 6 択（全状態集約）。

    2026-06-23 改修: 独立ボタン（着手・廃止）を撤去し、[廃止] もピッカーに含める。
    """
    c, _, _ = client
    r = c.get(f"/board/_partial/change_picker?token={TOKEN}&type=plan")
    assert r.status_code == 200
    assert "change-picker" in r.text
    assert 'data-new-state="planned"' in r.text
    assert 'data-new-state="in-progress"' in r.text
    assert 'data-new-state="watching"' in r.text
    assert 'data-new-state="pending"' in r.text
    assert 'data-new-state="done"' in r.text
    # [廃止] もピッカーに含まれる（独立ボタン撤去のため）
    assert 'data-new-state="discarded"' in r.text
    # bugfix 専用ラベル active は廃止済み
    assert 'data-new-state="active"' not in r.text


def test_change_picker_partial_bugfix(client):
    """bugfix の状態変更ピッカーは [実行中]/[様子見]/[保留]/[完了]/[廃止] の 5 択（[計画] 除外）。"""
    c, _, _ = client
    r = c.get(f"/board/_partial/change_picker?token={TOKEN}&type=bugfix")
    assert r.status_code == 200
    assert 'data-new-state="in-progress"' in r.text
    assert 'data-new-state="watching"' in r.text
    assert 'data-new-state="pending"' in r.text
    assert 'data-new-state="done"' in r.text
    assert 'data-new-state="discarded"' in r.text
    # plan 専用の [計画] は bugfix では出さない
    assert 'data-new-state="planned"' not in r.text
    # 廃止 active キーは消えた
    assert 'data-new-state="active"' not in r.text


def test_change_picker_partial_pending(client):
    """pending ファイルの状態変更ピッカーは [保留]/[計画]/[廃止] の 3 択。"""
    c, _, _ = client
    r = c.get(f"/board/_partial/change_picker?token={TOKEN}&type=pending")
    assert r.status_code == 200
    assert 'data-new-state="pending"' in r.text
    assert 'data-new-state="planned"' in r.text
    assert 'data-new-state="discarded"' in r.text


def test_change_picker_partial_no_type_falls_back_to_plan(client):
    """type 未指定は plan 相当（6 択）にフォールバック。"""
    c, _, _ = client
    r = c.get(f"/board/_partial/change_picker?token={TOKEN}")
    assert r.status_code == 200
    assert 'data-new-state="planned"' in r.text
    assert 'data-new-state="in-progress"' in r.text
    assert 'data-new-state="discarded"' in r.text


def test_label_picker_partial_includes_discarded(client):
    """一括ピッカー（_label_picker.html）も [廃止] を含む（列ヘッダーから独立廃止ボタン撤去のため）。"""
    c, _, _ = client
    r = c.get(f"/board/_partial/label_picker?token={TOKEN}")
    assert r.status_code == 200
    assert 'data-new-state="discarded"' in r.text
    assert 'data-new-state="done"' in r.text


def test_change_picker_requires_token(client):
    c, _, _ = client
    assert c.get("/board/_partial/change_picker").status_code == 403


def test_back_picker_route_removed(client):
    """旧 _back_picker.html とそのルートは削除済み（404）。"""
    c, _, _ = client
    assert c.get(f"/board/_partial/back_picker?token={TOKEN}").status_code == 404


def test_section_actions_include_label_picker(client):
    """各列のセクション一括ボタン群に「ラベル変更▾」が含まれる。

    OPS_DEFAULT/OPS_GRADUATE に label-picker 種を追加した事を保証する。
    クリック時に列内全カードの path を集めて label_picker を出す動線は keymap.js 側。
    """
    c, _, _ = client
    html = c.get(f"/board?token={TOKEN}").text
    # マクロが kind="label-picker" 用の data-action を section-bulk と別経路で出す
    assert 'data-action="section-open-label-picker"' in html
    # 「やり忘れ」「今日」「実行中」+ 卒業判定セクション全てで使えるべき（OPS_DEFAULT / OPS_GRADUATE 双方）
    assert html.count('data-action="section-open-label-picker"') >= 4
    # ボタン文言
    assert "ラベル変更▾" in html


def test_bulk_bar_includes_label_picker(client):
    """上部 sticky バー（選択中カードへの一括）にも「ラベル変更▾」が含まれる。"""
    c, _, _ = client
    html = c.get(f"/board?token={TOKEN}").text
    assert 'data-action="bulk-open-label-picker"' in html


def test_card_actions_are_unified(client):
    """カード下段は全カードで「変更▾ / 期日更新▾」の 2 ボタン固定。

    2026-06-23 改修:
    - バッジクリック動線を撤去（state-badge / due-badge は表示専用）
    - 独立ボタン（着手・廃止）を全廃し、状態変更は「変更▾」ピッカーに集約
    協議: docs/local/kanban-card-ux-options/index.html。
    """
    c, _, _ = client
    html = c.get(f"/board?token={TOKEN}").text

    # 新ボタン
    assert 'data-action="open-change-picker"' in html
    assert "変更▾" in html

    # カード下段の独立ボタン群は全廃
    assert 'data-action="discard"' not in html
    assert 'data-action="start"' not in html
    assert 'data-action="open-back-picker"' not in html
    assert 'data-action="back-watching"' not in html
    assert "戻す▾" not in html
    assert "様子見に戻す" not in html
    assert 'class="act act-start"' not in html
    assert 'class="act act-discard"' not in html


def test_section_actions_no_independent_status_buttons(client):
    """列ヘッダー一括は「+1d / +1w / ラベル変更▾」のみ。独立「着手」「廃止」「完了」は撤去。"""
    c, _, _ = client
    html = c.get(f"/board?token={TOKEN}").text
    # 一括「ラベル変更▾」は残る
    assert 'data-action="section-open-label-picker"' in html
    # 列ヘッダーで in-progress / discarded / done への直行 section-bulk は撤去
    assert 'data-action="section-bulk" data-section="overdue" data-op="status" data-state="in-progress"' not in html
    assert 'data-action="section-bulk" data-section="overdue" data-op="status" data-state="discarded"' not in html
    assert 'data-action="section-bulk" data-section="graduate" data-op="status" data-state="done"' not in html
    # ただし期日先送り (+1d / +1w) は section-bulk として残る
    assert 'data-action="section-bulk"' in html  # due は残る


def test_badges_are_display_only(client):
    """state-badge と due-badge は表示専用（クリック動線を持たない）。

    2026-06-23 改修: バッジから開くピッカーを廃止し、操作は下段ボタンに集約。
    プロジェクトバッジ（絞り込み機能）は下段に同等機能が無いためクリック可能のまま残す。
    """
    c, _, _ = client
    html = c.get(f"/board?token={TOKEN}").text

    # state-badge / due-badge から data-action が消えている
    assert 'data-action="open-label-picker"' not in html
    # 個別カードの open-due-picker は下段「期日更新▾」ボタンには残る（バッジ側を消した）。
    # state-badge-static / due-badge-static の表示専用クラスが付く
    assert "state-badge-static" in html
    assert "due-badge-static" in html
    # プロジェクトバッジは引き続きクリック可能（絞り込み）
    assert 'data-action="select-project"' in html


def test_due_picker_partial(client):
    c, _, _ = client
    r = c.get(f"/board/_partial/due_picker?token={TOKEN}")
    assert r.status_code == 200
    assert "due-picker" in r.text
    assert "data-spec=\"+1d\"" in r.text


def test_cards_raw_returns_md_source_and_mtime(client):
    """edit.js が編集 textarea を生 MD で初期化できることを保証する。

    プレビュー HTML（レンダリング後）を textarea に入れると、保存で Markdown 構造が
    壊れる。生本文を返す専用口（``/api/cards/raw``）が必要。
    """
    c, root, _ = client
    target = root / "proj_a" / "plan_overdue.md"
    r = c.get(f"/api/cards/raw?token={TOKEN}&path={target.as_posix()}")
    assert r.status_code == 200
    j = r.json()
    assert j["path"].endswith("plan_overdue.md")
    # 生 MD（frontmatter + H1）がそのまま返ること（改行コードは OS 依存なので正規化して比較）
    norm = j["content"].replace("\r\n", "\n")
    assert norm.startswith("---\ndue: 2024-01-01\n---\n")
    assert "# [計画] やり残し" in norm
    # mtime も付いてくる（edit.js が expected_mtime に乗せる用）
    assert isinstance(j["mtime"], (int, float))


def test_cards_raw_requires_token(client):
    c, root, _ = client
    target = root / "proj_a" / "plan_overdue.md"
    r = c.get(f"/api/cards/raw?path={target.as_posix()}")
    assert r.status_code == 403


def test_cards_raw_rejects_path_outside(client, tmp_path):
    c, _, _ = client
    outside = tmp_path / "outside.md"
    outside.write_text("# x\n", encoding="utf-8")
    r = c.get(f"/api/cards/raw?token={TOKEN}&path={outside.as_posix()}")
    assert r.status_code == 403


def test_root_redirects_to_board(client):
    """plan_consolidate-to-board: / → /board へ 302 リダイレクト。"""
    c, _, _ = client
    r = c.get(f"/?token={TOKEN}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/board")


def test_board_topbar_has_settings_and_health(client):
    """plan_consolidate-to-board §C1/§C2: topbar に ⚙ 設定ボタンと health chip。"""
    c, _, _ = client
    r = c.get(f"/board?token={TOKEN}")
    assert r.status_code == 200
    assert 'data-action="open-settings"' in r.text
    assert 'class="topbar-health"' in r.text
    assert "health-chip" in r.text


def test_settings_partial_lists_projects(client):
    """⚙ 設定モーダルの partial は注入対象プロジェクト一覧を返す。"""
    c, _, _ = client
    r = c.get(f"/board/_partial/settings?token={TOKEN}")
    assert r.status_code == 200
    # 注入セクションのヘッダとプロジェクトテーブルが含まれる
    assert "グローバル運用ルール" in r.text
    assert "プロジェクト別運用ルール" in r.text
    # フィクスチャの proj_a が一覧に出る
    assert "proj_a" in r.text


def test_settings_partial_requires_token(client):
    c, _, _ = client
    r = c.get("/board/_partial/settings")
    assert r.status_code == 403


def test_board_card_has_project_select_button(client):
    """カードのプロジェクトバッジが button 化されていて data-action="select-project" を持つ。"""
    c, _, _ = client
    r = c.get(f"/board?token={TOKEN}")
    assert r.status_code == 200
    assert 'data-action="select-project"' in r.text
    # カード要素自体に data-project 属性が乗っている（JS の selectProjectOnly が CSS セレクタで照合する）
    assert 'data-project="proj_a"' in r.text


def test_board_renders_bulk_ui_elements(client):
    """plan_kanban-bulk-edit §C2: 上部 sticky バー + セクションヘッダの一括ボタン + カード checkbox が描画される。"""
    c, _, _ = client
    r = c.get(f"/board?token={TOKEN}")
    assert r.status_code == 200
    body = r.text
    # 上部 sticky バー（横断選択用）
    assert 'id="bulk-bar"' in body
    assert 'data-action="bulk-select-all"' in body
    # 各セクションヘッダの「全選択」ボタン
    assert 'data-action="section-select-all"' in body
    # セクション別の一括ボタン（plan §C2: archive セクションのみ archive ボタン）
    assert 'data-section="overdue"' in body
    assert 'data-section="graduate"' in body
    assert 'data-section="archivable"' in body
    # カードに checkbox（このフィクスチャでは plan_overdue.md などが存在する想定）
    assert 'class="card-check"' in body


def test_cards_raw_rejects_non_md(client, tmp_path):
    """同じスキャンルート配下でも .md 以外は弾く（編集口の責務外）。

    既存の ``resolve_under_roots`` は ``isTextFile`` 相当で .md 以外を弾く設計のため、
    実際には 403（path outside scan roots）として返る。.md 限定の意図が満たされていれば
    ステータスコード 400/403 のどちらでも不変条件は守られる。
    """
    c, root, _ = client
    txt = root / "proj_a" / "note.txt"
    txt.write_text("plain", encoding="utf-8")
    r = c.get(f"/api/cards/raw?token={TOKEN}&path={txt.as_posix()}")
    assert r.status_code in (400, 403)
