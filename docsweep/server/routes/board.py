"""看板（カンバン）ボードのレンダリング & トリアージ JSON。

3 列レイアウト（🔴 やり忘れ / 🟡 今日 / 🟢 実行中）+ 折りたたみセクション
（▼ 卒業判定 / ▶ 未来期日 / ▶ 期日未設定 / ▶ archive 候補）を返す。

- ``GET /board``: HTML（フル）
- ``GET /board/fragment``: htmx 用パーシャル
- ``GET /api/board/triage``: JSON

書き込み系は ``routes/cards.py`` を参照。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ... import __version__
from ...engine import run_scan
from ...inject import list_injected
from ...models import Flag
from ...presets import DEFAULT_PRESET, PRESETS
from ...state import get_postpone_count
from ..security import check_token

_DIR = Path(__file__).parent.parent
TEMPLATES = Jinja2Templates(directory=str(_DIR / "templates"))

router = APIRouter()


def _scope_lang(request: Request, lang: str | None) -> str:
    from ..i18n import resolve_lang

    return resolve_lang(request, lang)


def _t(request: Request, lang: str | None = None) -> dict:
    """lang 解決済みの文言 dict（テンプレの ``T``）。"""
    from ..i18n import get_messages

    return get_messages(_scope_lang(request, lang))


def _state_labels(request: Request, lang: str) -> dict[str, str]:
    """ピッカー用に state key → ブラケット付きラベル（lang 解決済み）を返す。

    ラベル語彙は ``.docsweep.yaml`` の states が正本（内蔵デフォルトを含め二言語辞書を持つ）。
    テンプレにハードコードせずここから出すことで、設定と表示が常に同期する。
    """
    sm = request.app.state.docsweep.config.state_model
    return {s.key: f"[{s.label(lang)}]" for s in sm.states}


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
    # 2026-06-23 改修: active を in-progress に統合。
    if rec.state == "in-progress":
        return "active_future"
    return "future"


def _card_view(
    rec,
    config,
    backref_count: int = 0,
    t: dict | None = None,
    *,
    link_progress: str | None = None,
) -> dict:
    """テンプレートに渡すカード dict（path/state/期日/postpone を整形）。"""
    from ..i18n import get_messages

    t = t or get_messages("ja")
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
                due_label = t["due_overdue_by"].format(n=-delta)
                due_kind = "overdue"
            elif delta == 0:
                due_label = t["due_today"]
                due_kind = "today"
            else:
                due_label = t["due_in"].format(n=delta)
                due_kind = "future"
        except ValueError:
            due_label = t["due_invalid"]
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
        # OKF（plan_okf-adoption_2026-06-29.md C4）拡張: tags / owner / related と
        # 「このファイルを参照している plan/bugfix/pending の件数（逆参照）」をカードに出す。
        # frontmatter 無しファイルは空値で出る（後方互換）。
        "tags": list(rec.tags),
        "owner": rec.owner,
        "review_status": rec.review_status,
        "related": list(rec.related),
        "related_count": len(rec.related),
        "last_reviewed": rec.last_reviewed,
        "backref_count": backref_count,
        "link_progress": link_progress,  # UX W3 / P8
    }


def _backref_map(records) -> dict[str, int]:
    """C2 で ``docsweep.related.backref_counts`` に移管された逆参照集計の薄いラッパ。

    CLI ``docsweep show`` / ``docsweep context`` と同じロジックを 1 箇所に集約することで、
    Web UI とコマンドラインの逆参照件数が必ず一致するようにする。
    """
    from ...related import backref_counts

    return backref_counts(list(records))


def _linkcheck_map(config, records) -> dict[str, str]:
    """plan path → progress_hint（失敗時は空 dict）。"""
    try:
        from ...linkcheck import linkcheck
    except Exception:  # noqa: BLE001
        return {}
    try:
        # 全 plan は重いので open plan のみ（上限 40）
        plans = [
            r for r in records
            if r.type == "plan" and r.state not in {"done", "discarded"}
        ][:40]
        if not plans:
            return {}
        out: dict[str, str] = {}
        for lc in linkcheck(config):
            out[lc.plan_path] = lc.progress_hint
            if len(out) >= 40:
                break
        return out
    except Exception:  # noqa: BLE001
        return {}


def _filter_records_by_profile(records, config, profile: str | None):
    """Cookie の profile 名で roots 配下に絞る（UX W2 / P41）。"""
    if not profile or profile in ("", "all"):
        return records
    roots = (config.profiles or {}).get(profile)
    if not roots:
        return records
    norms = []
    for r in roots:
        try:
            norms.append(Path(r).resolve().as_posix().lower())
        except OSError:
            norms.append(str(r).replace("\\", "/").lower())
    if not norms:
        return records
    out = []
    for rec in records:
        pr = (rec.project_root or "").replace("\\", "/").lower()
        if any(pr == n or pr.startswith(n.rstrip("/") + "/") for n in norms):
            out.append(rec)
    return out


def _columns(records, config, t: dict | None = None) -> dict:
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
    backrefs = _backref_map(records)
    lc_map = _linkcheck_map(config, records)
    for rec in records:
        col = _column_key(rec, today)
        card = _card_view(
            rec, config,
            backref_count=backrefs.get(rec.path, 0),
            t=t,
            link_progress=lc_map.get(rec.path),
        )
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


def _index_freshness() -> dict:
    """index.db の鮮度（UX W1 / P61）。board トップバー表示用。"""
    from datetime import datetime, timezone

    from ...index import db_path

    path = db_path()
    if not path.is_file():
        return {
            "exists": False,
            "path": str(path),
            "age_hours": None,
            "level": "warn",  # missing
            "label": "index 未作成",
        }
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {
            "exists": False,
            "path": str(path),
            "age_hours": None,
            "level": "warn",
            "label": "index 読めず",
        }
    age_h = max(0.0, datetime.now(timezone.utc).timestamp() - mtime) / 3600.0
    if age_h >= 168:
        level, label = "bad", f"index {age_h:.0f}h 前"
    elif age_h >= 24:
        level, label = "warn", f"index {age_h:.0f}h 前"
    else:
        level, label = "ok", f"index {age_h:.1f}h 前"
    return {
        "exists": True,
        "path": str(path),
        "age_hours": round(age_h, 2),
        "level": level,
        "label": label,
    }


def _today_pick_view(config, t: dict | None = None) -> dict | None:
    """brief の today_pick を board 固定ピン用に取る（UX W1 / P6）。

    横断 roots では all_projects のうち score 最大の 1 件をピンする。
    """
    try:
        from ...brief.service import build_brief
    except Exception:  # noqa: BLE001
        return None
    try:
        brief = build_brief(config, all_projects=True)
    except Exception:  # noqa: BLE001
        return None
    best = None
    best_score = -1.0
    for proj in brief.projects:
        tp = proj.today_pick
        if not tp:
            continue
        sc = tp.get("score") or {}
        total = float(sc.get("total", 0.0)) if isinstance(sc, dict) else 0.0
        if best is None or total > best_score:
            best = tp
            best_score = total
    if not best:
        return None
    sc = best.get("score") if isinstance(best.get("score"), dict) else {}
    return {
        "path": best.get("path"),
        "name": Path(best.get("path") or "").name,
        "project": best.get("project") or "",
        "state_label": best.get("state_label") or "[?]",
        "title": best.get("title") or "",
        "summary": best.get("summary") or "",
        "rel": best.get("rel") or "",
        "age_days": best.get("age_days"),
        "score_total": (sc or {}).get("total"),
        "score": sc,
    }


def _board_data(request: Request, t: dict | None = None) -> dict:
    state = request.app.state.docsweep
    result = run_scan(state.config)
    profile = request.cookies.get("docsweep_profile") or "all"
    records = _filter_records_by_profile(result.records, state.config, profile)
    cols = _columns(records, state.config, t=t)
    try:
        from ...auto_triage import suggest_transitions
        suggestion_count = len(suggest_transitions(state.config).suggestions)
    except Exception:  # noqa: BLE001
        suggestion_count = 0
    profiles = sorted((state.config.profiles or {}).keys())
    return {
        "version": __version__,
        "cols": cols,
        "counts": {k: len(v) for k, v in cols.items()},
        "root": str(state.config.roots[0]) if state.config.roots else "",
        # _card.html がカード色分けで参照（.docsweep.yaml の due: ブロックで上書き可）。
        "postpone_warn": state.config.due_warn_threshold,
        "postpone_alert": state.config.due_alert_threshold,
        "health": _health(records),
        "today_pick": _today_pick_view(state.config, t=t),
        "index_freshness": _index_freshness(),
        "profile": profile,
        "profiles": profiles,
        "suggestion_count": suggestion_count,
    }


@router.get("/board", response_class=HTMLResponse)
def board(
    request: Request,
    token: str = Query(default=""),
    lang: str | None = None,
):
    check_token(request, token, status_code=403, detail="invalid or missing token")
    from ..i18n import get_messages

    resolved_lang = _scope_lang(request, lang)
    t = get_messages(resolved_lang)
    data = _board_data(request, t=t)
    return TEMPLATES.TemplateResponse(
        request,
        "board.html",
        {
            "token": request.app.state.docsweep.token,
            "lang": resolved_lang,
            "T": t,
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
    check_token(request, token, status_code=403, detail="invalid or missing token")
    from ..i18n import get_messages

    resolved_lang = _scope_lang(request, lang)
    t = get_messages(resolved_lang)
    data = _board_data(request, t=t)
    return TEMPLATES.TemplateResponse(
        request,
        "_board_body.html",
        {
            "token": request.app.state.docsweep.token,
            "lang": resolved_lang,
            "T": t,
            "data": data,
        },
    )


@router.get("/api/board/triage")
def board_triage_json(
    request: Request,
    token: str = Query(default=""),
):
    """JSON 版（MCP / CLI 検証用に同じ表示データを返す）。"""
    check_token(request, token, status_code=403, detail="invalid or missing token")
    data = _board_data(request)
    return JSONResponse(
        {
            "counts": data["counts"],
            "columns": data["cols"],
        }
    )


@router.get("/api/cards/context")
def card_context(
    request: Request,
    token: str = Query(default=""),
    path: str = Query(...),
):
    """作業開始パック: path の context 文字列を返す（UX W1 / P7）。"""
    from ...context import collect_context, render_context
    from ..security import resolve_under_roots

    check_token(request, token, status_code=403, detail="invalid or missing token")
    cfg = request.app.state.docsweep.config
    resolved = resolve_under_roots(path, cfg.roots)
    if resolved is None:
        raise HTTPException(status_code=403, detail="path out of scope or not .md")
    # collect_context は scan の path 表記（posix 寄り）と一致させる
    candidates = [
        resolved.as_posix(),
        str(resolved),
        str(resolved.resolve()),
    ]
    last_err: Exception | None = None
    for cand in candidates:
        try:
            bundle = collect_context(cand, cfg)
            text = render_context(bundle, fmt="markdown")
            return JSONResponse({
                "path": resolved.as_posix(),
                "text": text,
                "chars": len(text),
            })
        except (FileNotFoundError, ValueError) as e:
            last_err = e
            continue
    raise HTTPException(
        status_code=404,
        detail=str(last_err) if last_err else "not found",
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

    check_token(request, token, status_code=403, detail="invalid or missing token")
    cfg = request.app.state.docsweep.config
    resolved = resolve_under_roots(path, cfg.roots)
    if resolved is None:
        raise HTTPException(status_code=403, detail="path outside scan roots")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="not found")
    if resolved.suffix.lower() != ".md":
        raise HTTPException(status_code=400, detail="only .md files are readable here")
    try:
        text = resolved.open("r", encoding="utf-8", newline="").read()
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return JSONResponse(
        {
            "path": resolved.as_posix(),
            "content": text,
            "mtime": resolved.stat().st_mtime,
        }
    )


@router.get("/api/cards/detail")
def card_detail(
    request: Request,
    token: str = Query(default=""),
    path: str = Query(...),
):
    """指定カードの OKF 拡張詳細 + 逆参照（このファイルを related に挙げているファイル群）を返す。

    C4 の詳細パネルがカード選択時に呼ぶ。frontmatter 無しファイルでも 200 で空値を返す。
    """
    from ..security import resolve_under_roots

    check_token(request, token, status_code=403, detail="invalid or missing token")
    state = request.app.state.docsweep
    cfg = state.config
    resolved = resolve_under_roots(path, cfg.roots)
    if resolved is None:
        raise HTTPException(status_code=403, detail="path outside scan roots")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="not found")
    target_path = resolved.as_posix()

    result = run_scan(cfg)
    records = list(result.records)
    me = next((r for r in records if r.path == target_path), None)

    # 逆参照: 各 record の related[] にこのファイルを示す値が含まれていれば積む。
    target_name = resolved.name
    backrefs: list[dict] = []
    for r in records:
        if r.path == target_path:
            continue
        for ref in r.related:
            if ref == target_path or Path(ref).name == target_name:
                backrefs.append({
                    "path": r.path,
                    "name": Path(r.path).name,
                    "project": r.project,
                    "type": r.type,
                    "state": r.state,
                    "state_label": r.state_label,
                    "title": r.title,
                })
                break

    return JSONResponse({
        "path": target_path,
        "found": me is not None,
        "tags": list(me.tags) if me else [],
        "owner": (me.owner if me else None),
        "review_status": (me.review_status if me else None),
        "related": list(me.related) if me else [],
        "last_reviewed": (me.last_reviewed if me else None),
        "mtime": (me.mtime if me else resolved.stat().st_mtime),
        "backrefs": sorted(backrefs, key=lambda b: b["name"]),
    })


@router.get("/board/_partial/label_picker", response_class=HTMLResponse)
def label_picker_partial(
    request: Request,
    token: str = Query(default=""),
):
    """ラベル選択セグメント partial（keymap.js が fetch して body に貼る）。"""
    check_token(request, token, status_code=403, detail="invalid or missing token")
    resolved_lang = _scope_lang(request, None)
    return TEMPLATES.TemplateResponse(
        request, "_label_picker.html",
        {"T": _t(request), "labels": _state_labels(request, resolved_lang)},
    )


@router.get("/board/_partial/change_picker", response_class=HTMLResponse)
def change_picker_partial(
    request: Request,
    token: str = Query(default=""),
    type: str | None = Query(default=None),
):
    """状態変更ピッカー partial（個別カードの「変更▾」専用・[廃止] を除く全許可状態）。

    file_type に応じて選択肢を出し分ける（種別連番）:
    - plan / 未知: [計画] / [実行中] / [様子見] / [保留] / [完了] の 5 択
    - bugfix:     [実行中] / [様子見] / [保留] / [完了] の 4 択
    - pending:    [保留] / [計画] の 2 択
    2026-06-23 改修: active/対応中 を in-progress/実行中 に統合。bugfix と plan の唯一の差は
    「bugfix に [計画] が無い」だけになった。

    [廃止] はカード下段の独立ボタン（誤クリック防止）に分離されているためピッカーから除外。

    経緯: docs/local/kanban-card-ux-options/index.html — バッジクリック動線を廃し、
    下段 3 ボタン（変更▾ / 期日更新▾ / 廃止）に全操作を集約する方針。
    """
    check_token(request, token, status_code=403, detail="invalid or missing token")
    resolved_lang = _scope_lang(request, None)
    return TEMPLATES.TemplateResponse(
        request, "_change_picker.html",
        {"file_type": type, "T": _t(request), "labels": _state_labels(request, resolved_lang)},
    )


def _settings_state(records, config=None) -> dict:
    """注入モーダル用のプロジェクト一覧 + グローバル inject 状態 + presets。"""
    from ...excluded import is_excluded, list_known_projects, load_excluded

    injected = {it["path"]: it for it in list_injected()}
    projects: dict[str, dict] = {}
    for r in records:
        root = r.project_root
        if root not in projects:
            info = injected.get(root)
            projects[root] = {
                "name": r.project, "root": root,
                "injected": info is not None,
                "preset": (info or {}).get("preset"),
                "version": (info or {}).get("version"),
                "enabled": not is_excluded(root),
            }
    if config is not None:
        try:
            for p in list_known_projects(config):
                if p["root"] not in projects:
                    projects[p["root"]] = {
                        "name": p["name"], "root": p["root"],
                        "injected": False, "preset": None, "version": None,
                        "enabled": p["enabled"],
                    }
                else:
                    projects[p["root"]]["enabled"] = p["enabled"]
        except Exception:  # noqa: BLE001
            for e in load_excluded():
                if e not in projects:
                    projects[e] = {
                        "name": Path(e).name, "root": e,
                        "injected": False, "preset": None, "version": None,
                        "enabled": False,
                    }
    global_by_agent = {it.get("agent"): it for it in injected.values() if it.get("scope") == "global"}
    return {
        "projects": sorted(projects.values(), key=lambda x: x["name"]),
        "global_claude": "claude" in global_by_agent,
        "global_codex": "codex" in global_by_agent,
        "global_claude_version": (global_by_agent.get("claude") or {}).get("version"),
        "global_codex_version": (global_by_agent.get("codex") or {}).get("version"),
        "presets": list(PRESETS),
        "default_preset": DEFAULT_PRESET,
    }


@router.get("/board/_partial/settings", response_class=HTMLResponse)
def settings_partial(
    request: Request,
    token: str = Query(default=""),
):
    """⚙ 設定モーダルの中身（プロジェクト一覧 + グローバル inject タブ + presets）。"""
    check_token(request, token, status_code=403, detail="invalid or missing token")
    state = request.app.state.docsweep
    result = run_scan(state.config)
    return TEMPLATES.TemplateResponse(
        request, "_settings.html",
        {
            "token": state.token,
            "settings": _settings_state(result.records, config=state.config),
            "roots": [Path(r).as_posix() for r in state.config.roots],
            "version": __version__,
            "T": _t(request),
            "lang": _scope_lang(request, None),
        },
    )


@router.get("/api/suggestions")
def api_suggestions(
    request: Request,
    token: str = Query(default=""),
):
    """auto-triage 提案トレイ JSON（UX W2 / P35）。"""
    from ...auto_triage import suggest_transitions

    check_token(request, token, status_code=403, detail="invalid or missing token")
    cfg = request.app.state.docsweep.config
    result = suggest_transitions(cfg)
    return JSONResponse(result.to_dict())


@router.post("/api/suggestions/apply")
def api_suggestions_apply(
    request: Request,
    token: str = Form(default=""),
    path: str = Form(...),
    action: str = Form(...),
    to: str | None = Form(default=None),
    dry_run: bool = Form(default=False),
):
    """提案 1 件を Accept（apply）する。"""
    from ...auto_triage import apply_suggestions

    check_token(request, token, status_code=403, detail="invalid or missing token")
    cfg = request.app.state.docsweep.config
    decisions = [{"path": path, "action": action, "to": to}]
    res = apply_suggestions(cfg, decisions, dry_run=dry_run)
    return JSONResponse(res.to_dict())


@router.post("/api/project/toggle")
def api_project_toggle(
    request: Request,
    token: str = Form(default=""),
    root: str = Form(...),
    enabled: str = Form(...),
):
    """プロジェクト ON/OFF（除外リスト）（UX W2 / P39）。"""
    from ...excluded import disable_project, enable_project, is_excluded

    check_token(request, token, status_code=403, detail="invalid or missing token")
    want_on = str(enabled).strip().lower() in ("1", "true", "yes", "on")
    if want_on:
        enable_project(root)
    else:
        disable_project(root)
    return JSONResponse({
        "root": root,
        "enabled": not is_excluded(root),
    })


@router.post("/api/profile")
def api_set_profile(
    request: Request,
    token: str = Form(default=""),
    profile: str = Form(default="all"),
):
    """看板の profile cookie を設定（UX W2 / P41）。"""
    from fastapi.responses import JSONResponse as JR

    check_token(request, token, status_code=403, detail="invalid or missing token")
    name = (profile or "all").strip() or "all"
    resp = JR({"profile": name})
    resp.set_cookie(
        "docsweep_profile", name,
        max_age=60 * 60 * 24 * 365,
        httponly=False,
        samesite="lax",
    )
    return resp


@router.get("/board/_partial/due_picker", response_class=HTMLResponse)
def due_picker_partial(
    request: Request,
    token: str = Query(default=""),
):
    """期日変更ポップオーバー partial（keymap.js が fetch して body に貼る）。"""
    check_token(request, token, status_code=403, detail="invalid or missing token")
    return TEMPLATES.TemplateResponse(request, "_due_picker.html", {"T": _t(request)})
