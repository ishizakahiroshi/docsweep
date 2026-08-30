/* docsweep — UX W4（C4）の画面まわり。

   ここに入れているもの:
   - P15 カード密度モード（compact / cozy / detailed）
   - P65 高コントラストの明示トグル（縮小モーションは CSS の media query が担当）
   - P21 pin / snooze（サーバー state.json 側。MD は触らない）
   - P22 focus session（1 枚だけ残して他を薄くする）
   - P11 g g / g e（vim 派の最上/最下）
   - P5  30 秒クイックツアー（初回だけ・localStorage で覚える）
   - P59 一括破壊操作の 2 段階確認（サーバーが 409 で要求してきたら打ち込ませて再送）

   不変条件:
   - inline script / inline イベント属性を使わない（CSP script-src 'self'）。
   - 表示の好みは localStorage、文書に関わる状態はサーバーへ。両者を混ぜない。
   - keymap.js の既存キー割り当てを奪わない（入力欄では何もしない）。 */
(function () {
  "use strict";

  const TOKEN = document.body.dataset.token || "";
  const LS = {
    density: "docsweep.density",
    contrast: "docsweep.contrast",
    tour: "docsweep.tourSeen",
    showSnoozed: "docsweep.showSnoozed",
  };

  function store(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* private mode 等 */ }
  }
  function read(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function T(key) {
    return (typeof window.DS_T === "function" ? window.DS_T(key) : null) || key;
  }
  function fmt(key, arg) {
    if (typeof window.DS_T === "function") return window.DS_T(key, arg);
    return key + " " + String(arg);
  }

  async function postForm(url, data) {
    const sp = new URLSearchParams();
    sp.set("token", TOKEN);
    Object.keys(data || {}).forEach((k) => sp.set(k, String(data[k])));
    const res = await fetch(url, {
      method: "POST",
      headers: { "X-Docsweep-Token": TOKEN, "Content-Type": "application/x-www-form-urlencoded" },
      body: sp.toString(),
    });
    return { ok: res.ok, status: res.status, json: await res.json().catch(() => null) };
  }

  // ===== P15: 密度 ==========================================================

  const DENSITIES = ["compact", "cozy", "detailed"];

  function applyDensity(value) {
    const v = DENSITIES.indexOf(value) >= 0 ? value : "cozy";
    document.body.dataset.density = v;
    const sel = document.getElementById("density-select");
    if (sel && sel.value !== v) sel.value = v;
  }

  function initDensity() {
    applyDensity(read(LS.density) || "cozy");
    const sel = document.getElementById("density-select");
    if (!sel) return;
    sel.addEventListener("change", () => {
      applyDensity(sel.value);
      store(LS.density, sel.value);
    });
  }

  // ===== P65: 高コントラスト ================================================

  function applyContrast(on) {
    if (on) document.body.dataset.contrast = "high";
    else delete document.body.dataset.contrast;
    const btn = document.getElementById("contrast-toggle");
    if (btn) btn.setAttribute("aria-pressed", on ? "true" : "false");
  }

  function initContrast() {
    applyContrast(read(LS.contrast) === "1");
    const btn = document.getElementById("contrast-toggle");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const on = document.body.dataset.contrast !== "high";
      applyContrast(on);
      store(LS.contrast, on ? "1" : "0");
    });
  }

  // ===== P21: pin / snooze ==================================================

  function cardOf(el) {
    let cur = el;
    while (cur && cur !== document.body) {
      if (cur.classList && cur.classList.contains("card")) return cur;
      cur = cur.parentNode;
    }
    return null;
  }

  async function togglePin(card) {
    const path = card.dataset.path;
    if (!path) return;
    const next = card.dataset.pinned !== "1";
    const res = await postForm("/api/cards/pin", { path: path, pinned: next });
    if (!res.ok) return;
    card.dataset.pinned = next ? "1" : "0";
    card.classList.toggle("pinned", next);
    const btn = card.querySelector('[data-action="toggle-pin"]');
    if (btn) {
      btn.setAttribute("aria-pressed", next ? "true" : "false");
      btn.title = next ? T("unpin_action") : T("pin_action");
    }
    renderStateBadge(card, "pin-badge", next, T("pin_badge"));
  }

  async function toggleSnooze(card) {
    const path = card.dataset.path;
    if (!path) return;
    const on = card.dataset.snoozed !== "1";
    const url = on ? "/api/cards/snooze" : "/api/cards/snooze/clear";
    const res = await postForm(url, { path: path });
    if (!res.ok) return;
    card.dataset.snoozed = on ? "1" : "0";
    card.classList.toggle("snoozed", on);
    const btn = card.querySelector('[data-action="toggle-snooze"]');
    if (btn) {
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.title = on ? T("unsnooze_action") : T("snooze_action");
    }
    renderStateBadge(card, "snooze-badge", on, T("snooze_badge"));
  }

  function renderStateBadge(card, cls, on, text) {
    const head = card.querySelector(".card-head");
    if (!head) return;
    const existing = head.querySelector("." + cls);
    if (on && !existing) {
      const span = document.createElement("span");
      span.className = cls;
      span.textContent = text;
      head.appendChild(span);
    } else if (!on && existing) {
      existing.remove();
    }
  }

  // snooze したカードは board から消える。消えたままだと解除できないので、
  // 「非表示分も出す」トグルを 1 つ置く（出すと半透明で並び、💤 で解除できる）。

  function applyShowSnoozed(on) {
    if (on) document.body.dataset.showSnoozed = "1";
    else delete document.body.dataset.showSnoozed;
    const btn = document.getElementById("show-snoozed-toggle");
    if (btn) btn.setAttribute("aria-pressed", on ? "true" : "false");
  }

  function initShowSnoozed() {
    applyShowSnoozed(read(LS.showSnoozed) === "1");
    const btn = document.getElementById("show-snoozed-toggle");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const on = document.body.dataset.showSnoozed !== "1";
      applyShowSnoozed(on);
      store(LS.showSnoozed, on ? "1" : "0");
    });
  }

  // ===== P22: focus session =================================================

  function enterFocus(card) {
    const path = card.dataset.path;
    if (!path) return;
    document.querySelectorAll(".card.focus-target").forEach((c) => c.classList.remove("focus-target"));
    card.classList.add("focus-target");
    document.body.dataset.focusPath = path;
    const bar = document.getElementById("focus-bar");
    const name = document.getElementById("focus-name");
    if (name) name.textContent = (card.querySelector(".card-name") || {}).textContent || path;
    if (bar) bar.hidden = false;
    card.focus();
  }

  function exitFocus() {
    delete document.body.dataset.focusPath;
    document.querySelectorAll(".card.focus-target").forEach((c) => c.classList.remove("focus-target"));
    const bar = document.getElementById("focus-bar");
    if (bar) bar.hidden = true;
  }

  function initFocus() {
    const exit = document.getElementById("focus-exit");
    if (exit) exit.addEventListener("click", exitFocus);
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (document.body.dataset.focusPath) exitFocus();
    });
  }

  // ===== P11: g g / g e =====================================================

  function initVimJumps() {
    let pendingG = false;
    let pendingAt = 0;
    document.addEventListener("keydown", (e) => {
      const tag = e.target && e.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const now = Date.now();
      if (e.key === "g" && (!pendingG || now - pendingAt > 800)) {
        pendingG = true;
        pendingAt = now;
        return;
      }
      if (!pendingG) return;
      const cards = Array.from(document.querySelectorAll(".card:not(.filter-hide):not(.snoozed)"));
      if (e.key === "g" && cards.length) {
        e.preventDefault();
        cards[0].focus();
      } else if (e.key === "e" && cards.length) {
        e.preventDefault();
        cards[cards.length - 1].focus();
      }
      pendingG = false;
    });
  }

  // ===== P5: 30 秒クイックツアー ============================================

  // 文言は i18n.js（ja / en）側に持つ。ここには key だけを置く。
  const TOUR = ["tour_1", "tour_2", "tour_3", "tour_4"];

  function initTour() {
    const mask = document.getElementById("tour");
    if (!mask) return;
    if (read(LS.tour) === "1") return;
    let step = 0;
    const steps = document.getElementById("tour-steps");
    const title = document.getElementById("tour-title");
    const body = document.getElementById("tour-body");
    const next = document.getElementById("tour-next");
    const skip = document.getElementById("tour-skip");

    function close() {
      mask.hidden = true;
      store(LS.tour, "1");
    }
    function render() {
      const key = TOUR[step];
      if (title) title.textContent = T(key + "_title");
      if (body) body.textContent = T(key + "_body");
      if (steps) {
        steps.textContent = "";
        TOUR.forEach((_, i) => {
          const dot = document.createElement("span");
          dot.className = "tour-dot" + (i === step ? " on" : "");
          steps.appendChild(dot);
        });
      }
      if (next) next.textContent = step === TOUR.length - 1 ? T("tour_done") : T("tour_next");
    }
    if (next) {
      next.addEventListener("click", () => {
        if (step >= TOUR.length - 1) { close(); return; }
        step += 1;
        render();
      });
    }
    if (skip) skip.addEventListener("click", close);
    mask.hidden = false;
    render();
    if (next) next.focus();
  }

  // ===== P59: 2 段階確認（サーバーの 409 を受けて打ち込ませる） ==============
  // keymap.js の一括 API 呼び出しをラップする。confirm_required が返ったら
  // フレーズを尋ねて 1 度だけ再送する。閾値未満のときは何も変わらない。

  function askPhrase(phrase, count) {
    const dlg = document.getElementById("confirm-dialog");
    const row = document.getElementById("confirm-phrase-row");
    const label = document.getElementById("confirm-phrase-label");
    const input = document.getElementById("confirm-phrase");
    const msg = document.getElementById("confirm-message");
    if (!dlg || !row || !input || !msg) return Promise.resolve(null);
    msg.textContent = fmt("confirm_phrase_count", count);
    if (label) label.textContent = fmt("confirm_phrase_label", phrase);
    row.hidden = false;
    input.value = "";
    return new Promise((resolve) => {
      function done() {
        dlg.removeEventListener("close", done);
        row.hidden = true;
        resolve(dlg.returnValue === "ok" ? input.value : null);
      }
      dlg.addEventListener("close", done);
      try {
        dlg.showModal();
      } catch (e) {
        // 既に開いている等で開けないときは、window.prompt へ落とさない。
        // prompt はレンダラを止めるので、確認できないなら「確認しなかった」＝
        // 操作を進めない、に倒す（サーバー側は confirm 無しなら 409 のまま）。
        dlg.removeEventListener("close", done);
        row.hidden = true;
        resolve(null);
        return;
      }
      input.focus();
    });
  }

  function initBulkConfirm() {
    const original = window.fetch.bind(window);
    window.fetch = async function (input, init) {
      const res = await original(input, init);
      const url = typeof input === "string" ? input : (input && input.url) || "";
      if (res.status !== 409 || url.indexOf("/api/cards/") < 0) return res;
      let payload = null;
      try { payload = await res.clone().json(); } catch (e) { return res; }
      if (!payload || payload.error !== "confirm_required") return res;
      const typed = await askPhrase(payload.phrase, payload.count);
      if (typed === null) return res;
      const body = new URLSearchParams((init && init.body) || "");
      body.set("confirm", typed);
      return original(input, Object.assign({}, init, { body: body.toString() }));
    };
  }

  // ===== 配線 ================================================================

  document.addEventListener("click", (e) => {
    const btn = e.target && e.target.closest ? e.target.closest("[data-action]") : null;
    if (!btn) return;
    const action = btn.dataset.action;
    if (action !== "toggle-pin" && action !== "toggle-snooze" && action !== "focus-card") return;
    const card = cardOf(btn);
    if (!card) return;
    e.preventDefault();
    e.stopPropagation();
    if (action === "toggle-pin") togglePin(card);
    else if (action === "toggle-snooze") toggleSnooze(card);
    else enterFocus(card);
  });

  function boot() {
    initDensity();
    initContrast();
    initShowSnoozed();
    initFocus();
    initVimJumps();
    initBulkConfirm();
    initTour();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
