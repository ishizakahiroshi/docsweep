// htmx は /static/htmx.min.js で window.htmx としてグローバルに提供される。
// htmx.org パッケージの named export 型から global const を宣言する。
declare const htmx: typeof import('htmx.org');
