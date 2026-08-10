// brief ページの操作（今は「context をコピー」だけ）。
//
// アプリの CSP は `script-src 'self'` で inline script も inline イベント属性
// （onclick= 等）も許可していない。template へ戻すと CSP に弾かれ、ボタンが
// 無反応になったことにも気づけないので、必ず外部 js + data-action で書く。

(function () {
  "use strict";

  const dataEl = document.getElementById("page-data");
  const MSG = dataEl ? JSON.parse(dataEl.textContent) : {};

  async function copyContext(path) {
    // 簡易: クリップボード API で path を打ち込んで、後段で context CLI 実行を促す。
    // 本格対応は別 endpoint (POST /api/brief/context) を作る予定。
    try {
      await navigator.clipboard.writeText(path);
      alert((MSG.copied || "{path}").replace("{path}", path));
    } catch (e) {
      alert((MSG.copyFailed || "") + e);
    }
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest('[data-action="copy-context"]');
    if (!btn) return;
    copyContext(btn.dataset.path || "");
  });
})();
