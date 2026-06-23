"""看板（カンバン）ボードのレンダリング & トリアージ JSON。

3 列レイアウト（🔴 やり忘れ / 🟡 今日 / 🟢 実行中）+ 折りたたみセクション
（▼ 卒業判定 / ▶ 未来期日 / ▶ 期日未設定 / ▶ archive 候補）を返す。

- ``GET /board``: HTML（フル）
- ``GET /board/fragment``: htmx 用パーシャル
- ``GET /api/board/triage``: JSON

書き込み系は ``routes/cards.py`` を参照。
"""

from __future__ import annotations

import secrets
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ... import __version__
from ...engine import run_scan
from ...inject import list_injected
from ...models import Flag
from ...presets import DEFAULT_PRESET, PRESETS
from ...state import get_postpone_count

_DIR = Path(__file__).parent.parent
TEMPLATES = Jinja2Templates(directory=str(_DIR / "templates"))

router = APIRouter()


def _check_token(request: Request, token_q: str | None) -> None:
    """app.state.docsweep.token と照合（既存 _check_token と同等の振る舞い）。"""
    state = request.app.state.docsweep
    supplied = token_q or request.headers.get("x-docsweep-token")
    if not supplied or not secrets.compare_digest(supplied, state.token):
        raise HTTPException(status_code=403, detail="invalid or missing token")


def _scope_lang(request: Request, lang: str | None) -> str:
    cfg = request.app.state.docsweep.config
    return lang if lang in ("ja", "en") else cfg.lang


def _column_key(rec, today: date) -> str:
    """カードの所属列を決める（C2 plan §C1 列定義の機械化）。"""
    if rec.state in ("done", "discarded"):
        return "archivable"
    flags = set(rec.flags)
    if Flag.OVERDUE_TODO.value in flags:
        return "overdue"
    if Flag.OVERDUE_GRADUATE.value in flags:
        return "graduate"
    if not rec.due:
        return "no_due"
    try:
        d = date.fromisoformat(rec.due)
    except ValueError:
        return "no_due"
    if d == today:
        return "today"
    if d < today:
        # OVERDUE フラグが立たないケース（done/discarded を除く）への保険。
        return "overdue"
    # d > today
    if rec.state in ("in-progress", "active"):
        return "active_future"
    return "future"


def _card_view(rec, config) -> dict:
    """テンプレートに渡すカード dict（path/state/期日/postpone を整形）。"""
    project_root = Path(rec.project_root)
    abs_path = Path(rec.path)
    try:
        postpone = get_postpone_count(project_root, abs_path)
    except Exception:  # noqa: BLE001 - state.json 破損時も UI は止めない
        postpone = 0

    due_label = ""
    due_kind = "none"
    if rec.due:
        try:
            d = date.fromisoformat(rec.due)
            delta = (d - date.today()).days
            if delta < 0:
                due_label = f"{-delta} 日超過"
                due_kind = "overdue"
            elif delta == 0:
                due_label = "今日"
                due_kind = "today"
            else:
                due_label = f"あと {delta} 日"
                due_kind = "future"
        except ValueError:
            due_label = "期日不正"
            due_kind = "parse_error"

    name = abs_path.name
    type_def = config.match_type(name)
    file_type = type_def.name if type_def else None

    return {
        "path": rec.path,
        "name": name,
        "project": rec.project,
        "type": file_type,
        "state": rec.state,
        "state_label": rec.state_label or "[?]",
        "title": rec.title,
        "summary": rec.summary,
        "due": rec.due,
        "due_label": due_label,
        "due_kind": due_kind,
        "postpone_count": postpone,
        "mtime": rec.mtime,
        "flags": list(rec.flags),
    }


def _columns(records, config) -> dict:
    """全カードを 3 列 + 4 セクションに分配する。"""
    today = date.today()
    cols: dict[str, list[dict]] = {
        "overdue": [],
        "today": [],
        "active": [],
        "graduate": [],
        "future": [],
        "no_due": [],
        "archivable": [],
    }
    for rec in records:
        col = _column_key(rec, today)
        card = _card_view(rec, config)
        if col == "active_future":
            # 「実行中で未来期日」は 🟢 実行中列に入れる。
            cols["active"].append(card)
        elif col == "today":
            cols["today"].append(card)
            # 今日が期日かつ in-progress/active なら 🟢 列にも複写する選択肢があるが、
            # 重複は混乱の元なので「今日」列だけに置く（plan §C1 列定義に従う）。
        elif col == "overdue":
            cols["overdue"].append(card)
        elif col == "graduate":
            cols["graduate"].append(card)
        elif col == "future":
            cols["future"].append(card)
        elif col == "no_due":
            cols["no_due"].append(card)
        elif col == "archivable":
            cols["archivable"].append(card)
    # 並び順: overdue/graduate は due 昇順（古いほど上）、他は postpone 多→少 → name 昇順。
    cols["overdue"].sort(key=lambda c: (c["due"] or ""))
    cols["graduate"].sort(key=lambda c: (c["due"] or ""))
    cols["today"].sort(key=lambda c: -c["postpone_count"])
    cols["active"].sort(key=lambda c: (c["due"] or "~"))
    cols["future"].sort(key=lambda c: (c["due"] or "~"))
    cols["no_due"].sort(key=lambda c: c["name"])
    cols["archivable"].sort(key=lambda c: c["name"])
    return cols


