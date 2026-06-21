"""FastAPI アプリ本体。htmx でプレビュー主・既定アプリ起動従の UI を配信する。"""

from __future__ import annotations

import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..config import Config
from ..engine import ScanResult, apply_action, auto_sweep, run_scan
from ..index import build_index, write_index
from ..inject import (
    eject,
    eject_global,
    inject,
    inject_global,
    list_injected,
    preview_global,
    preview_inject,
)
from ..models import Flag
from .sanitize import sanitize_html
from .security import resolve_under_roots

_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(_DIR / "templates"))


class ServerState:
    def __init__(self, config: Config, token: str):
        self.config = config
        self.token = token


def _render_markdown(text: str) -> str:
    try:
        import markdown
    except ImportError:  # pragma: no cover - web extra 未導入
        # フォールバック: プレーンテキストとして安全表示。
        import html

        return f"<pre>{html.escape(text)}</pre>"
    # 信頼できない .md 由来の生 HTML/script/javascript: を必ず除去してから返す（XSS 対策）。
    rendered = markdown.markdown(
        text, extensions=["tables", "fenced_code", "toc", "sane_lists"]
    )
    return sanitize_html(rendered)


def _find_doc(result: ScanResult, path: str):
    target = Path(path).resolve().as_posix()
    return next((d for d in result.docs if d.record.path == target), None)


