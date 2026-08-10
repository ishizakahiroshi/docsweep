// capture ページ（貼り付けたテキストから draft を起こして保存する画面）。
//
// アプリの CSP は `script-src 'self'` で inline script を許可していない。
// template へ戻すとフォームの submit ハンドラが 1 つも付かず、
// 「ボタンを押しても何も起きない」だけの画面になるので、必ず外部 js で書く。

(function () {
  "use strict";

  const dataEl = document.getElementById("page-data");
  const PAGE = dataEl ? JSON.parse(dataEl.textContent) : {};
  const TOKEN = PAGE.token || "";
  const MSG = PAGE.msg || {};

  let lastDrafts = [];

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function renderDrafts(drafts) {
    const area = document.getElementById("drafts-area");
    area.innerHTML = "";
    if (drafts.length === 0) {
      area.innerHTML = "<p>" + escapeHtml(MSG.noDrafts) + "</p>";
      document.getElementById("save-area").style.display = "none";
      return;
    }
    drafts.forEach((d, idx) => {
      const div = document.createElement("div");
      div.className = "draft";
      div.innerHTML = `
      <label>
        <input type="checkbox" data-idx="${idx}" checked>
        <span class="kind">${escapeHtml(d.kind)}</span>
        <span class="fname">${escapeHtml(d.suggested_filename)}</span>
      </label>
      <div class="title">${escapeHtml(d.title)}</div>
      <pre>${escapeHtml(d.body.substring(0, 400))}</pre>
    `;
      div.querySelector("input").addEventListener("change", (e) => {
        div.classList.toggle("checked", e.target.checked);
      });
      div.classList.add("checked");
      area.appendChild(div);
    });
    document.getElementById("save-area").style.display = "block";
  }

  document.getElementById("capture-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = document.getElementById("text").value;
    const useLLM = document.getElementById("use-llm").checked;
    const res = await fetch("/api/capture/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-docsweep-token": TOKEN },
      body: JSON.stringify({ text, use_llm: useLLM }),
    });
    if (!res.ok) {
      alert(MSG.extractFailed + res.status);
      return;
    }
    const data = await res.json();
    lastDrafts = data.drafts || [];
    renderDrafts(lastDrafts);
  });

  document.getElementById("save-btn").addEventListener("click", async () => {
    const selected = [];
    document.querySelectorAll("#drafts-area input[type=checkbox]").forEach(cb => {
      if (cb.checked) selected.push(lastDrafts[parseInt(cb.dataset.idx)]);
    });
    if (selected.length === 0) {
      alert(MSG.selectOne);
      return;
    }
    const res = await fetch("/api/capture/save", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-docsweep-token": TOKEN },
      body: JSON.stringify({ drafts: selected }),
    });
    if (!res.ok) {
      alert(MSG.saveFailed + res.status);
      return;
    }
    const data = await res.json();
    const area = document.getElementById("result-area");
    area.innerHTML = "<h3>" + escapeHtml(MSG.saved) + "</h3><ul>" +
      data.saved.map(p => `<li>${escapeHtml(p)}</li>`).join("") + "</ul>";
  });
})();
