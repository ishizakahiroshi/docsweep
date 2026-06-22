/* docsweep — カード列ドラッグ&ドロップ（期日操作のみ）。
   不変条件:
   - DnD で変えるのは due だけ（ラベルは数字キー or バッジ経由でしか変えない）
   - 🔴 → 🟡 = due=today / 🟡 → 🟢 = due=today（着手扱い）+ ラベルも変更
   - 未来期日折りたたみへ落とすと date picker でユーザーに尋ねる
   - それ以外の組み合わせはドロップを拒否（視覚 dnd-over を出さない） */
(function () {
  "use strict";

  const TOKEN = document.body.dataset.token || "";

  function fd(obj) {
    const sp = new URLSearchParams();
    sp.set("token", TOKEN);
    Object.keys(obj || {}).forEach((k) => sp.set(k, String(obj[k])));
    return sp.toString();
  }

  async function postForm(url, data) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "X-Docsweep-Token": TOKEN, "Content-Type": "application/x-www-form-urlencoded" },
      body: fd(data),
    });
    return { ok: res.ok, status: res.status, json: await res.json().catch(() => null) };
  }

  function dropTarget(el) {
    let cur = el;
    while (cur && cur !== document.body) {
      if (cur.dataset && cur.dataset.column) return { kind: "col", el: cur, key: cur.dataset.column };
      if (cur.classList && cur.classList.contains("fold-future")) return { kind: "fold", el: cur, key: "future" };
      cur = cur.parentNode;
    }
    return null;
  }

  function fromColumnOf(card) {
    let cur = card.parentNode;
    while (cur && cur !== document.body) {
      if (cur.dataset && cur.dataset.column) return cur.dataset.column;
      cur = cur.parentNode;
    }
    return null;
  }

  function allowed(from, to) {
    // 🔴 → 🟡 (today), 🟡 → 🟢 (today + 着手), 任意 → 未来期日折りたたみ。
    if (to === "today" && (from === "overdue" || from === "no_due")) return "today";
    if (to === "active" && from === "today") return "start";
    if (to === "future") return "future";
    return null;
  }

  document.addEventListener("dragstart", (e) => {
    const card = e.target.closest && e.target.closest(".card");
    if (!card) return;
    card.classList.add("dragging");
    e.dataTransfer.setData("text/plain", card.dataset.path);
    e.dataTransfer.effectAllowed = "move";
  });

  document.addEventListener("dragend", (e) => {
    const card = e.target.closest && e.target.closest(".card");
    if (card) card.classList.remove("dragging");
    document.querySelectorAll(".dnd-over").forEach((el) => el.classList.remove("dnd-over"));
  });

  document.addEventListener("dragover", (e) => {
    const card = document.querySelector(".card.dragging");
    if (!card) return;
    const t = dropTarget(e.target);
    if (!t) return;
    const from = fromColumnOf(card);
    if (!allowed(from, t.key)) return;
    e.preventDefault();
    document.querySelectorAll(".dnd-over").forEach((el) => el.classList.remove("dnd-over"));
    t.el.classList.add("dnd-over");
  });

  document.addEventListener("drop", async (e) => {
    const card = document.querySelector(".card.dragging");
    if (!card) return;
    const t = dropTarget(e.target);
    if (!t) return;
    const from = fromColumnOf(card);
    const kind = allowed(from, t.key);
    if (!kind) return;
    e.preventDefault();
    document.querySelectorAll(".dnd-over").forEach((el) => el.classList.remove("dnd-over"));

    const path = card.dataset.path;
    const mtime = card.dataset.mtime;

    if (kind === "today") {
      await postForm("/api/cards/due", { path, new_due: "today", expected_mtime: mtime });
    } else if (kind === "start") {
      const r1 = await postForm("/api/cards/status", {
        path, new_state: card.dataset.type === "bugfix" ? "active" : "in-progress",
        expected_mtime: mtime,
      });
      // 状態書き換えで mtime が変わるので、新 mtime を以って due も更新する。
      const newMtime = r1.json && r1.json.new_mtime ? r1.json.new_mtime : mtime;
      await postForm("/api/cards/due", { path, new_due: "today", expected_mtime: newMtime });
    } else if (kind === "future") {
      const v = window.prompt("いつまで先送りしますか？（YYYY-MM-DD または +1w / +1m）", "+1w");
      if (!v) return;
      await postForm("/api/cards/due", { path, new_due: v, expected_mtime: mtime });
    }

    if (typeof window.__docsweepReloadBoard === "function") {
      window.__docsweepReloadBoard();
    }
  });
})();
