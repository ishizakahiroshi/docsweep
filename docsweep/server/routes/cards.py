"""カード書き込み口（ラベル / 期日 / 本文 / archive）。

全エンドポイント共通の不変条件:
- トークン検証 + ``realpath`` 解決 + スコープ境界チェック
- 書き込みは必ず ``docsweep.services.*`` 経由（Web UI に新しい特権を持たせない）
- 物理削除の口は持たない（最悪 archive 止まり・v0.1.0 §C4 不変条件）
- mtime 競合は 409 で返す（Web UI は警告ダイアログを出す）
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ...atomic import ConflictError
from ...services.archive import archive_done, undo_last_batch
from ...services.content import ContentValidationError, update_content
from ...services.due import DueParseError, update_due
from ...services.frontmatter import (
    ALLOWED_FIELDS,
    LIST_FIELDS,
    FrontmatterValidationError,
    current_owner,
    update_frontmatter_field,
)
from ...services.status import StatusValidationError, update_status
from ..security import check_token, resolve_under_roots

router = APIRouter()


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
    check_token(request, token, status_code=403, detail="invalid or missing token")
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
    check_token(request, token, status_code=403, detail="invalid or missing token")
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
    check_token(request, token, status_code=403, detail="invalid or missing token")
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
    check_token(request, token, status_code=403, detail="invalid or missing token")
    resolved = _resolve(request, path)
    cfg = request.app.state.docsweep.config
    res = archive_done(config=cfg, paths=[resolved.as_posix()], dry_run=dry_run)
    return JSONResponse(res.to_dict())


# ------------------------------------------------------------------
# bulk endpoints（plan_kanban-bulk-edit で追加）
# - 既存 services を for ループで呼ぶ薄いラッパ
# - 1 件失敗しても他は続行（部分成功）
# - 失敗は failed[] に {path, error, kind} で集約
# - スコープ外パス / mtime conflict / validation エラーは個別に振り分け
# ------------------------------------------------------------------


def _try_resolve(request: Request, raw_path: str) -> tuple[Path | None, dict | None]:
    """スコープ境界チェック。OK で (Path, None)、NG で (None, failed-entry dict)。"""
    cfg = request.app.state.docsweep.config
    resolved = resolve_under_roots(raw_path, cfg.roots)
    if resolved is None or not resolved.is_file():
        return None, {
            "path": raw_path,
            "error": "path outside scan roots or not a file",
            "kind": "path_scope",
        }
    return resolved, None


@router.post("/api/cards/bulk/due")
def post_bulk_due(
    request: Request,
    token: str = Form(default=""),
    paths: list[str] = Form(...),
    new_due: str = Form(...),
    reason: str | None = Form(default=None),
):
    """複数ファイルの frontmatter ``due:`` を一括書き換え。

    ``new_due`` の parse は全件共通なので、ここで失敗したら 400 で即返す。
    各 path の mtime conflict / validation 失敗は個別に ``failed[]`` へ。
    """
    check_token(request, token, status_code=403, detail="invalid or missing token")
    cfg = request.app.state.docsweep.config
    ok: list[dict] = []
    failed: list[dict] = []
    for raw in paths:
        resolved, err = _try_resolve(request, raw)
        if err is not None:
            failed.append(err)
            continue
        project_root = _project_root_for(resolved, cfg.roots)
        try:
            res = update_due(
                resolved, new_due,
                project_root=project_root, reason=reason,
                warn_threshold=cfg.due_warn_threshold,
                alert_threshold=cfg.due_alert_threshold,
            )
            ok.append(res.to_dict())
        except DueParseError as e:
            # new_due 自体が parse 不能なら全件失敗なので 400 で早期 return。
            raise HTTPException(status_code=400, detail=str(e)) from e
        except ConflictError as e:
            failed.append({
                "path": resolved.as_posix(), "error": str(e), "kind": "conflict",
                "expected_mtime": e.expected, "actual_mtime": e.actual,
            })
        except (OSError, ValueError) as e:
            failed.append({"path": resolved.as_posix(), "error": str(e), "kind": "internal"})
    return JSONResponse({"ok": ok, "failed": failed})


@router.post("/api/cards/bulk/status")
def post_bulk_status(
    request: Request,
    token: str = Form(default=""),
    paths: list[str] = Form(...),
    new_state: str = Form(...),
):
    """複数ファイルの H1 ラベルを一括書き換え。``[完了]`` / ``[廃止]`` 指定で archive 移送まで一気通貫。

    各 path のファイル種別×ラベル組み合わせ違反は services 層が validation で弾く
    → 個別に ``failed[]`` へ振り分け（bugfix は [計画] 不可、pending は [様子見]/[実行中] 不可など）。
    """
    check_token(request, token, status_code=403, detail="invalid or missing token")
    cfg = request.app.state.docsweep.config
    ok: list[dict] = []
    failed: list[dict] = []
    archive_targets: list[str] = []
    for raw in paths:
        resolved, err = _try_resolve(request, raw)
        if err is not None:
            failed.append(err)
            continue
        project_root = _project_root_for(resolved, cfg.roots)
        file_type = _file_type(request, resolved.name)
        try:
            res = update_status(
                resolved, new_state,
                project_root=project_root, config=cfg,
                file_type=file_type,
            )
            ok.append(res.to_dict())
            if res.archive_triggered:
                archive_targets.append(resolved.as_posix())
        except StatusValidationError as e:
            failed.append({"path": resolved.as_posix(), "error": str(e), "kind": "validation"})
        except ConflictError as e:
            failed.append({
                "path": resolved.as_posix(), "error": str(e), "kind": "conflict",
                "expected_mtime": e.expected, "actual_mtime": e.actual,
            })
        except (OSError, ValueError) as e:
            failed.append({"path": resolved.as_posix(), "error": str(e), "kind": "internal"})
    archive_result = None
    if archive_targets:
        # archive_triggered のものをまとめて移送（単数 API と同じ閉じた口を通す）
        archive_result = archive_done(config=cfg, paths=archive_targets).to_dict()
    return JSONResponse({"ok": ok, "failed": failed, "archive": archive_result})


@router.post("/api/cards/frontmatter")
def post_frontmatter(
    request: Request,
    token: str = Form(default=""),
    path: str = Form(...),
    field: str = Form(...),
    value: str = Form(default=""),
    expected_mtime: str | None = Form(default=None),
):
    """OKF frontmatter フィールド（tags / owner / related / review_status / last_reviewed）の単体書き換え。

    list 系（tags / related）は ``value`` をカンマ区切りで受ける（空文字 → 空 list）。
    スカラ系は ``value`` をそのまま書き込む（空文字 → 値を空にして行は残す）。
    本文・H1 ラベル・他フィールドは触らない（C4 plan の後方互換 100% を担保）。
    """
    check_token(request, token, status_code=403, detail="invalid or missing token")
    resolved = _resolve(request, path)
    expected = _parse_mtime(expected_mtime)
    if field not in ALLOWED_FIELDS:
        raise HTTPException(status_code=400, detail=f"unknown field: {field}")
    if field in LIST_FIELDS:
        # カンマ区切りで受ける。空白だけの要素は捨てる。
        new_value: list[str] | str = [
            s.strip() for s in (value or "").split(",") if s.strip()
        ]
    else:
        new_value = value
    try:
        res = update_frontmatter_field(
            resolved, field, new_value, expected_mtime=expected,
        )
    except FrontmatterValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return JSONResponse(res.to_dict())


@router.get("/api/user/current")
def get_current_user(request: Request, token: str = Query(default="")):
    """claim/unclaim ボタン用に「現在ユーザー名」を返す。

    解決順は ``services.frontmatter.current_owner`` 参照（git config → OS ログイン）。
    C2 で ``docsweep config user.name`` が来たら本ハンドラもそちらを最優先にする。
    """
    check_token(request, token, status_code=403, detail="invalid or missing token")
    cfg = request.app.state.docsweep.config
    cwd = Path(cfg.roots[0]) if cfg.roots else None
    return JSONResponse({"name": current_owner(cwd=cwd)})


@router.post("/api/cards/claim")
def post_claim(
    request: Request,
    token: str = Form(default=""),
    path: str = Form(...),
    unclaim: bool = Form(default=False),
    expected_mtime: str | None = Form(default=None),
):
    """frontmatter の owner を現在ユーザーで書き換える。``unclaim=true`` で値を空にする。

    C2 の `docsweep claim` と同じファイルを書き換える（Web UI と CLI で動作を揃える）。
    git 未導入環境でも OS ログイン名でフォールバックして動く。
    """
    check_token(request, token, status_code=403, detail="invalid or missing token")
    resolved = _resolve(request, path)
    expected = _parse_mtime(expected_mtime)
    cfg = request.app.state.docsweep.config
    cwd = Path(cfg.roots[0]) if cfg.roots else None
    owner = "" if unclaim else current_owner(cwd=cwd)
    try:
        res = update_frontmatter_field(
            resolved, "owner", owner, expected_mtime=expected,
        )
    except FrontmatterValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    payload = res.to_dict()
    payload["owner"] = owner
    return JSONResponse(payload)


@router.post("/api/cards/undo")
def post_undo(
    request: Request,
    token: str = Form(default=""),
):
    """直近の archive バッチを取り消す（archive 配下から元の場所へ復元）。

    Undo 対象は最新の未復元 batch_id のみ。restore エントリが moves.jsonl に追記され、
    二重 Undo を防ぐ。
    """
    check_token(request, token, status_code=403, detail="invalid or missing token")
    cfg = request.app.state.docsweep.config
    res = undo_last_batch(config=cfg)
    return JSONResponse(res.to_dict())


@router.post("/api/cards/bulk/archive")
def post_bulk_archive(
    request: Request,
    token: str = Form(default=""),
    paths: list[str] = Form(...),
    dry_run: bool = Form(default=False),
):
    """複数ファイルを archive へ一括移送。``[完了]`` / ``[廃止]`` 以外は services 層が拒否。

    スコープ外パスは ``failed_validation[]`` に分けて返す（services の skipped[] とは別枠）。
    """
    check_token(request, token, status_code=403, detail="invalid or missing token")
    cfg = request.app.state.docsweep.config
    valid: list[str] = []
    failed_validation: list[dict] = []
    for raw in paths:
        resolved, err = _try_resolve(request, raw)
        if err is not None:
            failed_validation.append(err)
            continue
        valid.append(resolved.as_posix())
    res = archive_done(config=cfg, paths=valid, dry_run=dry_run)
    out = res.to_dict()
    out["failed_validation"] = failed_validation
    return JSONResponse(out)
