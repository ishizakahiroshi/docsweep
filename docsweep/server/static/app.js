// docsweep Web UI — 受信トレイ型の即決操作。概要だけで取捨選択し、詳細はモーダルで。
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
  for (const c of cards) {
    await post("/api/apply", { path: c.dataset.path, action: c.dataset.arch });
  }
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
