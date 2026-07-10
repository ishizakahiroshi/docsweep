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

  // ===== ⚙ 設定モーダル（注入 / eject / グローバル inject） ==================
  async function openSuggestions() {
    const dlg = document.getElementById("suggestions-dialog");
    const body = document.getElementById("suggestions-body");
    if (!dlg || !body) return;
    body.innerHTML = "<p class='settings-note'>" + DS_T("loading") + "</p>";
    dlg.showModal();
    const res = await fetch("/api/suggestions?token=" + encodeURIComponent(TOKEN), {
      headers: headers(),
    });
    const json = await safeJson(res);
    if (!res.ok) {
      body.innerHTML = "<p class='settings-note'>failed: " + res.status + "</p>";
      return;
    }
    const items = (json && json.suggestions) || [];
    if (!items.length) {
      body.innerHTML = "<h3>提案トレイ</h3><p class='settings-note'>" + DS_T("suggestions_empty") + "</p>";
      return;
    }
    let html = "<h3>提案トレイ</h3><ul class='suggestions-list'>";
    items.forEach(function (s, i) {
      html += "<li class='suggestion-item' data-idx='" + i + "'>"
        + "<div><b>" + (s.proposed_action || "") + "</b> "
        + (s.proposed_to ? ("→ " + s.proposed_to + " ") : "")
        + "<code>" + (s.path || "") + "</code></div>"
        + "<div class='settings-note'>" + (s.reason || "") + " (c=" + (s.confidence || 0) + ")</div>"
        + "<div class='tp-actions'>"
        + "<button type='button' class='primary' data-action='suggestion-accept' data-path='" + (s.path || "") + "' data-act='" + (s.proposed_action || "") + "' data-to='" + (s.proposed_to || "") + "'>" + DS_T("suggestions_accept") + "</button> "
        + "<button type='button' class='ghost' data-action='suggestion-skip'>" + DS_T("suggestions_skip") + "</button>"
        + "</div></li>";
    });
    html += "</ul>";
    body.innerHTML = html;
  }

  async function openSettings() {
    const dlg = document.getElementById("settings-dialog");
    const body = document.getElementById("settings-body");
    if (!dlg || !body) return;
    body.innerHTML = "<p class='settings-note'>" + DS_T("loading") + "</p>";
    try { dlg.showModal(); } catch (e) { return; }
    await refreshSettings();
  }
  async function refreshSettings() {
    const body = document.getElementById("settings-body");
    if (!body) return;
    const res = await fetch("/board/_partial/settings?token=" + encodeURIComponent(TOKEN), {
      headers: { "X-Docsweep-Token": TOKEN },
    });
    if (!res.ok) {
      body.innerHTML = `<p class="settings-note">${DS_T("settings_load_failed", res.status)}</p>`;
      return;
    }
    body.innerHTML = await res.text();
  }
  async function configRoots(op, path) {
    // スキャンルートの追加・削除。runtime 反映 + config.yaml 永続化はサーバー側で行う。
    const res = await fetch("/api/config/roots", {
      method: "POST", headers: headers(), body: fd({ op: op, path: path }),
    });
    const json = await safeJson(res);
    if (!res.ok) {
      showToast(DS_T("roots_failed", (json && json.detail) || res.status), { undoable: false });
      return;
    }
    if (json && json.persisted === false) {
      showToast(DS_T("roots_runtime_only", (json && json.warning) || ""), { undoable: false, duration: 8000 });
    } else {
      showToast(DS_T(op === "add" ? "roots_added" : "roots_removed"), { undoable: false, duration: 5000 });
    }
    await refreshSettings();
    reloadBoard();
  }
  function setUiLang(lang) {
    // 言語は cookie（1 年）に保存してリロード。config.yaml は書き換えない（ユーザー設定温存）。
    document.cookie = "docsweep_lang=" + lang + "; path=/; max-age=31536000; samesite=lax";
    location.reload();
  }
  async function settingsInject(opts) {
    // opts: { scope: 'project'|'global', project?, agent? }
    const data = { token: TOKEN, scope: opts.scope };
    if (opts.project) data.project = opts.project;
    if (opts.agent) data.agent = opts.agent;
    const res = await fetch("/api/inject", {
      method: "POST", headers: headers(), body: fd(data),
    });
    const json = await safeJson(res);
    if (!res.ok) {
      showToast(DS_T("inject_failed", (json && json.detail) || res.status), { undoable: false });
      return;
    }
    showToast(DS_T("injected", json && json.yaml ? `（${basename(json.yaml)}）` : ""), { undoable: false, duration: 6000 });
    refreshSettings();
  }
  async function settingsEject(opts) {
    const data = { token: TOKEN, scope: opts.scope };
    if (opts.project) data.project = opts.project;
    if (opts.agent) data.agent = opts.agent;
    const res = await fetch("/api/eject", {
      method: "POST", headers: headers(), body: fd(data),
    });
    const json = await safeJson(res);
    if (!res.ok) {
      showToast(DS_T("eject_failed", (json && json.detail) || res.status), { undoable: false });
      return;
    }
    const removed = (json && json.removed) || [];
    showToast(DS_T("ejected", removed.length), { undoable: false, duration: 6000 });
    refreshSettings();
  }

  // ===== 一括の絶対日付ダイアログ =============================================
  function bulkDateDialog() {
    const dlg = document.getElementById("bulk-due-dialog");
    const input = document.getElementById("bulk-due-date");
    const count = document.getElementById("bulk-due-count");
    if (!dlg || !input) return Promise.resolve(null);
    // 初期値: 今日（+0 日）を提示。ユーザーは日付ピッカーで変える。
    const today = new Date();
    input.value = today.toISOString().slice(0, 10);
    if (count) count.textContent = String(selected.size);
    return new Promise((resolve) => {
      const onClose = () => {
        dlg.removeEventListener("close", onClose);
        resolve(dlg.returnValue === "ok" ? input.value : null);
      };
      dlg.addEventListener("close", onClose);
      try { dlg.showModal(); } catch (e) { resolve(window.prompt("YYYY-MM-DD", input.value)); }
    });
  }

  // ===== 検索ボックス（カードのタイトル / ファイル名 / 概要で絞り込み） ========
  // C4: tag:foo / owner:me / status:planned のキー付きトークンを認識し、
  // CLI 側 `docsweep triage --tag` 等の絞り込みを Web UI に持ち込む。
  // 残りの空白区切りトークンは「カード全文への部分一致」（既存挙動）。
  let currentQuery = "";

  function parseQuery(raw) {
    const tokens = (raw || "").trim().split(/\s+/).filter(Boolean);
    const out = { tags: [], owners: [], states: [], reviews: [], free: [] };
    tokens.forEach((tok) => {
      const m = tok.match(/^(tag|owner|status|review):(.+)$/i);
      if (!m) { out.free.push(tok.toLowerCase()); return; }
      const key = m[1].toLowerCase();
      const val = m[2].toLowerCase();
      if (key === "tag") out.tags.push(val);
      else if (key === "owner") out.owners.push(val);
      else if (key === "status") out.states.push(val);
      else if (key === "review") out.reviews.push(val);
    });
    return out;
  }

  function cardMatchesParsed(card, parsed) {
    // 自由語: 全文部分一致（複数なら AND）
    if (parsed.free.length > 0) {
      const text = ((card.dataset.path || "") + " " + (card.textContent || "")).toLowerCase();
      for (const f of parsed.free) {
        if (!text.includes(f)) return false;
      }
    }
    // tag: カード上の .okf-tag[data-tag] を集めて部分一致 OR 集合（複数 tag は AND）
    if (parsed.tags.length > 0) {
      const tagEls = Array.from(card.querySelectorAll(".okf-tag[data-tag]"));
      const tags = tagEls.map((el) => (el.dataset.tag || "").toLowerCase());
      for (const t of parsed.tags) {
        if (!tags.some((x) => x.includes(t))) return false;
      }
    }
    // owner: .okf-owner のテキスト（"👤 name"）から「name」だけ拾って部分一致
    if (parsed.owners.length > 0) {
      const ownerEl = card.querySelector(".okf-owner");
      const owner = ownerEl ? (ownerEl.textContent || "").replace(/^\W*/, "").toLowerCase() : "";
      for (const o of parsed.owners) {
        if (!owner.includes(o)) return false;
      }
    }
    // status: data-state（内部 state key）と state-badge のテキスト（"[計画]" 等）
    if (parsed.states.length > 0) {
      const stateKey = (card.dataset.state || "").toLowerCase();
      const stateBadge = card.querySelector(".state-badge");
      const stateLabel = stateBadge ? (stateBadge.textContent || "").toLowerCase() : "";
      for (const s of parsed.states) {
        if (!stateKey.includes(s) && !stateLabel.includes(s)) return false;
      }
    }
    return true;
  }

  function applySearch() {
    const q = currentQuery.trim();
    const parsed = q ? parseQuery(q) : null;
    let visible = 0;
    const cards = Array.from(document.querySelectorAll(".card[data-path]"));
    cards.forEach((card) => {
      if (!q) { card.hidden = false; visible++; return; }
      const hit = cardMatchesParsed(card, parsed);
      card.hidden = !hit;
      if (hit) visible++;
    });
    // 各セクションの「該当なし」フラグ
    document.querySelectorAll(".col[data-column], .fold[data-section]").forEach((sec) => {
      const total = sec.querySelectorAll(".card[data-path]").length;
      const shown = sec.querySelectorAll(".card[data-path]:not([hidden])").length;
      sec.dataset.empty = (total > 0 && shown === 0) ? "true" : "false";
    });
    // 検索カウント表示
    const cnt = document.getElementById("search-count");
    const clearBtn = document.getElementById("search-clear");
    if (cnt) {
      if (q) {
        cnt.hidden = false;
        cnt.textContent = DS_T("hits", visible);
      } else {
        cnt.hidden = true;
        cnt.textContent = "";
      }
    }
    if (clearBtn) clearBtn.hidden = !q;
  }
  function setSearchQuery(q) {
    currentQuery = q || "";
    const input = document.getElementById("card-search");
    if (input && input.value !== currentQuery) input.value = currentQuery;
    applySearch();
  }
  document.addEventListener("input", (e) => {
    if (e.target && e.target.id === "card-search") {
      currentQuery = e.target.value;
      applySearch();
    }
  });
  document.addEventListener("change", (e) => {
    if (e.target && e.target.id === "profile-select") {
      const sp = new URLSearchParams();
      sp.set("token", TOKEN);
      sp.set("profile", e.target.value || "all");
      fetch("/api/profile", { method: "POST", headers: headers(), body: sp.toString() })
        .then(function () {
          if (typeof window.__docsweepReloadBoard === "function") window.__docsweepReloadBoard();
          else location.reload();
        });
    }
  });

  // フィルタチップ（P14）
  document.addEventListener("click", function (e) {
    const chip = e.target.closest && e.target.closest(".fchip");
    if (!chip) return;
    e.preventDefault();
    document.querySelectorAll(".fchip").forEach(function (c) { c.classList.remove("on"); });
    chip.classList.add("on");
    const f = chip.dataset.filter || "all";
    document.querySelectorAll(".card").forEach(function (card) {
      let show = true;
      if (f === "type:plan") show = card.dataset.type === "plan";
      else if (f === "type:bugfix") show = card.dataset.type === "bugfix";
      else if (f === "flag:overdue") show = (card.closest(".col-overdue") != null);
      else if (f === "flag:needs_decision") {
        const flags = (card.dataset.flags || "") + " " + (card.querySelector(".okf-tag") ? "tag" : "");
        show = (card.dataset.state === "planned" || card.dataset.state === "watching"
          || (card.textContent || "").indexOf("needs") >= 0);
        // 簡易: flags が dataset に無いので postpone/overdue 列以外の要判断は state で近似
        show = card.dataset.state === "planned" || card.dataset.state === "watching"
          || card.closest(".col-overdue") != null;
      }
      card.classList.toggle("filter-hide", f !== "all" && !show);
    });
  }, true);

  // fold open/close 記憶 + 静かな朝モード（P10）
  function restoreFolds() {
    try {
      const raw = localStorage.getItem("docsweep_folds");
      if (!raw) return;
      const map = JSON.parse(raw);
      document.querySelectorAll("details.fold[data-section]").forEach(function (el) {
        const key = el.dataset.section;
        if (key && Object.prototype.hasOwnProperty.call(map, key)) {
          el.open = !!map[key];
        }
      });
    } catch (err) { /* ignore */ }
  }
  document.addEventListener("toggle", function (e) {
    const el = e.target;
    if (!el || !el.classList || !el.classList.contains("fold")) return;
    try {
      const map = JSON.parse(localStorage.getItem("docsweep_folds") || "{}");
      if (el.dataset.section) map[el.dataset.section] = !!el.open;
      localStorage.setItem("docsweep_folds", JSON.stringify(map));
    } catch (err) { /* ignore */ }
  }, true);
  setTimeout(restoreFolds, 0);

  // 今日の tips（P69）
  (function showTip() {
    try {
      const tips = [
        "u キーで Undo（archive 直後）",
        "docsweep intent \"昨日何やった\" でコマンド候補",
        "設定でプロジェクトを OFF にできる",
        "find --q で本文検索",
        "day open / day close で 1 日を儀式化",
      ];
      const week = Math.floor(Date.now() / (7 * 864e5));
      const tip = tips[week % tips.length];
      const bar = document.querySelector(".topbar-health");
      if (bar && !document.getElementById("daily-tip")) {
        const span = document.createElement("span");
        span.id = "daily-tip";
        span.className = "health-chip";
        span.title = "今日の tips";
        span.textContent = "tip: " + tip;
        bar.appendChild(span);
      }
    } catch (err) { /* ignore */ }
  })();

  // ===== Undo トースト ========================================================
  let toastTimer = null;
  function showToast(message, opts) {
    opts = opts || {};
    const toast = document.getElementById("toast");
    const msg = document.getElementById("toast-msg");
    const undoBtn = document.getElementById("toast-undo");
    if (!toast || !msg) return;
    msg.textContent = message;
    if (undoBtn) {
      undoBtn.hidden = !opts.undoable;
      undoBtn.disabled = false;
    }
    toast.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, opts.duration || 10000);
  }
  function hideToast() {
    const toast = document.getElementById("toast");
    if (toast) toast.hidden = true;
    if (toastTimer) { clearTimeout(toastTimer); toastTimer = null; }
  }
  async function copyWorkPack(path) {
    const url = "/api/cards/context?token=" + encodeURIComponent(TOKEN)
      + "&path=" + encodeURIComponent(path);
    try {
      const res = await fetch(url, { headers: headers() });
      const json = await safeJson(res);
      if (!res.ok || !json || !json.text) {
        showToast(DS_T("work_pack_fail"), { undoable: false });
        return;
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(json.text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = json.text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
      }
      showToast(DS_T("work_pack_ok"), { undoable: false, duration: 4000 });
    } catch (err) {
      showToast(DS_T("work_pack_fail"), { undoable: false });
    }
  }

  async function callUndo() {
    const undoBtn = document.getElementById("toast-undo");
    if (undoBtn) undoBtn.disabled = true;
    const sp = new URLSearchParams();
    sp.set("token", TOKEN);
    const res = await fetch("/api/cards/undo", {
      method: "POST", headers: headers(), body: sp.toString(),
    });
    const json = await safeJson(res);
    if (!res.ok) {
      showToast(DS_T("undo_failed", (json && json.detail) || res.status), { undoable: false });
      return;
    }
    const restored = (json && json.restored) || [];
    const failed = (json && json.failed) || [];
    if (restored.length === 0) {
      showToast(DS_T("undo_nothing"), { undoable: false });
      return;
    }
    showToast(
      DS_T("undo_restored", restored.length) + (failed.length ? DS_T("undo_restored_failed_suffix", failed.length) : ""),
      { undoable: false, duration: 6000 }
    );
    reloadBoard();
  }
  window.__docsweepShowToast = showToast;
  window.__docsweepHideToast = hideToast;

  async function reloadBoard() {
    const res = await fetch("/board/fragment?token=" + encodeURIComponent(TOKEN), {
      headers: { "X-Docsweep-Token": TOKEN },
    });
    if (!res.ok) return;
    document.getElementById("board-body").innerHTML = await res.text();
    // 差し替え後に既存の選択を可能な範囲で復元する（path 基準で照合）。
    syncSelectionToCheckboxes();
    updateBulkBar();
    // 検索クエリも再適用（DOM が差し替わったので filter を効かせ直す）
    applySearch();
  }
  window.__docsweepReloadBoard = reloadBoard;

  // ⏻ サーバー停止（画面右上ボタン）。確認 → POST /api/shutdown → uvicorn が graceful 停止する。
  // 停止後は HTTP が落ちるためページは「サーバーを停止しました」と表示してリンクを止める。
  async function shutdownServer() {
    const ok = await confirmDialog(DS_T("shutdown_confirm"));
    if (!ok) return;
    const btn = document.getElementById("shutdown-btn");
    if (btn) btn.disabled = true;
    try {
      await fetch("/api/shutdown", { method: "POST", headers: headers(), body: fd({}) });
    } catch (e) {
      // 停止処理中にコネクションが切れることがある（むしろ正常）。エラーは無視。
    }
    // 画面ロック: 看板本体とトップバー操作を覆い、ユーザーに「もう動かない」と示す。
    const overlay = document.createElement("div");
    overlay.className = "shutdown-overlay";
    overlay.innerHTML =
      "<div class=\"shutdown-card\">" +
      "<div class=\"shutdown-icon\">⏻</div>" +
      "<h2>" + DS_T("shutdown_done_title") + "</h2>" +
      "<p>" + DS_T("shutdown_done_body") + "</p>" +
      "</div>";
    document.body.appendChild(overlay);
  }

  // ===== 一括編集: 選択状態管理 ===========================================
  // 選択中の path 集合（ページ全体で 1 つ・リロードで消えてよい）
  const selected = new Set();

  function allCardPaths() {
    return Array.from(document.querySelectorAll(".card[data-path]")).map((el) => el.dataset.path);
  }

  function sectionCardPaths(section) {
    // 列（.col[data-column]）or 折りたたみ（.fold[data-section]）配下のカード
    const colEl = document.querySelector(`.col[data-column="${section}"]`);
    if (colEl) return Array.from(colEl.querySelectorAll(".card[data-path]")).map((el) => el.dataset.path);
    const foldEl = document.querySelector(`.fold[data-section="${section}"]`);
    if (foldEl) return Array.from(foldEl.querySelectorAll(".card[data-path]")).map((el) => el.dataset.path);
    return [];
  }

  function setSelected(path, on) {
    if (on) selected.add(path); else selected.delete(path);
    const card = document.querySelector(`.card[data-path="${cssEscape(path)}"]`);
    if (card) card.classList.toggle("selected", on);
    const cb = document.querySelector(`.card-check[data-path="${cssEscape(path)}"]`);
    if (cb) cb.checked = on;
  }

  function clearSelection() {
    Array.from(selected).forEach((p) => setSelected(p, false));
  }

  function syncSelectionToCheckboxes() {
    // リロード後に DOM が変わったので、現在残っているカードの checkbox をオン状態に同期
    Array.from(document.querySelectorAll(".card[data-path]")).forEach((card) => {
      const p = card.dataset.path;
      const on = selected.has(p);
      card.classList.toggle("selected", on);
      const cb = card.querySelector(".card-check");
      if (cb) cb.checked = on;
    });
    // もう DOM に無い path は集合から落とす
    const alive = new Set(allCardPaths());
    Array.from(selected).forEach((p) => { if (!alive.has(p)) selected.delete(p); });
  }

  // 選択中の path がどのセクションに属するかをラベル名で集計
  const SECTION_LABELS = {
    overdue:    DS_T("sec_overdue"),
    today:      DS_T("sec_today"),
    active:     DS_T("sec_active"),
    graduate:   DS_T("sec_graduate"),
    future:     DS_T("sec_future"),
    no_due:     DS_T("sec_no_due"),
    archivable: DS_T("sec_archivable"),
  };
  function sectionOfCard(cardEl) {
    let cur = cardEl ? cardEl.parentElement : null;
    while (cur && cur !== document.body) {
      if (cur.dataset && cur.dataset.column) return cur.dataset.column;
      if (cur.dataset && cur.dataset.section) return cur.dataset.section;
      cur = cur.parentElement;
    }
    return null;
  }
  function getSectionBreakdown() {
    const counts = {};
    selected.forEach((path) => {
      const card = document.querySelector(`.card[data-path="${cssEscape(path)}"]`);
      const sec = sectionOfCard(card);
      if (sec) counts[sec] = (counts[sec] || 0) + 1;
    });
    return counts;
  }
  function formatBreakdown(counts) {
    // SECTION_LABELS の順序で並べる（プレゼンの一貫性）
    const parts = [];
    Object.keys(SECTION_LABELS).forEach((k) => {
      if (counts[k]) parts.push(`${SECTION_LABELS[k]} ${counts[k]}`);
    });
    return parts.length > 0 ? `（${parts.join(" / ")}）` : "";
  }

  function getProjectBreakdown() {
    const counts = {};
    selected.forEach((path) => {
      const card = document.querySelector(`.card[data-path="${cssEscape(path)}"]`);
      const proj = card && card.dataset.project;
      if (proj) counts[proj] = (counts[proj] || 0) + 1;
    });
    return counts;
  }

  // 板面上の全プロジェクトを集計（名前順）
  function getAllProjectsSorted() {
    const counts = {};
    Array.from(document.querySelectorAll(".card[data-project]")).forEach((c) => {
      const p = c.dataset.project;
      counts[p] = (counts[p] || 0) + 1;
    });
    return Object.entries(counts).sort(([a], [b]) => a.localeCompare(b));
  }

  // 現在の選択に含まれるプロジェクトの一覧と件数
  function getSelectedPerProject() {
    const map = {};
    selected.forEach((path) => {
      const card = document.querySelector(`.card[data-path="${cssEscape(path)}"]`);
      const proj = card && card.dataset.project;
      if (proj) map[proj] = (map[proj] || 0) + 1;
    });
    return map;
  }

  function renderProjectsDropdown() {
    const dd = document.getElementById("bulk-projects-dropdown");
    if (!dd) return;
    dd.innerHTML = "";
    const all = getAllProjectsSorted();
    const sel = getSelectedPerProject();

    // ヘッダ操作
    const actions = document.createElement("div");
    actions.className = "bp-actions";
    const selAll = document.createElement("button");
    selAll.type = "button";
    selAll.textContent = DS_T("bp_all_on");
    selAll.dataset.action = "bp-all-on";
    const selNone = document.createElement("button");
    selNone.type = "button";
    selNone.textContent = DS_T("bp_all_off");
    selNone.dataset.action = "bp-all-off";
    actions.appendChild(selAll);
    actions.appendChild(selNone);
    dd.appendChild(actions);

    const divider = document.createElement("div");
    divider.className = "bp-divider";
    dd.appendChild(divider);

    // 各プロジェクト行（checkbox + 名前 + 件数）
    all.forEach(([proj, total]) => {
      const selN = sel[proj] || 0;
      const row = document.createElement("label");
      row.className = "bp-row" + (selN > 0 && selN < total ? " partial" : "");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "bp-check";
      cb.dataset.project = proj;
      cb.checked = selN > 0;
      // 部分選択（同プロジェクトの一部だけ選択中）は indeterminate で示す
      cb.indeterminate = selN > 0 && selN < total;
      const name = document.createElement("span");
      name.className = "bp-name";
      name.textContent = `📂 ${proj}`;
      const cnt = document.createElement("span");
      cnt.className = "bp-count";
      cnt.textContent = `${selN} / ${total}`;
      row.appendChild(cb);
      row.appendChild(name);
      row.appendChild(cnt);
      dd.appendChild(row);
    });
  }

  function toggleProjectsDropdown() {
    const dd = document.getElementById("bulk-projects-dropdown");
    if (!dd) return;
    if (dd.hidden) {
      renderProjectsDropdown();
      dd.hidden = false;
    } else {
      dd.hidden = true;
    }
  }

  function closeProjectsDropdown() {
    const dd = document.getElementById("bulk-projects-dropdown");
    if (dd) dd.hidden = true;
  }

  function setProjectSelection(project, on) {
    const cards = Array.from(document.querySelectorAll(`.card[data-project="${cssEscape(project)}"]`));
    cards.forEach((c) => setSelected(c.dataset.path, on));
    updateBulkBar();
  }

  function updateProjSummary() {
    const sum = document.getElementById("bulk-proj-summary");
    const toggle = document.getElementById("bulk-proj-toggle");
    if (!sum || !toggle) return;
    const sel = getSelectedPerProject();
    const n = Object.keys(sel).length;
    const allProj = getAllProjectsSorted().length;
    sum.textContent = n > 0 ? `${n}/${allProj}` : "—";
    toggle.classList.toggle("has-filter", n > 0 && n < allProj);
  }

  // addProjectToSelection は既存（カード Shift+クリック用）→ そのまま残す
  function addProjectToSelection(project) {
    setProjectSelection(project, true);
  }
  function selectProjectOnly(project) {
    // 該当プロジェクトだけに切替（カードの 📂 バッジクリック挙動）。再クリックで全解除のトグル。
    const cards = Array.from(document.querySelectorAll(`.card[data-project="${cssEscape(project)}"]`));
    const targetPaths = cards.map((c) => c.dataset.path);
    if (targetPaths.length === 0) return;
    const allOn = targetPaths.every((p) => selected.has(p))
      && Array.from(selected).every((p) => targetPaths.includes(p));
    clearSelection();
    if (!allOn) {
      targetPaths.forEach((p) => setSelected(p, true));
    }
    updateBulkBar();
  }

  function updateBulkBar() {
    const bar = document.getElementById("bulk-bar");
    if (!bar) return;
    const n = selected.size;
    const total = allCardPaths().length;
    const nEl = document.getElementById("bulk-count-n");
    const totalEl = document.getElementById("bulk-total-n");
    if (nEl) nEl.textContent = String(n);
    if (totalEl) totalEl.textContent = String(total);
    const bkEl = document.getElementById("bulk-breakdown");
    if (bkEl) bkEl.textContent = n > 0 ? formatBreakdown(getSectionBreakdown()) : "";
    // プロジェクト絞り込みサマリ + 開いていればドロップダウン再描画
    updateProjSummary();
    const dd = document.getElementById("bulk-projects-dropdown");
    if (dd && !dd.hidden) renderProjectsDropdown();
    bar.hidden = n === 0;
  }

  function cssEscape(s) {
    return (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/["\\]/g, "\\$&");
  }

  // selectProjectOnly / addProjectToSelection はドロップダウン化時に下方へ移動（重複削除済み）

  // ===== 一括 API 呼び出し ================================================
  async function bulkApi(op, paths, params) {
    if (!paths || paths.length === 0) return null;
    const sp = new URLSearchParams();
    sp.set("token", TOKEN);
    paths.forEach((p) => sp.append("paths", p));
    if (op === "due") sp.set("new_due", params.spec);
    else if (op === "status") sp.set("new_state", params.state);
    // op === "archive" は paths のみ
    const url = op === "due" ? "/api/cards/bulk/due"
              : op === "status" ? "/api/cards/bulk/status"
              : "/api/cards/bulk/archive";
    const res = await fetch(url, { method: "POST", headers: headers(), body: sp.toString() });
    return { ok: res.ok, status: res.status, json: await safeJson(res) };
  }

  function summarizeBulkResult(json, op) {
    if (!json) return DS_T("no_response");
    const okN = (json.ok || json.moved || []).length;
    const failed = json.failed || json.failed_validation || [];
    const skipped = json.skipped || [];
    const okMsg = DS_T("ok_count", okN);
    const ngMsg = failed.length ? DS_T("fail_count", failed.length) : "";
    const skMsg = skipped.length ? DS_T("skip_count", skipped.length) : "";
    let detail = "";
    if (failed.length) {
      detail = DS_T("fail_head") + failed.slice(0, 5).map((f) => `  ${basename(f.path)}: ${f.error || f.kind || ""}`).join("\n");
      if (failed.length > 5) detail += DS_T("fail_more", failed.length - 5);
    }
    return `${okMsg}${ngMsg}${skMsg}${detail}`;
  }

  function basename(p) {
    if (!p) return "";
    const parts = String(p).split(/[\\/]/);
    return parts[parts.length - 1] || p;
  }

  async function runBulk(op, paths, params, opts) {
    opts = opts || {};
    if (!paths || paths.length === 0) {
      await confirmDialog(DS_T("no_selection"));
      return;
    }
    const stateLabel = (s) => DS_T("state_" + (s || "").replace("-", "_"));
    let action;
    if (op === "due") {
      action = DS_T("bulk_due", params.spec);
    } else if (op === "status") {
      const archiveBound = params.state === "done" || params.state === "discarded";
      action = DS_T(archiveBound ? "bulk_status_archive" : "bulk_status", stateLabel(params.state));
    } else {
      action = DS_T("bulk_archive");
    }
    const danger = (op === "status" && (params.state === "done" || params.state === "discarded")) || op === "archive";
    const head = DS_T(danger ? "confirm_danger" : "confirm_normal", paths.length, action);
    const ok = await confirmDialog(head);
    if (!ok) return;

    const result = await bulkApi(op, paths, params);
    if (!result) return;
    if (!result.ok && result.status !== 200) {
      await confirmDialog(DS_T("api_failed", result.status, (result.json && result.json.detail) || ""));
      return;
    }
    // archive 系（archive 直接 or status で archive_triggered）は Undo 可能トーストを表示
    const archived = (op === "archive")
      || (op === "status" && result.json && result.json.archive
          && result.json.archive.moved && result.json.archive.moved.length > 0);
    if (archived) {
      const movedN = op === "archive"
        ? ((result.json.moved && result.json.moved.length) || 0)
        : ((result.json.archive && result.json.archive.moved.length) || 0);
      if (movedN > 0) {
        showToast(DS_T("archived_n", movedN), { undoable: true });
      } else {
        await confirmDialog(summarizeBulkResult(result.json, op));
      }
    } else {
      await confirmDialog(summarizeBulkResult(result.json, op));
    }
    if (!opts.keepSelection) clearSelection();
    reloadBoard();
  }

  // ===== UI 配線: checkbox / セクション一括 / 上部 sticky バー ============
  document.addEventListener("change", (e) => {
    const cb = e.target.closest && e.target.closest(".card-check");
    if (!cb) return;
    setSelected(cb.dataset.path, cb.checked);
    updateBulkBar();
  });

  document.addEventListener("click", async (e) => {
    // セクションヘッダ「全選択」（トグル: 全選択済なら全解除）
    const sel = e.target.closest && e.target.closest("[data-action='section-select-all']");
    if (sel) {
      e.preventDefault();
      const section = sel.dataset.section;
      const paths = sectionCardPaths(section);
      const allOn = paths.length > 0 && paths.every((p) => selected.has(p));
      paths.forEach((p) => setSelected(p, !allOn));
      updateBulkBar();
      return;
    }
    // セクション一括ボタン
    const sec = e.target.closest && e.target.closest("[data-action='section-bulk']");
    if (sec) {
      e.preventDefault();
      const section = sec.dataset.section;
      const op = sec.dataset.op;
      const paths = sectionCardPaths(section);
      const params = { spec: sec.dataset.spec, state: sec.dataset.state };
      runBulk(op, paths, params);
      return;
    }
    // セクション一括「ラベル変更▾」: 列内全カードを対象に label_picker を出す
    const secLP = e.target.closest && e.target.closest("[data-action='section-open-label-picker']");
    if (secLP) {
      e.preventDefault();
      e.stopPropagation();
      const paths = sectionCardPaths(secLP.dataset.section);
      openLabelPickerForPaths(paths, secLP);
      return;
    }
    // 上部 sticky バーの一括ボタン
    const bb = e.target.closest && e.target.closest("[data-action='bulk']");
    if (bb) {
      e.preventDefault();
      const op = bb.dataset.op;
      const paths = Array.from(selected);
      const params = { spec: bb.dataset.spec, state: bb.dataset.state };
      runBulk(op, paths, params);
      return;
    }
    // 上部 sticky バー「ラベル変更▾」: 選択中のカードを対象に label_picker を出す
    const bulkLP = e.target.closest && e.target.closest("[data-action='bulk-open-label-picker']");
    if (bulkLP) {
      e.preventDefault();
      e.stopPropagation();
      const paths = Array.from(selected);
      openLabelPickerForPaths(paths, bulkLP);
      return;
    }
    // バルクバー「📅 日付」: 絶対日付で期日一括設定
    const bdd = e.target.closest && e.target.closest("[data-action='bulk-due-date']");
    if (bdd) {
      e.preventDefault();
      const paths = Array.from(selected);
      if (paths.length === 0) { confirmDialog(DS_T("no_selection")); return; }
      bulkDateDialog().then((date) => {
        if (!date) return;
        runBulk("due", paths, { spec: date });
      });
      return;
    }
    // 解除ボタン
    const bc = e.target.closest && e.target.closest("[data-action='bulk-clear']");
    if (bc) {
      e.preventDefault();
      clearSelection();
      updateBulkBar();
      return;
    }
    // プロジェクトバッジクリック（切替 or 追加・Shift+クリックで追加モード）
    const proj = e.target.closest && e.target.closest("[data-action='select-project']");
    if (proj) {
      e.preventDefault();
      e.stopPropagation();
      if (e.shiftKey) {
        addProjectToSelection(proj.dataset.project);
      } else {
        selectProjectOnly(proj.dataset.project);
      }
      return;
    }
    // バルクバー「📂 プロジェクト ▾」開閉
    const dropToggle = e.target.closest && e.target.closest("[data-action='open-projects-dropdown']");
    if (dropToggle) {
      e.preventDefault();
      e.stopPropagation();
      toggleProjectsDropdown();
      return;
    }
    // ドロップダウン内 checkbox（label 経由で label クリックでも入る）
    const cb = e.target.closest && e.target.closest(".bp-check");
    if (cb) {
      // ブラウザの checkbox 状態は既に切り替わってる（label の中の input）。新状態を反映。
      e.stopPropagation();
      setProjectSelection(cb.dataset.project, cb.checked);
      return;
    }
    // ドロップダウン内「全 ON / 全 OFF」
    const bpOn = e.target.closest && e.target.closest("[data-action='bp-all-on']");
    if (bpOn) {
      e.preventDefault();
      e.stopPropagation();
      Array.from(document.querySelectorAll(".card[data-path]")).forEach((c) => setSelected(c.dataset.path, true));
      updateBulkBar();
      return;
    }
    const bpOff = e.target.closest && e.target.closest("[data-action='bp-all-off']");
    if (bpOff) {
      e.preventDefault();
      e.stopPropagation();
      clearSelection();
      updateBulkBar();
      return;
    }
    // ドロップダウン外クリックで閉じる（toggle ボタン自身は除外）
    if (!e.target.closest(".bulk-projects-wrap")) {
      closeProjectsDropdown();
    }
    // 今日の 1 個 → 編集ペイン
    const openTp = e.target.closest && e.target.closest("[data-action='open-today-pick']");
    if (openTp) {
      e.preventDefault();
      const p = openTp.dataset.path;
      if (p && typeof window.__docsweepLoadEditPane === "function") {
        const fake = { dataset: { path: p, mtime: "" } };
        window.__docsweepLoadEditPane(fake);
        const card = document.querySelector('.card[data-path="' + CSS.escape(p) + '"]');
        if (card) card.focus();
      }
      return;
    }
    // 作業開始パック: context を clipboard へ（P7）
    const copyCtx = e.target.closest && e.target.closest("[data-action='copy-context']");
    if (copyCtx) {
      e.preventDefault();
      const p = copyCtx.dataset.path || (document.querySelector(".ep-workpack") && document.querySelector(".ep-workpack").dataset.path);
      if (p) copyWorkPack(p);
      return;
    }
    // トーストの Undo / × 閉じ
    if (e.target.closest && e.target.closest("[data-action='undo']")) {
      e.preventDefault();
      callUndo();
      return;
    }
    if (e.target.closest && e.target.closest("[data-action='toast-close']")) {
      e.preventDefault();
      hideToast();
      return;
    }
    // ⚙ 設定モーダル
    if (e.target.closest && e.target.closest("[data-action='open-settings']")) {
      e.preventDefault();
      openSettings();
      return;
    }
    if (e.target.closest && e.target.closest("[data-action='open-suggestions']")) {
      e.preventDefault();
      openSuggestions();
      return;
    }
    const sugAcc = e.target.closest && e.target.closest("[data-action='suggestion-accept']");
    if (sugAcc) {
      e.preventDefault();
      const sp = new URLSearchParams();
      sp.set("token", TOKEN);
      sp.set("path", sugAcc.dataset.path || "");
      sp.set("action", sugAcc.dataset.act || "");
      if (sugAcc.dataset.to) sp.set("to", sugAcc.dataset.to);
      fetch("/api/suggestions/apply", { method: "POST", headers: headers(), body: sp.toString() })
        .then(function () {
          const li = sugAcc.closest(".suggestion-item");
          if (li) li.remove();
          if (typeof window.__docsweepReloadBoard === "function") window.__docsweepReloadBoard();
        });
      return;
    }
    const sugSkip = e.target.closest && e.target.closest("[data-action='suggestion-skip']");
    if (sugSkip) {
      e.preventDefault();
      const li = sugSkip.closest(".suggestion-item");
      if (li) li.remove();
      return;
    }
    const togProj = e.target.closest && e.target.closest("[data-action='settings-toggle-project']");
    if (togProj) {
      e.preventDefault();
      const root = togProj.dataset.root;
      const currentlyOn = togProj.dataset.enabled === "true";
      const sp = new URLSearchParams();
      sp.set("token", TOKEN);
      sp.set("root", root);
      sp.set("enabled", currentlyOn ? "false" : "true");
      fetch("/api/project/toggle", { method: "POST", headers: headers(), body: sp.toString() })
        .then(function () { openSettings(); });
      return;
    }
    const hlRel = e.target.closest && e.target.closest("[data-action='highlight-related']");
    if (hlRel) {
      e.preventDefault();
      e.stopPropagation();
      const names = (hlRel.dataset.related || "").split("|").filter(Boolean);
      document.querySelectorAll(".card").forEach(function (c) {
        c.classList.remove("related-hl");
        const name = (c.dataset.path || "").split(/[/\\]/).pop();
        if (names.indexOf(name) >= 0) c.classList.add("related-hl");
      });
      return;
    }
    // ⏻ サーバー停止
    if (e.target.closest && e.target.closest("[data-action='shutdown-server']")) {
      e.preventDefault();
      shutdownServer();
      return;
    }
    // 設定モーダル: スキャンルートの追加・削除
    const addRoot = e.target.closest && e.target.closest("[data-action='settings-add-root']");
    if (addRoot) {
      e.preventDefault();
      const input = document.getElementById("settings-root-input");
      const v = input ? input.value.trim() : "";
      if (!v) return;
      configRoots("add", v);
      return;
    }
    const rmRoot = e.target.closest && e.target.closest("[data-action='settings-remove-root']");
    if (rmRoot) {
      e.preventDefault();
      confirmDialog(DS_T("roots_remove_confirm", rmRoot.dataset.path)).then((ok) => {
        if (ok) configRoots("remove", rmRoot.dataset.path);
      });
      return;
    }
    // 設定モーダル: 表示言語トグル（cookie に保存してリロード）
    const langBtn = e.target.closest && e.target.closest("[data-action='settings-set-lang']");
    if (langBtn) {
      e.preventDefault();
      setUiLang(langBtn.dataset.lang);
      return;
    }
    if (e.target.closest && e.target.closest("[data-action='settings-inject']")) {
      e.preventDefault();
      const btn = e.target.closest("[data-action='settings-inject']");
      settingsInject({ scope: "project", project: btn.dataset.project });
      return;
    }
    if (e.target.closest && e.target.closest("[data-action='settings-eject']")) {
      e.preventDefault();
      const btn = e.target.closest("[data-action='settings-eject']");
      settingsEject({ scope: "project", project: btn.dataset.project });
      return;
    }
    if (e.target.closest && e.target.closest("[data-action='settings-inject-global']")) {
      e.preventDefault();
      const btn = e.target.closest("[data-action='settings-inject-global']");
      settingsInject({ scope: "global", agent: btn.dataset.agent });
      return;
    }
    if (e.target.closest && e.target.closest("[data-action='settings-eject-global']")) {
      e.preventDefault();
      const btn = e.target.closest("[data-action='settings-eject-global']");
      settingsEject({ scope: "global", agent: btn.dataset.agent });
      return;
    }
    // 検索クリア
    if (e.target.closest && e.target.closest("[data-action='search-clear']")) {
      e.preventDefault();
      setSearchQuery("");
      const input = document.getElementById("card-search");
      if (input) input.focus();
      return;
    }
    // 「画面全選択」リンク（トグル: 全選択済なら全解除）
    const ba = e.target.closest && e.target.closest("[data-action='bulk-select-all']");
    if (ba) {
      e.preventDefault();
      const paths = allCardPaths();
      const allOn = paths.length > 0 && paths.every((p) => selected.has(p));
      paths.forEach((p) => setSelected(p, !allOn));
      updateBulkBar();
      return;
    }
  }, true);  // capture phase で先に拾う（カードクリックの本文表示より先に処理）

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

  async function openLabelPickerForPaths(paths, anchor) {
    // 既存 _label_picker.html を「複数 path への一括 status 適用」用に流用する。
    // セクション一括（列内全カード）と sticky バー（選択中のみ）の両方から呼ばれる。
    // 適用は runBulk("status", paths, {state}) — 既存 services 経由でラベル不許可は failed[] へ。
    if (!paths || paths.length === 0) {
      await confirmDialog(DS_T("no_target"));
      return;
    }
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
      await runBulk("status", paths, { state: newState });
    });
  }

  async function openChangePicker(card, anchor) {
    // 状態変更ピッカー（個別カードの「変更▾」専用・[廃止] 除く）。
    // ファイル種別をサーバーに渡して選択肢を出し分ける:
    //   plan / 未知 → [計画]/[実行中]/[様子見]/[保留]/[完了] の 5 択
    //   bugfix      → [実行中]/[様子見]/[保留]/[完了] の 4 択（active→in-progress 統合済み）
    //   pending     → [保留]/[計画] の 2 択
    // fetchPartial は URL に "?token=..." を後付けする実装のため、
    // type も渡したい本関数では直接 fetch して URL に両方を載せる。
    closePicker();
    const t = card.dataset.type || "";
    const params = new URLSearchParams({ token: TOKEN });
    if (t) params.set("type", t);
    const res = await fetch("/board/_partial/change_picker?" + params.toString(), {
      headers: { "X-Docsweep-Token": TOKEN },
    });
    if (!res.ok) return;
    const html = await res.text();
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
    if (openPicker && !openPicker.contains(e.target)
        && !e.target.closest("[data-action='open-change-picker']")
        && !e.target.closest("[data-action='open-due-picker']")
        && !e.target.closest("[data-action='section-open-label-picker']")
        && !e.target.closest("[data-action='bulk-open-label-picker']")) {
      closePicker();
    }
  });

  // ===== ラベル変更 / 期日変更 ---------------------------------------------
  async function applyStatus(card, newState) {
    const path = card.dataset.path;
    const mtime = card.dataset.mtime;
    const needsConfirm = (newState === "done" || newState === "discarded");
    if (needsConfirm) {
      const label = DS_T(newState === "done" ? "state_done" : "state_discarded");
      const ok = await confirmDialog(DS_T("confirm_done_single", label));
      if (!ok) return;
    }
    const { ok, status, json } = await postForm("/api/cards/status", {
      path: path,
      new_state: newState,
      expected_mtime: mtime,
    });
    if (!ok) {
      const msg = (json && json.detail) ? json.detail : ("status " + status);
      await confirmDialog(DS_T("status_change_failed", msg));
      return;
    }
    // [完了]/[廃止] で archive 連動した時は Undo トースト
    if (json && json.archive_triggered && json.archive && json.archive.moved && json.archive.moved.length > 0) {
      showToast(DS_T("archived_one"), { undoable: true });
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
      await confirmDialog(DS_T("due_change_failed", msg));
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

    const changeBtn = e.target.closest("[data-action='open-change-picker']");
    if (changeBtn) { e.stopPropagation(); openChangePicker(card, changeBtn); return; }

    const dueBtn = e.target.closest("[data-action='open-due-picker']");
    if (dueBtn) { e.stopPropagation(); openDuePicker(card, dueBtn); return; }
    // 2026-06-23 改修: 独立「廃止」ボタンを撤去（変更▾ピッカーに集約）。discard ハンドラ削除。

    // それ以外はカードをフォーカス + 右ペインに本文を流し込む。
    if (typeof window.__docsweepLoadEditPane === "function") {
      window.__docsweepLoadEditPane(card);
    }
    card.focus();
  });

  // ===== キーボード --------------------------------------------------------
  // 2026-06-23 改修: active/対応中 を in-progress/実行中 に統合。種別分岐廃止。
  // [廃止] は下段独立ボタンに分離（誤クリック防止）したので数字キー 6 は廃止。
  const NUMBER_TO_STATE = {
    "1": "planned",
    "2": "in-progress",
    "3": "watching",
    "4": "pending",
    "5": "done",
  };

  document.addEventListener("keydown", (e) => {
    // 検索ボックス内では Escape だけ拾う（クリア用）
    if (e.target && e.target.id === "card-search") {
      if (e.key === "Escape") {
        e.preventDefault();
        setSearchQuery("");
        e.target.blur();
      }
      return;
    }
    if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
    // '/' で検索ボックスにフォーカス
    if (e.key === "/") {
      const input = document.getElementById("card-search");
      if (input) {
        e.preventDefault();
        input.focus();
        input.select();
        return;
      }
    }
    const card = document.activeElement && document.activeElement.classList && document.activeElement.classList.contains("card")
      ? document.activeElement : null;

    // Help / Escape -------------------------------------------------------
    if (e.key === "?") {
      e.preventDefault();
      confirmDialog(DS_T("help"));
      return;
    }
    if (e.key === "Escape") {
      closePicker();
      // ピッカーが開いていなければ、Esc で選択も解除する
      if (selected.size > 0) { clearSelection(); updateBulkBar(); }
      return;
    }
    // 'a' で画面全カード選択（カードフォーカス・ピッカー開いていないとき・トグル）
    if (e.key === "a" && !openPicker) {
      e.preventDefault();
      const paths = allCardPaths();
      const allOn = paths.length > 0 && paths.every((p) => selected.has(p));
      paths.forEach((p) => setSelected(p, !allOn));
      updateBulkBar();
      return;
    }

    if (!card) return;

    // Number keys ---------------------------------------------------------
    if (NUMBER_TO_STATE[e.key]) {
      e.preventDefault();
      applyStatus(card, NUMBER_TO_STATE[e.key]);
      return;
    }
    if (e.key === "2") {
      e.preventDefault();
      // 2026-06-23 改修: active/対応中 を in-progress/実行中 に統合したため種別分岐は不要。
      applyStatus(card, "in-progress");
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

  // j/k カード移動・x 選択・u Undo（カードフォーカス不要）（UX W2 / P11）
  document.addEventListener("keydown", (e) => {
    if (e.target && (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT" || e.target.tagName === "SELECT")) return;
    if (e.key === "u" && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      callUndo();
      return;
    }
    if (e.key !== "j" && e.key !== "k" && e.key !== "x") return;
    const cards = Array.from(document.querySelectorAll(".card:not(.filter-hide)"));
    if (!cards.length) return;
    const cur = document.activeElement && document.activeElement.classList
      && document.activeElement.classList.contains("card")
      ? document.activeElement : null;
    let idx = cur ? cards.indexOf(cur) : -1;
    if (e.key === "j") {
      e.preventDefault();
      idx = Math.min(cards.length - 1, idx + 1);
      cards[idx].focus();
      if (typeof window.__docsweepLoadEditPane === "function") window.__docsweepLoadEditPane(cards[idx]);
    } else if (e.key === "k") {
      e.preventDefault();
      idx = Math.max(0, idx <= 0 ? 0 : idx - 1);
      cards[idx].focus();
      if (typeof window.__docsweepLoadEditPane === "function") window.__docsweepLoadEditPane(cards[idx]);
    } else if (e.key === "x" && cur) {
      e.preventDefault();
      const path = cur.dataset.path;
      if (path) setSelected(path, !selected.has(path));
      updateBulkBar();
    }
  });
})();
