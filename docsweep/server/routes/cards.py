"""カード書き込み口（ラベル / 期日 / 本文 / archive）。

全エンドポイント共通の不変条件:
- トークン検証 + ``realpath`` 解決 + スコープ境界チェック
- 書き込みは必ず ``docsweep.services.*`` 経由（Web UI に新しい特権を持たせない）
- 物理削除の口は持たない（最悪 archive 止まり・v0.1.0 §C4 不変条件）
- mtime 競合は 409 で返す（Web UI は警告ダイアログを出す）
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse

from ...atomic import ConflictError
from ...services.archive import archive_done
from ...services.content import ContentValidationError, update_content
from ...services.due import DueParseError, update_due
from ...services.status import StatusValidationError, update_status
from ..security import resolve_under_roots

router = APIRouter()


def _check_token(request: Request, token_q: str | None) -> None:
    state = request.app.state.docsweep
    supplied = token_q or request.headers.get("x-docsweep-token")
    if not supplied or not secrets.compare_digest(supplied, state.token):
        raise HTTPException(status_code=403, detail="invalid or missing token")


def _resolve(request: Request, raw_path: str) -> Path:
    cfg = request.app.state.docsweep.config
    resolved = resolve_under_roots(raw_path, cfg.roots)
    if resolved is None:
        raise HTTPException(status_code=403, detail="path outside scan roots")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return resolved


def _project_root_for(path: Path, roots: list[Path]) -> Path:
    """書き込み対象 path から ``.docsweep/state.json`` を置くプロジェクト境界を辿る。

    ``atomic._project_root_for`` と同じ判定だが、最終フォールバックを「スキャンルート」に
    する（スキャンルートが見つかる限り state.json はそこに置けるため）。
    """
    cur = path.parent.resolve()
    while True:
        for marker in (".docsweep.yaml", ".git", "pyproject.toml", "package.json"):
            if (cur / marker).exists():
                return cur
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    # フォールバック: パスを含む最初のスキャンルート。
    for root in roots:
        try:
            path.resolve().relative_to(Path(root).resolve())
            return Path(root).resolve()
        except ValueError:
            continue
    return path.parent.resolve()


def _file_type(request: Request, name: str) -> str | None:
    cfg = request.app.state.docsweep.config
    td = cfg.match_type(name)
    return td.name if td else None


def _parse_mtime(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="expected_mtime must be float") from e


@router.post("/api/cards/status")
def post_status(
    request: Request,
    token: str = Form(default=""),
    path: str = Form(...),
    new_state: str = Form(...),
    expected_mtime: str | None = Form(default=None),
):
    """H1 ラベル書き換え。`[完了]` / `[廃止]` 指定時は続けて archive 移送。"""
    _check_token(request, token)
    resolved = _resolve(request, path)
    cfg = request.app.state.docsweep.config
    project_root = _project_root_for(resolved, cfg.roots)
    expected = _parse_mtime(expected_mtime)
    file_type = _file_type(request, resolved.name)
    try:
        res = update_status(
            resolved,
            new_state,
            project_root=project_root,
            config=cfg,
            file_type=file_type,
            expected_mtime=expected,
        )
    except StatusValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    payload = res.to_dict()
    if res.archive_triggered:
        # 不変条件: `[完了]` / `[廃止]` のみ archive 移送。ここで続けて呼ぶことで
        # Web UI は「ラベル変更 → 移送」を 1 操作で済ませられる。
        arc = archive_done(config=cfg, paths=[resolved.as_posix()])
        payload["archive"] = arc.to_dict()
    return JSONResponse(payload)


@router.post("/api/cards/due")
def post_due(
    request: Request,
    token: str = Form(default=""),
    path: str = Form(...),
    new_due: str = Form(...),
    reason: str | None = Form(default=None),
    expected_mtime: str | None = Form(default=None),
):
    """frontmatter `due:` 書き換え + postpone_count 自動インクリメント。"""
    _check_token(request, token)
    resolved = _resolve(request, path)
    cfg = request.app.state.docsweep.config
    project_root = _project_root_for(resolved, cfg.roots)
    expected = _parse_mtime(expected_mtime)
    try:
        res = update_due(
            resolved,
            new_due,
            project_root=project_root,
            reason=reason,
            expected_mtime=expected,
            warn_threshold=cfg.due_warn_threshold,
            alert_threshold=cfg.due_alert_threshold,
        )
    except DueParseError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return JSONResponse(res.to_dict())


@router.post("/api/cards/content")
def post_content(
    request: Request,
    token: str = Form(default=""),
    path: str = Form(...),
    content: str = Form(default=""),
    expected_mtime: str | None = Form(default=None),
):
    """本文全置換（楽観ロック・mtime 不一致は 409）。"""
    _check_token(request, token)
    resolved = _resolve(request, path)
    expected = _parse_mtime(expected_mtime)
    try:
        res = update_content(resolved, content, expected_mtime=expected)
    except ContentValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return JSONResponse(res.to_dict())


@router.post("/api/cards/archive")
def post_archive(
    request: Request,
    token: str = Form(default=""),
    path: str = Form(...),
    dry_run: bool = Form(default=False),
):
    """`[完了]` / `[廃止]` 確定済みファイルを archive へ移送する閉じた口。"""
    _check_token(request, token)
    resolved = _resolve(request, path)
    cfg = request.app.state.docsweep.config
    res = archive_done(config=cfg, paths=[resolved.as_posix()], dry_run=dry_run)
    return JSONResponse(res.to_dict())
