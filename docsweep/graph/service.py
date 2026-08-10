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


def _make_id_resolver(records) -> callable:
    """basename が全体で一意なら basename、複数出現なら ``project/basename`` を返す関数を作る。

    後方互換を保つため、衝突が無い通常ケースでは以前と同じ basename id を返す。
    衝突があるケースだけ project 付き複合キーに切り替えて edge の混線を防ぐ。
    """
    counts: dict[str, int] = {}
    for r in records:
        name = Path(r.path).name
        counts[name] = counts.get(name, 0) + 1

    def _resolve(record) -> str:
        name = Path(record.path).name
        if counts.get(name, 0) > 1:
            return f"{record.project}/{name}"
        return name

    return _resolve


def build_graph(config: Config, *, project: str | None = None) -> GraphData:
    """全 md を node、frontmatter ``related`` を edge にしたグラフを返す。

    Args:
        config: ロード済み Config
        project: プロジェクト名でフィルタ（None で全体）

    Returns:
        ``GraphData``。
    """
    records = scan_records(config, project=project)
    resolve_id = _make_id_resolver(records)
    # basename → 属する project の一覧（同名複数プロジェクトを許容）。
    # related の参照は「同一プロジェクト内優先」で解決し、無ければ他プロジェクトの候補が
    # 1 件だけならそれに解決、複数プロジェクト同名なら未解決として残す。
    name_to_records: dict[str, list] = {}
    for r in records:
        name_to_records.setdefault(Path(r.path).name, []).append(r)

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    referenced: set[str] = set()
    outgoing_count: dict[str, int] = {}

    for r in records:
        nid = resolve_id(r)
        for ref in r.related or []:
            ref_name = Path(ref).name
            candidates = name_to_records.get(ref_name, [])
            # 同一プロジェクト内優先、次いで単一候補、複数候補なら未解決として残す。
            same_proj = [c for c in candidates if c.project == r.project]
            if same_proj:
                target_id = resolve_id(same_proj[0])
                resolved = True
            elif len(candidates) == 1:
                target_id = resolve_id(candidates[0])
                resolved = True
            else:
                target_id = ref_name
                resolved = False
            edges.append(GraphEdge(
                source=nid,
                target=target_id,
                resolved=resolved,
            ))
            if resolved:
                referenced.add(target_id)
            outgoing_count[nid] = outgoing_count.get(nid, 0) + 1

    for r in records:
        nid = resolve_id(r)
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
