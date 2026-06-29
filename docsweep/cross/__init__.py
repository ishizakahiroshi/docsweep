"""C4 (wings): cross — 全プロジェクト束ねて「今日の 1 個」を 1 件断定する俯瞰の双子。

brief は「プロジェクトごとに 1 件」、cross は「複数プロジェクトの中から 1 件」を出す。
凍結予備軍（長期間動いていない open）も一覧にして、archive 候補をユーザーに提案する。

主役は :func:`service.build_cross`。CLI/Web/MCP はすべてこの関数を経由する。
"""

from .service import CrossResult, build_cross

__all__ = ["CrossResult", "build_cross"]
