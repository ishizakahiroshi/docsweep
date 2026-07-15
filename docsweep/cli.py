"""薄い CLI（玄人・cron・AI 委譲向け）。

非対話を厳守する出力（--auto / --json）と、人間向けテーブル表示を提供する。
口の粒度は MCP 前提（1 コマンド＝1 ツール）: scan / triage / apply / sweep ...
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .config import DEFAULT_PROJECT_MARKERS, load_config
from .engine import apply_action, auto_sweep, run_scan


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


def _print_records_table(records, lang: str) -> None:
    if not records:
        print("（該当ファイルなし）")
        return
    for r in records:
        label = r.state_label or "[?]"
        flags = f" !{','.join(r.flags)}" if r.flags else ""
        summary = f" — {r.summary}" if r.summary else ""
        print(f"{label:<8} {r.age_days:>4}d  {r.project}/{Path(r.path).name}{flags}{summary}")


def cmd_day(args: argparse.Namespace) -> int:
    """1 日の開閉（UX W2 / P18）。"""
    from .day import day_close, day_open

    cfg = _build_config(args)
    phase = args.phase
    if phase == "open":
        result = day_open(cfg)
        payload = result.to_dict()
    else:
        result = day_close(cfg)
        payload = result.to_dict()
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if phase == "open":
        print(f"day open · {payload.get('generated_at')}")
        print(f"  overdue: {payload.get('overdue_count')} · open: {payload.get('open_count')}")
        tp = payload.get("today_pick")
        if tp:
            print(f"  today_pick: {tp.get('state_label')} {tp.get('title') or tp.get('rel')}")
            print(f"    {tp.get('path')}")
        else:
            print("  today_pick: （なし）")
        yd = payload.get("yesterday_done") or []
        if yd:
            print(f"  yesterday_done: {len(yd)} 件")
    else:
        print(f"day close · {payload.get('generated_at')}")
        print(f"  touched_today: {len(payload.get('touched_today') or [])}")
        print(f"  incomplete_due: {len(payload.get('incomplete_due') or [])}")
        for it in (payload.get("suggest_defer") or [])[:5]:
            print(f"    defer? {it.get('state_label')} {it.get('name')} due={it.get('due')}")
    return 0


def cmd_intent(args: argparse.Namespace) -> int:
    """意図 → コマンド（UX W2 / P28）。"""
    from .intent import route_intent

    text = " ".join(args.text)
    route = route_intent(text)
    if getattr(args, "json", False):
        print(json.dumps(route.to_dict(), ensure_ascii=False, indent=2))
    else:
        cmdline = " ".join(["docsweep", route.command, *route.args])
        print(f"intent: {route.intent}")
        print(f"→ {cmdline}")
        print(f"  ({route.reason}; confidence={route.confidence:.2f})")
    return 0


def cmd_fix_conflict(args: argparse.Namespace) -> int:
    """conflict 修理（UX W2 / P37）。"""
    from .fix_conflict import fix_conflicts, list_conflicts

    cfg = _build_config(args)
    if getattr(args, "list", False):
        rows = list_conflicts(cfg)
        if getattr(args, "json", False):
            print(json.dumps({"conflicts": rows}, ensure_ascii=False, indent=2))
        else:
            if not rows:
                print("conflict なし")
            for r in rows:
                print(f"{r.get('state_label')} {r.get('path')} (source={r.get('state_source')})")
        return 0
    res = fix_conflicts(
        cfg,
        prefer=getattr(args, "prefer", "h1") or "h1",
        paths=getattr(args, "paths", None),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    else:
        if not res.items:
            print("修理対象の conflict なし")
        for it in res.items:
            mark = "ok" if it.fixed else "ng"
            print(f"[{mark}] {it.path}: {it.detail}")
    return 0 if all(i.fixed for i in res.items) or not res.items else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """環境ヘルスチェック（UX W1 / P3）。"""
    from .doctor import format_human, run_doctor

    global_path = Path(args.config) if getattr(args, "config", None) else None
    report = run_doctor(global_path=global_path)
    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_human(report), end="")
    return 0 if report.ok else 1


def cmd_notify(args: argparse.Namespace) -> int:
    """OS ローカル通知（UX W4 / P53）。"""
    from .notify import notify_overdue

    cfg = _build_config(args)
    res = notify_overdue(cfg, dry_run=bool(getattr(args, "dry_run", False)))
    if getattr(args, "json", False):
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"[{res.backend}] {res.title}: {res.body}")
        if res.detail and not res.sent:
            print(f"  detail: {res.detail}", file=sys.stderr)
    return 0 if res.sent or getattr(args, "dry_run", False) else 1


def cmd_init(args: argparse.Namespace) -> int:
    """初回ウィザード（UX W1 / P1）。"""
    from .init_cmd import interactive_prompts, run_init

    yes = bool(getattr(args, "yes", False))
    root = getattr(args, "root", None)
    lang = getattr(args, "lang", None) or "ja"
    agent = getattr(args, "agent", None) or "claude"
    if not yes and root is None and not getattr(args, "force", False):
        answers = interactive_prompts()
        root = answers["root"]
        lang = answers["lang"]
        agent = answers["agent"]
    global_path = Path(args.config) if getattr(args, "config", None) else None
    result = run_init(
        yes=yes,
        root=root,
        lang=lang,
        agent=agent,
        global_path=global_path,
        force=bool(getattr(args, "force", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.message)
        if result.created or not getattr(args, "force", False):
            print("次の一手:")
            for s in result.next_steps:
                print(f"  {s}")
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    """直近 archive/promote バッチを復元（UX W1 / P12 CLI）。"""
    from .services.archive import undo_last_batch

    cfg = _build_config(args)
    res = undo_last_batch(config=cfg)
    payload = {
        "batch_id": res.batch_id,
        "restored": [
            {"src": e.src, "dst": e.dst, "project": e.project, "state": e.state}
            for e in res.restored
        ],
        "failed": list(res.failed),
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not res.batch_id:
            print("Undo 対象がありません（既に復元済み、または batch_id 無し）")
            return 1
        print(f"batch {res.batch_id}: {len(res.restored)} 件を復元")
        for e in res.restored:
            print(f"  {e.dst} -> {e.src}")
        if res.failed:
            print(f"失敗 {len(res.failed)} 件:")
            for f in res.failed:
                print(f"  {f}")
    return 1 if res.failed and not res.restored else 0


def cmd_scan(args: argparse.Namespace) -> int:
    from .engine import scan_records

    cfg = _build_config(args)
    project = getattr(args, "project", None)
    records = scan_records(cfg, project=project)
    if project:
        records = [r for r in records if r.project == project]
    if not getattr(args, "all", False):
        from .models import Flag
        records = [r for r in records if Flag.NEEDS_DECISION.value in r.flags or r.state == "pending"]
    if getattr(args, "json", False):
        print(json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2))
    else:
        _print_records_table(records, cfg.lang)
    return 0


def cmd_triage(args: argparse.Namespace) -> int:
    """残作業ビュー（要判断＋保留・古い順）を JSON で出す。MCP triage と同一契約。

    plan_okf-adoption_2026-06-29.md C1 で追加:
      ``--tag X`` で frontmatter ``tags:`` 絞り込み、``--show owner/tags`` で表示列追加、
      ``--review`` でインタラクティブ triage（キー判定ループ）。
    """
    from .reports import build_triage

    cfg = _build_config(args)

    # --review はインタラクティブ実行へ即委譲（JSON は出さない）。
    if getattr(args, "review", False):
        from .interactive import run_interactive_triage
        return run_interactive_triage(cfg)

    payload = build_triage(cfg, project=getattr(args, "project", None))

    tags = getattr(args, "tags", None) or []
    if tags:
        want = {t.strip().lower() for t in tags if t and t.strip()}

        def _has_tag(item: dict) -> bool:
            item_tags = {str(t).strip().lower() for t in (item.get("tags") or []) if t}
            return bool(item_tags & want)

        payload = {
            **payload,
            "items": [i for i in payload.get("items", []) if _has_tag(i)],
            "needs_fix": [i for i in payload.get("needs_fix", []) if _has_tag(i)],
        }

    head = int(getattr(args, "head", 0) or 0)
    if head > 0:
        payload = {
            **payload,
            "items": (payload.get("items") or [])[:head],
            "needs_fix": (payload.get("needs_fix") or [])[:head],
            "head": head,
        }

    show = getattr(args, "show", None) or []
    if show:
        _print_triage_table(payload, show=show)
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _print_triage_table(payload: dict, *, show: list[str]) -> None:
    """``--show`` 指定時の人間向けテーブル出力。owner / tags 列を任意で追加する。"""
    items = payload.get("items", [])
    if not items:
        print("（該当ファイルなし）")
        return
    for it in items:
        label = it.get("state") or "[?]"
        rel = it.get("rel") or it.get("path") or "?"
        extras: list[str] = []
        if "owner" in show:
            owner = it.get("owner") or "-"
            extras.append(f"owner={owner}")
        if "tags" in show:
            tags = it.get("tags") or []
            extras.append("tags=" + (",".join(tags) if tags else "-"))
        extra_s = ("  " + " ".join(extras)) if extras else ""
        print(f"{label:<10} {it.get('age_days', 0):>4}d  {it.get('project')}/{rel}{extra_s}")


def cmd_apply(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    result = run_scan(cfg)
    target = Path(args.path).resolve().as_posix()
    doc = next((d for d in result.docs if d.record.path == target), None)
    if doc is None:
        print(f"対象が見つかりません（スキャン範囲外?）: {args.path}", file=sys.stderr)
        return 2
    try:
        entry = apply_action(doc, args.action, cfg, to=args.to, dry_run=args.dry_run)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps(entry.to_dict(), ensure_ascii=False))
    return 0


def _print_moves_summary(moved, cfg, *, action: str, dry_run: bool) -> None:
    """移送/昇格ログの末尾に「合計・状態別・プロジェクト別」を出す。

    フラット出力では俯瞰できないため、走査直後に確認用の集計をまとめて見せる。
    JSON 出力には混ぜない（機械可読を汚さない）。``action`` は "移送" / "昇格" 等の
    呼び出し側の動作語。dry-run 時は "予定" を付ける。
    """
    if not moved:
        return
    from collections import Counter

    sm = cfg.state_model
    lang = cfg.lang

    def _label(k: str | None) -> str:
        s = sm.by_key(k) if k else None
        return s.label(lang) if s else (k or "(none)")

    by_state = Counter(_label(m.status) for m in moved)
    by_proj = Counter(m.project for m in moved)
    verb = f"{action}予定" if dry_run else action
    print()
    print(f"{verb}合計: {len(moved)} 件 ({len(by_proj)} プロジェクト)")
    print("  状態別: " + " / ".join(f"{k} {v}" for k, v in by_state.most_common()))
    print("  プロジェクト別:")
    for proj, n in by_proj.most_common():
        print(f"    {proj}: {n} 件")


def cmd_sweep(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    moved = auto_sweep(cfg, project=getattr(args, "project", None), dry_run=args.dry_run)
    if not args.dry_run and cfg.roots:
        from .aggregate_index import write_index

        write_index(cfg)
    if getattr(args, "json", False):
        print(json.dumps([m.to_dict() for m in moved], ensure_ascii=False, indent=2))
    else:
        verb = "移送予定" if args.dry_run else "移送"
        if not moved:
            print("自動移送対象なし（done/discarded のラベル確定ファイルが無い）")
        for m in moved:
            print(f"{verb}: {m.src} -> {m.dst}")
        _print_moves_summary(moved, cfg, action="移送", dry_run=args.dry_run)
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    from .engine import promote_state

    cfg = _build_config(args)
    try:
        moved = promote_state(cfg, from_state=args.state, to_state=args.to, project=args.project, dry_run=args.dry_run)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps([m.to_dict() for m in moved], ensure_ascii=False, indent=2))
    else:
        if not moved:
            print(f"昇格対象なし（{args.state} のファイルが無い）")
        for m in moved:
            print(f"昇格→archive: {m.src} -> {m.dst}")
        _print_moves_summary(moved, cfg, action="昇格", dry_run=args.dry_run)
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    from .aggregate_index import write_index

    cfg = _build_config(args)
    json_path, md_path = write_index(cfg)
    print(f"INDEX を生成しました:\n  {md_path}\n  {json_path}")
    return 0


def cmd_index_sync(args: argparse.Namespace) -> int:
    """SQLite 索引へ差分同期。``projects.search_paths`` 配下を走査して mtime 差分のみ更新。"""
    from .scan import sync_index

    cfg = _build_config(args)
    stats = sync_index(cfg, full=False, prune_projects=getattr(args, "prune_projects", False))
    payload = {
        "projects": stats.projects,
        "files_total": stats.files_total,
        "files_added": stats.files_added,
        "files_updated": stats.files_updated,
        "files_unchanged": stats.files_unchanged,
        "files_deleted": stats.files_deleted,
        "projects_removed": stats.projects_removed,
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        pruned = f", projects_removed={stats.projects_removed}" if stats.projects_removed else ""
        print(
            f"索引同期 完了: projects={stats.projects} "
            f"files={stats.files_total} "
            f"(added={stats.files_added}, updated={stats.files_updated}, "
            f"unchanged={stats.files_unchanged}, deleted={stats.files_deleted}{pruned})"
        )
    return 0


def cmd_index_rebuild(args: argparse.Namespace) -> int:
    """SQLite 索引を全件再構築。``files`` テーブルをクリア → 全走査 → 末尾で VACUUM。"""
    from . import index as db
    from .scan import sync_index

    cfg = _build_config(args)
    stats = sync_index(cfg, full=True, prune_projects=getattr(args, "prune_projects", False))

    reclaimed_bytes = 0
    vacuum_skipped = bool(getattr(args, "no_vacuum", False))
    vacuum_error: str | None = None
    if not vacuum_skipped:
        try:
            with db.connect() as conn:
                before = db.collect_stats(conn)["db_size_bytes"]
                db.vacuum(conn)
                after = db.collect_stats(conn)["db_size_bytes"]
                reclaimed_bytes = max(0, before - after)
        except sqlite3.OperationalError as e:
            # 他プロセスが書込中などで VACUUM 失敗 → 統計は出すが警告
            vacuum_error = str(e)

    payload = {
        "projects": stats.projects,
        "files_total": stats.files_total,
        "files_added": stats.files_added,
        "files_updated": stats.files_updated,
        "files_unchanged": stats.files_unchanged,
        "files_deleted": stats.files_deleted,
        "projects_removed": stats.projects_removed,
        "mode": "rebuild",
        "vacuum_skipped": vacuum_skipped,
        "vacuum_reclaimed_bytes": reclaimed_bytes,
        "vacuum_error": vacuum_error,
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"索引再構築 完了: projects={stats.projects} files={stats.files_total}"
        )
        if vacuum_error:
            print(
                f"  VACUUM 失敗: {vacuum_error}（他プロセスが DB を掴んでいる可能性。"
                "Web UI / index-watch を停止して `docsweep index-vacuum` を再実行してください）",
                file=sys.stderr,
            )
        elif not vacuum_skipped:
            print(f"  VACUUM 完了: 回収 {_format_bytes(reclaimed_bytes)}")
    return 0


def cmd_index_vacuum(args: argparse.Namespace) -> int:
    """``VACUUM`` を手動実行して索引 DB の freelist を解放しファイルを縮める。"""
    from . import index as db

    try:
        with db.connect() as conn:
            before = db.collect_stats(conn)
            db.vacuum(conn)
            after = db.collect_stats(conn)
    except sqlite3.OperationalError as e:
        print(
            f"VACUUM 失敗: {e}（他プロセスが DB を掴んでいる可能性。"
            "Web UI / index-watch を停止して再実行してください）",
            file=sys.stderr,
        )
        return 2

    reclaimed = max(0, before["db_size_bytes"] - after["db_size_bytes"])
    payload = {
        "db_size_before": before["db_size_bytes"],
        "db_size_after": after["db_size_bytes"],
        "reclaimed_bytes": reclaimed,
        "freelist_before": before["freelist_count"],
        "freelist_after": after["freelist_count"],
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"VACUUM 完了: {_format_bytes(before['db_size_bytes'])} → "
            f"{_format_bytes(after['db_size_bytes'])} "
            f"(回収 {_format_bytes(reclaimed)} / freelist "
            f"{before['freelist_count']} → {after['freelist_count']} pages)"
        )
    return 0


def _format_bytes(n: int) -> str:
    """人間可読サイズ表記（KiB / MiB / GiB）。観測値の表示専用。"""
    if n < 1024:
        return f"{n} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        n_f = n / 1024.0
        if n_f < 1024 or unit == "TiB":
            return f"{n_f:.1f} {unit}"
        n = int(n_f)
    return f"{n} B"


def cmd_index_stats(args: argparse.Namespace) -> int:
    """索引 DB のサイズ・行数・embedding・freelist を観測する。

    人間向けは要点だけ、``--json`` は ``collect_stats`` の生 dict をそのまま返す。
    """
    from . import index as db

    with db.connect() as conn:
        stats = db.collect_stats(conn)

    if getattr(args, "json", False):
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    from datetime import datetime, timezone

    def _iso(ts: float | None) -> str:
        if ts is None:
            return "-"
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    print(f"DB: {stats['db_path']}")
    print(f"  size      = {_format_bytes(stats['db_size_bytes'])}"
          f" (used {_format_bytes(stats['used_bytes'])}"
          f" + freelist {_format_bytes(stats['freelist_bytes'])}"
          f" / {stats['freelist_count']} pages)")
    if stats['wal_size_bytes'] or stats['shm_size_bytes']:
        print(f"  wal/shm   = {_format_bytes(stats['wal_size_bytes'])}"
              f" / {_format_bytes(stats['shm_size_bytes'])}")
    print(f"  pages     = {stats['page_count']} × {stats['page_size']} B")
    print(f"行数: projects={stats['projects']} files={stats['files']}"
          f" tags={stats['tags']} related={stats['related']}")
    if stats['embedding_rows']:
        print(f"embedding: {stats['embedding_rows']} 行"
              f" / 合計 {_format_bytes(stats['embedding_bytes'])}")
    else:
        print("embedding: なし")
    print(f"mtime 範囲: {_iso(stats['mtime_min'])} 〜 {_iso(stats['mtime_max'])}")
    print(f"schema_version: {stats['schema_version']}")
    return 0


def cmd_index_watch(args: argparse.Namespace) -> int:
    """``search_paths`` 配下を監視し、md 変更を検知したら ``sync_index`` を debounce 起動。

    watchdog 依存。``pip install 'docsweep[watch]'`` で導入する。
    """
    try:
        from watchdog.events import PatternMatchingEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print(
            "watch には watchdog が必要です: `pip install 'docsweep[watch]'`",
            file=sys.stderr,
        )
        return 2

    import threading
    import time

    from .scan import _expand_search_paths, sync_index

    cfg = _build_config(args)
    roots = _expand_search_paths(cfg)
    if not roots:
        print(
            "監視対象がありません。~/.docsweep/config.yaml の projects.search_paths を設定してください",
            file=sys.stderr,
        )
        return 2

    debounce_seconds = float(getattr(args, "debounce", 0.5))
    debounce_lock = threading.Lock()
    debounce_timer: list[threading.Timer | None] = [None]

    def run_sync() -> None:
        try:
            stats = sync_index(cfg)
        except Exception as e:  # 監視ループは止めない
            print(f"[watch] 同期エラー: {e}", file=sys.stderr)
            return
        if stats.files_added or stats.files_updated or stats.files_deleted:
            print(
                f"[watch] 同期: added={stats.files_added} "
                f"updated={stats.files_updated} deleted={stats.files_deleted}"
            )
        # C3 (bloat-mitigation): 各 sync 後に -wal を切り詰める。長時間運用で -wal が肥大しない。
        try:
            from . import index as db
            with db.connect() as conn:
                db.checkpoint_truncate(conn)
        except Exception as e:  # noqa: BLE001 — checkpoint 失敗は致命ではない
            print(f"[watch] checkpoint 警告: {e}", file=sys.stderr)

    def schedule_sync() -> None:
        with debounce_lock:
            if debounce_timer[0] is not None:
                debounce_timer[0].cancel()
            t = threading.Timer(debounce_seconds, run_sync)
            t.daemon = True
            debounce_timer[0] = t
            t.start()

    class _MdHandler(PatternMatchingEventHandler):
        def __init__(self) -> None:
            super().__init__(patterns=["*.md"], ignore_directories=True)

        def on_any_event(self, event) -> None:  # type: ignore[override]
            schedule_sync()

    observer = Observer()
    handler = _MdHandler()
    for root in roots:
        observer.schedule(handler, str(root), recursive=True)

    print(f"[watch] {len(roots)} プロジェクトを監視中 (Ctrl-C で終了)")
    # 起動時に 1 回フル同期して索引を新鮮にする
    run_sync()
    observer.start()
    try:
        while observer.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[watch] 終了")
    finally:
        observer.stop()
        observer.join()
    return 0


def _render_brief_human(result, lang: str = "ja") -> str:
    """brief の人間向け 1 画面出力。CLI と Web で共通のフォーマット感を持つ。"""
    lines: list[str] = []
    lines.append("docsweep brief")
    lines.append("=" * 40)
    for proj in result.projects:
        lines.append("")
        lines.append(f"# {proj.project}  (open={proj.open_count}, stale={proj.stale_count})")
        if proj.today_pick:
            tp = proj.today_pick
            lines.append("")
            lines.append("  >>> 今日の 1 個")
            lines.append(f"    {tp.get('state_label') or '[?]'} {tp.get('rel')}  ({proj.project})")
            if tp.get("title"):
                lines.append(f"    {tp['title']}")
            if tp.get("summary"):
                lines.append(f"    {tp['summary']}")
            score = (tp.get("score") or {}).get("total")
            if score is not None:
                lines.append(f"    score: {score}")
        else:
            lines.append("  (今日着手すべきものは無し — 全件終端済 or pending のみ)")

        if proj.co_running:
            lines.append("")
            lines.append("  併走:")
            for d in proj.co_running:
                lines.append(f"    {d.get('state_label') or '[?]'} {d.get('rel')}  ({d.get('age_days')}d)")

        if proj.watchouts:
            lines.append("")
            lines.append("  要注意 (陳腐化/期限切れ):")
            for d in proj.watchouts:
                flags = ",".join(d.get("flags") or [])
                lines.append(f"    {d.get('state_label') or '[?]'} {d.get('rel')}  [{flags}]")

        if proj.yesterday_done:
            lines.append("")
            lines.append("  昨日終わったこと:")
            for d in proj.yesterday_done:
                lines.append(f"    {d.get('state_label') or '[?]'} {d.get('rel')}")
    return "\n".join(lines)


def _render_activity_human(result, lang: str = "ja") -> str:
    """activity の人間向け 1 画面出力。日付見出し＋軸ラベル（触った/期限）を明示する。"""
    lines: list[str] = []
    lines.append("docsweep activity")
    lines.append("=" * 40)
    hit = False
    for iso in sorted(result.dates):
        bucket = result.dates[iso]
        if not bucket.touched and not bucket.due:
            continue
        hit = True
        marker = "  (今日)" if iso == result.today else ""
        lines.append("")
        lines.append(f"# {iso}{marker}")
        if bucket.touched:
            lines.append("  触った:")
            for d in bucket.touched:
                lines.append(f"    {d.get('state_label') or '[?]'} {d.get('project')}/{d.get('rel')}")
        if bucket.due:
            lines.append("  期限:")
            for d in bucket.due:
                lines.append(f"    {d.get('state_label') or '[?]'} {d.get('project')}/{d.get('rel')}")
    if not hit:
        lines.append("")
        lines.append("（該当ファイルなし）")
    return "\n".join(lines)


def cmd_activity(args: argparse.Namespace) -> int:
    """過去に触ったもの/今後期限のものを日付でまとめる（brief/cross の日付版）。

    plan_activity-summary.md C1 の主要 deliverable。新規永続化は一切せず、既存の
    ``scan_records()`` が持つ mtime/due だけを日付でグルーピングする読み取り専用コマンド。
    """
    from .activity import ActivityDateError, build_activity

    cfg = _build_config(args)
    try:
        result = build_activity(
            cfg,
            dates=getattr(args, "dates", None),
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            project=getattr(args, "project", None),
            all_projects=bool(getattr(args, "all_projects", False)),
        )
    except ActivityDateError as e:
        print(str(e), file=sys.stderr)
        return 2

    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(_render_activity_human(result, lang=cfg.lang))
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    """今日の 1 個を断定する朝の入口（CLI 側）。

    plan_docsweep-wings_2026-06-29.md C3 の主要 deliverable。``--all`` で
    cross 相当の横並び要約に切り替わる（cross が来るまでのつなぎではなく恒久仕様）。
    """
    from .brief import build_brief

    cfg = _build_config(args)
    result = build_brief(
        cfg,
        project=getattr(args, "project", None),
        all_projects=bool(getattr(args, "all_projects", False)),
    )

    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(_render_brief_human(result, lang=cfg.lang))

    # 「続きやる?」プロンプト or --continue で即 context をクリップボードへ
    today_pick_path: str | None = None
    if result.projects and result.projects[0].today_pick:
        today_pick_path = result.projects[0].today_pick.get("path")

    if today_pick_path and getattr(args, "auto_continue", False):
        _copy_context_to_clipboard(today_pick_path, cfg)
    elif today_pick_path and sys.stdin.isatty() and sys.stdout.isatty():
        print("")
        try:
            ans = input("続きやる? (Y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"
        if ans in ("", "y", "yes"):
            _copy_context_to_clipboard(today_pick_path, cfg)
    return 0


def _copy_context_to_clipboard(file_path: str, cfg) -> None:
    """`docsweep context --clipboard <file>` 相当を内部呼び出しで実行。"""
    try:
        from .context import collect_context, render_context, to_clipboard
    except ImportError:
        return
    try:
        bundle = collect_context(file_path, cfg)
    except (FileNotFoundError, ValueError) as e:
        print(f"context 生成失敗: {e}", file=sys.stderr)
        return
    text = render_context(bundle, fmt="prompt")
    if to_clipboard(text):
        print(f"context をクリップボードへコピー: {Path(file_path).name}")
    else:
        print("クリップボードコピー失敗（OS 依存）。代わりに stdout に出力します:\n")
        print(text)


def cmd_cross(args: argparse.Namespace) -> int:
    """全プロジェクト束ねた俯瞰（C4 cross）。``--explain`` は内訳表示モード。"""
    from .cross import build_cross
    from .cross.service import explain_score

    cfg = _build_config(args)

    if getattr(args, "explain", None):
        result = explain_score(cfg, args.explain)
        if result is None:
            print(f"対象が見つかりません: {args.explain}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    project_arg = getattr(args, "project", None)
    projects = [p.strip() for p in project_arg.split(",")] if project_arg else None
    result = build_cross(cfg, projects=projects)

    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    # 人間向け
    print("docsweep cross")
    print("=" * 40)
    print(f"projects: {result.total_projects}  open: {result.total_open}")
    if result.top_pick:
        tp = result.top_pick
        print("")
        print(f">>> 今日の 1 個 ({tp['project']}/{tp['rel']})")
        print(f"    {tp.get('state_label') or '[?]'} {tp.get('title') or tp['rel']}")
        if tp.get("summary"):
            print(f"    {tp['summary']}")
        print(f"    score: {tp.get('score')}")
    else:
        print("(対象 open ファイル無し)")

    if result.runners_up:
        print("")
        print("次点:")
        for d in result.runners_up:
            print(f"  {d.get('state_label') or '[?]'} {d['project']}/{d['rel']}  ({d['age_days']}d, score={d.get('score')})")

    if result.frozen_candidates:
        print("")
        print(f"凍結予備軍 ({len(result.frozen_candidates)} 件・archive 候補):")
        for d in result.frozen_candidates:
            print(f"  {d.get('state_label') or '[?]'} {d['project']}/{d['rel']}  ({d['age_days']}d)")

    print("")
    print("プロジェクト別:")
    for p in result.project_summaries:
        top_label = p.today_one["rel"] if p.today_one else "(open無し)"
        print(f"  {p.project}: open={p.open_count} stale={p.stale_count}  top={top_label}")
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    """会話履歴から plan / bugfix / pending 草案を抽出 (heuristic / LLM)。"""
    from .capture import extract_drafts, save_drafts

    cfg = _build_config(args)

    # 入力ソース解決
    source = getattr(args, "source", "clipboard")
    if source == "clipboard":
        text = _read_clipboard()
    elif source == "file":
        fpath = getattr(args, "file", None)
        if not fpath:
            print("--from file には --file <path> が必要です", file=sys.stderr)
            return 2
        text = Path(fpath).read_text(encoding="utf-8", errors="replace")
    elif source == "-":
        text = sys.stdin.read()
    else:
        text = ""

    if not text.strip():
        print("入力が空です", file=sys.stderr)
        return 2

    drafts = extract_drafts(
        text,
        config=cfg,
        project=getattr(args, "project", None),
        max_drafts=int(getattr(args, "max", 5)),
        use_llm=bool(getattr(args, "llm", False)),
    )

    if not drafts:
        if getattr(args, "json", False):
            print(json.dumps({"drafts": [], "saved": []}, ensure_ascii=False, indent=2))
        else:
            print("草案候補は見つかりませんでした (heuristic マーカー未検出)")
        return 0

    saved: list[Path] = []
    if getattr(args, "save_all", False):
        out_dir = Path(args.out_dir) if getattr(args, "out_dir", None) else _resolve_out_dir(cfg)
        saved = save_drafts(drafts, config=cfg, target_dir=out_dir)

    if getattr(args, "json", False):
        print(json.dumps({
            "drafts": [d.to_dict() for d in drafts],
            "saved": [str(p) for p in saved],
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"草案候補: {len(drafts)} 件")
    for d in drafts:
        print(f"  [{d.id}] {d.kind:7s} {d.suggested_filename}")
        print(f"          {d.title}")
    if saved:
        print(f"\n保存: {len(saved)} 件")
        for p in saved:
            print(f"  {p}")
    elif not getattr(args, "save_all", False):
        print("\n(保存するには --save-all を付けるか、--json で受け取って MCP capture_save を呼んでください)")
    return 0


def _read_clipboard() -> str:
    """OS クリップボードから text を取得。失敗時は空文字。"""
    try:
        import subprocess
        if sys.platform == "win32":
            r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                               capture_output=True, text=True, timeout=3, encoding="utf-8")
            if r.returncode == 0:
                return r.stdout
        elif sys.platform == "darwin":
            r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                return r.stdout
        else:
            for cmd in (["xclip", "-selection", "clipboard", "-o"], ["xsel", "-b"], ["wl-paste"]):
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                    if r.returncode == 0:
                        return r.stdout
                except FileNotFoundError:
                    continue
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _resolve_out_dir(cfg) -> Path:
    """capture 草案の保存先を決める（既定: 最初の root の docs/local/）。"""
    if cfg.roots:
        root = Path(cfg.roots[0])
        return root / "docs" / "local"
    return Path.cwd() / "docs" / "local"


def cmd_linkcheck(args: argparse.Namespace) -> int:
    """plan の整合チェック（C5）。"""
    from .linkcheck import linkcheck

    cfg = _build_config(args)
    results = linkcheck(cfg, target=getattr(args, "file", None))
    if getattr(args, "json", False):
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
        return 0
    for r in results:
        print(f"{r.plan_name}: {r.progress_hint}")
        for f in r.declared_files:
            mark = "✓" if f.exists else "✗"
            mention = " (commit言及)" if f.mentioned_in_commit else ""
            print(f"  {mark} {f.path}  touches={f.touches_since_plan}{mention}")
    return 0


def cmd_auto_triage(args: argparse.Namespace) -> int:
    """状態遷移提案 / 適用（C5）。"""
    from .auto_triage import apply_suggestions, suggest_transitions

    cfg = _build_config(args)
    if getattr(args, "suggest", False):
        result = suggest_transitions(cfg, target=getattr(args, "file", None))
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    apply_arg = getattr(args, "apply", None)
    if apply_arg:
        decisions = json.loads(Path(apply_arg).read_text(encoding="utf-8"))
        if isinstance(decisions, dict):
            decisions = decisions.get("decisions") or decisions.get("suggestions") or []
        result = apply_suggestions(cfg, decisions, dry_run=getattr(args, "dry_run", False))
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    return 2


def cmd_graph(args: argparse.Namespace) -> int:
    """関係性グラフ JSON 出力（C5）。"""
    from .graph import build_graph

    cfg = _build_config(args)
    g = build_graph(cfg, project=getattr(args, "project", None))
    print(json.dumps(g.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_resurrect(args: argparse.Namespace) -> int:
    """archive 蘇生（C6）。embedding 未インストール時は Jaccard。"""
    from .resurrect import find_candidates

    cfg = _build_config(args)
    result = find_candidates(
        cfg,
        threshold=float(getattr(args, "threshold", 0.5)),
        use_embedding=not bool(getattr(args, "no_embedding", False)),
        top_k_per_archive=int(getattr(args, "top_k", 1)),
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    from .aggregate_index import build_index

    cfg = _build_config(args)
    idx = build_index(cfg)
    if getattr(args, "json", False):
        print(json.dumps(idx.pending, ensure_ascii=False, indent=2))
    else:
        if not idx.pending:
            print("保留（pending）はありません。")
        for d in idx.pending:
            summary = f" — {d['summary']}" if d.get("summary") else ""
            print(f"[保留] {d['age_days']:>4}d  {d['project']}/{Path(d['path']).name}{summary}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .reports import render_report

    print(render_report(_build_config(args)))
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    from .reports import render_summary

    print(render_summary(_build_config(args), project=getattr(args, "project", None)))
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    from .scan import detect_project_root
    from .secrets_guard import format_warnings, scan_secrets
    from .similar_guard import find_similar_open
    from .templates_gen import new_doc, new_split_plans

    if getattr(args, "project_dir", None):
        project_dir = Path(args.project_dir)
    else:
        # --project-dir 省略時は cwd をそのまま使わず、.git 等の project marker を
        # 上へ辿って自動検出する（scan 系コマンドと同じ detect_project_root を再利用）。
        # cwd がサブディレクトリ（例: リポジトリ内の web/）のときに、そこを誤って
        # プロジェクトルート扱いしてしまう問題への対処。
        cwd = Path.cwd().resolve()
        project_dir = detect_project_root(cwd, Path(cwd.anchor), DEFAULT_PROJECT_MARKERS, {})
    # ``.docsweep.yaml`` の ``due:`` ブロックから default_offset_days を読む。
    # --no-due 指定時は空 dict を渡してオフセット計算自体を無効化する（嘘の日付防止）。
    cfg = load_config(project_dir=project_dir)
    # 類似ガード（現役 open）
    try:
        sim = find_similar_open(cfg, topic=args.topic)
        if sim:
            print("類似の現役ドキュメントがあります（重複防止ヒント）:", file=sys.stderr)
            for s in sim[:3]:
                print(f"  - {s.get('state_label')} {s.get('path')}", file=sys.stderr)
    except Exception:
        pass
    offsets: dict[str, int] = {} if getattr(args, "no_due", False) else cfg.due_default_offset_days
    split_n = int(getattr(args, "split", 0) or 0)
    if split_n > 0:
        if args.type != "plan":
            print("--split は plan のみ対応です", file=sys.stderr)
            return 2
        created = new_split_plans(
            args.topic,
            n=split_n,
            project_dir=project_dir,
            title=args.title,
            due=getattr(args, "due", None),
            offset_days=offsets,
        )
        for d in created:
            print(f"生成しました: {d.path}" + (f" (due={d.due})" if d.due else ""))
        return 0
    doc = new_doc(
        args.type, args.topic,
        project_dir=project_dir, title=args.title,
        due=getattr(args, "due", None),
        offset_days=offsets,
    )
    try:
        body = doc.path.read_text(encoding="utf-8")
        for w in format_warnings(scan_secrets(body)):
            print(f"warn: {w}", file=sys.stderr)
    except Exception:
        pass
    if doc.due:
        print(f"生成しました: {doc.path} (due={doc.due})")
    else:
        print(f"生成しました: {doc.path}")
    return 0


def cmd_project(args: argparse.Namespace) -> int:
    """project list|enable|disable（UX W2 / P39）。"""
    from .excluded import disable_project, enable_project, list_known_projects, load_excluded

    sub = getattr(args, "project_cmd", None)
    if sub == "list":
        cfg = _build_config(args)
        rows = list_known_projects(cfg)
        if getattr(args, "json", False):
            print(json.dumps({"projects": rows, "excluded": sorted(load_excluded())},
                             ensure_ascii=False, indent=2))
        else:
            for r in rows:
                mark = "ON " if r["enabled"] else "OFF"
                print(f"[{mark}] {r['name']:<24} open≈{r['open_approx']:<4} {r['root']}")
        return 0
    if sub == "enable":
        s = enable_project(args.root)
        payload = {"enabled": True, "root": args.root, "excluded": sorted(s)}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if getattr(args, "json", False)
              else f"enabled: {args.root}")
        return 0
    if sub == "disable":
        s = disable_project(args.root)
        payload = {"enabled": False, "root": args.root, "excluded": sorted(s)}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if getattr(args, "json", False)
              else f"disabled: {args.root}")
        return 0
    print("usage: docsweep project list|enable|disable", file=sys.stderr)
    return 2


def cmd_history(args: argparse.Namespace) -> int:
    from .history import read_history

    cfg = _build_config(args)
    res = read_history(cfg, limit=int(getattr(args, "limit", 30) or 30))
    if getattr(args, "json", False):
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if not res.entries:
        print("履歴なし")
        return 0
    for e in res.entries:
        print(f"{e.ts}  {e.op:<8}  {e.project}  {e.src} -> {e.dst}")
    return 0


def cmd_cookbook(args: argparse.Namespace) -> int:
    from .cookbook import get_scenario, list_scenarios, render_cookbook

    name = getattr(args, "scenario", None)
    if getattr(args, "json", False):
        if name:
            items = get_scenario(name) or []
            print(json.dumps({"scenario": name, "items": items}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"scenarios": list_scenarios()}, ensure_ascii=False, indent=2))
        return 0
    print(render_cookbook(name), end="")
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    from .memory_scan import scan_memory

    res = scan_memory(
        paths=getattr(args, "paths", None),
        stale_days=int(getattr(args, "stale_days", 90) or 90),
    )
    if getattr(args, "json", False):
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(f"memory scan: {len(res.files)} files (stale≥{res.stale_over_days}d: "
          f"{sum(1 for f in res.files if f.age_days >= res.stale_over_days)})")
    for f in res.files[:30]:
        mark = "STALE" if f.age_days >= res.stale_over_days else "ok"
        print(f"  [{mark}] {f.age_days:>4}d  {f.path}")
    return 0


def cmd_ics(args: argparse.Namespace) -> int:
    from .ics_export import write_ics

    cfg = _build_config(args)
    out = write_ics(cfg, Path(getattr(args, "out", None) or "docsweep-due.ics"))
    print(f"wrote {out}")
    return 0


def cmd_review_week(args: argparse.Namespace) -> int:
    """週次レビューサマリ（UX W3 / P19 MVP）。"""
    from .auto_triage import suggest_transitions
    from .engine import scan_records
    from .models import Flag

    cfg = _build_config(args)
    records = scan_records(cfg)
    watching = [r for r in records if r.state == "watching"]
    old_planned = [
        r for r in records
        if r.state == "planned" and (r.age_days or 0) >= 90
    ]
    conflict = [r for r in records if Flag.CONFLICT.value in (r.flags or [])]
    suggestions = suggest_transitions(cfg).suggestions
    payload = {
        "watching_count": len(watching),
        "watching": [
            {"path": r.path, "title": r.title, "age_days": r.age_days}
            for r in watching[:20]
        ],
        "old_planned_count": len(old_planned),
        "old_planned": [
            {"path": r.path, "title": r.title, "age_days": r.age_days}
            for r in old_planned[:20]
        ],
        "conflict_count": len(conflict),
        "suggestion_count": len(suggestions),
        "suggestions": [s.to_dict() for s in suggestions[:20]],
        "hints": [
            "docsweep project list  # 不要プロジェクトを disable",
            "docsweep fix-conflict --list",
            "docsweep auto-triage --suggest",
            "docsweep promote --dry-run",
        ],
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"review-week")
        print(f"  watching: {payload['watching_count']}")
        print(f"  planned≥90d: {payload['old_planned_count']}")
        print(f"  conflict: {payload['conflict_count']}")
        print(f"  auto-triage suggestions: {payload['suggestion_count']}")
        for h in payload["hints"]:
            print(f"  next: {h}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    from .review import run_review

    return run_review(_build_config(args))


def cmd_inject(args: argparse.Namespace) -> int:
    from .inject import inject, inject_global

    tag = "（dry-run）" if args.dry_run else ""
    if getattr(args, "is_global", False):
        r = inject_global(
            agent=args.agent, target=args.global_target, lang=args.lang or "ja", dry_run=args.dry_run,
        )
        print(f"inject {r.project}{tag}: 書込={r.written or '-'} 温存/不変={r.skipped or '-'}")
        for w in r.warnings:
            print(f"  ⚠ {w}")
        return 0

    r = inject(
        Path(args.project), preset=args.preset, write_yaml=not args.no_yaml,
        include_guidance=not args.no_guidance, lang=args.lang, dry_run=args.dry_run,
    )
    print(f"inject {r.project}{tag}: 書込={r.written or '-'} 温存/不変={r.skipped or '-'}")
    if r.yaml_path:
        print(f"  .docsweep.yaml: {r.yaml_path}")
    for w in r.warnings:
        print(f"  ⚠ {w}")
    return 0


def cmd_eject(args: argparse.Namespace) -> int:
    from .inject import eject, eject_global, list_injected

    def _report(r) -> None:
        tag = "（dry-run）" if args.dry_run else ""
        yaml = " +yaml" if getattr(r, "purged_yaml", False) else ""
        print(f"eject {r.project}{tag}: 除去={r.removed or '-'}{yaml}")
        for w in r.warnings:
            print(f"  ⚠ {w}")

    if getattr(args, "is_global", False):
        _report(eject_global(agent=args.agent, target=args.global_target, dry_run=args.dry_run))
        return 0
    if args.all:
        for it in list_injected():
            if it.get("scope") == "global":
                _report(eject_global(agent=it.get("agent") or "claude", target=it["path"], dry_run=args.dry_run))
            else:
                _report(eject(Path(it["path"]), purge=args.purge, dry_run=args.dry_run))
        return 0
    _report(eject(Path(args.project).resolve(), purge=args.purge, dry_run=args.dry_run))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    from .inject import list_injected

    items = list_injected()
    if getattr(args, "json", False):
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        if not items:
            print("注入済みプロジェクトはありません。")
        for it in items:
            scope = it.get("scope", "project")
            tag = f"global:{it.get('agent')}" if scope == "global" else (it.get("preset") or "-")
            ver = f"v{it['version']}" if it.get("version") else "v?"
            print(f"{tag:<16} {ver:<4} {it['path']}  ({it['ts']})")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    try:
        from . import mcp_server
    except ImportError:
        print("MCP には mcp extra が必要です: pip install 'docsweep[mcp]'", file=sys.stderr)
        return 3
    try:
        mcp_server.run(cfg)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 3
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    if not cfg.roots:
        # --root も config の roots も無ければカレントフォルダを採用（手軽起動）。
        cfg.roots = [Path.cwd()]
        print(f"（--root 未指定のためカレントを使用: {Path.cwd()}）")
    try:
        import secrets

        import uvicorn

        from .server.app import create_app
    except ImportError:
        print("Web UI には web extra が必要です: pip install 'docsweep[web]'", file=sys.stderr)
        return 3

    # トークンはコマンドライン引数（他プロセスから見える）より環境変数を推奨。
    token = args.token or os.environ.get("DOCSWEEP_TOKEN") or secrets.token_urlsafe(16)
    app = create_app(cfg, token=token, read_only=bool(getattr(args, "read_only", False)))
    url = f"http://127.0.0.1:{args.port}/?token={token}"
    print("=" * 60)
    print("  ブラウザでこのアドレスを開いてください（自動で開きます）:")
    print(f"  {url}")
    if getattr(args, "read_only", False):
        print("  [read-only] 書き込み API は 403 です")
    print("=" * 60)
    print("（Ctrl+C または画面右上の ⏻ で停止）")
    if not args.no_browser:
        import threading
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    # uvicorn.run(app, ...) だと外から graceful 停止できないため、Server/Config を直接使い、
    # インスタンスを app.state に渡すことで /api/shutdown から should_exit=True できるようにする。
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    app.state.docsweep.server = server
    try:
        server.run()
    except KeyboardInterrupt:
        # Python 3.14 の asyncio.runners は Ctrl+C を KeyboardInterrupt として再送出する。
        # 正常な停止操作なのでスタックトレースを見せず 1 行で終える。
        print("停止しました（Ctrl+C）")
    return 0


def cmd_migrate_frontmatter(args: argparse.Namespace) -> int:
    """既存 md に OKF frontmatter を非破壊的に挿入する。"""
    from .migrate import apply_migration, plan_migration

    cfg = _build_config(args)
    project = getattr(args, "project", None)
    apply = getattr(args, "apply", False)
    if apply:
        result = apply_migration(cfg, project=project)
    else:
        result = plan_migration(cfg, project=project)
    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if not result.planned and not result.skipped:
        print("migrate-frontmatter: 対象なし")
        return 0
    verb = "適用" if apply else "予定"
    print(f"migrate-frontmatter {verb}: {len(result.planned)} 件 / skipped {len(result.skipped)} 件")
    for p in result.planned:
        marker = "[適用済]" if (apply and p.path in result.applied) else "[予定]"
        mode_note = "（既存frontmatterへ不足キー追記）" if p.mode == "upgrade" else ""
        print(f"  {marker} {p.doc_type:<8} status={p.status:<11} {p.path}{mode_note}")
    for p in result.skipped:
        print(f"  [skip] {p.path}  ({p.skipped_reason})")
    return 0


def cmd_fix_related(args: argparse.Namespace) -> int:
    """片側参照 related: [B] を B 側にも追記して対称化する。"""
    from .related import apply_fix_related, plan_fix_related

    cfg = _build_config(args)
    if getattr(args, "apply", False):
        result = apply_fix_related(cfg)
    else:
        result = plan_fix_related(cfg)
    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if not result.fixes:
        print("fix-related: 対称化が必要な参照はありません")
        return 0
    verb = "適用" if getattr(args, "apply", False) else "予定"
    print(f"fix-related {verb}: {len(result.fixes)} ファイルに追記")
    for fix in result.fixes:
        marker = "[適用済]" if fix.path in result.applied else "[予定]"
        print(f"  {marker} {fix.path}  + related: [{', '.join(fix.additions)}]")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """指定ファイルを参照している plan/bugfix/pending を逆参照表示。"""
    from .engine import scan_records
    from .related import backref_records, forward_records

    cfg = _build_config(args)
    records = list(scan_records(cfg))
    target_path = Path(args.file).resolve().as_posix()
    target = next((r for r in records if r.path == target_path), None)
    if target is None:
        # basename フォールバック
        name = Path(args.file).name
        target = next((r for r in records if Path(r.path).name == name), None)
    if target is None:
        print(f"対象が見つかりません: {args.file}", file=sys.stderr)
        return 2
    forwards = forward_records(target, records)
    backs = backref_records(target, records)
    payload = {
        "target": target.to_dict(),
        "forward": [r.to_dict() for r in forwards],
        "backref": [r.to_dict() for r in backs],
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"対象: {target.path}")
    print(f"  state: {target.state_label or '-'}  type: {target.type or '-'}")
    print(f"  related (forward): {len(forwards)} 件")
    for r in forwards:
        print(f"    -> {r.state_label or '[?]'} {r.type or '?':<8} {r.path}")
    print(f"  逆参照 (backref): {len(backs)} 件")
    for r in backs:
        print(f"    <- {r.state_label or '[?]'} {r.type or '?':<8} {r.path}")
    return 0


def cmd_stale(args: argparse.Namespace) -> int:
    """review_status 別の前倒し陳腐化候補を列挙。"""
    from .stale import find_stale

    cfg = _build_config(args)
    result = find_stale(cfg, project=getattr(args, "project", None))
    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if not result.items:
        print("stale: 対象なし")
        return 0
    print(f"stale: {len(result.items)} 件")
    for it in result.items:
        last = f" last_reviewed={it.last_reviewed}" if it.last_reviewed else ""
        print(
            f"  [{it.review_status}] +{it.days_over}d (>{it.threshold})  "
            f"{it.project}/{Path(it.path).name}{last}"
        )
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    """本文 + 親 plan + related を 1 つの AI 用プロンプトに組み立て stdout 出力。"""
    from .context import collect_context, render_context, to_clipboard

    cfg = _build_config(args)
    target_path = Path(args.file).resolve().as_posix()
    try:
        bundle = collect_context(target_path, cfg)
    except (FileNotFoundError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2
    text = render_context(bundle, fmt=args.format)
    if getattr(args, "clipboard", False):
        ok = to_clipboard(text)
        if not ok:
            print("クリップボードに書き出せませんでした（フォールバックで stdout 出力します）", file=sys.stderr)
            print(text)
        else:
            print(f"クリップボードへ書き出しました ({len(text)} chars)")
        return 0
    print(text)
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    """frontmatter の owner を現ユーザーで上書き / unclaim。"""
    from .claim import claim
    from .services.frontmatter import FrontmatterValidationError

    path = Path(args.file)
    try:
        result = claim(path, unclaim=getattr(args, "unclaim", False))
    except FileNotFoundError:
        print(f"ファイルが見つかりません: {args.file}", file=sys.stderr)
        return 2
    except FrontmatterValidationError as e:
        print(f"frontmatter 書き換え失敗: {e}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if getattr(args, "unclaim", False):
        print(f"unclaim: owner を空にしました ({result.path})")
    else:
        print(f"claim: owner={result.owner} claimed_at={result.claimed_at} ({result.path})")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """``~/.docsweep/config.yaml`` の user 設定を CLI から読み書き。"""
    from .config import (
        SETTABLE_KEYS,
        get_user_setting,
        list_settings,
        set_user_setting,
    )

    if getattr(args, "list_all", False):
        settings = list_settings()
        if getattr(args, "json", False):
            print(json.dumps(settings, ensure_ascii=False, indent=2))
        else:
            for k in sorted(settings):
                v = settings[k]
                print(f"{k} = {v if v is not None else '(未設定)'}")
        return 0
    if getattr(args, "get_key", None):
        key = args.get_key
        try:
            v = get_user_setting(key)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        if getattr(args, "json", False):
            print(json.dumps({key: v}, ensure_ascii=False))
        else:
            print(v if v is not None else "")
        return 0
    if getattr(args, "unset_key", None):
        key = args.unset_key
        try:
            set_user_setting(key, None)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        print(f"unset: {key}")
        return 0
    key = args.key
    value = args.value
    if not key:
        print(f"使い方: docsweep config <key> [<value>]  /  --list  /  --get KEY  /  --unset KEY  （許可キー: {sorted(SETTABLE_KEYS)}）")
        return 2
    if value is None:
        try:
            v = get_user_setting(key)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        print(v if v is not None else "")
        return 0
    try:
        path = set_user_setting(key, value)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"設定: {key} = {value}  ({path})")
    return 0


def cmd_timeline(args: argparse.Namespace) -> int:
    """topic を含む plan/bugfix/pending を時系列で列挙。"""
    from .timeline import build_timeline, render_timeline

    cfg = _build_config(args)
    result = build_timeline(cfg, args.topic)
    print(render_timeline(result, fmt=args.format))
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    """自由クエリで FileRecord を絞り込む。"""
    from .find import FindFilters, find_records, resolve_owner_alias

    cfg = _build_config(args)
    owner = resolve_owner_alias(getattr(args, "owner", None))
    filters = FindFilters(
        owner=owner,
        tags=list(getattr(args, "tags", None) or []),
        types=list(getattr(args, "types", None) or []),
        states=list(getattr(args, "states", None) or []),
        review_statuses=list(getattr(args, "review_statuses", None) or []),
        project=getattr(args, "project", None),
        q=getattr(args, "q", None),
    )
    records = find_records(cfg, filters)
    if getattr(args, "json", False):
        print(json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2))
        return 0
    if not records:
        print("find: 該当なし")
        return 0
    _print_records_table(records, cfg.lang)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """``docsweep export --okf`` — OKF 互換 zip を書き出す。"""
    from .export import run_export

    cfg = _build_config(args)
    # ``--okf`` 未指定でも現状は OKF 一択なので暗黙に有効化（将来 ``--format`` 追加余地）。
    out = Path(args.out) if getattr(args, "out", None) else None
    result = run_export(
        cfg,
        out=out,
        project=getattr(args, "project", None),
        include_archive=getattr(args, "include_archive", False),
    )
    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print(f"OKF export: {result.file_count} files -> {result.out_path}")
    if result.include_archive:
        print("  (archive/ 配下も含めました)")
    return 0


def cmd_completion(args: argparse.Namespace) -> int:
    """シェル補完スクリプトを stdout 出力。"""
    from .completion import render_completion

    cfg = _build_config(args)
    print(render_completion(args.shell, cfg))
    return 0


_SUBCOMMANDS = {
    "scan", "triage", "apply", "sweep", "serve", "promote", "index", "pending",
    "index-sync", "index-rebuild", "index-watch", "index-stats", "index-vacuum",
    "brief", "cross", "capture",
    "linkcheck", "auto-triage", "graph", "resurrect",
    "report", "summary", "new", "review", "inject", "eject", "list", "mcp",
    "migrate-frontmatter", "fix-related", "show", "stale", "context", "claim",
    "config", "timeline", "find", "completion", "export", "activity",
    "doctor", "init", "undo", "day", "intent", "fix-conflict", "notify",
    "project", "review-week", "history", "cookbook", "memory", "ics",
}

_DISPATCH = {
    "scan": cmd_scan, "triage": cmd_triage, "apply": cmd_apply, "sweep": cmd_sweep,
    "serve": cmd_serve, "promote": cmd_promote, "index": cmd_index, "pending": cmd_pending,
    "index-sync": cmd_index_sync, "index-rebuild": cmd_index_rebuild,
    "index-watch": cmd_index_watch, "index-stats": cmd_index_stats,
    "index-vacuum": cmd_index_vacuum,
    "brief": cmd_brief, "cross": cmd_cross,
    "capture": cmd_capture, "linkcheck": cmd_linkcheck,
    "auto-triage": cmd_auto_triage, "graph": cmd_graph,
    "resurrect": cmd_resurrect,
    "report": cmd_report, "summary": cmd_summary, "new": cmd_new, "review": cmd_review,
    "inject": cmd_inject, "eject": cmd_eject, "list": cmd_list, "mcp": cmd_mcp,
    "migrate-frontmatter": cmd_migrate_frontmatter,
    "fix-related": cmd_fix_related,
    "show": cmd_show,
    "stale": cmd_stale,
    "context": cmd_context,
    "claim": cmd_claim,
    "config": cmd_config,
    "timeline": cmd_timeline,
    "find": cmd_find,
    "completion": cmd_completion,
    "export": cmd_export,
    "activity": cmd_activity,
    "doctor": cmd_doctor,
    "init": cmd_init,
    "undo": cmd_undo,
    "day": cmd_day,
    "intent": cmd_intent,
    "fix-conflict": cmd_fix_conflict,
    "notify": cmd_notify,
    "project": cmd_project,
    "review-week": cmd_review_week,
    "history": cmd_history,
    "cookbook": cmd_cookbook,
    "memory": cmd_memory,
    "ics": cmd_ics,
}


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # サブコマンド未指定（かつ --version/-h 以外）は既定 scan に回す。
    if raw and raw[0] not in _SUBCOMMANDS and raw[0] not in ("--version", "-h", "--help"):
        raw = ["scan", *raw]
    parser = build_parser()
    args = parser.parse_args(raw)
    if args.command is None:
        return cmd_scan(parser.parse_args(["scan"]))
    handler = _DISPATCH.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    code = handler(args)
    # 次の一手ヒント（うるさくしない・失敗しても握りつぶす）
    try:
        from .hints import suggest_after_command

        cfg = None
        try:
            cfg = _build_config(args)
        except Exception:
            cfg = None
        hint = suggest_after_command(args.command, cfg)
        if hint and not getattr(args, "json", False):
            print(hint, file=sys.stderr)
    except Exception:
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
