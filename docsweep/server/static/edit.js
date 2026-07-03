/* docsweep — 右ペイン本文プレビュー/編集（Ctrl+S + mtime トラッキング）。
   - カードを選択したら GET /preview で HTML を取得（既存ルートを再利用）
   - 編集タブは textarea。保存時に mtime を expected_mtime に乗せる
   - 409 Conflict（mtime 不一致）なら警告ダイアログ */
(function () {
  "use strict";

  const TOKEN = document.body.dataset.token || "";
  let currentPath = null;
  let currentMtime = null;

  function $(sel) { return document.querySelector(sel); }

  function setTab(name) {
    document.querySelectorAll(".ep-tab").forEach((t) => {
      const on = t.dataset.tab === name;
      t.classList.toggle("on", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll("[data-tabpanel]").forEach((p) => {
      p.hidden = p.dataset.tabpanel !== name;
    });
  }

  async function loadPreviewHtml(path) {
    const url = "/preview?token=" + encodeURIComponent(TOKEN) + "&path=" + encodeURIComponent(path);
    const res = await fetch(url, { headers: { "X-Docsweep-Token": TOKEN } });
    if (!res.ok) return null;
    return await res.text();
  }

  async function loadRawContent(path) {
    // GET /api/cards/raw が生 MD と mtime を JSON で返す（読み取り専用・スコープ境界チェック付き）。
    // 編集 textarea にはここで取れた原文をそのまま入れる（HTML レンダリング後のテキストを使うと
    // 保存時に Markdown 構造が壊れるため）。
    const url = "/api/cards/raw?token=" + encodeURIComponent(TOKEN) + "&path=" + encodeURIComponent(path);
    const res = await fetch(url, { headers: { "X-Docsweep-Token": TOKEN } });
    if (!res.ok) return null;
    return await res.json().catch(() => null);
  }

  async function loadDetail(path) {
    // OKF 詳細（tags/owner/related/review_status/last_reviewed + 逆参照）を取りに行く。
    // frontmatter 無しファイルでも 200 で空値が返るので、UI は常に編集可能な状態で出す。
    const url = "/api/cards/detail?token=" + encodeURIComponent(TOKEN) + "&path=" + encodeURIComponent(path);
    const res = await fetch(url, { headers: { "X-Docsweep-Token": TOKEN } });
    if (!res.ok) return null;
    return await res.json().catch(() => null);
  }

  function fillOkfPane(detail) {
    const section = document.querySelector(".ep-okf");
    if (!section) return;
    section.hidden = false;
    const owner = section.querySelector("[data-bind='owner']");
    const tags = section.querySelector("[data-bind='tags']");
    const related = section.querySelector("[data-bind='related']");
    const reviewStatus = section.querySelector("[data-bind='review_status']");
    const lastReviewed = section.querySelector("[data-bind='last_reviewed']");
    const status = section.querySelector("[data-bind='okf-status']");
    if (owner) owner.value = (detail && detail.owner) || "";
    if (tags) tags.value = (detail && detail.tags || []).join(", ");
    if (related) related.value = (detail && detail.related || []).join(", ");
    if (reviewStatus) reviewStatus.value = (detail && detail.review_status) || "";
    if (lastReviewed) lastReviewed.textContent = (detail && detail.last_reviewed) || "—";
    if (status) status.textContent = "";

    const wrap = section.querySelector("[data-bind='backrefs']");
    const list = section.querySelector("[data-bind='backref-list']");
    const backrefs = (detail && detail.backrefs) || [];
    if (list) list.innerHTML = "";
    if (backrefs.length > 0 && list && wrap) {
      wrap.hidden = false;
      backrefs.forEach((b) => {
        const li = document.createElement("li");
        const stateSpan = document.createElement("span");
        stateSpan.className = "br-state";
        stateSpan.textContent = b.state_label || "[?]";
        const nameSpan = document.createElement("span");
        nameSpan.textContent = b.name + " — " + (b.title || "");
        li.appendChild(stateSpan);
        li.appendChild(nameSpan);
        list.appendChild(li);
      });
    } else if (wrap) {
      wrap.hidden = true;
    }
  }

  async function loadEditPane(card) {
    if (!card) return;
    currentPath = card.dataset.path;
    currentMtime = card.dataset.mtime;
    const pathLabel = $(".ep-path");
    if (pathLabel) pathLabel.textContent = currentPath;
    const mtimeLabel = $(".ep-mtime");
    if (mtimeLabel) mtimeLabel.textContent = "mtime=" + currentMtime;

    // プレビュー / 生 MD / OKF 詳細を並列取得（最後のは C4 で新設）。
    const [html, raw, detail] = await Promise.all([
      loadPreviewHtml(currentPath),
      loadRawContent(currentPath),
      loadDetail(currentPath),
    ]);
    fillOkfPane(detail);
    const previewEl = $(".ep-preview");
    if (previewEl) previewEl.innerHTML = html || ("<p>" + DS_T("preview_failed") + "</p>");

    const ta = $(".ep-textarea");
    if (ta) {
      if (raw && typeof raw.content === "string") {
        ta.value = raw.content;
        // raw の mtime を優先する（カードの dataset.mtime は board レンダリング時点・最新ではない）。
        if (raw.mtime) {
          currentMtime = String(raw.mtime);
          if (mtimeLabel) mtimeLabel.textContent = "mtime=" + currentMtime;
        }
      } else if (previewEl) {
        // フォールバック: raw が取れなければプレビューテキスト（保存はできるが構造劣化警告を出す）。
        ta.value = previewEl.textContent || "";
      }
    }
  }
  window.__docsweepLoadEditPane = loadEditPane;

  async function saveContent() {
    if (!currentPath) return;
    const ta = $(".ep-textarea");
    if (!ta) return;
    const sp = new URLSearchParams();
    sp.set("token", TOKEN);
    sp.set("path", currentPath);
    sp.set("content", ta.value);
    sp.set("expected_mtime", currentMtime || "");
    const res = await fetch("/api/cards/content", {
      method: "POST",
      headers: { "X-Docsweep-Token": TOKEN, "Content-Type": "application/x-www-form-urlencoded" },
      body: sp.toString(),
    });
    const body = await res.json().catch(() => null);
    if (res.status === 409) {
      window.alert(DS_T("save_conflict"));
      return;
    }
    if (!res.ok) {
      window.alert(DS_T("save_failed", body && body.detail ? body.detail : ("status " + res.status)));
      return;
    }
    currentMtime = body && body.new_mtime ? body.new_mtime : currentMtime;
    const mtimeLabel = $(".ep-mtime");
    if (mtimeLabel) mtimeLabel.textContent = DS_T("saved_mtime", currentMtime);
    if (typeof window.__docsweepReloadBoard === "function") {
      window.__docsweepReloadBoard();
    }
  }

  async function postOkfField(field, value) {
    if (!currentPath) return null;
    const sp = new URLSearchParams();
    sp.set("token", TOKEN);
    sp.set("path", currentPath);
    sp.set("field", field);
    sp.set("value", value);
    if (currentMtime) sp.set("expected_mtime", currentMtime);
    const res = await fetch("/api/cards/frontmatter", {
      method: "POST",
      headers: { "X-Docsweep-Token": TOKEN, "Content-Type": "application/x-www-form-urlencoded" },
      body: sp.toString(),
    });
    const body = await res.json().catch(() => null);
    if (res.status === 409) {
      window.alert(DS_T("fm_save_conflict"));
      return null;
    }
    if (!res.ok) {
      window.alert(DS_T("fm_save_failed", body && body.detail ? body.detail : ("status " + res.status)));
      return null;
    }
    if (body && body.new_mtime) currentMtime = String(body.new_mtime);
    return body;
  }

  async function saveOkf() {
    const section = document.querySelector(".ep-okf");
    if (!section) return;
    const owner = section.querySelector("[data-bind='owner']").value || "";
    const tags = section.querySelector("[data-bind='tags']").value || "";
    const related = section.querySelector("[data-bind='related']").value || "";
    const reviewStatus = section.querySelector("[data-bind='review_status']").value || "";
    const status = section.querySelector("[data-bind='okf-status']");
    // 1 フィールドずつ書く（後方互換を保ちつつ、不正値のフィールドだけ弾く挙動になる）。
    let lastOk = null;
    for (const [field, value] of [["owner", owner], ["tags", tags], ["related", related], ["review_status", reviewStatus]]) {
      const r = await postOkfField(field, value);
      if (r === null) { if (status) status.textContent = DS_T("okf_aborted", field); return; }
      lastOk = r;
    }
    if (status) status.textContent = DS_T("okf_saved", currentMtime);
    if (typeof window.__docsweepReloadBoard === "function") {
      window.__docsweepReloadBoard();
    }
  }

  async function claim(unclaim) {
    if (!currentPath) return;
    const sp = new URLSearchParams();
    sp.set("token", TOKEN);
    sp.set("path", currentPath);
    if (unclaim) sp.set("unclaim", "true");
    if (currentMtime) sp.set("expected_mtime", currentMtime);
    const res = await fetch("/api/cards/claim", {
      method: "POST",
      headers: { "X-Docsweep-Token": TOKEN, "Content-Type": "application/x-www-form-urlencoded" },
      body: sp.toString(),
    });
    const body = await res.json().catch(() => null);
    if (!res.ok) {
      window.alert(DS_T("claim_failed", body && body.detail ? body.detail : ("status " + res.status)));
      return;
    }
    if (body && body.new_mtime) currentMtime = String(body.new_mtime);
    const ownerInput = document.querySelector(".ep-okf [data-bind='owner']");
    if (ownerInput) ownerInput.value = (body && body.owner) || "";
    if (typeof window.__docsweepReloadBoard === "function") {
      window.__docsweepReloadBoard();
    }
  }

  document.addEventListener("click", (e) => {
    const tab = e.target.closest && e.target.closest(".ep-tab");
    if (tab) { setTab(tab.dataset.tab); return; }
    const saveBtn = e.target.closest && e.target.closest("[data-action='save-content']");
    if (saveBtn) { e.preventDefault(); saveContent(); return; }
    const okfBtn = e.target.closest && e.target.closest("[data-action='save-okf']");
    if (okfBtn) { e.preventDefault(); saveOkf(); return; }
    const claimBtn = e.target.closest && e.target.closest("[data-action='claim']");
    if (claimBtn) { e.preventDefault(); claim(false); return; }
    const unclaimBtn = e.target.closest && e.target.closest("[data-action='unclaim']");
    if (unclaimBtn) { e.preventDefault(); claim(true); return; }
  });

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
      // 編集ペインがアクティブなときのみ反応（textarea / ep-* 領域内）。
      const inEditor = e.target.closest && e.target.closest(".edit-pane");
      if (inEditor) {
        e.preventDefault();
        saveContent();
      }
    }
  });
})();
