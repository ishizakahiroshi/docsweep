"""C3: Web 側 brief — 朝の入口を 1 画面で。

CLI ``docsweep brief`` と同じ ``build_brief`` を呼ぶ（再実装しない）。

エンドポイント:
- ``GET /brief`` — HTML（フル画面）
- ``GET /api/brief`` — JSON
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ...brief import build_brief
from ..security import check_token

_DIR = Path(__file__).parent.parent
TEMPLATES = Jinja2Templates(directory=str(_DIR / "templates"))

router = APIRouter()


@router.get("/api/brief")
def api_brief(
    request: Request,
    token: str | None = Query(default=None),
    project: str | None = Query(default=None),
    all_projects: bool = Query(default=False, alias="all"),
) -> JSONResponse:
    """JSON 版 brief。CLI と同じ ``BriefResult.to_dict()`` を返す。"""
    check_token(request, token)
    state = request.app.state.docsweep
    result = build_brief(state.config, project=project, all_projects=all_projects)
    return JSONResponse(result.to_dict())


@router.get("/brief", response_class=HTMLResponse)
def page_brief(
    request: Request,
    token: str | None = Query(default=None),
    project: str | None = Query(default=None),
    all_projects: bool = Query(default=False, alias="all"),
) -> HTMLResponse:
    """HTML 版 brief（朝に開く想定のホーム画面）。"""
    check_token(request, token)
    state = request.app.state.docsweep
    result = build_brief(state.config, project=project, all_projects=all_projects)
    from ..i18n import get_messages, resolve_lang

    resolved_lang = resolve_lang(request)
    return TEMPLATES.TemplateResponse(
        request,
        "brief.html",
        {
            "brief": result.to_dict(),
            "token": state.token,
            "lang": resolved_lang,
            "T": get_messages(resolved_lang),
        },
    )