def create_app(config: Config, token: str | None = None) -> FastAPI:
    token = token or secrets.token_urlsafe(16)
    state = ServerState(config, token)
    app = FastAPI(title="docsweep", docs_url=None, redoc_url=None)
    app.state.docsweep = state

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        resp = await call_next(request)
        # 信頼できない .md 由来の外部リソース読込で token 入り URL が Referer に載るのを防ぎ、
        # MIME スニッフィングも無効化する（多層防御）。
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        # script は自分の static のみ許可（inline ハンドラ・注入 script を不許可）。
        # サニタイザを抜けた万一の XSS でも JS 実行を遮断する多層防御。style は health バーの
        # 動的 width のため unsafe-inline を許す（script ほど危険でない）。
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; connect-src 'self'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        return resp

    static_dir = _DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def _check_token(request: Request, token_q: str | None) -> None:
        supplied = token_q or request.headers.get("x-docsweep-token")
        if not supplied or not secrets.compare_digest(supplied, state.token):
            raise HTTPException(status_code=403, detail="invalid or missing token")

    def _scope(lang: str | None) -> str:
        return lang if lang in ("ja", "en") else state.config.lang

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, token: str = Query(default=""), lang: str | None = None):
        _check_token(request, token)
        result = run_scan(state.config)
        return TEMPLATES.TemplateResponse(
            request,
            "dashboard.html",
            {
                "token": state.token,
                "lang": _scope(lang),
                "version": __version__,
                "d": _dashboard_data(result, state.config),
            },
        )

    @app.get("/list", response_class=HTMLResponse)
    def list_partial(
        request: Request, token: str = Query(default=""), lang: str | None = None,
        filter: str = "needs",
    ):
        _check_token(request, token)
        result = run_scan(state.config)
        return TEMPLATES.TemplateResponse(
            request,
            "_list.html",
            {"token": state.token, "lang": _scope(lang), "groups": _group(result, state.config.state_model, _scope(lang), filter)},
        )

    @app.get("/fragment", response_class=HTMLResponse)
    def fragment(request: Request, token: str = Query(default=""), lang: str | None = None):
        """htmx 部分リフレッシュ用。#page-content 内のサイドバー+メインを再レンダリングして返す。"""
        _check_token(request, token)
        result = run_scan(state.config)
        return TEMPLATES.TemplateResponse(
            request,
            "_dashboard_body.html",
            {
                "token": state.token,
                "lang": _scope(lang),
                "version": __version__,
                "d": _dashboard_data(result, state.config),
            },
        )

    @app.get("/preview", response_class=HTMLResponse)
    def preview(request: Request, token: str = Query(default=""), path: str = Query(default="")):
        _check_token(request, token)
        resolved = resolve_under_roots(path, state.config.roots)
        if resolved is None:
            raise HTTPException(status_code=403, detail="path outside scan roots")
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="not found")
        result = run_scan(state.config)
        doc = _find_doc(result, str(resolved))
        text = resolved.read_text(encoding="utf-8", errors="replace")
        return TEMPLATES.TemplateResponse(
            request,
            "_preview.html",
            {
                "token": state.token,
                "html": _render_markdown(text),
                "record": doc.record if doc else None,
                "path": resolved.as_posix(),
            },
        )

    @app.post("/api/apply")
    def api_apply(
        request: Request,
        token: str = Form(default=""),
        path: str = Form(...),
        action: str = Form(...),
        to: str | None = Form(default=None),
    ):
        _check_token(request, token)
        resolved = resolve_under_roots(path, state.config.roots)
        if resolved is None:
            raise HTTPException(status_code=403, detail="path outside scan roots")
        result = run_scan(state.config)
        doc = _find_doc(result, str(resolved))
        if doc is None:
            raise HTTPException(status_code=404, detail="not found")
        try:
            entry = apply_action(doc, action, state.config, to=to)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return JSONResponse(entry.to_dict())

    @app.post("/api/open")
    def api_open(request: Request, token: str = Form(default=""), path: str = Form(...)):
        """既定アプリで開く（補助・従）。冪等な閲覧操作のみ・ルート配下限定。"""
        _check_token(request, token)
        resolved = resolve_under_roots(path, state.config.roots)
        if resolved is None or not resolved.is_file():
            raise HTTPException(status_code=403, detail="path outside scan roots")
        _open_in_default_app(resolved)
        return JSONResponse({"opened": resolved.as_posix()})

    @app.post("/api/reveal")
    def api_reveal(request: Request, token: str = Form(default=""), path: str = Form(...)):
        """ファイルの置き場フォルダを OS のファイルマネージャで開く（補助・従）。ルート配下限定。"""
        _check_token(request, token)
        resolved = resolve_under_roots(path, state.config.roots)
        if resolved is None or not resolved.is_file():
            raise HTTPException(status_code=403, detail="path outside scan roots")
        _reveal_in_file_manager(resolved)
        return JSONResponse({"revealed": resolved.parent.as_posix()})

    @app.post("/api/sweep")
    def api_sweep(request: Request, token: str = Form(default=""), dry_run: bool = Form(default=False)):
        """done/discarded を archive へ。watching は触らない。"""
        _check_token(request, token)
        moved = auto_sweep(state.config, dry_run=dry_run)
        # CLI sweep と同様、実移送後は横断 INDEX を再生成して陳腐化させない。
        if not dry_run and state.config.roots:
            write_index(state.config)
        return JSONResponse([m.to_dict() for m in moved])

    def _valid_project_dir(project: str) -> Path | None:
        """注入対象はスキャンで実在が確認できたプロジェクト境界に限定する（任意パスへの書込を防ぐ）。"""
        if not project:
            return None
        target = Path(project).resolve().as_posix()
        roots = {d.record.project_root for d in run_scan(state.config).docs}
        return Path(target) if target in roots else None

    @app.post("/api/inject")
    def api_inject(
        request: Request,
        token: str = Form(default=""),
        scope: str = Form(default="project"),
        project: str = Form(default=""),
        agent: str = Form(default="claude"),
        preset: str = Form(default=""),
        dry_run: bool = Form(default=False),
    ):
        """運用ルールを注入。dry_run=True は「何が書かれるか」のプレビューを返す（書き込まない）。"""
        _check_token(request, token)
        if scope == "global":
            if agent not in ("claude", "codex"):
                raise HTTPException(status_code=400, detail="unknown agent")
            if dry_run:
                return JSONResponse(preview_global(agent=agent, lang=state.config.lang))
            r = inject_global(agent=agent, lang=state.config.lang)
            return JSONResponse({"project": r.project, "written": r.written, "skipped": r.skipped, "warnings": r.warnings})
        pdir = _valid_project_dir(project)
        if pdir is None:
            raise HTTPException(status_code=403, detail="project outside scan roots")
        preset_name = preset or None
        try:
            if dry_run:
                return JSONResponse(preview_inject(pdir, preset=preset_name))
            r = inject(pdir, preset=preset_name)
        except ValueError as e:  # 未知の preset 等
            raise HTTPException(status_code=400, detail=str(e)) from e
        return JSONResponse({"project": r.project, "written": r.written, "skipped": r.skipped,
                             "warnings": r.warnings, "yaml": r.yaml_path})

    @app.post("/api/eject")
    def api_eject(
        request: Request,
        token: str = Form(default=""),
        scope: str = Form(default="project"),
        project: str = Form(default=""),
        agent: str = Form(default="claude"),
        purge: bool = Form(default=False),
        dry_run: bool = Form(default=False),
    ):
        """注入した管理ブロックを剥がす（手書きは温存）。dry_run=True は除去対象の確認のみ。"""
        _check_token(request, token)
        if scope == "global":
            if agent not in ("claude", "codex"):
                raise HTTPException(status_code=400, detail="unknown agent")
            r = eject_global(agent=agent, dry_run=dry_run)
            return JSONResponse({"project": r.project, "removed": r.removed, "warnings": r.warnings})
        pdir = _valid_project_dir(project)
        if pdir is None:
            raise HTTPException(status_code=403, detail="project outside scan roots")
        r = eject(pdir, purge=purge, dry_run=dry_run)
        return JSONResponse({"project": r.project, "removed": r.removed,
                             "warnings": r.warnings, "purged_yaml": r.purged_yaml})

    return app


