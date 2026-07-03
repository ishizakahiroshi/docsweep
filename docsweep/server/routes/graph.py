"""C5: Web 側 graph — plan/bugfix/pending の関係性ネットワーク。

CLI ``docsweep graph --json`` と同じ ``build_graph`` を呼ぶ。描画は cytoscape.js を
CDN から読み込んで HTML 内で完結（追加ビルド不要）。
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ...graph import build_graph

_DIR = Path(__file__).parent.parent
TEMPLATES = Jinja2Templates(directory=str(_DIR / "templates"))

router = APIRouter()


def _check_token(request: Request, token_q: str | None) -> None:
    state = request.app.state.docsweep
    supplied = token_q or request.headers.get("x-docsweep-token")
    if not supplied or not secrets.compare_digest(supplied, state.token):
        raise HTTPException(status_code=401, detail="token required")


@router.get("/api/graph")
def api_graph(
    request: Request,
    token: str | None = Query(default=None),
    project: str | None = Query(default=None),
) -> JSONResponse:
    _check_token(request, token)
    state = request.app.state.docsweep
    g = build_graph(state.config, project=project)
    return JSONResponse(g.to_dict())


@router.get("/graph", response_class=HTMLResponse)
def page_graph(
    request: Request,
    token: str | None = Query(default=None),
    project: str | None = Query(default=None),
) -> HTMLResponse:
    _check_token(request, token)
    state = request.app.state.docsweep
    g = build_graph(state.config, project=project)
    from ..i18n import get_messages, resolve_lang

    resolved_lang = resolve_lang(request)
    return TEMPLATES.TemplateResponse(
        request, "graph.html",
        {"graph": g.to_dict(), "token": state.token,
         "lang": resolved_lang, "T": get_messages(resolved_lang)},
    )
