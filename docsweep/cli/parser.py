"""argparse parser construction and shared CLI configuration loading."""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import __version__
from ..config import load_config

def _add_scope_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("paths", nargs="*", help="単発スキャンするルート（config 不要）")
    p.add_argument("--root", action="append", dest="roots", metavar="PATH", help="スキャンルート（複数可）")
    p.add_argument("--profile", help="config の named プロファイルを使う")
    p.add_argument("--config", help="グローバル config のパス（既定 ~/.docsweep/config.yaml）")
    p.add_argument("--project-dir", help="プロジェクト .docsweep.yaml を読むディレクトリ")
    p.add_argument("--lang", choices=("ja", "en"), help="表示言語")


def _build_config(args: argparse.Namespace):
    explicit = list(getattr(args, "roots", None) or [])
    explicit += list(getattr(args, "paths", None) or [])
    global_path = Path(args.config) if getattr(args, "config", None) else None
    project_dir = Path(args.project_dir) if getattr(args, "project_dir", None) else None
    cfg = load_config(
        project_dir=project_dir,
        explicit_roots=explicit or None,
        profile=getattr(args, "profile", None),
        global_path=global_path,
    )
    if getattr(args, "lang", None):
        cfg.lang = args.lang
    return cfg


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docsweep", description="AI 作業ドキュメントの横断スキャン・判定・archive 移送")
    parser.add_argument("--version", action="version", version=f"docsweep {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="スキャンして一覧表示（既定）")
    _add_scope_args(p_scan)
    p_scan.add_argument("--json", action="store_true", help="機械可読 JSON で出力")
    p_scan.add_argument("--all", action="store_true", help="全件表示（既定は要判断＋保留のみ）")
    p_scan.add_argument("--project", help="対象プロジェクトを絞る（sweep/promote と対称）")

    p_triage = sub.add_parser("triage", help="要判断ファイルを allowed_actions 付きで提示")
    _add_scope_args(p_triage)
    p_triage.add_argument("--json", action="store_true", default=True, help="JSON 出力（既定）")
    p_triage.add_argument("--project", help="対象プロジェクトを絞る")
    p_triage.add_argument(
        "--tag", action="append", dest="tags", metavar="NAME",
        help="frontmatter tags に該当する md だけ列挙する（複数指定で OR）",
    )
    p_triage.add_argument(
        "--show", action="append", dest="show", choices=("owner", "tags"),
        help="表示列を追加（owner / tags）。指定があれば人間向けテーブル表示にも切替え",
    )
    p_triage.add_argument(
        "--review", action="store_true",
        help="インタラクティブ triage を起動（c=完了/w=様子見/x=廃止/s=スキップ/l=後で/o=開く/q=終了）",
    )
    p_triage.add_argument(
        "--head", type=int, default=0, metavar="N",
        help="先頭 N 件だけ出す（1 件ループ用・UX W2 / P33）",
    )

    p_apply = sub.add_parser("apply", help="1 ファイルに閉じた action を適用")
    _add_scope_args(p_apply)
    p_apply.add_argument("--path", required=True, help="対象ファイルの絶対パス")
    p_apply.add_argument("--action", required=True, help="discard|keep|resume|relabel|promote")
    p_apply.add_argument("--to", help="relabel 時のラベル名")
    p_apply.add_argument("--dry-run", action="store_true")

    p_sweep = sub.add_parser("sweep", help="--auto 相当: done/discarded を archive へ自動移送")
    _add_scope_args(p_sweep)
    p_sweep.add_argument("--project", help="対象プロジェクトを絞る（promote と対称）")
    p_sweep.add_argument("--dry-run", action="store_true", help="移送内容を出力するだけ")
    p_sweep.add_argument("--json", action="store_true")

    p_serve = sub.add_parser("serve", help="ローカル Web UI を起動（127.0.0.1・トークン付き）")
    _add_scope_args(p_serve)
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--no-browser", action="store_true", help="ブラウザを自動で開かない")
    p_serve.add_argument("--token", help="アクセストークンを固定（未指定なら環境変数 DOCSWEEP_TOKEN、それも無ければ毎回ランダム生成）")
    p_serve.add_argument(
        "--read-only", action="store_true",
        help="閲覧・検索のみ（書き込み API を 403）（UX W4 / P58）",
    )
    p_serve.add_argument(
        "--allow-root-mutation", action="store_true",
        help="Web UI からのスキャンルート追加を許可（既定は拒否）",
    )

    p_promote = sub.add_parser("promote", help="release sweep: 様子見をまとめて完了へ昇格し archive へ")
    _add_scope_args(p_promote)
    p_promote.add_argument("--state", default="watching", help="昇格元の状態（既定 watching）")
    p_promote.add_argument("--to", default="done", help="昇格先の状態（既定 done）")
    p_promote.add_argument("--project", help="対象プロジェクトを絞る")
    p_promote.add_argument("--dry-run", action="store_true")
    p_promote.add_argument("--json", action="store_true")

    p_index = sub.add_parser("index", help="横断 INDEX（.docsweep/INDEX.md / .json）を再生成")
    _add_scope_args(p_index)

    # C1 (wings): SQLite 索引（~/.docsweep/index.db）への差分同期 / 全再構築
    p_index_sync = sub.add_parser(
        "index-sync",
        help="SQLite 索引へ差分同期（mtime 変化分のみ）。projects.search_paths 配下を走査"
    )
    _add_scope_args(p_index_sync)
    p_index_sync.add_argument("--json", action="store_true", help="統計を JSON で出力")
    p_index_sync.add_argument(
        "--prune-projects", action="store_true",
        help="search_paths から外れた孤児 projects を CASCADE 削除（既定 OFF・安全側）"
    )

    p_index_rebuild = sub.add_parser(
        "index-rebuild",
        help="SQLite 索引を全件再構築（files テーブルをクリアしてから全走査・末尾で VACUUM）"
    )
    _add_scope_args(p_index_rebuild)
    p_index_rebuild.add_argument("--json", action="store_true", help="統計を JSON で出力")
    p_index_rebuild.add_argument(
        "--prune-projects", action="store_true",
        help="search_paths から外れた孤児 projects を CASCADE 削除（既定 OFF・安全側）"
    )
    p_index_rebuild.add_argument(
        "--no-vacuum", action="store_true",
        help="末尾の VACUUM をスキップ（既定は VACUUM 実行）"
    )

    p_index_watch = sub.add_parser(
        "index-watch",
        help="search_paths 配下を監視し、md 変更を検知したら索引を自動同期（watchdog 依存）"
    )
    _add_scope_args(p_index_watch)
    p_index_watch.add_argument(
        "--debounce", type=float, default=0.5,
        help="連続変更をまとめる秒数（既定 0.5 秒）"
    )

    # C1 (bloat-mitigation): index-stats — 索引 DB の物理サイズ / 行数 / freelist を観測
    p_index_stats = sub.add_parser(
        "index-stats",
        help="索引 DB のサイズ・行数・embedding 容量・freelist を観測（肥大化対策の入口）"
    )
    _add_scope_args(p_index_stats)
    p_index_stats.add_argument("--json", action="store_true", help="JSON 出力")

    # C2 (bloat-mitigation): index-vacuum — 手動メンテ口（DELETE 後の物理サイズ回収）
    p_index_vacuum = sub.add_parser(
        "index-vacuum",
        help="VACUUM を手動実行して索引 DB の freelist を解放しファイルを縮める"
    )
    _add_scope_args(p_index_vacuum)
    p_index_vacuum.add_argument("--json", action="store_true", help="JSON 出力")

    # C3 (wings): brief — 今日の 1 個を断定する朝の入口
    p_brief = sub.add_parser(
        "brief",
        help="今日の 1 個を断定（朝の入口）。--all で全プロジェクト横並び要約"
    )
    _add_scope_args(p_brief)
    p_brief.add_argument("--project", help="対象プロジェクト名（既定は cwd プロジェクト）")
    p_brief.add_argument("--all", action="store_true", dest="all_projects",
                         help="全プロジェクトの横並び要約（search_paths 全体）")
    p_brief.add_argument("--json", action="store_true", help="JSON 出力（既定は人間向け）")
    p_brief.add_argument("--continue", action="store_true", dest="auto_continue",
                         help="末尾の対話プロンプトを出さず、今日の 1 個の context を即クリップボードへ")

    # C4 (wings): cross — 全プロジェクト束ねた俯瞰
    p_cross = sub.add_parser(
        "cross",
        help="全プロジェクト束ねて『今日の 1 個』を断定 + 凍結予備軍を一覧"
    )
    _add_scope_args(p_cross)
    p_cross.add_argument("--project", help="絞り込むプロジェクト名（カンマ区切りで複数可）")
    p_cross.add_argument("--explain", metavar="REL", help="指定ファイルのスコア内訳を表示")
    p_cross.add_argument("--json", action="store_true", help="JSON 出力（既定は人間向け）")

    # C2 (wings): capture — 会話履歴から plan / bugfix / pending の草案を抽出
    p_capture = sub.add_parser(
        "capture",
        help="会話履歴から plan/bugfix/pending の草案を抽出（heuristic / LLM）"
    )
    _add_scope_args(p_capture)
    p_capture.add_argument(
        "--from", dest="source", default="clipboard",
        choices=("clipboard", "file", "-"),
        help="入力ソース（clipboard / file <path> / - で stdin）",
    )
    p_capture.add_argument("--file", help="--from file 時の対象パス")
    p_capture.add_argument("--llm", action="store_true",
                           help="LLM 経路を使う（既定は heuristic）")
    p_capture.add_argument("--project", help="配置先プロジェクト名（既定は cwd）")
    p_capture.add_argument("--max", type=int, default=5, help="抽出候補上限（既定 5）")
    p_capture.add_argument("--save-all", action="store_true",
                           help="抽出された候補を全部 docs/local/ へ保存")
    p_capture.add_argument("--out-dir", help="保存先ディレクトリ（既定はプロジェクトの docs/local）")
    p_capture.add_argument("--json", action="store_true", help="JSON 出力（既定は人間向け）")

    # C5 (wings): linkcheck / auto-triage / graph
    p_linkcheck = sub.add_parser(
        "linkcheck",
        help="plan の『変更予定ファイル』と実装実態の整合チェック"
    )
    _add_scope_args(p_linkcheck)
    p_linkcheck.add_argument("--file", help="単一 plan を対象（既定は全 plan_*.md）")
    p_linkcheck.add_argument("--json", action="store_true")

    p_autotri = sub.add_parser(
        "auto-triage",
        help="LLM/ヒューリスティックで状態遷移を提案（--suggest）/適用（--apply）"
    )
    _add_scope_args(p_autotri)
    grp = p_autotri.add_mutually_exclusive_group(required=True)
    grp.add_argument("--suggest", action="store_true", help="提案を JSON で表示（適用しない）")
    grp.add_argument("--apply", help="JSON ファイルから decisions を読んで一括適用")
    p_autotri.add_argument("--file", help="--suggest 時の対象ファイル絞り込み")
    p_autotri.add_argument("--dry-run", action="store_true", help="--apply 時に実適用せず計画だけ")

    p_graph = sub.add_parser(
        "graph",
        help="plan/bugfix/pending の関係性ネットワークを JSON で出力"
    )
    _add_scope_args(p_graph)
    p_graph.add_argument("--project", help="プロジェクト名でフィルタ")
    p_graph.add_argument("--json", action="store_true", default=True)

    # C6 (wings): resurrect — archive 蘇生（embedding opt-in）
    p_resurrect = sub.add_parser(
        "resurrect",
        help="archive と最新 plan の類似ペアを抽出（embedding / Jaccard）"
    )
    _add_scope_args(p_resurrect)
    p_resurrect.add_argument("--threshold", type=float, default=0.5,
                             help="類似度の下限（既定 0.5）")
    p_resurrect.add_argument("--no-embedding", action="store_true",
                             help="embedding を使わず Jaccard フォールバックのみ")
    p_resurrect.add_argument("--top-k", type=int, default=1,
                             help="各 archive に対して何件の現役を候補化するか（既定 1）")
    p_resurrect.add_argument("--json", action="store_true", default=True)

    p_pending = sub.add_parser("pending", help="全プロジェクトの [保留] を一発表示")
    _add_scope_args(p_pending)
    p_pending.add_argument("--json", action="store_true")

    p_report = sub.add_parser("report", help="人間向け週次レポート")
    _add_scope_args(p_report)

    p_summary = sub.add_parser("summary", help="AI 要約 export（圧縮 INDEX を JSON で）")
    _add_scope_args(p_summary)
    p_summary.add_argument("--project", help="対象プロジェクトを絞る")

    p_new = sub.add_parser("new", help="テンプレファイル即生成（plan/bugfix/pending）")
    p_new.add_argument("type", choices=("plan", "bugfix", "pending"))
    p_new.add_argument("topic", help="ケバブケースの topic")
    p_new.add_argument("--title", help="H1 タイトル（既定は topic）")
    p_new.add_argument(
        "--project-dir",
        default=None,
        help="生成先プロジェクト（既定: cwd から .git 等の project marker を遡って自動検出）",
    )
    p_new.add_argument(
        "--due",
        help=(
            "初期 due を YYYY-MM-DD で明示指定（省略時は .docsweep.yaml の "
            "due.default_offset_days[plan/pending] から自動計算。bugfix は新規時に付けない）"
        ),
    )
    p_new.add_argument(
        "--no-due", action="store_true",
        help="初期 due の自動付与を抑止する（.docsweep.yaml の offset を無視して frontmatter を入れない）",
    )
    p_new.add_argument(
        "--split", type=int, default=0, metavar="N",
        help="親 plan + 子 N 本を一括生成し related 双方向を結ぶ（UX W3 / P26）",
    )

    p_review = sub.add_parser("review", help="対話チェックリストで選択分を archive へ一括移送")
    _add_scope_args(p_review)

    p_inject = sub.add_parser("inject", help="運用ルール（管理ブロック＋.docsweep.yaml）を注入")
    p_inject.add_argument("--project", default=".", help="注入先プロジェクト（既定 .）")
    p_inject.add_argument("--preset", help="プリセット名（claude-jp / frontmatter）")
    p_inject.add_argument("--no-yaml", action="store_true", help=".docsweep.yaml を書かない")
    p_inject.add_argument("--no-guidance", action="store_true", help="導線と due ルールを省きラベル節だけ注入（グローバルに寄せる場合）")
    p_inject.add_argument("--global", dest="is_global", action="store_true", help="個人グローバル設定へ導線＋due ルールを注入（全プロジェクトで効く）")
    p_inject.add_argument("--agent", choices=("claude", "codex"), default="claude", help="グローバル注入先の AI ツール（--global 時）")
    p_inject.add_argument("--lang", choices=("ja", "en"), help="注入文言の言語（プロジェクト注入は preset の言語を上書き / --global の既定は ja）")
    p_inject.add_argument("--global-target", dest="global_target", help="グローバル注入先を明示パスで上書き")
    p_inject.add_argument("--dry-run", action="store_true")

    p_eject = sub.add_parser("eject", help="注入した管理ブロックを剥がす（手書きは温存）")
    p_eject.add_argument("--project", default=".", help="対象プロジェクト（既定 .）")
    p_eject.add_argument("--all", action="store_true", help="マニフェスト記録の全プロジェクト/グローバルから除去")
    p_eject.add_argument("--purge", action="store_true", help=".docsweep.yaml も削除")
    p_eject.add_argument("--global", dest="is_global", action="store_true", help="グローバル設定から導線を剥がす")
    p_eject.add_argument("--agent", choices=("claude", "codex"), default="claude", help="グローバル先の AI ツール（--global 時）")
    p_eject.add_argument("--global-target", dest="global_target", help="グローバル先を明示パスで上書き")
    p_eject.add_argument("--dry-run", action="store_true")

    p_list = sub.add_parser("list", help="注入済みプロジェクト一覧")
    p_list.add_argument("--injected", action="store_true", help="（既定動作）")
    p_list.add_argument("--json", action="store_true")

    p_mcp = sub.add_parser("mcp", help="MCP サーバー（stdio）を起動")
    _add_scope_args(p_mcp)

    # ------------------------------------------------------------------
    # C2: OKF 採用 Phase 2 サブコマンド
    # ------------------------------------------------------------------

    p_migrate = sub.add_parser(
        "migrate-frontmatter",
        help="既存 md に OKF frontmatter を非破壊的に挿入する（H1 ラベルは温存）",
    )
    _add_scope_args(p_migrate)
    p_migrate.add_argument("--project", help="対象プロジェクトを絞る")
    p_migrate.add_argument("--apply", action="store_true", help="実適用（既定は dry-run）")
    p_migrate.add_argument("--dry-run", action="store_true", help="変更予定を表示するだけ（既定）")
    p_migrate.add_argument("--json", action="store_true")

    p_fix_related = sub.add_parser(
        "fix-related",
        help="片側参照 related: [B] を B 側にも追記して対称化する",
    )
    _add_scope_args(p_fix_related)
    p_fix_related.add_argument("--apply", action="store_true", help="実適用（既定は dry-run）")
    p_fix_related.add_argument("--dry-run", action="store_true")
    p_fix_related.add_argument("--json", action="store_true")

    p_show = sub.add_parser("show", help="指定ファイルを参照している plan/bugfix/pending を逆参照表示")
    _add_scope_args(p_show)
    p_show.add_argument("file", help="対象 md の絶対パスまたはスキャン範囲内のパス")
    p_show.add_argument("--json", action="store_true")

    p_stale = sub.add_parser(
        "stale",
        help="review_status 別の前倒し陳腐化候補を列挙（draft/review/published 別しきい値）",
    )
    _add_scope_args(p_stale)
    p_stale.add_argument("--project", help="対象プロジェクトを絞る")
    p_stale.add_argument("--json", action="store_true")

    p_context = sub.add_parser(
        "context",
        help="本文 + 親 plan + related を 1 つの AI 用プロンプト文字列で stdout 出力",
    )
    _add_scope_args(p_context)
    p_context.add_argument("file", help="対象 md")
    p_context.add_argument("--clipboard", action="store_true", help="OS クリップボードへ書き出す")
    p_context.add_argument("--format", choices=("markdown", "plain"), default="markdown")

    p_claim = sub.add_parser("claim", help="frontmatter の owner を現ユーザーで上書き")
    p_claim.add_argument("file", help="対象 md")
    p_claim.add_argument("--unclaim", action="store_true", help="owner を空にし claimed_at を削除")
    p_claim.add_argument("--json", action="store_true")

    p_config = sub.add_parser(
        "config", help="docsweep 自身の user 設定 (~/.docsweep/config.yaml) を読み書き"
    )
    p_config.add_argument("key", nargs="?", help="user.name / user.email")
    p_config.add_argument("value", nargs="?", help="設定する値（省略で取得）")
    p_config.add_argument("--get", dest="get_key", metavar="KEY", help="指定キーを取得")
    p_config.add_argument("--unset", dest="unset_key", metavar="KEY", help="指定キーを削除")
    p_config.add_argument("--list", dest="list_all", action="store_true", help="全キーを表示")
    p_config.add_argument("--json", action="store_true")

    p_activity = sub.add_parser(
        "activity",
        help="過去に触ったもの（mtime軸）/今後期限のもの（due軸）を日付でまとめる。既定は今日+昨日",
    )
    _add_scope_args(p_activity)
    p_activity.add_argument("--project", help="対象プロジェクト名（既定は cwd プロジェクト）")
    p_activity.add_argument(
        "--all", action="store_true", dest="all_projects", help="全プロジェクト横断で束ねる"
    )
    p_activity.add_argument(
        "--date", action="append", dest="dates",
        metavar="{today,yesterday,tomorrow,YYYY-MM-DD}",
        help="対象日を追加指定（複数指定で OR。既定は today+yesterday）",
    )
    p_activity.add_argument(
        "--since", help="対象レンジ開始（YYYY-MM-DD または +Nd/-Nd/+Nw/-Nw 等）"
    )
    p_activity.add_argument(
        "--until", help="対象レンジ終端（YYYY-MM-DD または +Nd/-Nd/+Nw/-Nw 等）"
    )
    p_activity.add_argument("--json", action="store_true", help="JSON 出力（既定は人間向け）")

    p_timeline = sub.add_parser(
        "timeline", help="topic を含む plan/bugfix/pending を時系列で列挙"
    )
    _add_scope_args(p_timeline)
    p_timeline.add_argument("topic", help="ファイル名/タイトルに含む topic 文字列")
    p_timeline.add_argument(
        "--format", choices=("markdown", "plain", "json"), default="markdown"
    )

    p_find = sub.add_parser(
        "find",
        help="自由クエリ: --q / --owner / --tag / --type / --status / --review-status / --project",
    )
    _add_scope_args(p_find)
    p_find.add_argument("--owner", help="owner 一致（'me' で現ユーザー）")
    p_find.add_argument("--tag", action="append", dest="tags", help="tag 一致（複数指定で OR）")
    p_find.add_argument("--type", action="append", dest="types", help="plan/bugfix/pending")
    p_find.add_argument(
        "--status", action="append", dest="states", help="state key またはラベル（実行中 / done 等）"
    )
    p_find.add_argument(
        "--review-status", action="append", dest="review_statuses",
        help="draft / review / published",
    )
    p_find.add_argument("--project", help="対象プロジェクトを絞る")
    p_find.add_argument(
        "--q", dest="q",
        help="全文検索（title/summary/本文の部分一致・MVP）",
    )
    p_find.add_argument("--json", action="store_true")

    p_completion = sub.add_parser(
        "completion", help="シェル補完スクリプトを stdout 出力 (bash/zsh/pwsh)"
    )
    _add_scope_args(p_completion)
    p_completion.add_argument("shell", choices=("bash", "zsh", "pwsh"))

    # ------------------------------------------------------------------
    # C3: OKF 採用 Phase 3 サブコマンド
    # ------------------------------------------------------------------

    p_export = sub.add_parser(
        "export",
        help="OKF 互換の zip を出力（docsweep を抜けても md が腐らないことを実演する材料）",
    )
    _add_scope_args(p_export)
    p_export.add_argument("--okf", action="store_true", help="OKF 形式で出力（現状は唯一の形式）")
    p_export.add_argument(
        "--out", help="出力先 zip パス（既定: ./docsweep-okf-<date>.zip）"
    )
    p_export.add_argument("--project", help="対象プロジェクトを絞る")
    p_export.add_argument(
        "--include-archive", action="store_true",
        help="archive/ 配下も含める（既定は除外）",
    )
    p_export.add_argument("--json", action="store_true")

    # UX W1: doctor / init / undo
    p_doctor = sub.add_parser(
        "doctor",
        help="環境ヘルスチェック（config / roots / index / inject / extras）",
    )
    p_doctor.add_argument("--json", action="store_true", help="機械可読 JSON で出力")
    p_doctor.add_argument("--config", help="グローバル config のパス（既定 ~/.docsweep/config.yaml）")

    p_init = sub.add_parser(
        "init",
        help="初回セットアップ（~/.docsweep/config.yaml を作成）",
    )
    p_init.add_argument("--yes", "-y", action="store_true", help="非対話（既定値で作成）")
    p_init.add_argument("--root", help="スキャン root（未指定なら対話 or cwd）")
    p_init.add_argument("--lang", choices=("ja", "en"), default="ja")
    p_init.add_argument(
        "--agent", choices=("claude", "codex", "none"), default="claude",
        help="AI ツール（inject ヒント用）",
    )
    p_init.add_argument("--force", action="store_true", help="既存 config を上書き")
    p_init.add_argument("--config", help="書き込み先 config パス")
    p_init.add_argument("--json", action="store_true")

    p_undo = sub.add_parser(
        "undo",
        help="直近の archive / promote バッチを元に戻す",
    )
    _add_scope_args(p_undo)
    p_undo.add_argument("--json", action="store_true")

    # UX W2: day / intent / fix-conflict
    p_day = sub.add_parser("day", help="1 日の開閉（open=朝 / close=夜）")
    _add_scope_args(p_day)
    p_day.add_argument("phase", choices=("open", "close"), help="open | close")
    p_day.add_argument("--json", action="store_true")

    p_intent = sub.add_parser(
        "intent",
        help="自然言語の意図を docsweep コマンドにマップする",
    )
    p_intent.add_argument("text", nargs="+", help="意図のテキスト（例: 昨日何やった）")
    p_intent.add_argument("--json", action="store_true")

    p_fix_conflict = sub.add_parser(
        "fix-conflict",
        help="frontmatter と H1 の食い違いを修理する",
    )
    _add_scope_args(p_fix_conflict)
    p_fix_conflict.add_argument(
        "--prefer", choices=("h1", "frontmatter", "both"), default="h1",
        help="どちらを正とするか（both=h1 と同じ）",
    )
    p_fix_conflict.add_argument("--path", action="append", dest="paths", help="対象 path（複数可）")
    p_fix_conflict.add_argument("--list", action="store_true", help="conflict 一覧のみ")
    p_fix_conflict.add_argument("--dry-run", action="store_true")
    p_fix_conflict.add_argument("--json", action="store_true")

    p_notify = sub.add_parser(
        "notify",
        help="overdue 件数を OS ローカル通知（クラウド push なし）",
    )
    _add_scope_args(p_notify)
    p_notify.add_argument("--dry-run", action="store_true", help="送らず本文だけ表示")
    p_notify.add_argument("--json", action="store_true")

    # UX W2 / P39: project enable|disable|list
    p_project = sub.add_parser(
        "project",
        help="プロジェクト除外リストの確認 / ON/OFF",
    )
    p_project_sub = p_project.add_subparsers(dest="project_cmd")
    p_pl = p_project_sub.add_parser("list", help="プロジェクト一覧と有効/除外")
    _add_scope_args(p_pl)
    p_pl.add_argument("--json", action="store_true")
    p_pe = p_project_sub.add_parser("enable", help="除外を解除して ON")
    p_pe.add_argument("root", help="プロジェクト root の絶対パス")
    p_pe.add_argument("--json", action="store_true")
    p_pd = p_project_sub.add_parser("disable", help="除外リストへ追加して OFF")
    p_pd.add_argument("root", help="プロジェクト root の絶対パス")
    p_pd.add_argument("--json", action="store_true")

    p_review_week = sub.add_parser(
        "review-week",
        help="週次レビュー用サマリ（watching / 古い planned / 提案件数）",
    )
    _add_scope_args(p_review_week)
    p_review_week.add_argument("--json", action="store_true")

    p_history = sub.add_parser("history", help="moves.jsonl 操作履歴（人が読める）")
    _add_scope_args(p_history)
    p_history.add_argument("--limit", type=int, default=30)
    p_history.add_argument("--json", action="store_true")

    p_cookbook = sub.add_parser("cookbook", help="シナリオ別コピペコマンド集")
    p_cookbook.add_argument(
        "scenario", nargs="?", default=None,
        help="morning / release / onboard / ai / hygiene（省略で一覧）",
    )
    p_cookbook.add_argument("--json", action="store_true")

    p_memory = sub.add_parser(
        "memory",
        help="AI memory ファイルの読み取り専用スキャン（看板には混ぜない）",
    )
    p_memory.add_argument("--path", action="append", dest="paths", help="追加スキャンパス")
    p_memory.add_argument("--stale-days", type=int, default=90)
    p_memory.add_argument("--json", action="store_true")

    p_ics = sub.add_parser("ics", help="due 付き open を .ics で export")
    _add_scope_args(p_ics)
    p_ics.add_argument("--out", default="docsweep-due.ics", help="出力パス")

    return parser
