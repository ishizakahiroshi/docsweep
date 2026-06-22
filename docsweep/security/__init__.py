"""書き込み系（MCP / Web）の共通セキュリティ層。

不変条件:
- スキャンルート配下のみ書き込み可（realpath 解決後にスコープ境界を検証）
- ``..`` を含むパスは拒否
- ``.md`` ファイルのみ書き込み可

詳細は :mod:`docsweep.security.path` を参照。
"""

from .path import PathScopeError, resolve_writable_md

__all__ = ["PathScopeError", "resolve_writable_md"]
