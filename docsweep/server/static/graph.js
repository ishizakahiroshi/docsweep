// graph ページの描画。
//
// アプリの CSP は `script-src 'self'` で inline script を許可していないため、
// このファイルは必ず外部 js として読み込む（template へ戻すと CSP に弾かれて
// 何のエラーも画面に出ないまま描画だけが行われなくなる）。
// グラフのデータは template 側の <script type="application/json" id="graph-data">
// から受け取る。JSON は実行されないので CSP の対象外。

(function () {
  "use strict";

  const dataEl = document.getElementById("graph-data");
  if (!dataEl) return;
  const GRAPH = JSON.parse(dataEl.textContent);

  const elements = [];
  for (const n of GRAPH.nodes) {
    elements.push({
      data: {
        id: n.id, label: n.label, type: n.type, state: n.state,
        stateLabel: n.state_label, project: n.project, isolated: n.isolated,
      },
    });
  }
  for (const e of GRAPH.edges) {
    // 未解決エッジは破線。target が node に存在しない場合は飛ばす
    if (!GRAPH.nodes.find(n => n.id === e.target)) continue;
    elements.push({
      data: { id: `${e.source}->${e.target}`, source: e.source, target: e.target, resolved: e.resolved },
    });
  }

  const colorByType = ele => {
    if (ele.data("isolated")) return "#c9c9c9";
    switch (ele.data("type")) {
      case "plan": return "#4a90e2";
      case "bugfix": return "#e2724a";
      case "pending": return "#888";
      default: return "#aaa";
    }
  };

  const cy = cytoscape({
    container: document.getElementById("cy"),
    elements,
    style: [
      {
        selector: "node",
        style: {
          "background-color": colorByType,
          "label": "data(label)",
          "font-size": 10,
          "text-valign": "bottom",
          "text-halign": "center",
          "width": 16, "height": 16,
        },
      },
      {
        selector: "edge",
        style: {
          "width": 1.5,
          "line-color": "#bbb",
          "target-arrow-color": "#bbb",
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
        },
      },
      {
        selector: "edge[resolved = 0]",
        style: { "line-style": "dashed", "opacity": 0.5 },
      },
    ],
    layout: { name: "cose", animate: false, idealEdgeLength: 80, nodeRepulsion: 8000 },
  });

  cy.on("tap", "node", evt => {
    const d = evt.target.data();
    alert(`${d.label}\n  state: ${d.stateLabel || d.state || "-"}\n  project: ${d.project}\n  type: ${d.type}`);
  });
})();
