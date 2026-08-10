"""サーバーのセキュリティ制約。

- バインドは 127.0.0.1 固定・トークン必須（呼び出し側で検証）。
- 閲覧/プレビュー/起動できる対象はスキャンルート配下のパスに限定。
  realpath 解決後にルート配下か検証し、外れたら拒否（任意パス起動・`..` 脱出を防ぐ）。
- テキスト（.md）のみ。
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request


TOKEN_COOKIE = "docsweep_token"
TOKEN_HEADER = "x-docsweep-token"


def check_token(
    request: Request,
    token_q: str | None,
    *,
    status_code: int = 401,
    detail: str = "token required",
) -> None:
    """Cookie / header / query のいずれかに正しい token があれば認証する。

    hybrid 移行中は不正な上位候補があっても下位候補を試す。たとえば古い Cookie が
    残っていても、正しい初回 URL token で再認証できる。
    """
    expected = request.app.state.docsweep.token
    candidates = (
        request.cookies.get(TOKEN_COOKIE),
        request.headers.get(TOKEN_HEADER),
        token_q,
    )
    if any(
        candidate is not None and secrets.compare_digest(candidate, expected)
        for candidate in candidates
    ):
        return
    raise HTTPException(status_code=status_code, detail=detail)


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_under_roots(raw_path: str, roots: list[Path]) -> Path | None:
    """raw_path を realpath 解決し、いずれかの root 配下なら解決済み Path を返す。

    範囲外・解決不能・.md 以外は None（呼び出し側で 403/400 にする）。
    Windows の大小文字差は os.path.normcase で吸収する。
    """
    if not raw_path:
        return None
    try:
        resolved = Path(os.path.realpath(raw_path))
    except OSError:
        return None
    if resolved.suffix.lower() != ".md":
        return None
    for root in roots:
        root_resolved = Path(os.path.realpath(str(root)))
        # 大小文字非依存の境界判定（Windows 対応）。
        try:
            rc = Path(os.path.normcase(str(resolved)))
            rr = Path(os.path.normcase(str(root_resolved)))
        except ValueError:
            continue
        if _is_under(rc, rr):
            return resolved
    return None
