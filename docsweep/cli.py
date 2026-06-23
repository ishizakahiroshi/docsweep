"""薄い CLI（玄人・cron・AI 委譲向け）。

非対話を厳守する出力（--auto / --json）と、人間向けテーブル表示を提供する。
口の粒度は MCP 前提（1 コマンド＝1 ツール）: scan / triage / apply / sweep ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .config import load_config
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

    p_promote = sub.add_parser("promote", help="release sweep: 様子見をまとめて完了へ昇格し archive へ")
    _add_scope_args(p_promote)
    p_promote.add_argument("--state", default="watching", help="昇格元の状態（既定 watching）")
    p_promote.add_argument("--to", default="done", help="昇格先の状態（既定 done）")
    p_promote.add_argument("--project", help="対象プロジェクトを絞る")
    p_promote.add_argument("--dry-run", action="store_true")
    p_promote.add_argument("--json", action="store_true")

    p_index = sub.add_parser("index", help="横断 INDEX（.docsweep/INDEX.md / .json）を再生成")
    _add_scope_args(p_index)

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
    p_new.add_argument("--project-dir", default=".", help="生成先プロジェクト（既定 .）")
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

    p_review = sub.add_parser("review", help="対話チェックリストで選択分を archive へ一括移送")
    _add_scope_args(p_review)

    p_inject = sub.add_parser("inject", help="運用ルール（管理ブロック＋.docsweep.yaml）を注入")
    p_inject.add_argument("--project", default=".", help="注入先プロジェクト（既定 .）")
    p_inject.add_argument("--preset", help="プリセット名（claude-jp / frontmatter）")
    p_inject.add_argument("--no-yaml", action="store_true", help=".docsweep.yaml を書かない")
    p_inject.add_argument("--no-guidance", action="store_true", help="導線を省きラベル節だけ注入（導線をグローバルに寄せる場合）")
    p_inject.add_argument("--global", dest="is_global", action="store_true", help="個人グローバル設定へ導線だけ注入（全プロジェクトで効く）")
    p_inject.add_argument("--agent", choices=("claude", "codex"), default="claude", help="グローバル注入先の AI ツール（--global 時）")
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


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    result = run_scan(cfg)
    records = result.records
    project = getattr(args, "project", None)
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
    """残作業ビュー（要判断＋保留・古い順）を JSON で出す。MCP triage と同一契約。"""
    from .reports import build_triage

    print(json.dumps(
        build_triage(_build_config(args), project=getattr(args, "project", None)),
        ensure_ascii=False, indent=2,
    ))
    return 0


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
        from .index import write_index

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
    from .index import write_index

    cfg = _build_config(args)
    json_path, md_path = write_index(cfg)
    print(f"INDEX を生成しました:\n  {md_path}\n  {json_path}")
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    from .index import build_index

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
    from .templates_gen import new_doc

    project_dir = Path(args.project_dir)
    # ``.docsweep.yaml`` の ``due:`` ブロックから default_offset_days を読む。
    # --no-due 指定時は空 dict を渡してオフセット計算自体を無効化する（嘘の日付防止）。
    cfg = load_config(project_dir=project_dir)
    offsets: dict[str, int] = {} if getattr(args, "no_due", False) else cfg.due_default_offset_days
    doc = new_doc(
        args.type, args.topic,
        project_dir=project_dir, title=args.title,
        due=getattr(args, "due", None),
        offset_days=offsets,
    )
    if doc.due:
        print(f"生成しました: {doc.path} (due={doc.due})")
    else:
        print(f"生成しました: {doc.path}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    from .review import run_review

    return run_review(_build_config(args))


def cmd_inject(args: argparse.Namespace) -> int:
    from .inject import inject, inject_global

    tag = "（dry-run）" if args.dry_run else ""
    if getattr(args, "is_global", False):
        r = inject_global(agent=args.agent, target=args.global_target, dry_run=args.dry_run)
        print(f"inject {r.project}{tag}: 書込={r.written or '-'} 温存/不変={r.skipped or '-'}")
        for w in r.warnings:
            print(f"  ⚠ {w}")
        return 0

    r = inject(
        Path(args.project), preset=args.preset, write_yaml=not args.no_yaml,
        include_guidance=not args.no_guidance, dry_run=args.dry_run,
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
    app = create_app(cfg, token=token)
    url = f"http://127.0.0.1:{args.port}/?token={token}"
    print("=" * 60)
    print("  ブラウザでこのアドレスを開いてください（自動で開きます）:")
    print(f"  {url}")
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
    server.run()
    return 0


_SUBCOMMANDS = {
    "scan", "triage", "apply", "sweep", "serve", "promote", "index", "pending",
    "report", "summary", "new", "review", "inject", "eject", "list", "mcp",
}

_DISPATCH = {
    "scan": cmd_scan, "triage": cmd_triage, "apply": cmd_apply, "sweep": cmd_sweep,
    "serve": cmd_serve, "promote": cmd_promote, "index": cmd_index, "pending": cmd_pending,
    "report": cmd_report, "summary": cmd_summary, "new": cmd_new, "review": cmd_review,
    "inject": cmd_inject, "eject": cmd_eject, "list": cmd_list, "mcp": cmd_mcp,
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
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
