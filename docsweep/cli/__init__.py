"""docsweep CLI entry point and backward-compatible command re-exports."""

from __future__ import annotations

import sys

from .parser import _build_config, build_parser
from .commands.read import cmd_activity, cmd_brief, cmd_context, cmd_cookbook, cmd_cross, cmd_day, cmd_doctor, cmd_export, cmd_find, cmd_graph, cmd_history, cmd_intent, cmd_linkcheck, cmd_list, cmd_pending, cmd_project, cmd_report, cmd_resurrect, cmd_scan, cmd_show, cmd_stale, cmd_summary, cmd_timeline, cmd_triage
from .commands.write import cmd_apply, cmd_auto_triage, cmd_capture, cmd_claim, cmd_fix_conflict, cmd_fix_related, cmd_migrate_frontmatter, cmd_new, cmd_promote, cmd_sweep
from .commands.notify import cmd_notify
from .commands.init import cmd_init, cmd_undo
from .commands.index import cmd_index, cmd_index_rebuild, cmd_index_stats, cmd_index_sync, cmd_index_vacuum, cmd_index_watch
from .commands.memory import cmd_memory
from .commands.ics import cmd_ics
from .commands.inject import cmd_eject, cmd_inject
from .commands.mcp import cmd_mcp
from .commands.serve import cmd_serve
from .commands.completion import cmd_completion
from .commands.excluded import cmd_config, cmd_review, cmd_review_week

_SUBCOMMANDS = {'scan', 'triage', 'apply', 'sweep', 'serve', 'promote', 'index', 'pending', 'index-sync', 'index-rebuild', 'index-watch', 'index-stats', 'index-vacuum', 'brief', 'cross', 'capture', 'linkcheck', 'auto-triage', 'graph', 'resurrect', 'report', 'summary', 'new', 'review', 'inject', 'eject', 'list', 'mcp', 'migrate-frontmatter', 'fix-related', 'show', 'stale', 'context', 'claim', 'config', 'timeline', 'find', 'completion', 'export', 'activity', 'doctor', 'init', 'undo', 'day', 'intent', 'fix-conflict', 'notify', 'project', 'review-week', 'history', 'cookbook', 'memory', 'ics'}

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
    'memory': cmd_memory,
    'ics': cmd_ics,
}

def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
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
