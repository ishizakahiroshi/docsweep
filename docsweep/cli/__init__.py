"""docsweep CLI entry point and backward-compatible command re-exports."""

from __future__ import annotations

import sys
from pathlib import Path

from .parser import _build_config, build_parser
from .commands.read import cmd_activity, cmd_brief, cmd_closeout_check, cmd_context, cmd_cookbook, cmd_cross, cmd_demo, cmd_day, cmd_doctor, cmd_export, cmd_find, cmd_graph, cmd_history, cmd_intent, cmd_linkcheck, cmd_list, cmd_okf_check, cmd_okf_profiles, cmd_pending, cmd_project, cmd_report, cmd_resurrect, cmd_scan, cmd_show, cmd_stale, cmd_summary, cmd_timeline, cmd_triage
from .commands.write import cmd_apply, cmd_auto_triage, cmd_capture, cmd_claim, cmd_fix_conflict, cmd_fix_related, cmd_migrate_frontmatter, cmd_new, cmd_promote, cmd_sweep
from .commands.notify import cmd_notify
from .commands.init import cmd_init, cmd_undo
from .commands.index import cmd_index, cmd_index_rebuild, cmd_index_stats, cmd_index_sync, cmd_index_vacuum, cmd_index_watch
from .commands.memory import cmd_memory
from .commands.ics import cmd_ics
from .commands.inject import cmd_eject, cmd_inject
from .commands.mcp import cmd_mcp
from .commands.provenance import cmd_provenance
from .commands.serve import cmd_serve
from .commands.completion import cmd_completion
from .commands.excluded import cmd_config, cmd_review, cmd_review_week

_SUBCOMMANDS = {'scan', 'triage', 'apply', 'sweep', 'serve', 'promote', 'index', 'pending', 'index-sync', 'index-rebuild', 'index-watch', 'index-stats', 'index-vacuum', 'brief', 'cross', 'capture', 'linkcheck', 'auto-triage', 'graph', 'resurrect', 'report', 'summary', 'new', 'provenance', 'review', 'inject', 'eject', 'list', 'mcp', 'migrate-frontmatter', 'fix-related', 'show', 'stale', 'context', 'claim', 'config', 'timeline', 'find', 'completion', 'export', 'okf-check', 'okf-profiles', 'closeout-check', 'activity', 'doctor', 'init', 'undo', 'day', 'intent', 'fix-conflict', 'notify', 'project', 'review-week', 'history', 'cookbook', 'memory', 'ics', 'demo'}

_DISPATCH = {
    'scan': cmd_scan,
    'triage': cmd_triage,
    'apply': cmd_apply,
    'sweep': cmd_sweep,
    'serve': cmd_serve,
    'promote': cmd_promote,
    'index': cmd_index,
    'pending': cmd_pending,
    'index-sync': cmd_index_sync,
    'index-rebuild': cmd_index_rebuild,
    'index-watch': cmd_index_watch,
    'index-stats': cmd_index_stats,
    'index-vacuum': cmd_index_vacuum,
    'brief': cmd_brief,
    'cross': cmd_cross,
    'capture': cmd_capture,
    'linkcheck': cmd_linkcheck,
    'auto-triage': cmd_auto_triage,
    'graph': cmd_graph,
    'resurrect': cmd_resurrect,
    'report': cmd_report,
    'summary': cmd_summary,
    'new': cmd_new,
    'provenance': cmd_provenance,
    'review': cmd_review,
    'inject': cmd_inject,
    'eject': cmd_eject,
    'list': cmd_list,
    'mcp': cmd_mcp,
    'migrate-frontmatter': cmd_migrate_frontmatter,
    'fix-related': cmd_fix_related,
    'show': cmd_show,
    'stale': cmd_stale,
    'context': cmd_context,
    'claim': cmd_claim,
    'config': cmd_config,
    'timeline': cmd_timeline,
    'find': cmd_find,
    'completion': cmd_completion,
    'export': cmd_export,
    'okf-check': cmd_okf_check,
    'okf-profiles': cmd_okf_profiles,
    'closeout-check': cmd_closeout_check,
    'activity': cmd_activity,
    'doctor': cmd_doctor,
    'init': cmd_init,
    'undo': cmd_undo,
    'day': cmd_day,
    'intent': cmd_intent,
    'fix-conflict': cmd_fix_conflict,
    'notify': cmd_notify,
    'project': cmd_project,
    'review-week': cmd_review_week,
    'history': cmd_history,
    'cookbook': cmd_cookbook,
    'demo': cmd_demo,
    'memory': cmd_memory,
    'ics': cmd_ics,
}

def _soften_console_encoding(json_mode: bool) -> None:
    """人間向け出力で、コンソールが表現できない文字を traceback にしない。

    ja-JP Windows の cp932 コンソールへリダイレクトすると、``—`` や ``–`` のような
    文字を含む **文書側のデータ** が ``UnicodeEncodeError`` になり、CLI が raw
    traceback で落ちる。装飾 glyph は ASCII 化して持ち込まない方針だが、要約に
    何が入るかは docsweep 側では決められない。

    そこで人間向け出力の描画境界だけを緩め、表現できない文字はバックスラッシュ
    表記へ落として見える形にする。ファイル上のデータは変えない。``--json`` は stdout の
    バイト列そのものが契約なので触らない（表現できなければ明示エラーで落とす）。
    """
    if json_mode:
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if reconfigure is None or encoding in ("utf8", "utf8sig"):
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (OSError, ValueError):
            continue


def _print_doc_hint(help_id: str) -> None:
    """エラー直後にドキュメント深リンクを 1 つ出す（UX W4 / P71）。

    ヒントの表示可否は ``hints.hints_enabled`` と同じ ``DOCSWEEP_HINTS`` で決める。
    ここで例外を出すと本来のエラーを覆い隠すので、失敗しても黙って諦める。
    """
    try:
        from ..doc_links import doc_hint
        from ..hints import hints_enabled

        line = doc_hint(help_id, enabled=hints_enabled())
        if line:
            print(line, file=sys.stderr)
    except Exception:
        return


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] not in _SUBCOMMANDS and raw[0] not in ("--version", "-h", "--help"):
        first = raw[0]
        # Keep the convenient ``docsweep <existing-directory>`` scan shorthand,
        # but do not turn a typo into an empty successful scan.  Leading flags
        # remain scan flags for the historical ``docsweep --root ...`` form.
        if first.startswith("-") or Path(first).is_dir():
            raw = ["scan", *raw]
        else:
            print(f"docsweep: unknown command or scan directory: {first}", file=sys.stderr)
            _print_doc_hint("cli.unknown_command")
            return 2
    parser = build_parser()
    args = parser.parse_args(raw)
    if args.command is None:
        return cmd_scan(parser.parse_args(["scan"]))
    handler = _DISPATCH.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    _soften_console_encoding(bool(getattr(args, "json", False)))
    try:
        code = handler(args)
    except UnicodeEncodeError as exc:
        # ここへ来るのは --json だけ（人間向けは上で緩めてある）。出力先の
        # コードページが payload を表現できないという環境エラーなので、
        # raw traceback ではなく短い指示を出して exit 2 で返す。
        print(
            f"docsweep: 出力先のエンコーディング {exc.encoding} では"
            "この内容を表現できません（PYTHONIOENCODING=utf-8 を指定してください）",
            file=sys.stderr,
        )
        _print_doc_hint("console.encoding")
        return 2
    try:
        from ..hints import suggest_after_command

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


__all__ = ["build_parser", "main", *sorted(name for name in globals() if name.startswith("cmd_"))]
