"""C6: Web 側 resurrect — archive 蘇生候補の一覧画面。

CLI ``docsweep resurrect`` と同じ ``find_candidates`` を呼ぶ。
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ...resurrect import find_candidates

_DIR = Path(__file__).parent.parent
TEMPLATES = Jinja2Templates(directory=str(_DIR / "templates"))

router = APIRouter()


def _check_token(request: Request, token_q: str | None) -> None:
    state = request.app.state.docsweep
    supplied = token_q or request.headers.get("x-docsweep-token")
    if not supplied or not secrets.compare_digest(supplied, state.token):
        raise HTTPException(status_code=401, detail="token required")


@router.get("/api/resurrect")
def api_resurrect(
    request: Request,
    token: str | None = Query(default=None),
    threshold: float = Query(default=0.5),
    no_embedding: bool = Query(default=False),
    top_k: int = Query(default=1, alias="topK"),
) -> JSONResponse:
    _check_token(request, token)
    state = request.app.state.docsweep
    result = find_candidates(
        state.config,
        threshold=threshold,
        use_embedding=not no_embedding,
        top_k_per_archive=top_k,
    )
    return JSONResponse(result.to_dict())


@router.get("/resurrect", response_class=HTMLResponse)
def page_resurrect(
    request: Request,
    token: str | None = Query(default=None),
    threshold: float = Query(default=0.5),
    no_embedding: bool = Query(default=False),
) -> HTMLResponse:
    _check_token(request, token)
    state = request.app.state.docsweep
    result = find_candidates(
        state.config, threshold=threshold, use_embedding=not no_embedding,
    )
    from ..i18n import get_messages, resolve_lang

    resolved_lang = resolve_lang(request)
    return TEMPLATES.TemplateResponse(
        request, "resurrect.html",
        {"result": result.to_dict(), "token": state.token,
         "lang": resolved_lang, "T": get_messages(resolved_lang)},
    )
