"""FastAPI アプリ本体。htmx でプレビュー主・既定アプリ起動従の UI を配信する。"""

from __future__ import annotations

import secrets
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..config import Config
from ..engine import ScanResult, apply_action, auto_sweep, run_scan
from ..aggregate_index import write_index
from ..inject import (
    eject,
    eject_global,
    inject,
    inject_global,
    list_injected,
    preview_global,
    preview_inject,
)
from .routes import board as board_routes
from .routes import brief as brief_routes
from .routes import cards as cards_routes
from .routes import capture as capture_routes
from .routes import cross as cross_routes
from .routes import graph as graph_routes
from .routes import resurrect as resurrect_routes
from .sanitize import sanitize_html
from .security import resolve_under_roots

_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(_DIR / "templates"))


class ServerState:
    def __init__(self, config: Config, token: str):
        self.config = config
        self.token = token
        # uvicorn.Server インスタンス。cmd_serve で起動した実体だけが代入する。
        # /api/shutdown はこれを参照して should_exit=True で graceful 停止する。
        # テスト等でアプリ単体生成された場合は None のまま（停止不可で 503 を返す）。
        self.server = None


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

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        """C3 (bloat-mitigation): shutdown 時に WAL を TRUNCATE checkpoint して -wal を縮める。

        Web UI を長時間起動した後の -wal 肥大を抑える。リクエスト毎の checkpoint は重いので
        shutdown 時の 1 回だけに絞る（通常は autocheckpoint=1000 ページ で十分・最終回収のみ）。
        """
        yield
        try:
            from .. import index as db
            with db.connect() as conn:
                db.checkpoint_truncate(conn)
        except Exception:
            # shutdown 経路は失敗しても致命ではないので握り潰す（プロセス停止を妨げない）。
            pass

    app = FastAPI(title="docsweep", docs_url=None, redoc_url=None, lifespan=_lifespan)
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

    # 旧 dashboard（/, /list, /fragment）は plan_consolidate-to-board で廃止し、
    # テンプレ・ヘルパも plan_legacy-stack-retirement で物理撤去済み（注入・health は看板に統合）。

    @app.get("/")
    def root_redirect(token: str = Query(default="")):
        """/ は看板へリダイレクト（旧 dashboard は廃止）。"""
        from fastapi.responses import RedirectResponse

        target = f"/board?token={token}" if token else "/board"
        return RedirectResponse(url=target, status_code=302)

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

    @app.post("/api/shutdown")
    def api_shutdown(request: Request, token: str = Form(default="")):
        """画面右上 ⏻ ボタン用。uvicorn を graceful 停止する。
        cmd_serve から起動した実体だけが state.server を持つ。テスト等で
        単体生成された FastAPI では server が無いため 503 を返す。"""
        _check_token(request, token)
        if state.server is None:
            raise HTTPException(status_code=503, detail="server is not stoppable in this context")
        # uvicorn.Server はメインループ内でこのフラグを毎周見てから抜ける。
        # 走行中のリクエスト（このレスポンス含む）は完了するまで待たれる。
        state.server.should_exit = True
        return JSONResponse({"shutting_down": True})

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

    # v0.1.0 第 2 段階の主役 UI: 看板（カンバン）ボード。
    # ルータ側は ``request.app.state.docsweep`` 経由で同じ config/token を参照する。
    app.include_router(board_routes.router)
    app.include_router(brief_routes.router)
    app.include_router(cards_routes.router)
    app.include_router(capture_routes.router)
    app.include_router(cross_routes.router)
    app.include_router(graph_routes.router)
    app.include_router(resurrect_routes.router)

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