def _health(records, top_n: int = 5) -> list[dict]:
    """プロジェクトごとの最古経過日数 chip（topbar 表示用）。
    上位 ``top_n`` を返す（多すぎ chip 防止）。
    """
    by: dict[str, int] = {}
    for r in records:
        cur = by.get(r.project)
        if cur is None or r.age_days > cur:
            by[r.project] = r.age_days
    rows = [{"project": p, "oldest": d} for p, d in by.items()]
    rows.sort(key=lambda x: x["oldest"], reverse=True)
    return rows[:top_n]


def _board_data(request: Request) -> dict:
    state = request.app.state.docsweep
    result = run_scan(state.config)
    cols = _columns(result.records, state.config)
    return {
        "version": __version__,
        "cols": cols,
        "counts": {k: len(v) for k, v in cols.items()},
        "root": str(state.config.roots[0]) if state.config.roots else "",
        # _card.html がカード色分けで参照（.docsweep.yaml の due: ブロックで上書き可）。
        "postpone_warn": state.config.due_warn_threshold,
        "postpone_alert": state.config.due_alert_threshold,
        "health": _health(result.records),
    }


@router.get("/board", response_class=HTMLResponse)
def board(
    request: Request,
    token: str = Query(default=""),
    lang: str | None = None,
):
    _check_token(request, token)
    data = _board_data(request)
    return TEMPLATES.TemplateResponse(
        request,
        "board.html",
        {
            "token": request.app.state.docsweep.token,
            "lang": _scope_lang(request, lang),
            "data": data,
        },
    )


@router.get("/board/fragment", response_class=HTMLResponse)
def board_fragment(
    request: Request,
    token: str = Query(default=""),
    lang: str | None = None,
):
    """htmx 用パーシャル（カラム本体だけを差し替える）。"""
    _check_token(request, token)
    data = _board_data(request)
    return TEMPLATES.TemplateResponse(
        request,
        "_board_body.html",
        {
            "token": request.app.state.docsweep.token,
            "lang": _scope_lang(request, lang),
            "data": data,
        },
    )


@router.get("/api/board/triage")
def board_triage_json(
    request: Request,
    token: str = Query(default=""),
):
    """JSON 版（MCP / CLI 検証用に同じ表示データを返す）。"""
    _check_token(request, token)
    data = _board_data(request)
    return JSONResponse(
        {
            "counts": data["counts"],
            "columns": data["cols"],
        }
    )


@router.get("/api/cards/raw")
def card_raw(
    request: Request,
    token: str = Query(default=""),
    path: str = Query(...),
):
    """編集ペイン用に生 MD と現在の mtime を返す（読み取り専用・スコープ境界チェック）。

    edit.js は textarea の初期値として原文（Markdown ソース）を必要とする。プレビュー用の
    ``/preview`` は HTML 変換済みなので、編集用には別口で生本文を返す。
    """
    from ..security import resolve_under_roots

    _check_token(request, token)
    cfg = request.app.state.docsweep.config
    resolved = resolve_under_roots(path, cfg.roots)
    if resolved is None:
        raise HTTPException(status_code=403, detail="path outside scan roots")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="not found")
    if resolved.suffix.lower() != ".md":
        raise HTTPException(status_code=400, detail="only .md files are readable here")
    try:
        text = resolved.read_text(encoding="utf-8", newline="")
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return JSONResponse(
        {
            "path": resolved.as_posix(),
            "content": text,
            "mtime": resolved.stat().st_mtime,
        }
    )


@router.get("/board/_partial/label_picker", response_class=HTMLResponse)
def label_picker_partial(
    request: Request,
    token: str = Query(default=""),
):
    """ラベル選択セグメント partial（keymap.js が fetch して body に貼る）。"""
    _check_token(request, token)
    return TEMPLATES.TemplateResponse(request, "_label_picker.html", {})


def _settings_state(records) -> dict:
    """注入モーダル用のプロジェクト一覧 + グローバル inject 状態 + presets。"""
    injected = {it["path"]: it for it in list_injected()}
    projects: dict[str, dict] = {}
    for r in records:
        root = r.project_root
        if root not in projects:
            info = injected.get(root)
            projects[root] = {
                "name": r.project, "root": root,
                "injected": info is not None, "preset": (info or {}).get("preset"),
            }
    global_agents = {it.get("agent") for it in injected.values() if it.get("scope") == "global"}
    return {
        "projects": sorted(projects.values(), key=lambda x: x["name"]),
        "global_claude": "claude" in global_agents,
        "global_codex": "codex" in global_agents,
        "presets": list(PRESETS),
        "default_preset": DEFAULT_PRESET,
    }


@router.get("/board/_partial/settings", response_class=HTMLResponse)
def settings_partial(
    request: Request,
    token: str = Query(default=""),
):
    """⚙ 設定モーダルの中身（プロジェクト一覧 + グローバル inject タブ + presets）。"""
    _check_token(request, token)
    state = request.app.state.docsweep
    result = run_scan(state.config)
    return TEMPLATES.TemplateResponse(
        request, "_settings.html",
        {
            "token": state.token,
            "settings": _settings_state(result.records),
        },
    )


@router.get("/board/_partial/due_picker", response_class=HTMLResponse)
def due_picker_partial(
    request: Request,
    token: str = Query(default=""),
):
    """期日変更ポップオーバー partial（keymap.js が fetch して body に貼る）。"""
    _check_token(request, token)
    return TEMPLATES.TemplateResponse(request, "_due_picker.html", {})
