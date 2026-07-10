"""Web UI の文言辞書（ja / en）— 単一正本。

設計の正本: docs/local/plan_v0.2.0-english-support.md §C1

- テンプレートへは ``get_messages(lang)`` の返す解決済み dict ``T`` を渡し
  ``{{ T.key }}`` で参照する（テンプレに言語分岐を書かない）。
- lang の解決順は ``?lang=`` クエリ > cookie ``docsweep_lang`` > config.lang > "ja"。
  cookie は設定モーダルの言語トグルが書く（config.yaml は書き換えない＝ユーザー設定温存）。
- JS 側の文言は static/i18n.js が両言語テーブルを静的に持つ（CSP script-src 'self' を維持）。
  サーバー側とキー体系を揃えるが、辞書自体は共有しない（テンプレ経由の注入をしないため）。
"""

from __future__ import annotations

from fastapi import Request

LANG_COOKIE = "docsweep_lang"
SUPPORTED_LANGS = ("ja", "en")

# key -> {ja, en}。プレースホルダは str.format 形式（{n} 等）。
MESSAGES: dict[str, dict[str, str]] = {
    # ---- board.html（トップバー / バルクバー / ダイアログ） ----
    # 2026-07-03: トップバーの「看板（カンバン）/ Kanban」サブタイトルは冗長のため撤去
    # （plan_web-roots-management §C2）。タブタイトルもプレーンな "docsweep" に統一。
    "board_page_title": {"ja": "docsweep", "en": "docsweep"},
    "search_placeholder": {
        "ja": "🔍 絞り込み: 自由語 / tag:foo / owner:me / status:計画 （/ でフォーカス）",
        "en": "🔍 Filter: free text / tag:foo / owner:me / status:planned (press / to focus)",
    },
    "search_clear_title": {"ja": "検索をクリア (Esc)", "en": "Clear search (Esc)"},
    "health_title": {
        "ja": "プロジェクトごとの最古経過日数（上位 {n} 件）",
        "en": "Oldest age in days per project (top {n})",
    },
    "reload_board": {"ja": "再スキャン", "en": "Rescan"},
    "shutdown_title": {"ja": "サーバーを停止", "en": "Stop the server"},
    "settings_btn_title": {"ja": "設定 / 注入", "en": "Settings / Inject"},
    "settings_btn": {"ja": "設定", "en": "Settings"},
    "bulk_aria": {"ja": "選択中の一括操作", "en": "Bulk actions for selection"},
    "bulk_selected_suffix": {"ja": "件選択中", "en": "selected"},
    "bulk_breakdown_title": {"ja": "選択カードのセクション内訳", "en": "Sections of the selected cards"},
    "bulk_projects": {"ja": "📂 プロジェクト", "en": "📂 Projects"},
    "bulk_projects_title": {
        "ja": "プロジェクトで絞り込み（チェックで ON/OFF）",
        "en": "Filter by project (toggle with checkboxes)",
    },
    "plus_1d": {"ja": "+1日", "en": "+1d"},
    "plus_1w": {"ja": "+1週", "en": "+1w"},
    "bulk_date": {"ja": "📅 日付", "en": "📅 Date"},
    "bulk_date_title": {"ja": "絶対日付で期日を一括設定", "en": "Set an absolute due date for all"},
    "change_label": {"ja": "ラベル変更▾", "en": "Change label ▾"},
    "bulk_label_title": {
        "ja": "選択カードを別のラベル ([計画]/[実行中]/[様子見]/[保留]/[完了]/[廃止]) に一括変更",
        "en": "Change the label of all selected cards ([Planned]/[In Progress]/[Watching]/[Pending]/[Done]/[Discarded])",
    },
    "bulk_clear": {"ja": "解除", "en": "Clear"},
    "bulk_clear_title": {"ja": "選択を解除（Esc）", "en": "Clear selection (Esc)"},
    "select_all_link": {"ja": "↳ 全選択", "en": "↳ Select all"},
    "select_all_title": {"ja": "画面の全カードを選択 (a キー)", "en": "Select every card on screen (key: a)"},
    "close_esc": {"ja": "閉じる (Esc)", "en": "Close (Esc)"},
    "close": {"ja": "閉じる", "en": "Close"},
    "loading": {"ja": "読み込み中…", "en": "Loading…"},
    "bulk_due_before": {"ja": "選択中の", "en": "Change the due date of"},
    "bulk_due_after": {"ja": "件の期日を変更します", "en": "selected cards"},
    "date_label": {"ja": "日付:", "en": "Date:"},
    "cancel": {"ja": "キャンセル", "en": "Cancel"},
    "set_btn": {"ja": "設定", "en": "Set"},
    "confirm_default": {"ja": "本当に実行しますか？", "en": "Are you sure?"},
    "execute": {"ja": "実行", "en": "OK"},

    # ---- _board_body.html（列 / セクション） ----
    "sec_select_all": {"ja": "✓ 全選択", "en": "✓ Select all"},
    "sec_select_all_title": {"ja": "このセクションを全選択", "en": "Select all cards in this section"},
    "sec_plus1d_title": {"ja": "全て +1 日先送り", "en": "Postpone all by 1 day"},
    "sec_plus1w_title": {"ja": "全て +1 週先送り", "en": "Postpone all by 1 week"},
    "sec_label_title": {
        "ja": "全カードを別のラベル ([計画]/[実行中]/[様子見]/[保留]/[完了]/[廃止]) に一括変更",
        "en": "Change the label of every card here ([Planned]/[In Progress]/[Watching]/[Pending]/[Done]/[Discarded])",
    },
    "to_archive": {"ja": "archive へ", "en": "To archive"},
    "to_archive_title": {"ja": "全て archive 配下へ移送", "en": "Move all into archive/"},
    "col_overdue": {"ja": "やり忘れ", "en": "Overdue"},
    "col_overdue_aria": {"ja": "やり忘れ列", "en": "Overdue column"},
    "col_today": {"ja": "今日", "en": "Today"},
    "col_today_aria": {"ja": "今日列", "en": "Today column"},
    "col_active": {"ja": "実行中", "en": "In progress"},
    "col_active_aria": {"ja": "実行中列", "en": "In-progress column"},
    "sec_graduate": {
        "ja": "卒業判定（[様子見] かつ overdue_graduate）",
        "en": "Graduation check ([Watching] & overdue_graduate)",
    },
    "sec_future": {"ja": "未来期日", "en": "Future due"},
    "sec_no_due": {"ja": "期日未設定", "en": "No due date"},
    "sec_archivable": {"ja": "archive 候補（[完了]/[廃止]）", "en": "Archive candidates ([Done]/[Discarded])"},
    "empty_overdue": {
        "ja": "やり忘れは 0 件。今日はきれい。上の「今日の 1 個」へ。",
        "en": "No overdue items. Clear day — open Today's pick above.",
    },
    "empty_today": {
        "ja": "今日 due のカードはなし。ピンの 1 個から始めてもよい。",
        "en": "Nothing due today. Start from the pinned pick if you like.",
    },
    "empty_active": {
        "ja": "実行中は空。着手したらここに並びます。",
        "en": "Nothing in progress yet. Cards appear here when you start work.",
    },
    "today_pick_aria": {"ja": "今日の 1 個", "en": "Today's pick"},
    "today_pick_label": {"ja": "今日の 1 個", "en": "Today's pick"},
    "today_pick_open": {"ja": "編集を開く", "en": "Open editor"},
    "work_pack_copy": {"ja": "AI に渡す", "en": "Copy for AI"},
    "work_pack_title": {
        "ja": "path / 本文 / related を context としてクリップボードへ",
        "en": "Copy path / body / related as context to clipboard",
    },
    "work_pack_ok": {"ja": "クリップボードへコピーしました", "en": "Copied to clipboard"},
    "work_pack_fail": {"ja": "コピーに失敗しました", "en": "Copy failed"},

    # ---- _card.html ----
    "card_check_title": {"ja": "選択（一括操作対象）", "en": "Select (target of bulk actions)"},
    "postpone_title": {"ja": "先送り回数", "en": "Postpone count"},
    "due_none": {"ja": "📅 期日未設定", "en": "📅 No due"},
    "proj_badge_title": {
        "ja": "クリック: このプロジェクトだけ全選択（他解除）・再クリック全解除 ／ Shift+クリック: このプロジェクトを追加（既存維持）",
        "en": "Click: select only this project (clears others), click again to clear / Shift+click: add this project to the selection",
    },
    "related_title": {"ja": "related: {n} 件", "en": "related: {n}"},
    "backref_title": {
        "ja": "このファイルを参照している plan/bugfix/pending: {n} 件",
        "en": "plan/bugfix/pending files referencing this one: {n}",
    },
    "change_btn": {"ja": "変更▾", "en": "Change ▾"},
    "change_btn_title": {
        "ja": "状態を変える（[計画]/[実行中]/[様子見]/[保留]/[完了]/[廃止] から選択）",
        "en": "Change the state ([Planned]/[In Progress]/[Watching]/[Pending]/[Done]/[Discarded])",
    },
    "due_btn": {"ja": "期日更新▾", "en": "Due ▾"},
    "due_btn_title": {"ja": "期日を変える", "en": "Change the due date"},
    # _card_view の due バッジ文言（サーバー側で組み立て）
    "due_overdue_by": {"ja": "{n} 日超過", "en": "{n}d overdue"},
    "due_today": {"ja": "今日", "en": "today"},
    "due_in": {"ja": "あと {n} 日", "en": "in {n}d"},
    "due_invalid": {"ja": "期日不正", "en": "invalid due"},

    # ---- _edit_pane.html ----
    "tab_preview": {"ja": "プレビュー", "en": "Preview"},
    "tab_edit": {"ja": "編集", "en": "Edit"},
    "select_card_hint": {"ja": "（カードを選択してください）", "en": "(select a card)"},
    "not_set": {"ja": "（未設定）", "en": "(not set)"},
    "claim_title": {"ja": "自分を owner にセット", "en": "Set yourself as owner"},
    "unclaim_title": {"ja": "owner を空にする", "en": "Clear the owner"},
    "tags_placeholder": {"ja": "カンマ区切り（例: ui, backend）", "en": "comma separated (e.g. ui, backend)"},
    "related_placeholder": {
        "ja": "カンマ区切り（例: plan_x.md, bugfix_y.md）",
        "en": "comma separated (e.g. plan_x.md, bugfix_y.md)",
    },
    "save_changes": {"ja": "変更を保存", "en": "Save changes"},
    "backrefs_heading": {"ja": "↩ このファイルを参照しているファイル", "en": "↩ Files referencing this one"},
    "save_ctrl_s": {"ja": "保存 (Ctrl+S)", "en": "Save (Ctrl+S)"},
    "edit_body_aria": {"ja": "本文編集", "en": "Edit body"},

    # ---- _preview.html ----
    "open_editor": {"ja": "エディタで開く", "en": "Open in editor"},
    "open_folder": {"ja": "📁 フォルダを開く", "en": "📁 Reveal in folder"},

    # ---- ピッカー ----
    "label_picker_aria": {"ja": "ラベルを選択", "en": "Choose a label"},
    "change_picker_aria": {"ja": "状態を選択", "en": "Choose a state"},
    "due_picker_aria": {"ja": "期日を変更", "en": "Change the due date"},
    "dp_plus1d": {"ja": "+1 日 (d)", "en": "+1 day (d)"},
    "dp_plus3d": {"ja": "+3 日", "en": "+3 days"},
    "dp_plus1w": {"ja": "+1 週 (w)", "en": "+1 week (w)"},
    "dp_plus1m": {"ja": "+1 ヶ月 (m)", "en": "+1 month (m)"},
    "dp_today": {"ja": "今日", "en": "Today"},
    "dp_custom": {"ja": "任意日付", "en": "Custom date"},

    # ---- _settings.html ----
    "settings_heading": {"ja": "⚙ 設定 / 注入", "en": "⚙ Settings / Inject"},
    "settings_lang_heading": {"ja": "表示言語", "en": "Display language"},
    "settings_lang_note": {
        "ja": "Web UI の表示言語。cookie に保存される（~/.docsweep/config.yaml の lang: は変更しない）。",
        "en": "Display language of the Web UI. Stored in a cookie (does not modify lang: in ~/.docsweep/config.yaml).",
    },
    "settings_roots_heading": {"ja": "スキャンルート", "en": "Scan roots"},
    "settings_roots_note": {
        "ja": "docsweep が走査するフォルダ。親ディレクトリでも個別プロジェクトフォルダでも追加できる"
              "（~/.docsweep/config.yaml の roots: に永続化。--root 起動中も画面には即反映される）。",
        "en": "Folders docsweep scans. You can add a parent directory or an individual project folder"
              " (persisted to roots: in ~/.docsweep/config.yaml; takes effect immediately even when"
              " started with --root).",
    },
    "settings_roots_remove": {"ja": "削除", "en": "Remove"},
    "settings_roots_add": {"ja": "追加", "en": "Add"},
    "settings_roots_last": {"ja": "最後の 1 個は削除できません", "en": "The last root cannot be removed"},
    "settings_roots_placeholder": {
        "ja": "絶対パス（例: C:/dev/github/public または C:/dev/my-project）",
        "en": "Absolute path (e.g. C:/dev/github/public or C:/dev/my-project)",
    },
    "settings_global_heading": {
        "ja": "グローバル運用ルール導線（全プロジェクトで効く）",
        "en": "Global guidance hook (applies to all projects)",
    },
    "settings_global_note": {
        "ja": "セッション開始時に docsweep triage を読む導線を AI ツールのグローバル設定へ注入する。",
        "en": "Injects the session-start hook (read docsweep triage first) into the AI tool's global settings.",
    },
    "settings_injected": {"ja": "✓ 注入済", "en": "✓ injected"},
    "settings_not_injected": {"ja": "未注入", "en": "not injected"},
    "settings_version_title": {
        "ja": "注入されている導線ブロックの版",
        "en": "Version of the injected guidance block",
    },
    "settings_project_heading": {
        "ja": "プロジェクト別運用ルール（プロジェクトの CLAUDE.md と .docsweep.yaml に注入）",
        "en": "Per-project rules (injected into the project's CLAUDE.md and .docsweep.yaml)",
    },
    "settings_preset_note": {"ja": "プリセット: 既定", "en": "Preset: default"},
    "settings_col_project": {"ja": "プロジェクト", "en": "Project"},
    "settings_col_state": {"ja": "状態", "en": "State"},
    "settings_col_actions": {"ja": "操作", "en": "Actions"},
    "settings_toggle_project": {
        "ja": "看板・scan から除外 / 復帰",
        "en": "Exclude from board/scan or re-enable",
    },
    "settings_project_on": {"ja": "ON", "en": "ON"},
    "settings_project_off": {"ja": "OFF", "en": "OFF"},
    "profile_switch_title": {
        "ja": "プロファイル切替（config profiles）",
        "en": "Switch profile (config profiles)",
    },
    "suggestions_btn": {"ja": "提案", "en": "Suggestions"},
    "suggestions_title": {
        "ja": "auto-triage 提案トレイ",
        "en": "auto-triage suggestion tray",
    },
    "suggestions_empty": {"ja": "提案はありません", "en": "No suggestions"},
    "suggestions_accept": {"ja": "採用", "en": "Accept"},
    "suggestions_skip": {"ja": "後で", "en": "Later"},
    "settings_no_projects": {
        "ja": "スキャン対象にプロジェクトがありません。",
        "en": "No projects found in the scan roots.",
    },
    "about_heading": {"ja": "このアプリについて / ライセンス", "en": "About & Licenses"},
    "about_description": {
        "ja": "AI コーディングツールが生成する plan / bugfix / pending md の蓄積・陳腐化を解決する"
              "クロスプラットフォーム CLI + Web UI + MCP ツール。処理はすべてローカルで完結します。",
        "en": "A cross-platform CLI + Web UI + MCP tool that solves the accumulation and staleness"
              " of plan / bugfix / pending md files generated by AI coding tools."
              " All processing stays local.",
    },
    "about_license_heading": {"ja": "アプリのライセンス", "en": "App License"},
    "about_license_text": {
        "ja": "docsweep は MIT License で配布されています。Copyright (c) 2026 Hiroshi Ishizaka (ishizakahiroshi)。",
        "en": "docsweep is distributed under the MIT License. Copyright (c) 2026 Hiroshi Ishizaka (ishizakahiroshi).",
    },
    "about_oss_heading": {"ja": "同梱しているオープンソースソフトウェア", "en": "Included Open Source Software"},
    "about_oss_htmx": {
        "ja": "0BSD License。Web UI の hypermedia ライブラリとして同梱。",
        "en": "0BSD License. Bundled as the hypermedia library for the Web UI.",
    },
    "about_cdn_heading": {"ja": "CDN から読み込むソフトウェア", "en": "Software loaded from CDN"},
    "about_oss_cytoscape": {
        "ja": "MIT License。graph ページのネットワーク可視化に使用（同梱せず unpkg CDN から取得）。",
        "en": "MIT License. Used for the network view on the graph page (fetched from the unpkg CDN, not bundled).",
    },
    "about_deps_heading": {"ja": "実行時依存（pip で個別取得）", "en": "Runtime dependencies (installed via pip)"},
    "about_deps_note": {
        "ja": "以下は wheel に同梱されず、pip install 時に PyPI から個別に取得されます"
              "（各パッケージが自身のライセンスを同梱）: PyYAML / FastAPI / uvicorn / Jinja2 /"
              " markdown / python-multipart / nh3 / questionary / mcp。詳細はリポジトリの NOTICES.md を参照。",
        "en": "The following are not bundled in the wheel; pip fetches them individually from PyPI"
              " (each ships its own license): PyYAML / FastAPI / uvicorn / Jinja2 / markdown /"
              " python-multipart / nh3 / questionary / mcp. See NOTICES.md in the repository for details.",
    },

    # ---- brief.html ----
    "brief_today_pick": {"ja": "今日の 1 個", "en": "Today's pick"},
    "brief_copy_context": {"ja": "context を AI に渡す", "en": "Hand the context to an AI"},
    "brief_empty": {
        "ja": "今日着手すべきものは無し（全件終端済 or pending のみ）",
        "en": "Nothing to start today (everything settled, or pending only)",
    },
    "brief_co_running": {"ja": "併走", "en": "Also running"},
    "brief_watchouts": {"ja": "要注意（陳腐化 / 期限切れ）", "en": "Watch out (stale / overdue)"},
    "brief_yesterday": {"ja": "昨日終わったこと", "en": "Finished yesterday"},
    "brief_copied": {
        "ja": "path をクリップボードへ:\n{path}\n\nターミナルで `docsweep context --clipboard <path>` を実行してください。",
        "en": "Path copied to clipboard:\n{path}\n\nRun `docsweep context --clipboard <path>` in a terminal.",
    },
    "brief_copy_failed": {"ja": "クリップボードコピー失敗: ", "en": "Failed to copy to clipboard: "},

    # ---- capture.html ----
    "capture_heading": {"ja": "capture — 会話を貼って草案抽出", "en": "capture — paste a conversation, extract drafts"},
    "capture_placeholder": {"ja": "ここに会話履歴を貼り付け...", "en": "Paste the conversation history here..."},
    "capture_use_llm": {"ja": "LLM 経路を使う (現状は mock のみ)", "en": "Use the LLM path (mock only for now)"},
    "capture_generate": {"ja": "草案を生成", "en": "Generate drafts"},
    "capture_save_selected": {"ja": "採用したものを保存", "en": "Save the accepted drafts"},
    "capture_extract_failed": {"ja": "extract 失敗: ", "en": "extract failed: "},
    "capture_no_drafts": {"ja": "草案候補なし", "en": "No draft candidates"},
    "capture_select_one": {"ja": "採用候補を選択してください", "en": "Select at least one draft"},
    "capture_save_failed": {"ja": "save 失敗: ", "en": "save failed: "},
    "capture_saved": {"ja": "保存しました:", "en": "Saved:"},

    # ---- cross.html ----
    "cross_projects": {"ja": "プロジェクト", "en": "Projects"},
    "cross_top_pick": {"ja": "全プロジェクト束ねた『今日の 1 個』", "en": "Today's pick across all projects"},
    "cross_empty": {"ja": "対象 open ファイル無し", "en": "No open files"},
    "cross_runners_up": {"ja": "次点", "en": "Runners-up"},
    "cross_frozen": {"ja": "凍結予備軍 — archive 候補", "en": "Freezer queue — archive candidates"},

    # ---- graph.html ----
    "graph_click_hint": {"ja": "クリックでノード詳細", "en": "Click a node for details"},

    # ---- resurrect.html ----
    "resurrect_jaccard_only": {"ja": "Jaccard のみ", "en": "Jaccard only"},
    "resurrect_recompute": {"ja": "再計算", "en": "Recompute"},
    "resurrect_col_similarity": {"ja": "類似度", "en": "Similarity"},
    "resurrect_col_archive": {"ja": "archive（過去）", "en": "archive (past)"},
    "resurrect_col_active": {"ja": "現役", "en": "active"},
    "resurrect_empty": {
        "ja": "蘇生候補なし（threshold を下げるか、archive を増やしてみてください）",
        "en": "No resurrection candidates (try lowering the threshold or archiving more files)",
    },
}


def get_messages(lang: str) -> dict[str, str]:
    """lang 解決済みの文言 dict を返す（テンプレの ``T``）。未知 lang は ja へフォールバック。"""
    key = lang if lang in SUPPORTED_LANGS else "ja"
    return {k: v.get(key) or v["ja"] for k, v in MESSAGES.items()}


def resolve_lang(request: Request, lang: str | None = None) -> str:
    """表示言語を解決する: ``?lang=`` クエリ > cookie > config.lang > ja。"""
    if lang in SUPPORTED_LANGS:
        return lang
    cookie = request.cookies.get(LANG_COOKIE)
    if cookie in SUPPORTED_LANGS:
        return cookie
    cfg_lang = getattr(request.app.state.docsweep.config, "lang", None)
    return cfg_lang if cfg_lang in SUPPORTED_LANGS else "ja"
