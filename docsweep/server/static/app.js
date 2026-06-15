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
    case "toggle-fold": { const f = el.closest(".fold"); if (f) f.classList.toggle("open"); break; }
    case "open-archivable": { const x = document.getElementById("archivable"); if (x) x.classList.add("open"); break; }
    case "close-modal": closeModal(); break;
  }
});
