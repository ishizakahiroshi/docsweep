"""C4: Web 側 cross — 全プロジェクト俯瞰。

CLI ``docsweep cross`` と同じ ``build_cross`` を呼ぶ。
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ...cross import build_cross

_DIR = Path(__file__).parent.parent
TEMPLATES = Jinja2Templates(directory=str(_DIR / "templates"))

router = APIRouter()


def _check_token(request: Request, token_q: str | None) -> None:
    state = request.app.state.docsweep
    supplied = token_q or request.headers.get("x-docsweep-token")
    if not supplied or not secrets.compare_digest(supplied, state.token):
        raise HTTPException(status_code=401, detail="token required")


def _parse_projects(arg: str | None) -> list[str] | None:
    if not arg:
        return None
    out = [p.strip() for p in arg.split(",") if p.strip()]
    return out or None


@router.get("/api/cross")
def api_cross(
    request: Request,
    token: str | None = Query(default=None),
    project: str | None = Query(default=None),
) -> JSONResponse:
    _check_token(request, token)
    state = request.app.state.docsweep
    result = build_cross(state.config, projects=_parse_projects(project))
    return JSONResponse(result.to_dict())


@router.get("/cross", response_class=HTMLResponse)
def page_cross(
    request: Request,
    token: str | None = Query(default=None),
    project: str | None = Query(default=None),
) -> HTMLResponse:
    _check_token(request, token)
    state = request.app.state.docsweep
    result = build_cross(state.config, projects=_parse_projects(project))
    return TEMPLATES.TemplateResponse(
        request,
        "cross.html",
        {"cross": result.to_dict(), "token": state.token},
    )
