"""C2: Web 側 capture — 会話を貼り付けて draft 抽出 / 採用。

エンドポイント:
- ``GET /capture`` — HTML（貼り付け UI）
- ``POST /api/capture/extract`` — 抽出 (JSON 返却)
- ``POST /api/capture/save`` — 採用された draft を保存
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ...capture import extract_drafts, save_drafts
from ...capture.models import Draft
from ...capture.service import CaptureScopeError
from ...work_queue import resolve_work_target
from ..security import check_token

_DIR = Path(__file__).parent.parent
TEMPLATES = Jinja2Templates(directory=str(_DIR / "templates"))

router = APIRouter()


@router.get("/capture", response_class=HTMLResponse)
def page_capture(
    request: Request,
    token: str | None = Query(default=None),
) -> HTMLResponse:
    check_token(request, token)
    state = request.app.state.docsweep
    from ..i18n import get_messages, resolve_lang

    resolved_lang = resolve_lang(request)
    return TEMPLATES.TemplateResponse(
        request, "capture.html",
        {"token": state.token, "lang": resolved_lang, "T": get_messages(resolved_lang)},
    )


@router.post("/api/capture/extract")
def api_capture_extract(
    request: Request,
    payload: dict = Body(default_factory=dict),
    token: str | None = Query(default=None),
) -> JSONResponse:
    check_token(request, token)
    state = request.app.state.docsweep
    text = str(payload.get("text") or "")
    project = payload.get("project") or None
    use_llm = bool(payload.get("use_llm", False))
    max_drafts = int(payload.get("max_drafts", 5))
    allow_sensitive = bool(payload.get("allow_sensitive", False))

    if not text.strip():
        raise HTTPException(status_code=400, detail="text is empty")

    try:
        drafts = extract_drafts(
            text, config=state.config, project=project,
            max_drafts=max_drafts, use_llm=use_llm,
            allow_sensitive=allow_sensitive,
        )
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse({
        "drafts": [d.to_dict() for d in drafts],
        "count": len(drafts),
    })


@router.post("/api/capture/save")
def api_capture_save(
    request: Request,
    payload: dict = Body(default_factory=dict),
    token: str | None = Query(default=None),
) -> JSONResponse:
    check_token(request, token)
    state = request.app.state.docsweep
    drafts_raw = payload.get("drafts") or []
    project = payload.get("project") or None
    out_dir = payload.get("out_dir") or None
    allow_sensitive = bool(payload.get("allow_sensitive", False))

    drafts: list[Draft] = []
    for d in drafts_raw:
        drafts.append(Draft(
            id=d.get("id", ""),
            kind=d.get("kind", "plan"),
            title=d.get("title", ""),
            body=d.get("body", ""),
            suggested_filename=d.get("suggested_filename", "draft.md"),
            source_hint=d.get("source_hint", ""),
            project=d.get("project") or project,
            tags=list(d.get("tags") or []),
        ))

    try:
        project_root, target = resolve_work_target(
            state.config,
            project=project,
            explicit_dir=Path(out_dir) if out_dir else None,
        )
        saved = save_drafts(
            drafts,
            config=state.config,
            target_dir=target,
            project_dir=project_root,
            allow_sensitive=allow_sensitive,
        )
    except (CaptureScopeError, PermissionError, ValueError) as e:
        # 400 に落とす（403 でも良いが、入力の不正として 400 を選ぶ）。
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse({
        "saved": [str(p) for p in saved],
        "count": len(saved),
    })
