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
  let currentQuery = "";
  function applySearch() {
    const q = currentQuery.trim().toLowerCase();
    let visible = 0;
    const cards = Array.from(document.querySelectorAll(".card[data-path]"));
    cards.forEach((card) => {
      if (!q) { card.hidden = false; visible++; return; }
      // 検索対象: data-path（ファイル名含む）+ card 内テキスト全体
      const text = ((card.dataset.path || "") + " " + (card.textContent || "")).toLowerCase();
      const hit = text.includes(q);
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
        cnt.textContent = `${visible} 件ヒット`;
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
      showToast(`Undo 失敗: ${(json && json.detail) || res.status}`, { undoable: false });
      return;
    }
    const restored = (json && json.restored) || [];
    const failed = (json && json.failed) || [];
    if (restored.length === 0) {
      showToast("Undo 対象がありません（既に復元済み or バッチ ID 無し）", { undoable: false });
      return;
    }
    showToast(
      `✓ ${restored.length} 件を復元しました` + (failed.length ? `（${failed.length} 件失敗）` : ""),
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
    overdue:    "🔴やり忘れ",
    today:      "🟡今日",
    active:     "🟢実行中",
    graduate:   "▼卒業判定",
    future:     "▶未来期日",
    no_due:     "▶期日未設定",
    archivable: "▶archive候補",
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
    selAll.textContent = "✓ 全プロジェクト ON";
    selAll.dataset.action = "bp-all-on";
    const selNone = document.createElement("button");
    selNone.type = "button";
    selNone.textContent = "✕ 全 OFF";
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
    if (!json) return "（応答なし）";
    const okN = (json.ok || json.moved || []).length;
    const failed = json.failed || json.failed_validation || [];
    const skipped = json.skipped || [];
    const okMsg = `成功 ${okN} 件`;
    const ngMsg = failed.length ? `／失敗 ${failed.length} 件` : "";
    const skMsg = skipped.length ? `／スキップ ${skipped.length} 件（archive 不可ラベル等）` : "";
    let detail = "";
    if (failed.length) {
      detail = "\n失敗:\n" + failed.slice(0, 5).map((f) => `  ${basename(f.path)}: ${f.error || f.kind || ""}`).join("\n");
      if (failed.length > 5) detail += `\n  …他 ${failed.length - 5} 件`;
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
      await confirmDialog("選択されているカードがありません。");
      return;
    }
    const labelMap = {
      due: `期日を ${params.spec} に更新`,
      status_in_progress: "[実行中] に変更",
      status_active: "[対応中] に変更",
      status_done: "[完了] に変更 → archive へ移送",
      status_discarded: "[廃止] に変更 → archive へ移送",
      archive: "archive 配下へ移送",
    };
    const key = op === "status" ? `status_${(params.state || "").replace("-", "_")}` : op;
    const action = labelMap[key] || op;
    const danger = (op === "status" && (params.state === "done" || params.state === "discarded")) || op === "archive";
    const head = danger
      ? `⚠ ${paths.length} 件のファイルを ${action} します。元には戻せません（archive から手動復元）。よろしいですか？`
      : `${paths.length} 件のファイルを ${action} します。よろしいですか？`;
    const ok = await confirmDialog(head);
    if (!ok) return;

    const result = await bulkApi(op, paths, params);
    if (!result) return;
    if (!result.ok && result.status !== 200) {
      await confirmDialog(`API 失敗: ${result.status}\n${(result.json && result.json.detail) || ""}`);
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
        showToast(`${movedN} 件を archive へ移送しました`, { undoable: true });
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
    // バルクバー「📅 日付」: 絶対日付で期日一括設定
    const bdd = e.target.closest && e.target.closest("[data-action='bulk-due-date']");
    if (bdd) {
      e.preventDefault();
      const paths = Array.from(selected);
      if (paths.length === 0) { confirmDialog("選択されているカードがありません。"); return; }
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
    // [完了]/[廃止] で archive 連動した時は Undo トースト
    if (json && json.archive_triggered && json.archive && json.archive.moved && json.archive.moved.length > 0) {
      showToast(`1 件を archive へ移送しました`, { undoable: true });
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
      confirmDialog(
        "キーボードショートカット:\n" +
        "  数字 1-6 = ラベル変更（1=計画 2=実行中/対応中 3=様子見 4=保留 5=完了 6=廃止）\n" +
        "  d = +1 日 / w = +1 週 / m = +1 ヶ月 / Shift+D = -1 日\n" +
        "  a = 画面全カード選択 / Esc = ピッカーを閉じる + 選択解除\n" +
        "  Tab = カード巡回 / Ctrl+S = 編集ペインを保存"
      );
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