def _open_in_default_app(path: Path) -> None:
    if sys.platform.startswith("win"):
        import os

        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def _reveal_in_file_manager(path: Path) -> None:
    """ファイルを内包フォルダごと開く。可能なら当該ファイルを選択状態にする。"""
    if sys.platform.startswith("win"):
        # explorer は成功時も exit 1 を返すので check=False。/select でファイルを選択表示。
        subprocess.run(["explorer", "/select,", str(path)], check=False)  # noqa: S603,S607
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)], check=False)
    else:
        # Linux は標準の「ファイル選択」手段が無いため親フォルダを開く。
        subprocess.run(["xdg-open", str(path.parent)], check=False)


def _counts(result: ScanResult) -> dict:
    recs = result.records
    return {
        "total": len(recs),
        "needs_decision": sum(1 for r in recs if Flag.NEEDS_DECISION.value in r.flags),
        "needs_fix": sum(1 for r in recs if Flag.NEEDS_FIX.value in r.flags),
        "auto_movable": sum(1 for r in recs if r.auto_movable and r.archivable),
        "watching": sum(1 for r in recs if r.state == "watching"),
    }


def _by_project(items: list[dict]) -> list[dict]:
    by: dict[str, list] = {}
    for item in items:
        by.setdefault(item["project"], []).append(item)
    return [{"project": name, "records": recs} for name, recs in by.items()]


