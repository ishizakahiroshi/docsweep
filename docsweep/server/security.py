"""サーバーのセキュリティ制約。

- バインドは 127.0.0.1 固定・トークン必須（呼び出し側で検証）。
- 閲覧/プレビュー/起動できる対象はスキャンルート配下のパスに限定。
  realpath 解決後にルート配下か検証し、外れたら拒否（任意パス起動・`..` 脱出を防ぐ）。
- テキスト（.md）のみ。
"""

from __future__ import annotations

import os
from pathlib import Path


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
