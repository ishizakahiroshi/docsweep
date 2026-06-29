"""C3 (wings): brief — 「今日の 1 個」を断定する出口の双子（CLI / Web 共通基盤）。

主役は :func:`service.build_brief` と :func:`score.score_record`。CLI/Web/MCP は全部この
2 関数を経由する（再実装しない）。
"""

from .service import BriefResult, build_brief

__all__ = ["BriefResult", "build_brief"]