def _dashboard_data(result: ScanResult, config: Config) -> dict:
    """受信トレイ型ダッシュボードの表示データを組み立てる。"""
    idx = build_index(config, result)
    recs = result.records

    roots = config.roots

    def _loc(path: str) -> str:
        p = Path(path)
        for root in roots:
            try:
                return p.relative_to(Path(root)).parent.as_posix()
            except ValueError:
                continue
        return p.parent.name

    def slim(r) -> dict:
        d = r.to_dict()
        d["name"] = Path(r.path).name
        d["loc"] = _loc(r.path)
        d["arch_action"] = "promote" if r.state == "watching" else "discard"
        # カードの「Nd 無更新」に hover で出す絶対更新日時（ローカルタイム）。
        d["mtime_str"] = (
            datetime.fromtimestamp(r.mtime).strftime("%Y-%m-%d %H:%M") if r.mtime else ""
        )
        d["overdue_todo"] = Flag.OVERDUE_TODO.value in r.flags
        d["overdue_graduate"] = Flag.OVERDUE_GRADUATE.value in r.flags
        return d

    by_age = sorted(recs, key=lambda r: r.age_days, reverse=True)
    # ① overdue レーン（due 超過 — due 昇順で古い締切を上に）。
    by_due = sorted(
        [r for r in recs if Flag.OVERDUE_TODO.value in r.flags or Flag.OVERDUE_GRADUATE.value in r.flags],
        key=lambda r: r.due or "",
    )
    overdue = [slim(r) for r in by_due]
    # ② 今すぐ判断（主役）＝陳腐化で要判断のもの。
    queue = [slim(r) for r in by_age if Flag.NEEDS_DECISION.value in r.flags]
    # ③ 落ち着いて確認＝保留・進行中（要判断に出ていないもの）。
    fold = [
        slim(r) for r in by_age
        if r.state in ("pending", "in-progress", "active") and Flag.NEEDS_DECISION.value not in r.flags
    ]
    # 要修正（ラベル欠落等）も拾えるようにキューの末尾へ。
    queue += [slim(r) for r in by_age if Flag.NEEDS_FIX.value in r.flags and Flag.NEEDS_DECISION.value not in r.flags]
    # archive 可能（完了・廃止）＝一括移送の対象。実行前に中身を確認できるよう一覧で持つ。
    archivable = [slim(r) for r in by_age if r.auto_movable and r.archivable]

    inprogress = sum(1 for r in recs if r.state in ("in-progress", "active"))
    done = sum(1 for r in recs if r.state == "done")

    return {
        "counts": idx.counts,
        "inprogress": inprogress,
        "done": done,
        "queue": queue,
        "fold": fold,
        "archivable": archivable,
        "overdue": overdue,
        "queue_by_project": _by_project(queue),
        "fold_by_project": _by_project(fold),
        "health": _health(recs),
        "root": str(config.roots[0]) if config.roots else "",
        **_inject_state(recs),
    }


def _inject_state(recs) -> dict:
    """各プロジェクトの注入状態（manifest 由来）と global 注入済み agent を組み立てる。"""
    injected = {it["path"]: it for it in list_injected()}
    projects: dict[str, dict] = {}
    for r in recs:
        root = r.project_root
        if root not in projects:
            info = injected.get(root)
            projects[root] = {
                "name": r.project, "root": root,
                "injected": info is not None, "preset": (info or {}).get("preset"),
            }
    from ..presets import DEFAULT_PRESET, PRESETS
    global_agents = {it.get("agent") for it in injected.values() if it.get("scope") == "global"}
    return {
        "projects": sorted(projects.values(), key=lambda x: x["name"]),
        "global_claude": "claude" in global_agents,
        "global_codex": "codex" in global_agents,
        "presets": list(PRESETS),
        "default_preset": DEFAULT_PRESET,
    }


def _health(recs) -> list[dict]:
    by: dict[str, list[int]] = {}
    for r in recs:
        by.setdefault(r.project, []).append(r.age_days)
    maxage = max((max(v) for v in by.values()), default=1) or 1
    rows = []
    for proj, ages in by.items():
        oldest = max(ages)
        level = "hi" if oldest >= 90 else ("mid" if oldest >= 30 else "lo")
        rows.append({
            "project": proj, "oldest": oldest,
            "pct": max(6, int(oldest / maxage * 100)), "level": level,
        })
    rows.sort(key=lambda x: x["oldest"], reverse=True)
    return rows


def _group(result: ScanResult, sm, lang: str, filter: str = "all") -> list[dict]:
    """state ごとにレコードを束ねた表示用グループを返す。"""
    records = result.records
    if filter == "needs":
        records = [
            r for r in records
            if Flag.NEEDS_DECISION.value in r.flags or Flag.NEEDS_FIX.value in r.flags or r.state == "pending"
        ]
    elif filter == "watching":
        records = [r for r in records if r.state == "watching"]
    elif filter == "archivable":
        records = [r for r in records if r.auto_movable and r.archivable]

    by_state: dict[str, list] = {}
    for r in records:
        by_state.setdefault(r.state or "unknown", []).append(r)

    groups: list[dict] = []
    for key, recs in by_state.items():
        st = sm.by_key(key) if sm else None
        label = f"[{st.label(lang)}]" if st else "[?]"
        groups.append({
            "key": key,
            "label": label,
            "records": sorted(recs, key=lambda r: r.age_days, reverse=True),
        })
    groups.sort(key=lambda g: g["key"])
    return groups
