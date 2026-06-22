/* docsweep — 看板（カンバン）の配線。
   - カードのフォーカス + 数字キー（1-6）でラベル変更
   - カードのフォーカス + d/w/m で期日 +1日/+1週/+1ヶ月、D で -1 日
   - バッジクリック / 3 ボタンの配線（ラベルピッカー・期日ピッカー展開）
   - 全 POST は X-Docsweep-Token ヘッダ経由（form の token 値も併用）

   CSP: script-src 'self' のためインラインハンドラ禁止。すべてイベント委譲。 */
(function () {
  "use strict";

  const TOKEN = document.body.dataset.token || "";

  function headers() {
    return {
      "X-Docsweep-Token": TOKEN,
      "Content-Type": "application/x-www-form-urlencoded",
    };
  }

  function fd(obj) {
    const sp = new URLSearchParams();
    sp.set("token", TOKEN);
    Object.keys(obj || {}).forEach((k) => {
      if (obj[k] !== undefined && obj[k] !== null) sp.set(k, String(obj[k]));
    });
    return sp.toString();
  }

  async function postForm(url, data) {
    const res = await fetch(url, { method: "POST", headers: headers(), body: fd(data) });
    return { ok: res.ok, status: res.status, json: await safeJson(res) };
  }

  async function safeJson(res) {
    try { return await res.json(); } catch (e) { return null; }
  }

  async function reloadBoard() {
    const res = await fetch("/board/fragment?token=" + encodeURIComponent(TOKEN), {
      headers: { "X-Docsweep-Token": TOKEN },
    });
    if (!res.ok) return;
    document.getElementById("board-body").innerHTML = await res.text();
  }
  window.__docsweepReloadBoard = reloadBoard;

  // ===== 確認ダイアログ ----------------------------------------------------
  function confirmDialog(message) {
    return new Promise((resolve) => {
      const dlg = document.getElementById("confirm-dialog");
      if (!dlg) { resolve(window.confirm(message)); return; }
      document.getElementById("confirm-message").textContent = message;
      const form = dlg.querySelector("form");
      const onClose = () => {
        dlg.removeEventListener("close", onClose);
        resolve(dlg.returnValue === "ok");
      };
      dlg.addEventListener("close", onClose);
      try { dlg.showModal(); } catch (e) { resolve(window.confirm(message)); }
      form.onsubmit = null;
    });
  }

  // ===== ピッカー ----------------------------------------------------------
  let openPicker = null;

  function closePicker() {
    if (openPicker && openPicker.parentNode) openPicker.parentNode.removeChild(openPicker);
    openPicker = null;
  }

  async function fetchPartial(url) {
    const res = await fetch(url + "?token=" + encodeURIComponent(TOKEN), {
      headers: { "X-Docsweep-Token": TOKEN },
    });
    if (!res.ok) return null;
    return await res.text();
  }

  async function openLabelPicker(card, anchor) {
    closePicker();
    const html = await fetchPartial("/board/_partial/label_picker");
    if (!html) return;
    const wrap = document.createElement("div");
    wrap.innerHTML = html;
    const picker = wrap.firstElementChild;
    placePicker(picker, anchor);
    openPicker = picker;
    document.body.appendChild(picker);
    picker.addEventListener("click", async (e) => {
      const btn = e.target.closest(".lp-opt");
      if (!btn) return;
      const newState = btn.dataset.newState;
      closePicker();
      await applyStatus(card, newState);
    });
  }

  async function openDuePicker(card, anchor) {
    closePicker();
    const html = await fetchPartial("/board/_partial/due_picker");
    if (!html) return;
    const wrap = document.createElement("div");
    wrap.innerHTML = html;
    const picker = wrap.firstElementChild;
    placePicker(picker, anchor);
    openPicker = picker;
    document.body.appendChild(picker);
    picker.addEventListener("click", async (e) => {
      const btn = e.target.closest(".dp-quick");
      if (!btn) return;
      const spec = btn.dataset.spec;
      closePicker();
      await applyDue(card, spec);
    });
    const dateInput = picker.querySelector(".dp-date");
    if (dateInput) {
      dateInput.addEventListener("change", async () => {
        const v = dateInput.value;
        if (!v) return;
        closePicker();
        await applyDue(card, v);
      });
    }
  }

  function placePicker(picker, anchor) {
    const r = anchor.getBoundingClientRect();
    picker.style.left = (window.scrollX + r.left) + "px";
    picker.style.top = (window.scrollY + r.bottom + 4) + "px";
  }

  document.addEventListener("click", (e) => {
    if (openPicker && !openPicker.contains(e.target) && !e.target.closest("[data-action='open-label-picker']") && !e.target.closest("[data-action='open-due-picker']")) {
      closePicker();
    }
  });

  // ===== ラベル変更 / 期日変更 ---------------------------------------------
  async function applyStatus(card, newState) {
    const path = card.dataset.path;
    const mtime = card.dataset.mtime;
    const needsConfirm = (newState === "done" || newState === "discarded");
    if (needsConfirm) {
      const ok = await confirmDialog(
        newState === "done"
          ? "このファイルを [完了] にして archive へ移送します。よろしいですか？"
          : "このファイルを [廃止] にして archive へ移送します。よろしいですか？"
      );
      if (!ok) return;
    }
    const { ok, status, json } = await postForm("/api/cards/status", {
      path: path,
      new_state: newState,
      expected_mtime: mtime,
    });
    if (!ok) {
      const msg = (json && json.detail) ? json.detail : ("status " + status);
      await confirmDialog("ラベル変更に失敗しました: " + msg);
      return;
    }
    reloadBoard();
  }

  async function applyDue(card, spec) {
    const path = card.dataset.path;
    const mtime = card.dataset.mtime;
    const { ok, status, json } = await postForm("/api/cards/due", {
      path: path,
      new_due: spec,
      expected_mtime: mtime,
    });
    if (!ok) {
      const msg = (json && json.detail) ? json.detail : ("status " + status);
      await confirmDialog("期日変更に失敗しました: " + msg);
      return;
    }
    if (json && json.warning) {
      await confirmDialog(json.warning);
    }
    reloadBoard();
  }

  // ===== クリック委譲 ------------------------------------------------------
  document.addEventListener("click", (e) => {
    const reload = e.target.closest("[data-action='reload-board']");
    if (reload) { reloadBoard(); return; }

    const card = e.target.closest(".card");
    if (!card) return;

    const labelBtn = e.target.closest("[data-action='open-label-picker']");
    if (labelBtn) { e.stopPropagation(); openLabelPicker(card, labelBtn); return; }

    const dueBtn = e.target.closest("[data-action='open-due-picker']");
    if (dueBtn) { e.stopPropagation(); openDuePicker(card, dueBtn); return; }

    const startBtn = e.target.closest("[data-action='start']");
    if (startBtn) { e.stopPropagation(); applyStatus(card, "in-progress"); return; }

    const discardBtn = e.target.closest("[data-action='discard']");
    if (discardBtn) { e.stopPropagation(); applyStatus(card, "discarded"); return; }

    // それ以外はカードをフォーカス + 右ペインに本文を流し込む。
    if (typeof window.__docsweepLoadEditPane === "function") {
      window.__docsweepLoadEditPane(card);
    }
    card.focus();
  });

  // ===== キーボード --------------------------------------------------------
  const NUMBER_TO_STATE = {
    "1": "planned",
    // "2" は file_type 別: plan → in-progress / bugfix → active
    "3": "watching",
    "4": "pending",
    "5": "done",
    "6": "discarded",
  };

  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
    const card = document.activeElement && document.activeElement.classList && document.activeElement.classList.contains("card")
      ? document.activeElement : null;

    // Help / Escape -------------------------------------------------------
    if (e.key === "?") {
      e.preventDefault();
      confirmDialog(
        "キーボードショートカット:\n" +
        "  数字 1-6 = ラベル変更（1=計画 2=実行中/対応中 3=様子見 4=保留 5=完了 6=廃止）\n" +
        "  d = +1 日 / w = +1 週 / m = +1 ヶ月 / Shift+D = -1 日\n" +
        "  Tab = カード巡回 / Esc = ピッカーを閉じる / Ctrl+S = 編集ペインを保存"
      );
      return;
    }
    if (e.key === "Escape") { closePicker(); return; }

    if (!card) return;

    // Number keys ---------------------------------------------------------
    if (NUMBER_TO_STATE[e.key]) {
      e.preventDefault();
      applyStatus(card, NUMBER_TO_STATE[e.key]);
      return;
    }
    if (e.key === "2") {
      e.preventDefault();
      const t = card.dataset.type;
      applyStatus(card, t === "bugfix" ? "active" : "in-progress");
      return;
    }

    // Date keys -----------------------------------------------------------
    if (e.key === "d") { e.preventDefault(); applyDue(card, "+1d"); return; }
    if (e.key === "w") { e.preventDefault(); applyDue(card, "+1w"); return; }
    if (e.key === "m") { e.preventDefault(); applyDue(card, "+1m"); return; }
    if (e.key === "D" && e.shiftKey) {
      e.preventDefault();
      // -1 日。services/due.py の resolve_due は負の +Nd を受け付けない設計のため、
      // 「今日 - 1 日」をクライアントで算出して絶対指定で送る。
      const d = new Date(); d.setDate(d.getDate() - 1);
      const iso = d.toISOString().slice(0, 10);
      applyDue(card, iso);
      return;
    }
  });
})();
