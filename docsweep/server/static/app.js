// docsweep Web UI — 受信トレイ型の即決操作。概要だけで取捨選択し、詳細はモーダルで。
// CSP（script-src 'self'）下で動くよう inline onclick は使わず、data-action 属性 +
// document への単一イベント委譲で配線する（動的挿入されるプレビュー内のボタンにも効く）。
function token() { return document.body.dataset.token; }

function post(url, data) {
  const body = new URLSearchParams(Object.assign({ token: token() }, data));
  return fetch(url, { method: "POST", body });
}

function actionLabel(a) {
  return ({
    discard: "archive へ移送", promote: "完了にして archive へ",
    relabel: "保留にする", resume: "再開する", keep: "維持",
  })[a] || a;
}

async function act(path, action, to) {
  const data = { path, action };
  if (action === "relabel" && to) data.to = to;
  if (!confirm(actionLabel(action) + "：\n" + path.split("/").pop() + "\n\nよろしいですか？")) return;
  const r = await post("/api/apply", data);
  if (!r.ok) { alert("失敗: " + (await r.text())); return; }
  location.reload();
}

async function openf(path) {
  const r = await post("/api/open", { path });
  if (!r.ok) alert("開けませんでした: " + (await r.text()));
}

async function revealf(path) {
  const r = await post("/api/reveal", { path });
  if (!r.ok) alert("フォルダを開けませんでした: " + (await r.text()));
}

async function sweep() {
  if (!confirm("完了 / 廃止 のファイルを各プロジェクトの archive/ へ移送します。\n（様子見は触りません）")) return;
  const r = await post("/api/sweep", { dry_run: "false" });
  if (!r.ok) { alert("失敗: " + (await r.text())); return; }
  const moved = await r.json();
  alert(moved.length + " 件を archive へ移送しました。");
  location.reload();
}

async function archiveAll() {
  const cards = [...document.querySelectorAll(".qcard[data-arch]")];
  if (!cards.length) return;
  if (!confirm("表示中の " + cards.length + " 件を archive へ移送します。\nよろしいですか？")) return;
  let ok = 0, failed = 0;
  for (const c of cards) {
    const r = await post("/api/apply", { path: c.dataset.path, action: c.dataset.arch });
    r.ok ? ok++ : failed++;
  }
  if (failed) alert(ok + " 件を archive へ移送、" + failed + " 件は移送できませんでした（要修正のファイルが残っています）。");
  location.reload();
}

async function detail(path) {
  const body = document.getElementById("modal-body");
  body.innerHTML = '<div class="empty">読み込み中…</div>';
  document.getElementById("modal").classList.remove("hidden");
  const r = await fetch("/preview?token=" + encodeURIComponent(token()) + "&path=" + encodeURIComponent(path));
  body.innerHTML = r.ok ? await r.text() : '<div class="empty">読み込みに失敗しました。</div>';
}

function closeModal() {
  document.getElementById("modal").classList.add("hidden");
}

// ---- inject / eject（プレビュー必須 → 確認 → 実行）----
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
  if (scope === "project") { data.project = project; data.preset = presetVal(); } else data.agent = agent;
  const r = await post(url, data);
  if (!r.ok) { alert("プレビュー失敗: " + (await r.text())); return; }
  renderInjectPreview(await r.json(), op, scope, project, agent);
}

function renderInjectPreview(pv, op, scope, project, agent) {
  const body = document.getElementById("modal-body");
  body.innerHTML = "";
  const opLabel = op === "inject" ? "注入" : "解除";
  body.appendChild(mk("h2", "inj-h", "🔧 " + opLabel + "プレビュー"));
  body.appendChild(mk("div", "inj-target", pv.path));
  if (scope === "global") {
    body.appendChild(mk("div", "inj-warnbox",
      "⚠ これは個人グローバル設定への書き込みです。全プロジェクトのセッションに影響します。"));
  }
  (pv.warnings || []).forEach((msg) => body.appendChild(mk("div", "inj-warnbox", "⚠ " + msg)));

  if (op === "inject") {
    (pv.blocks || []).forEach((b) => {
      body.appendChild(mk("div", "inj-file", "▶ " + b.file + " に追記:"));
      body.appendChild(mk("pre", "inj-pre", b.text));
    });
    if (pv.scope === "global" && pv.guidance) {
      body.appendChild(mk("div", "inj-file", "▶ " + pv.guidance_path + "（docsweep 所有・自動生成）:"));
      body.appendChild(mk("pre", "inj-pre", pv.guidance));
    }
    if (pv.scope === "project") {
      body.appendChild(mk("div", "inj-note",
        pv.yaml_exists ? ".docsweep.yaml は既存（温存）" : ".docsweep.yaml を新規作成します"));
    }
  } else {
    const removed = pv.removed || [];
    body.appendChild(mk("div", "inj-note", removed.length
      ? "次のファイルから docsweep 管理ブロックを除去します: " + removed.join(", ")
      : "除去対象の管理ブロックは見つかりませんでした。"));
    if (scope === "project") {
      const lab = mk("label", "inj-purge-lab");
      const cb = mk("input");
      cb.type = "checkbox";
      cb.id = "inj-purge";
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(" .docsweep.yaml も削除する"));
      body.appendChild(lab);
    }
  }

  const apply = mk("button", "btn danger", "この内容で" + opLabel + "を実行");
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
    data.project = project;
    if (op === "inject") data.preset = presetVal();
    if (op === "eject") data.purge = document.getElementById("inj-purge")?.checked ? "true" : "false";
  } else {
    data.agent = agent;
  }
  const r = await post(url, data);
  if (!r.ok) { alert("失敗: " + (await r.text())); return; }
  location.reload();
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

document.addEventListener("click", (e) => {
  // モーダル背景（#modal 自身）クリックで閉じる。カード内クリックでは閉じない。
  if (e.target.id === "modal") { closeModal(); return; }
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const p = el.dataset.path;
  switch (el.dataset.action) {
    case "discard":
    case "promote": act(p, el.dataset.action); break;
    case "relabel": act(p, "relabel", el.dataset.to); break;
    case "detail": detail(p); break;
    case "open": openf(p); break;
    case "reveal": revealf(p); break;
    case "sweep": sweep(); break;
    case "archive-all": archiveAll(); break;
    case "inject-preview": injectPreview("inject", el.dataset.scope, el.dataset.project, el.dataset.agent); break;
    case "eject-preview": injectPreview("eject", el.dataset.scope, el.dataset.project, el.dataset.agent); break;
    case "inject-apply": injectApply(el.dataset.op, el.dataset.scope, el.dataset.project, el.dataset.agent); break;
    case "toggle-fold": { const f = el.closest(".fold"); if (f) f.classList.toggle("open"); break; }
    case "open-archivable": { const x = document.getElementById("archivable"); if (x) x.classList.add("open"); break; }
    case "toggle-all-open":
      document.querySelectorAll(".proj-accordion").forEach(d => { d.open = true; }); break;
    case "toggle-all-close":
      document.querySelectorAll(".proj-accordion").forEach(d => { d.open = false; }); break;
    case "close-modal": closeModal(); break;
  }
});
