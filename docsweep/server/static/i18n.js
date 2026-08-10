/* docsweep — Web UI の JS 側文言辞書（ja / en）。
   - サーバー側の正本は docsweep/server/i18n.py（テンプレ文言）。JS 実行時文言は本ファイルが持つ。
   - 言語は <body data-lang> で決まる（?lang= / cookie / config.lang をサーバーが解決済み）。
   - CSP script-src 'self' を維持するため、テンプレからのインライン注入はしない（静的 2 言語テーブル）。
   - 置換は "{0}" "{1}" の位置引数。DS_T("key", a, b) で埋める。 */
(function () {
  "use strict";

  const TABLES = {
    ja: {
      loading: "読み込み中…",
      settings_load_failed: "設定の読み込みに失敗: {0}",
      inject_failed: "注入失敗: {0}",
      injected: "✓ 注入しました{0}",
      eject_failed: "eject 失敗: {0}",
      ejected: "✓ eject しました（{0} 件除去）",
      hits: "{0} 件ヒット",
      undo_failed: "Undo 失敗: {0}",
      undo_nothing: "Undo 対象がありません（既に復元済み or バッチ ID 無し）",
      undo_restored: "✓ {0} 件を復元しました",
      undo_restored_failed_suffix: "（{0} 件失敗）",
      work_pack_ok: "クリップボードへコピーしました",
      work_pack_fail: "コピーに失敗しました",
      suggestions_empty: "提案はありません",
      suggestions_accept: "採用",
      suggestions_skip: "後で",
      shutdown_confirm: "docsweep のサーバーを停止します。停止後はブラウザのリロードでは再起動できません（端末で再度 docsweep serve を実行してください）。よろしいですか？",
      shutdown_done_title: "サーバーを停止しました",
      shutdown_done_body: "再度開くには端末で <code>docsweep serve</code> を実行してください。",
      sec_overdue: "🔴やり忘れ",
      sec_today: "🟡今日",
      sec_active: "🟢実行中",
      sec_graduate: "▼卒業判定",
      sec_future: "▶未来期日",
      sec_no_due: "▶期日未設定",
      sec_archivable: "▶archive候補",
      bp_all_on: "✓ 全プロジェクト ON",
      bp_all_off: "✕ 全 OFF",
      no_response: "（応答なし）",
      ok_count: "成功 {0} 件",
      fail_count: "／失敗 {0} 件",
      skip_count: "／スキップ {0} 件（archive 不可ラベル等）",
      fail_head: "\n失敗:\n",
      fail_more: "\n  …他 {0} 件",
      no_selection: "選択されているカードがありません。",
      no_target: "対象カードがありません（選択 or セクション内が空）。",
      bulk_due: "期日を {0} に更新",
      bulk_status: "{0} に変更",
      bulk_status_archive: "{0} に変更 → archive へ移送",
      bulk_archive: "archive 配下へ移送",
      confirm_danger: "⚠ {0} 件のファイルを {1} します。元には戻せません（archive から手動復元）。よろしいですか？",
      confirm_normal: "{0} 件のファイルを {1} します。よろしいですか？",
      api_failed: "API 失敗: {0}\n{1}",
      archived_n: "{0} 件を archive へ移送しました",
      archived_one: "1 件を archive へ移送しました",
      confirm_done_single: "このファイルを {0} にして archive へ移送します。よろしいですか？",
      status_change_failed: "ラベル変更に失敗しました: {0}",
      due_change_failed: "期日変更に失敗しました: {0}",
      state_planned: "[計画]",
      state_in_progress: "[実行中]",
      state_watching: "[様子見]",
      state_pending: "[保留]",
      state_done: "[完了]",
      state_discarded: "[廃止]",
      help:
        "キーボードショートカット:\n" +
        "  数字 1-5 = ラベル変更（1=計画 2=実行中 3=様子見 4=保留 5=完了・廃止は下段ボタン）\n" +
        "  d = +1 日 / w = +1 週 / m = +1 ヶ月 / Shift+D = -1 日\n" +
        "  a = 画面全カード選択 / Esc = ピッカーを閉じる + 選択解除\n" +
        "  Tab = カード巡回 / Ctrl+S = 編集ペインを保存\n" +
        "  ※ 状態 / 期日のバッジは表示専用です。変更はカード下段の「変更▾」「期日更新▾」ボタンから行ってください。",
      dnd_future_prompt: "いつまで先送りしますか？（YYYY-MM-DD または +1w / +1m）",
      preview_failed: "プレビューを取得できませんでした。",
      saved_mtime: "保存しました (mtime={0})",
      save_conflict: "保存に失敗しました: 他のエディタが同じファイルを変更しています（mtime conflict）。\nカードを再選択して最新版を読み込み直してください。",
      save_failed: "保存に失敗しました: {0}",
      fm_save_conflict: "frontmatter 保存に失敗しました: 他のエディタが同じファイルを変更しています（mtime conflict）。\nカードを再選択して最新版を読み込み直してください。",
      fm_save_failed: "frontmatter 保存に失敗しました: {0}",
      okf_aborted: "保存中断（{0}）",
      okf_saved: "✓ 保存しました (mtime={0})",
      claim_failed: "claim 失敗: {0}",
      roots_failed: "ルート変更に失敗: {0}",
      roots_added: "✓ スキャンルートを追加しました",
      roots_removed: "✓ スキャンルートを削除しました",
      roots_runtime_only: "画面には反映しましたが config.yaml への保存に失敗: {0}",
      roots_remove_confirm: "スキャンルート {0} を外します（ファイルは削除されません）。よろしいですか？",
    },
    en: {
      loading: "Loading…",
      settings_load_failed: "Failed to load settings: {0}",
      inject_failed: "Inject failed: {0}",
      injected: "✓ Injected{0}",
      eject_failed: "Eject failed: {0}",
      ejected: "✓ Ejected ({0} block(s) removed)",
      hits: "{0} hit(s)",
      undo_failed: "Undo failed: {0}",
      undo_nothing: "Nothing to undo (already restored, or no batch ID)",
      undo_restored: "✓ Restored {0} file(s)",
      undo_restored_failed_suffix: " ({0} failed)",
      work_pack_ok: "Copied to clipboard",
      work_pack_fail: "Copy failed",
      suggestions_empty: "No suggestions",
      suggestions_accept: "Accept",
      suggestions_skip: "Later",
      shutdown_confirm: "This stops the docsweep server. After that, reloading the browser cannot restart it (run docsweep serve again in a terminal). Continue?",
      shutdown_done_title: "Server stopped",
      shutdown_done_body: "Run <code>docsweep serve</code> in a terminal to open it again.",
      sec_overdue: "🔴Overdue",
      sec_today: "🟡Today",
      sec_active: "🟢In progress",
      sec_graduate: "▼Graduation",
      sec_future: "▶Future due",
      sec_no_due: "▶No due",
      sec_archivable: "▶Archive candidates",
      bp_all_on: "✓ All projects ON",
      bp_all_off: "✕ All OFF",
      no_response: "(no response)",
      ok_count: "{0} succeeded",
      fail_count: " / {0} failed",
      skip_count: " / {0} skipped (non-archivable label etc.)",
      fail_head: "\nFailed:\n",
      fail_more: "\n  …and {0} more",
      no_selection: "No cards are selected.",
      no_target: "No target cards (selection or section is empty).",
      bulk_due: "set the due date to {0}",
      bulk_status: "change the label to {0}",
      bulk_status_archive: "change the label to {0} → move to archive",
      bulk_archive: "move into archive/",
      confirm_danger: "⚠ This will {1} for {0} file(s). This cannot be undone from here (restore manually from archive). Continue?",
      confirm_normal: "This will {1} for {0} file(s). Continue?",
      api_failed: "API failed: {0}\n{1}",
      archived_n: "Moved {0} file(s) to archive",
      archived_one: "Moved 1 file to archive",
      confirm_done_single: "This marks the file as {0} and moves it to archive. Continue?",
      status_change_failed: "Failed to change the label: {0}",
      due_change_failed: "Failed to change the due date: {0}",
      state_planned: "[Planned]",
      state_in_progress: "[In Progress]",
      state_watching: "[Watching]",
      state_pending: "[Pending]",
      state_done: "[Done]",
      state_discarded: "[Discarded]",
      help:
        "Keyboard shortcuts:\n" +
        "  1-5 = change label (1=Planned 2=In Progress 3=Watching 4=Pending 5=Done; Discard via the bottom button)\n" +
        "  d = +1 day / w = +1 week / m = +1 month / Shift+D = -1 day\n" +
        "  a = select all cards on screen / Esc = close picker + clear selection\n" +
        "  Tab = cycle cards / Ctrl+S = save the edit pane\n" +
        "  Note: the state / due badges are display-only. Use the \"Change ▾\" / \"Due ▾\" buttons on each card.",
      dnd_future_prompt: "Postpone until when? (YYYY-MM-DD or +1w / +1m)",
      preview_failed: "Could not fetch the preview.",
      saved_mtime: "Saved (mtime={0})",
      save_conflict: "Save failed: another editor changed the same file (mtime conflict).\nRe-select the card to reload the latest version.",
      save_failed: "Save failed: {0}",
      fm_save_conflict: "Failed to save frontmatter: another editor changed the same file (mtime conflict).\nRe-select the card to reload the latest version.",
      fm_save_failed: "Failed to save frontmatter: {0}",
      okf_aborted: "Save aborted ({0})",
      okf_saved: "✓ Saved (mtime={0})",
      claim_failed: "claim failed: {0}",
      roots_failed: "Failed to change roots: {0}",
      roots_added: "✓ Scan root added",
      roots_removed: "✓ Scan root removed",
      roots_runtime_only: "Applied to this session, but saving to config.yaml failed: {0}",
      roots_remove_confirm: "Remove the scan root {0}? (No files are deleted.)",
    },
  };

  const lang = (document.body && document.body.dataset.lang) === "en" ? "en" : "ja";
  const table = TABLES[lang];

  window.DS_LANG = lang;
  window.DS_T = function (key) {
    let s = table[key];
    if (s === undefined) s = TABLES.ja[key];
    if (s === undefined) return key;
    for (let i = 1; i < arguments.length; i++) {
      s = s.split("{" + (i - 1) + "}").join(String(arguments[i]));
    }
    return s;
  };
})();
