"""関係性グラフのノード・エッジ生成。

入力: ``scan_records(config)`` から得た FileRecord 群
出力: ``{nodes: [...], edges: [...]}`` の JSON 構造
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import Config
from ..engine import scan_records


@dataclass
class GraphNode:
    id: str        # rel path（プロジェクト内で一意）
    label: str     # basename
    project: str
    type: str | None
    state: str | None
    state_label: str | None
    tags: list[str] = field(default_factory=list)
    isolated: bool = False  # related が 0 かつ被参照も 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GraphEdge:
    source: str  # GraphNode.id
    target: str
    resolved: bool  # related の参照先が実在ノードに解決できたか

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GraphData:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }


def _record_id(path: str) -> str:
    return Path(path).name


def build_graph(config: Config, *, project: str | None = None) -> GraphData:
    """全 md を node、frontmatter ``related`` を edge にしたグラフを返す。

    Args:
        config: ロード済み Config
        project: プロジェクト名でフィルタ（None で全体）

    Returns:
        ``GraphData``。
    """
    records = scan_records(config, project=project)
    name_to_id: dict[str, str] = {}
    for r in records:
        name_to_id[Path(r.path).name] = _record_id(r.path)

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    referenced: set[str] = set()
    outgoing_count: dict[str, int] = {}

    for r in records:
        nid = _record_id(r.path)
        for ref in r.related or []:
            ref_name = Path(ref).name
            target = name_to_id.get(ref_name)
            resolved = target is not None
            edges.append(GraphEdge(
                source=nid,
                target=target or ref_name,
                resolved=resolved,
            ))
            if resolved:
                referenced.add(target)
            outgoing_count[nid] = outgoing_count.get(nid, 0) + 1

    for r in records:
        nid = _record_id(r.path)
        is_isolated = (outgoing_count.get(nid, 0) == 0) and (nid not in referenced)
        nodes.append(GraphNode(
            id=nid,
            label=Path(r.path).name,
            project=r.project,
            type=r.type,
            state=r.state,
            state_label=r.state_label,
            tags=list(r.tags or []),
            isolated=is_isolated,
        ))

    return GraphData(nodes=nodes, edges=edges)
