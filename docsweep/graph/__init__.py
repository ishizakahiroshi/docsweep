"""C5: graph — plan / bugfix / pending の関係性ネットワークを JSON で返す。

Web /graph で force-directed graph として描画する想定。CLI からも JSON で取れる。
"""

from .service import GraphData, build_graph

__all__ = ["GraphData", "build_graph"]
