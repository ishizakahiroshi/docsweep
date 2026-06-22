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

  async function loadEditPane(card) {
    if (!card) return;
    currentPath = card.dataset.path;
    currentMtime = card.dataset.mtime;
    const pathLabel = $(".ep-path");
    if (pathLabel) pathLabel.textContent = currentPath;
    const mtimeLabel = $(".ep-mtime");
    if (mtimeLabel) mtimeLabel.textContent = "mtime=" + currentMtime;

    // プレビュー（既存 /preview ルート）と生 MD（新規 /api/cards/raw）を並列取得する。
    const [html, raw] = await Promise.all([
      loadPreviewHtml(currentPath),
      loadRawContent(currentPath),
    ]);
    const previewEl = $(".ep-preview");
    if (previewEl) previewEl.innerHTML = html || "<p>プレビューを取得できませんでした。</p>";

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
      window.alert(
        "保存に失敗しました: 他のエディタが同じファイルを変更しています（mtime conflict）。\n" +
        "カードを再選択して最新版を読み込み直してください。"
      );
      return;
    }
    if (!res.ok) {
      window.alert("保存に失敗しました: " + (body && body.detail ? body.detail : ("status " + res.status)));
      return;
    }
    currentMtime = body && body.new_mtime ? body.new_mtime : currentMtime;
    const mtimeLabel = $(".ep-mtime");
    if (mtimeLabel) mtimeLabel.textContent = "保存しました (mtime=" + currentMtime + ")";
    if (typeof window.__docsweepReloadBoard === "function") {
      window.__docsweepReloadBoard();
    }
  }

  document.addEventListener("click", (e) => {
    const tab = e.target.closest && e.target.closest(".ep-tab");
    if (tab) { setTab(tab.dataset.tab); return; }
    const saveBtn = e.target.closest && e.target.closest("[data-action='save-content']");
    if (saveBtn) { e.preventDefault(); saveContent(); return; }
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
