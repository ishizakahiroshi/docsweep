(function() {
  "use strict";
  function token() {
    return document.body.dataset.token ?? "";
  }
  function post(url, data) {
    const body = new URLSearchParams({ token: token(), ...data });
    return fetch(url, { method: "POST", body });
  }
  function refreshContent() {
    htmx.ajax("GET", `/fragment?token=${encodeURIComponent(token())}`, {
      target: "#page-content",
      swap: "innerHTML"
    });
  }
  function actionLabel(a) {
    return {
      discard: "archive へ移送",
      promote: "完了にして archive へ",
      relabel: "保留にする",
      resume: "再開する",
      keep: "維持"
    }[a] ?? a;
  }
  async function act(path, action, to) {
    const data = { path, action };
    if (action === "relabel" && to) data.to = to;
    if (!confirm(`${actionLabel(action)}：
${path.split("/").pop()}

よろしいですか？`)) return;
    const r = await post("/api/apply", data);
    if (!r.ok) {
      alert("失敗: " + await r.text());
      return;
    }
    refreshContent();
  }
  async function openf(path) {
    const r = await post("/api/open", { path });
    if (!r.ok) alert("開けませんでした: " + await r.text());
  }
  async function revealf(path) {
    const r = await post("/api/reveal", { path });
    if (!r.ok) alert("フォルダを開けませんでした: " + await r.text());
  }
  async function sweep() {
    if (!confirm("完了 / 廃止 のファイルを各プロジェクトの archive/ へ移送します。\n（様子見は触りません）")) return;
    const r = await post("/api/sweep", { dry_run: "false" });
    if (!r.ok) {
      alert("失敗: " + await r.text());
      return;
    }
    const moved = await r.json();
    alert(`${moved.length} 件を archive へ移送しました。`);
    refreshContent();
  }
  async function archiveAll() {
    const cards = [...document.querySelectorAll(".qcard[data-arch]")];
    if (!cards.length) return;
    if (!confirm(`表示中の ${cards.length} 件を archive へ移送します。
よろしいですか？`)) return;
    let ok = 0, failed = 0;
    for (const c of cards) {
      const r = await post("/api/apply", {
        path: c.dataset.path ?? "",
        action: c.dataset.arch ?? ""
      });
      r.ok ? ok++ : failed++;
    }
    if (failed) {
      alert(`${ok} 件を archive へ移送、${failed} 件は移送できませんでした（要修正のファイルが残っています）。`);
    }
    refreshContent();
  }
  function showModal() {
    document.getElementById("modal").classList.remove("hidden");
    document.getElementById("modal-body").innerHTML = '<div class="empty">読み込み中…</div>';
  }
  function closeModal() {
    document.getElementById("modal").classList.add("hidden");
  }
  function mk(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function presetVal() {
    const s = document.getElementById("inj-preset");
    return s ? s.value : "";
  }
  async function injectPreview(op, scope, project, agent) {
    const url = op === "inject" ? "/api/inject" : "/api/eject";
    const data = { scope, dry_run: "true" };
    if (scope === "project") {
      if (project) data.project = project;
      data.preset = presetVal();
    } else {
      if (agent) data.agent = agent;
    }
    const r = await post(url, data);
    if (!r.ok) {
      alert("プレビュー失敗: " + await r.text());
      return;
    }
    renderInjectPreview(await r.json(), op, scope, project, agent);
  }
  function renderInjectPreview(pv, op, scope, project, agent) {
    const body = document.getElementById("modal-body");
    body.innerHTML = "";
    const opLabel = op === "inject" ? "注入" : "解除";
    body.appendChild(mk("h2", "inj-h", `🔧 ${opLabel}プレビュー`));
    body.appendChild(mk("div", "inj-target", pv.path));
    if (scope === "global") {
      body.appendChild(mk(
        "div",
        "inj-warnbox",
        "⚠ これは個人グローバル設定への書き込みです。全プロジェクトのセッションに影響します。"
      ));
    }
    (pv.warnings || []).forEach((msg) => body.appendChild(mk("div", "inj-warnbox", `⚠ ${msg}`)));
    if (op === "inject") {
      (pv.blocks || []).forEach((b) => {
        body.appendChild(mk("div", "inj-file", `▶ ${b.file} に追記:`));
        body.appendChild(mk("pre", "inj-pre", b.text));
      });
      if (pv.scope === "global" && pv.guidance) {
        body.appendChild(mk("div", "inj-file", `▶ ${pv.guidance_path}（docsweep 所有・自動生成）:`));
        body.appendChild(mk("pre", "inj-pre", pv.guidance));
      }
      if (pv.scope === "project") {
        body.appendChild(mk(
          "div",
          "inj-note",
          pv.yaml_exists ? ".docsweep.yaml は既存（温存）" : ".docsweep.yaml を新規作成します"
        ));
      }
    } else {
      const removed = pv.removed || [];
      body.appendChild(mk("div", "inj-note", removed.length ? `次のファイルから docsweep 管理ブロックを除去します: ${removed.join(", ")}` : "除去対象の管理ブロックは見つかりませんでした。"));
      if (scope === "project") {
        const lab = mk("label", "inj-purge-lab");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.id = "inj-purge";
        lab.appendChild(cb);
        lab.appendChild(document.createTextNode(" .docsweep.yaml も削除する"));
        body.appendChild(lab);
      }
    }
    const apply = mk("button", "btn danger", `この内容で${opLabel}を実行`);
    apply.dataset.action = "inject-apply";
    apply.dataset.op = op;
    apply.dataset.scope = scope;
    if (project) apply.dataset.project = project;
    if (agent) apply.dataset.agent = agent;
    body.appendChild(apply);
    document.getElementById("modal").classList.remove("hidden");
  }
  async function injectApply(op, scope, project, agent) {
    const url = op === "inject" ? "/api/inject" : "/api/eject";
    const data = { scope, dry_run: "false" };
    if (scope === "project") {
      if (project) data.project = project;
      if (op === "inject") data.preset = presetVal();
      const purgeEl = document.getElementById("inj-purge");
      if (op === "eject") data.purge = (purgeEl == null ? void 0 : purgeEl.checked) ? "true" : "false";
    } else {
      if (agent) data.agent = agent;
    }
    const r = await post(url, data);
    if (!r.ok) {
      alert("失敗: " + await r.text());
      return;
    }
    refreshContent();
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
  });
  document.addEventListener("htmx:responseError", (e) => {
    var _a;
    const target = (_a = e.detail) == null ? void 0 : _a.target;
    if ((target == null ? void 0 : target.id) === "modal-body") {
      target.innerHTML = '<div class="empty">読み込みに失敗しました。</div>';
    }
  });
  document.addEventListener("click", (e) => {
    if (e.target.id === "modal") {
      closeModal();
      return;
    }
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const p = el.dataset.path ?? "";
    switch (el.dataset.action) {
      case "discard":
      case "promote":
        void act(p, el.dataset.action);
        break;
      case "relabel":
        void act(p, "relabel", el.dataset.to);
        break;
      case "detail": {
        showModal();
        const previewUrl = `/preview?token=${encodeURIComponent(token())}&path=${encodeURIComponent(p)}`;
        void htmx.ajax("GET", previewUrl, { target: "#modal-body", swap: "innerHTML" });
        break;
      }
      case "open":
        void openf(p);
        break;
      case "reveal":
        void revealf(p);
        break;
      case "sweep":
        void sweep();
        break;
      case "archive-all":
        void archiveAll();
        break;
      case "inject-preview":
        void injectPreview("inject", el.dataset.scope ?? "", el.dataset.project, el.dataset.agent);
        break;
      case "eject-preview":
        void injectPreview("eject", el.dataset.scope ?? "", el.dataset.project, el.dataset.agent);
        break;
      case "inject-apply":
        void injectApply(el.dataset.op ?? "", el.dataset.scope ?? "", el.dataset.project, el.dataset.agent);
        break;
      case "toggle-fold": {
        const f = el.closest(".fold");
        if (f) f.classList.toggle("open");
        break;
      }
      case "open-archivable": {
        const x = document.getElementById("archivable");
        if (x) x.classList.add("open");
        break;
      }
      case "toggle-all-open":
        document.querySelectorAll(".proj-accordion").forEach((d) => {
          d.open = true;
        });
        break;
      case "toggle-all-close":
        document.querySelectorAll(".proj-accordion").forEach((d) => {
          d.open = false;
        });
        break;
      case "close-modal":
        closeModal();
        break;
      case "view": {
        const view = el.dataset.view;
        if (!view) break;
        document.querySelectorAll("section.view").forEach((sec) => {
          sec.classList.toggle("hidden", sec.dataset.view !== view);
        });
        document.querySelectorAll(".nav[data-primary]").forEach((nav) => {
          nav.classList.toggle("active", nav.dataset.view === view);
        });
        const titles = {
          dashboard: ["ダッシュボード", "今日捌くべき判断"],
          settings: ["設定", "運用ルールの注入・管理"]
        };
        const [t, s] = titles[view] ?? [view, ""];
        const titleEl = document.getElementById("view-title");
        const subEl = document.getElementById("view-sub");
        if (titleEl) titleEl.textContent = t;
        if (subEl) subEl.textContent = s;
        break;
      }
    }
  });
})();
