/* docsweep — Web UI の JS 側文言（DS_T）。
   - 文言の正本は docsweep/i18n/locales/<言語>/ui.json の js.* キー。本ファイルは文言を持たない。
   - サーバーが表示言語 1 つ分の表を <script type="application/json" id="ds-i18n"> に埋める
     （JSON は実行されないので CSP script-src 'self' のまま渡せる。server/i18n.py の js_messages）。
   - 言語は <body data-lang> で決まる（?lang= / cookie / 設定・OS をサーバーが解決済み）。
   - 置換は "{0}" "{1}" の位置引数。DS_T("key", a, b) で埋める。未知のキーはキー名をそのまま返す。 */
(function () {
  "use strict";

  let table = {};
  const dataEl = document.getElementById("ds-i18n");
  if (dataEl) {
    try {
      table = JSON.parse(dataEl.textContent) || {};
    } catch (e) {
      table = {};
    }
  }

  window.DS_LANG = (document.body && document.body.dataset.lang) || "";
  window.DS_T = function (key) {
    const s = Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;
    if (typeof s !== "string") return key;
    const args = Array.prototype.slice.call(arguments, 1);
    // 1 回の走査で埋める（埋めた値の中の "{1}" 等を続けて置換しない）。対応する引数が無い番号は残す。
    return s.replace(/\{(\d+)\}/g, function (m, i) {
      return Number(i) < args.length ? String(args[Number(i)]) : m;
    });
  };
})();
