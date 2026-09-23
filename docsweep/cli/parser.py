"""argparse parser construction and shared CLI configuration loading."""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import __version__
from ..config import SECRET_POLICIES, WORK_POLICIES, load_config
from ..i18n import SUPPORTED_LANGS, t


def _add_lang_arg(p: argparse.ArgumentParser, *, nested: bool = False) -> None:
    """表示言語の ``--lang`` を足す。値は ``main()`` が argv から先に読むので、受け付けるだけ。

    入れ子のサブコマンド（``release close`` 等）では既定値を持たせない。argparse は子の
    namespace の値を親へそのまま写すため、子の既定値 None が ``release --lang en close`` の
    指定を消してしまう。選べる言語は ``locales/`` のフォルダで決まる（コードに一覧を持たない）。
    """
    default = argparse.SUPPRESS if nested else None
    p.add_argument("--lang", choices=SUPPORTED_LANGS, default=default, help=t("help.scope.lang"))


def _add_scope_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("paths", nargs="*", help=t("help.scope.paths"))
    p.add_argument("--root", action="append", dest="roots", metavar="PATH", help=t("help.scope.root"))
    p.add_argument("--profile", help=t("help.scope.profile"))
    p.add_argument("--config", help=t("help.scope.config"))
    p.add_argument("--project-dir", help=t("help.scope.project_dir"))
    _add_lang_arg(p)
    p.add_argument("--work-dir", help=t("help.scope.work_dir"))
    p.add_argument("--work-policy", choices=sorted(WORK_POLICIES), help=t("help.scope.work_policy"))
    p.add_argument("--secret-policy", choices=sorted(SECRET_POLICIES), help=t("help.scope.secret_policy"))
    p.add_argument("--allow-sensitive", action="store_true", help=t("help.scope.allow_sensitive"))


def _non_negative_int(value: str) -> int:
    """Parse a day offset while rejecting values that would create a past due date."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(t("help.type.non_negative_int")) from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(t("help.type.non_negative_int"))
    return parsed


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
    # --lang は表示言語だけを変える（main() が argv から先に読む）。cfg.lang は文書の言語なので
    # 上書きしない。設定で言語を決めていないときは Config.document_lang() が表示言語を使う。
    if getattr(args, "work_dir", None):
        cfg.work_dir = str(args.work_dir)
        cfg.work_dir_explicit = True
    if getattr(args, "work_policy", None):
        cfg.work_policy = str(args.work_policy)
        cfg.work_policy_explicit = True
    if getattr(args, "secret_policy", None):
        cfg.secret_policy = str(args.secret_policy)
    return cfg


def build_parser() -> argparse.ArgumentParser:
    """CLI のパーサーを作る。help の文言は、呼んだ時点の表示言語で辞書から引く。"""
    parser = argparse.ArgumentParser(prog="docsweep", description=t("help.root._description"))
    parser.add_argument("--version", action="version", version=f"docsweep {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help=t("help.scan._command"))
    _add_scope_args(p_scan)
    p_scan.add_argument("--json", action="store_true", help=t("help.common.json_machine"))
    p_scan.add_argument("--all", action="store_true", help=t("help.scan.all"))
    p_scan.add_argument("--project", help=t("help.scan.project"))

    p_triage = sub.add_parser("triage", help=t("help.triage._command"))
    _add_scope_args(p_triage)
    p_triage.add_argument("--json", action="store_true", default=True, help=t("help.triage.json"))
    p_triage.add_argument("--project", help=t("help.common.project_filter"))
    p_triage.add_argument(
        "--tag", action="append", dest="tags", metavar="NAME",
        help=t("help.triage.tag"),
    )
    p_triage.add_argument(
        "--show", action="append", dest="show", choices=("owner", "tags"),
        help=t("help.triage.show"),
    )
    p_triage.add_argument(
        "--review", action="store_true",
        help=t("help.triage.review"),
    )
    p_triage.add_argument(
        "--head", type=int, default=0, metavar="N",
        help=t("help.triage.head"),
    )

    p_apply = sub.add_parser("apply", help=t("help.apply._command"))
    _add_scope_args(p_apply)
    p_apply.add_argument("--path", required=True, help=t("help.apply.path"))
    p_apply.add_argument("--action", required=True, help="discard|keep|resume|relabel|promote")
    p_apply.add_argument("--to", help=t("help.apply.to"))
    p_apply.add_argument(
        "--watching-days", type=_non_negative_int, metavar="N",
        help=t("help.apply.watching_days"),
    )
    p_apply.add_argument("--dry-run", action="store_true")

    p_sweep = sub.add_parser("sweep", help=t("help.sweep._command"))
    _add_scope_args(p_sweep)
    p_sweep.add_argument("--project", help=t("help.sweep.project"))
    p_sweep.add_argument("--dry-run", action="store_true", help=t("help.sweep.dry_run"))
    p_sweep.add_argument("--json", action="store_true")

    p_mv = sub.add_parser(
        "mv",
        help=t("help.mv._command"),
        description=t("help.mv._description"),
    )
    p_mv.add_argument("sources", nargs="+", metavar="SRC", help=t("help.mv.sources"))
    p_mv.add_argument("--to", required=True, metavar="DIR", help=t("help.mv.to"))
    p_mv.add_argument("--dry-run", action="store_true", help=t("help.mv.dry_run"))
    p_mv.add_argument("--no-body", action="store_true", help=t("help.mv.no_body"))
    p_mv.add_argument("--json", action="store_true")
    p_mv.add_argument("--project-dir", help=t("help.mv.project_dir"))
    p_mv.add_argument("--config", help=t("help.scope.config"))
    _add_lang_arg(p_mv)

    p_serve = sub.add_parser("serve", help=t("help.serve._command"))
    _add_scope_args(p_serve)
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--no-browser", action="store_true", help=t("help.serve.no_browser"))
    p_serve.add_argument("--token", help=t("help.serve.token"))
    p_serve.add_argument(
        "--read-only", action="store_true",
        help=t("help.serve.read_only"),
    )
    p_serve.add_argument(
        "--allow-root-mutation", action="store_true",
        help=t("help.serve.allow_root_mutation"),
    )

    p_promote = sub.add_parser("promote", help=t("help.promote._command"))
    _add_scope_args(p_promote)
    p_promote.add_argument("--state", default="watching", help=t("help.promote.state"))
    p_promote.add_argument("--to", default="done", help=t("help.promote.to"))
    p_promote.add_argument("--project", help=t("help.common.project_filter"))
    p_promote.add_argument(
        "--due-expired", action="store_true",
        help=t("help.promote.due_expired"),
    )
    p_promote.add_argument("--dry-run", action="store_true")
    p_promote.add_argument(
        "--yes", action="store_true",
        help=t("help.promote.yes"),
    )
    p_promote.add_argument("--json", action="store_true")

    p_index = sub.add_parser("index", help=t("help.index._command"))
    _add_scope_args(p_index)

    # C1 (wings): SQLite 索引（~/.docsweep/index.db）への差分同期 / 全再構築
    p_index_sync = sub.add_parser(
        "index-sync",
        help=t("help.index_sync._command")
    )
    _add_scope_args(p_index_sync)
    p_index_sync.add_argument("--json", action="store_true", help=t("help.common.json_stats"))
    p_index_sync.add_argument(
        "--prune-projects", action="store_true",
        help=t("help.common.prune_projects")
    )

    p_index_rebuild = sub.add_parser(
        "index-rebuild",
        help=t("help.index_rebuild._command")
    )
    _add_scope_args(p_index_rebuild)
    p_index_rebuild.add_argument("--json", action="store_true", help=t("help.common.json_stats"))
    p_index_rebuild.add_argument(
        "--prune-projects", action="store_true",
        help=t("help.common.prune_projects")
    )
    p_index_rebuild.add_argument(
        "--no-vacuum", action="store_true",
        help=t("help.index_rebuild.no_vacuum")
    )

    p_index_watch = sub.add_parser(
        "index-watch",
        help=t("help.index_watch._command")
    )
    _add_scope_args(p_index_watch)
    p_index_watch.add_argument(
        "--debounce", type=float, default=0.5,
        help=t("help.index_watch.debounce")
    )

    # C1 (bloat-mitigation): index-stats — 索引 DB の物理サイズ / 行数 / freelist を観測
    p_index_stats = sub.add_parser(
        "index-stats",
        help=t("help.index_stats._command")
    )
    _add_scope_args(p_index_stats)
    p_index_stats.add_argument("--json", action="store_true", help=t("help.common.json"))

    # C2 (bloat-mitigation): index-vacuum — 手動メンテ口（DELETE 後の物理サイズ回収）
    p_index_vacuum = sub.add_parser(
        "index-vacuum",
        help=t("help.index_vacuum._command")
    )
    _add_scope_args(p_index_vacuum)
    p_index_vacuum.add_argument("--json", action="store_true", help=t("help.common.json"))

    # C3 (wings): brief — 今日の 1 個を断定する朝の入口
    p_brief = sub.add_parser(
        "brief",
        help=t("help.brief._command")
    )
    _add_scope_args(p_brief)
    p_brief.add_argument("--project", help=t("help.common.project_cwd"))
    p_brief.add_argument("--all", action="store_true", dest="all_projects",
                         help=t("help.brief.all"))
    p_brief.add_argument("--json", action="store_true", help=t("help.common.json_human_default"))
    p_brief.add_argument("--continue", action="store_true", dest="auto_continue",
                         help=t("help.brief.continue"))

    # C4 (wings): cross — 全プロジェクト束ねた俯瞰
    p_cross = sub.add_parser(
        "cross",
        help=t("help.cross._command")
    )
    _add_scope_args(p_cross)
    p_cross.add_argument("--project", help=t("help.cross.project"))
    p_cross.add_argument("--explain", metavar="REL", help=t("help.cross.explain"))
    p_cross.add_argument("--json", action="store_true", help=t("help.common.json_human_default"))

    # C2 (wings): capture — 会話履歴から plan / bugfix / pending の草案を抽出
    p_capture = sub.add_parser(
        "capture",
        help=t("help.capture._command")
    )
    _add_scope_args(p_capture)
    p_capture.add_argument(
        "--from", dest="source", default="clipboard",
        choices=("clipboard", "file", "-"),
        help=t("help.capture.from"),
    )
    p_capture.add_argument("--file", help=t("help.capture.file"))
    p_capture.add_argument("--llm", action="store_true",
                           help=t("help.capture.llm"))
    p_capture.add_argument("--project", help=t("help.capture.project"))
    p_capture.add_argument("--max", type=int, default=5, help=t("help.capture.max"))
    p_capture.add_argument("--save-all", action="store_true",
                           help=t("help.capture.save_all"))
    p_capture.add_argument("--out-dir", help=t("help.capture.out_dir"))
    p_capture.add_argument("--json", action="store_true", help=t("help.common.json_human_default"))

    # C5 (wings): linkcheck / auto-triage / graph
    p_linkcheck = sub.add_parser(
        "linkcheck",
        help=t("help.linkcheck._command")
    )
    _add_scope_args(p_linkcheck)
    p_linkcheck.add_argument("--file", help=t("help.linkcheck.file"))
    p_linkcheck.add_argument("--json", action="store_true")

    p_autotri = sub.add_parser(
        "auto-triage",
        help=t("help.auto_triage._command")
    )
    _add_scope_args(p_autotri)
    grp = p_autotri.add_mutually_exclusive_group(required=True)
    grp.add_argument("--suggest", action="store_true", help=t("help.auto_triage.suggest"))
    grp.add_argument("--apply", help=t("help.auto_triage.apply"))
    p_autotri.add_argument("--file", help=t("help.auto_triage.file"))
    p_autotri.add_argument("--dry-run", action="store_true", help=t("help.auto_triage.dry_run"))

    p_graph = sub.add_parser(
        "graph",
        help=t("help.graph._command")
    )
    _add_scope_args(p_graph)
    p_graph.add_argument("--project", help=t("help.graph.project"))
    p_graph.add_argument("--json", action="store_true", default=True)

    # C6 (wings): resurrect — archive 蘇生（embedding opt-in）
    p_resurrect = sub.add_parser(
        "resurrect",
        help=t("help.resurrect._command")
    )
    _add_scope_args(p_resurrect)
    p_resurrect.add_argument("--threshold", type=float, default=0.5,
                             help=t("help.resurrect.threshold"))
    p_resurrect.add_argument("--no-embedding", action="store_true",
                             help=t("help.resurrect.no_embedding"))
    p_resurrect.add_argument("--top-k", type=int, default=1,
                             help=t("help.resurrect.top_k"))
    p_resurrect.add_argument("--json", action="store_true", default=True)

    p_pending = sub.add_parser("pending", help=t("help.pending._command"))
    _add_scope_args(p_pending)
    p_pending.add_argument("--json", action="store_true")

    p_report = sub.add_parser("report", help=t("help.report._command"))
    _add_scope_args(p_report)

    p_summary = sub.add_parser("summary", help=t("help.summary._command"))
    _add_scope_args(p_summary)
    p_summary.add_argument("--project", help=t("help.common.project_filter"))

    p_new = sub.add_parser("new", help=t("help.new._command"))
    p_new.add_argument("type", choices=("plan", "bugfix", "pending"))
    p_new.add_argument("topic", help=t("help.new.topic"))
    p_new.add_argument("--title", help=t("help.new.title"))
    p_new.add_argument("--config", help=t("help.scope.config"))
    p_new.add_argument("--work-dir", help=t("help.scope.work_dir"))
    p_new.add_argument("--work-policy", choices=sorted(WORK_POLICIES), help=t("help.scope.work_policy"))
    p_new.add_argument("--secret-policy", choices=sorted(SECRET_POLICIES), help=t("help.scope.secret_policy"))
    p_new.add_argument("--allow-sensitive", action="store_true", help=t("help.new.allow_sensitive"))
    p_new.add_argument(
        "--project-dir",
        default=None,
        help=t("help.new.project_dir"),
    )
    p_new.add_argument(
        "--due",
        help=t("help.new.due"),
    )
    p_new.add_argument(
        "--no-due", action="store_true",
        help=t("help.new.no_due"),
    )
    p_new.add_argument(
        "--target-release",
        help=t("help.new.target_release"),
    )
    p_new.add_argument(
        "--split", type=int, default=0, metavar="N",
        help=t("help.new.split"),
    )
    p_new.add_argument(
        "--titles", metavar="A,B,C",
        help=t("help.new.titles"),
    )
    p_new.add_argument(
        "--delegate", action="store_true",
        help=t("help.new.delegate"),
    )
    for flag, dest, help_text in (
        ("--ai-agent", "ai_agent", t("help.new.ai_agent")),
        ("--ai-runtime", "ai_runtime", t("help.new.ai_runtime")),
        ("--ai-provider", "ai_provider", "AI provider"),
        ("--ai-model-id", "ai_model_id", t("help.common.ai_model_id")),
        ("--ai-model-display", "ai_model_display", t("help.common.ai_model_display")),
        ("--ai-reasoning", "ai_reasoning", "reasoning profile"),
        ("--ai-model-source", "ai_model_source", "orchestrator/runtime/cli/ui/user-reported/unavailable"),
        ("--actor-key", "actor_key", t("help.common.actor_key")),
        ("--ai-session-log", "ai_session_log", t("help.new.ai_session_log")),
    ):
        p_new.add_argument(flag, dest=dest, help=help_text)
    _add_lang_arg(p_new)

    p_provenance = sub.add_parser(
        "provenance",
        help=t("help.provenance._command"),
    )
    _add_lang_arg(p_provenance, nested=True)
    p_prov_sub = p_provenance.add_subparsers(dest="provenance_action", required=True)

    def add_provenance_scope(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--project-dir", help=t("help.provenance.project_dir"))
        parser.add_argument("--config", help=t("help.common.config_path"))
        parser.add_argument("--json", action="store_true", help=t("help.provenance.json"))
        _add_lang_arg(parser, nested=True)

    def add_ai_metadata(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--agent", help=t("help.provenance.agent"))
        parser.add_argument("--runtime", help=t("help.provenance.runtime"))
        parser.add_argument("--provider", help=t("help.provenance.provider"))
        parser.add_argument("--model-id", help=t("help.common.ai_model_id"))
        parser.add_argument("--model-display", help=t("help.common.ai_model_display"))
        parser.add_argument("--reasoning", help=t("help.provenance.reasoning"))
        parser.add_argument("--model-source", help=t("help.provenance.model_source"))
        parser.add_argument("--actor-key", help=t("help.common.actor_key"))

    p_prov_init = p_prov_sub.add_parser("init", help=t("help.provenance.init._command"))
    add_provenance_scope(p_prov_init)
    add_ai_metadata(p_prov_init)
    p_prov_init.add_argument("--path", required=True, help=t("help.provenance.path"))
    p_prov_init.add_argument(
        "--update",
        action="store_true",
        help=t("help.provenance.init.update"),
    )

    p_prov_start = p_prov_sub.add_parser("start", help=t("help.provenance.start._command"))
    add_provenance_scope(p_prov_start)
    add_ai_metadata(p_prov_start)
    p_prov_start.add_argument("--path", required=True, help=t("help.provenance.path"))
    p_prov_start.add_argument("--context", required=True, help=t("help.provenance.start.context"))
    p_prov_start.add_argument(
        "--role", required=True, choices=("implementation", "review", "verification")
    )
    p_prov_start.add_argument("--notes", default="", help=t("help.provenance.start.notes"))

    p_prov_finish = p_prov_sub.add_parser("finish", help=t("help.provenance.finish._command"))
    add_provenance_scope(p_prov_finish)
    p_prov_finish.add_argument(
        "--execution", required=True, help=t("help.provenance.finish.execution")
    )
    p_prov_finish.add_argument(
        "--result", required=True, choices=("completed", "partial", "failed", "cancelled")
    )
    p_prov_finish.add_argument("--evidence-refs", default="", help=t("help.provenance.finish.evidence_refs"))
    p_prov_finish.add_argument(
        "--evidence-ref", action="append", default=[],
        help=t("help.provenance.finish.evidence_ref"),
    )
    p_prov_finish.add_argument("--notes", help=t("help.provenance.finish.notes"))

    p_prov_check = p_prov_sub.add_parser("check", help=t("help.provenance.check._command"))
    add_provenance_scope(p_prov_check)
    p_prov_check.add_argument("--path", required=True, help=t("help.provenance.path"))
    p_prov_check.add_argument(
        "--fix-work-path",
        action="store_true",
        help=t("help.provenance.check.fix_work_path"),
    )

    p_review = sub.add_parser("review", help=t("help.review._command"))
    _add_scope_args(p_review)

    p_inject = sub.add_parser("inject", help=t("help.inject._command"))
    p_inject.add_argument("--project", default=".", help=t("help.inject.project"))
    p_inject.add_argument("--preset", help=t("help.inject.preset"))
    p_inject.add_argument("--no-yaml", action="store_true", help=t("help.inject.no_yaml"))
    p_inject.add_argument("--no-guidance", action="store_true", help=t("help.inject.no_guidance"))
    p_inject.add_argument("--global", dest="is_global", action="store_true", help=t("help.inject.global"))
    p_inject.add_argument(
        "--agent", choices=("claude", "codex"), default="claude", help=t("help.inject.agent")
    )
    p_inject.add_argument("--lang", choices=SUPPORTED_LANGS, help=t("help.inject.lang"))
    p_inject.add_argument("--global-target", dest="global_target", help=t("help.inject.global_target"))
    p_inject.add_argument("--dry-run", action="store_true")

    p_eject = sub.add_parser("eject", help=t("help.eject._command"))
    p_eject.add_argument("--project", default=".", help=t("help.eject.project"))
    p_eject.add_argument("--all", action="store_true", help=t("help.eject.all"))
    p_eject.add_argument("--purge", action="store_true", help=t("help.eject.purge"))
    p_eject.add_argument("--global", dest="is_global", action="store_true", help=t("help.eject.global"))
    p_eject.add_argument(
        "--agent", choices=("claude", "codex"), default="claude", help=t("help.eject.agent")
    )
    p_eject.add_argument("--global-target", dest="global_target", help=t("help.eject.global_target"))
    p_eject.add_argument("--dry-run", action="store_true")
    _add_lang_arg(p_eject)

    p_list = sub.add_parser("list", help=t("help.list._command"))
    p_list.add_argument("--injected", action="store_true", help=t("help.list.injected"))
    p_list.add_argument("--json", action="store_true")
    _add_lang_arg(p_list)

    p_mcp = sub.add_parser("mcp", help=t("help.mcp._command"))
    _add_scope_args(p_mcp)

    # ------------------------------------------------------------------
    # C2: OKF 採用 Phase 2 サブコマンド
    # ------------------------------------------------------------------

    p_migrate = sub.add_parser(
        "migrate-frontmatter",
        help=t("help.migrate_frontmatter._command"),
    )
    _add_scope_args(p_migrate)
    p_migrate.add_argument("--project", help=t("help.common.project_filter"))
    p_migrate.add_argument("--apply", action="store_true", help=t("help.common.apply_default_dry_run"))
    p_migrate.add_argument("--dry-run", action="store_true", help=t("help.migrate_frontmatter.dry_run"))
    p_migrate.add_argument("--json", action="store_true")

    p_fix_related = sub.add_parser(
        "fix-related",
        help=t("help.fix_related._command"),
    )
    _add_scope_args(p_fix_related)
    p_fix_related.add_argument("--apply", action="store_true", help=t("help.common.apply_default_dry_run"))
    p_fix_related.add_argument("--dry-run", action="store_true")
    p_fix_related.add_argument("--json", action="store_true")

    p_show = sub.add_parser("show", help=t("help.show._command"))
    _add_scope_args(p_show)
    p_show.add_argument("file", help=t("help.show.file"))
    p_show.add_argument("--json", action="store_true")

    p_stale = sub.add_parser(
        "stale",
        help=t("help.stale._command"),
    )
    _add_scope_args(p_stale)
    p_stale.add_argument("--project", help=t("help.common.project_filter"))
    p_stale.add_argument("--json", action="store_true")

    p_context = sub.add_parser(
        "context",
        help=t("help.context._command"),
    )
    _add_scope_args(p_context)
    p_context.add_argument("file", help=t("help.common.target_md"))
    p_context.add_argument("--clipboard", action="store_true", help=t("help.context.clipboard"))
    p_context.add_argument("--format", choices=("markdown", "plain"), default="markdown")

    p_claim = sub.add_parser("claim", help=t("help.claim._command"))
    p_claim.add_argument("file", help=t("help.common.target_md"))
    p_claim.add_argument("--unclaim", action="store_true", help=t("help.claim.unclaim"))
    p_claim.add_argument("--json", action="store_true")
    _add_lang_arg(p_claim)

    p_config = sub.add_parser(
        "config", help=t("help.config._command")
    )
    p_config.add_argument("key", nargs="?", help="user.name / user.email")
    p_config.add_argument("value", nargs="?", help=t("help.config.value"))
    p_config.add_argument("--get", dest="get_key", metavar="KEY", help=t("help.config.get"))
    p_config.add_argument("--unset", dest="unset_key", metavar="KEY", help=t("help.config.unset"))
    p_config.add_argument("--list", dest="list_all", action="store_true", help=t("help.config.list"))
    p_config.add_argument(
        "--from-github", dest="from_github", action="store_true",
        help=t("help.config.from_github"),
    )
    p_config.add_argument("--json", action="store_true")
    _add_lang_arg(p_config)

    p_activity = sub.add_parser(
        "activity",
        help=t("help.activity._command"),
    )
    _add_scope_args(p_activity)
    p_activity.add_argument("--project", help=t("help.common.project_cwd"))
    p_activity.add_argument(
        "--all", action="store_true", dest="all_projects", help=t("help.activity.all")
    )
    p_activity.add_argument(
        "--date", action="append", dest="dates",
        metavar="{today,yesterday,tomorrow,YYYY-MM-DD}",
        help=t("help.activity.date"),
    )
    p_activity.add_argument(
        "--since", help=t("help.activity.since")
    )
    p_activity.add_argument(
        "--until", help=t("help.activity.until")
    )
    p_activity.add_argument("--json", action="store_true", help=t("help.common.json_human_default"))

    p_timeline = sub.add_parser(
        "timeline", help=t("help.timeline._command")
    )
    _add_scope_args(p_timeline)
    p_timeline.add_argument("topic", help=t("help.timeline.topic"))
    p_timeline.add_argument(
        "--format", choices=("markdown", "plain", "json"), default="markdown"
    )

    p_find = sub.add_parser(
        "find",
        help=t("help.find._command"),
    )
    _add_scope_args(p_find)
    p_find.add_argument("--owner", help=t("help.find.owner"))
    p_find.add_argument("--tag", action="append", dest="tags", help=t("help.find.tag"))
    p_find.add_argument("--type", action="append", dest="types", help="plan/bugfix/pending")
    p_find.add_argument(
        "--status", action="append", dest="states", help=t("help.find.status")
    )
    p_find.add_argument(
        "--review-status", action="append", dest="review_statuses",
        help="draft / review / published",
    )
    p_find.add_argument("--project", help=t("help.common.project_filter"))
    p_find.add_argument(
        "--q", dest="q",
        help=t("help.find.q"),
    )
    p_find.add_argument(
        "--target-release",
        help=t("help.find.target_release"),
    )
    p_find.add_argument(
        "--missing-target-release", action="store_true",
        help=t("help.find.missing_target_release"),
    )
    p_find.add_argument("--json", action="store_true")

    p_target_release = sub.add_parser(
        "target-release",
        help=t("help.target_release._command"),
    )
    _add_lang_arg(p_target_release, nested=True)
    p_target_release_sub = p_target_release.add_subparsers(
        dest="target_release_action", required=True
    )
    p_target_release_set = p_target_release_sub.add_parser(
        "set", help=t("help.target_release.set._command")
    )
    p_target_release_set.add_argument("--path", required=True, help=t("help.target_release.set.path"))
    p_target_release_set.add_argument("--to", required=True, help=t("help.target_release.set.to"))
    p_target_release_set.add_argument("--project-dir", help=t("help.target_release.set.project_dir"))
    p_target_release_set.add_argument("--config", help=t("help.common.config_path"))
    p_target_release_set.add_argument("--json", action="store_true")
    _add_lang_arg(p_target_release_set, nested=True)

    p_release = sub.add_parser("release", help=t("help.release._command"))
    _add_lang_arg(p_release, nested=True)
    p_release_sub = p_release.add_subparsers(dest="release_action", required=True)
    p_release_close = p_release_sub.add_parser(
        "close", help=t("help.release.close._command")
    )
    p_release_close.add_argument("tag", help=t("help.release.close.tag"))
    p_release_close.add_argument("--root", action="append", dest="roots", metavar="PATH")
    p_release_close.add_argument("--profile", help=t("help.scope.profile"))
    p_release_close.add_argument("--config", help=t("help.common.config_path"))
    p_release_close.add_argument("--project-dir", help=t("help.release.close.project_dir"))
    p_release_close.add_argument("--project", help=t("help.release.close.project"))
    p_release_close.add_argument("--dry-run", action="store_true")
    p_release_close.add_argument("--json", action="store_true")
    _add_lang_arg(p_release_close, nested=True)

    p_workspace = sub.add_parser(
        "workspace", help=t("help.workspace._command")
    )
    _add_lang_arg(p_workspace, nested=True)
    p_workspace_sub = p_workspace.add_subparsers(
        dest="workspace_action", required=True
    )
    p_workspace_migrate = p_workspace_sub.add_parser(
        "migrate-release-tracking",
        help=t("help.workspace.migrate_release_tracking._command"),
    )
    p_workspace_migrate.add_argument(
        "--root", action="append", dest="roots", metavar="PATH",
        help=t("help.workspace.migrate_release_tracking.root"),
    )
    p_workspace_migrate.add_argument(
        "--config", help=t("help.workspace.migrate_release_tracking.config")
    )
    p_workspace_migrate.add_argument(
        "--exclude", action="append", default=[],
        help=t("help.workspace.migrate_release_tracking.exclude"),
    )
    p_workspace_migrate.add_argument(
        "--default-target", help=t("help.workspace.migrate_release_tracking.default_target"),
    )
    p_workspace_migrate.add_argument(
        "--manifest", help=t("help.workspace.migrate_release_tracking.manifest"),
    )
    p_workspace_migrate.add_argument(
        "--apply-manifest", metavar="PATH",
        help=t("help.workspace.migrate_release_tracking.apply_manifest"),
    )
    p_workspace_migrate.add_argument(
        "--apply", action="store_true",
        help=t("help.workspace.migrate_release_tracking.apply"),
    )
    p_workspace_migrate.add_argument(
        "--review", action="store_true",
        help=t("help.workspace.migrate_release_tracking.review"),
    )
    p_workspace_migrate.add_argument(
        "--auto", action="store_true",
        help=t("help.workspace.migrate_release_tracking.auto"),
    )
    p_workspace_migrate.add_argument(
        "--journal", help=t("help.workspace.migrate_release_tracking.journal"),
    )
    p_workspace_migrate.add_argument("--json", action="store_true")
    _add_lang_arg(p_workspace_migrate, nested=True)

    p_completion = sub.add_parser(
        "completion", help=t("help.completion._command")
    )
    _add_scope_args(p_completion)
    p_completion.add_argument("shell", choices=("bash", "zsh", "pwsh"))

    # ------------------------------------------------------------------
    # C3: OKF 採用 Phase 3 サブコマンド
    # ------------------------------------------------------------------

    p_export = sub.add_parser(
        "export",
        help=t("help.export._command"),
    )
    _add_scope_args(p_export)
    p_export.add_argument("--okf", action="store_true", help=t("help.export.okf"))
    p_export.add_argument(
        "--out", help=t("help.export.out")
    )
    p_export.add_argument("--project", help=t("help.common.project_filter"))
    p_export.add_argument(
        "--include-archive", action="store_true",
        help=t("help.export.include_archive"),
    )
    p_export.add_argument(
        "--okf-version", default="0.2",
        help=t("help.export.okf_version"),
    )
    p_export.add_argument(
        "--okf-profile", help=t("help.export.okf_profile"),
    )
    p_export.add_argument(
        "--okf-profile-sha256", help=t("help.export.okf_profile_sha256"),
    )
    p_export.add_argument("--json", action="store_true")

    p_okf_check = sub.add_parser(
        "okf-check",
        help=t("help.okf_check._command"),
    )
    p_okf_check.add_argument("bundle", help=t("help.okf_check.bundle"))
    p_okf_check.add_argument("--okf-version", default="0.2")
    p_okf_check.add_argument("--okf-profile", help=t("help.okf_check.okf_profile"))
    p_okf_check.add_argument("--okf-profile-sha256", help=t("help.okf_check.okf_profile_sha256"))
    p_okf_check.add_argument("--json", action="store_true")
    _add_lang_arg(p_okf_check)

    p_okf_profiles = sub.add_parser(
        "okf-profiles", help=t("help.okf_profiles._command")
    )
    p_okf_profiles.add_argument("--json", action="store_true")
    _add_lang_arg(p_okf_profiles)

    p_closeout = sub.add_parser(
        "closeout-check",
        help=t("help.closeout_check._command"),
    )
    p_closeout.add_argument(
        "--path", required=True,
        help=t("help.closeout_check.path"),
    )
    p_closeout.add_argument(
        "--to", choices=("watching", "done"), default="watching",
        help=t("help.closeout_check.to"),
    )
    p_closeout.add_argument("--project-dir", help=t("help.closeout_check.project_dir"))
    p_closeout.add_argument("--json", action="store_true", help=t("help.common.json_machine"))
    _add_lang_arg(p_closeout)

    # UX W1: doctor / init / undo
    p_doctor = sub.add_parser(
        "doctor",
        help=t("help.doctor._command"),
    )
    p_doctor.add_argument("--json", action="store_true", help=t("help.common.json_machine"))
    p_doctor.add_argument("--config", help=t("help.scope.config"))
    p_doctor.add_argument("--project-dir", help=t("help.doctor.project_dir"))
    _add_lang_arg(p_doctor)

    p_init = sub.add_parser(
        "init",
        help=t("help.init._command"),
    )
    p_init.add_argument("--yes", "-y", action="store_true", help=t("help.init.yes"))
    p_init.add_argument("--root", help=t("help.init.root"))
    p_init.add_argument("--lang", choices=SUPPORTED_LANGS, default=None, help=t("help.init.lang"))
    p_init.add_argument(
        "--agent", choices=("claude", "codex", "none"), default="claude",
        help=t("help.init.agent"),
    )
    p_init.add_argument("--force", action="store_true", help=t("help.init.force"))
    p_init.add_argument("--config", help=t("help.init.config"))
    p_init.add_argument("--json", action="store_true")

    p_undo = sub.add_parser(
        "undo",
        help=t("help.undo._command"),
    )
    _add_scope_args(p_undo)
    p_undo.add_argument("--json", action="store_true")

    # UX W2: day / intent / fix-conflict
    p_day = sub.add_parser("day", help=t("help.day._command"))
    _add_scope_args(p_day)
    p_day.add_argument("phase", choices=("open", "close"), help="open | close")
    p_day.add_argument("--json", action="store_true")

    p_intent = sub.add_parser(
        "intent",
        help=t("help.intent._command"),
    )
    p_intent.add_argument("text", nargs="+", help=t("help.intent.text"))
    p_intent.add_argument("--json", action="store_true")
    _add_lang_arg(p_intent)

    p_fix_conflict = sub.add_parser(
        "fix-conflict",
        help=t("help.fix_conflict._command"),
    )
    _add_scope_args(p_fix_conflict)
    p_fix_conflict.add_argument(
        "--prefer", choices=("h1", "frontmatter", "both"), default="h1",
        help=t("help.fix_conflict.prefer"),
    )
    # dest は positional の ``paths``（_add_scope_args のスキャンルート）と分ける。
    # 同名にすると positional 側の既定値 [] が append 結果を潰し、--path で絞ったつもりでも
    # 全 conflict が処理対象になっていた（完了済み plan を巻き戻す誤修正の原因）。
    # さらに _build_config が ``paths`` をスキャンルート扱いするため、dest を共有したままだと
    # 指定した md ファイルがスキャンルートに混入する。
    p_fix_conflict.add_argument(
        "--path", action="append", dest="target_paths", metavar="PATH",
        help=t("help.fix_conflict.path"),
    )
    p_fix_conflict.add_argument("--list", action="store_true", help=t("help.fix_conflict.list"))
    p_fix_conflict.add_argument("--dry-run", action="store_true")
    p_fix_conflict.add_argument("--json", action="store_true")

    p_notify = sub.add_parser(
        "notify",
        help=t("help.notify._command"),
    )
    _add_scope_args(p_notify)
    p_notify.add_argument("--dry-run", action="store_true", help=t("help.notify.dry_run"))
    p_notify.add_argument("--json", action="store_true")

    # UX W2 / P39: project enable|disable|list
    p_project = sub.add_parser(
        "project",
        help=t("help.project._command"),
    )
    _add_lang_arg(p_project, nested=True)
    p_project_sub = p_project.add_subparsers(dest="project_cmd")
    p_pl = p_project_sub.add_parser("list", help=t("help.project.list._command"))
    _add_scope_args(p_pl)
    p_pl.add_argument("--json", action="store_true")
    p_pe = p_project_sub.add_parser("enable", help=t("help.project.enable._command"))
    p_pe.add_argument("root", help=t("help.common.project_root_abs"))
    p_pe.add_argument("--json", action="store_true")
    _add_lang_arg(p_pe, nested=True)
    p_pd = p_project_sub.add_parser("disable", help=t("help.project.disable._command"))
    p_pd.add_argument("root", help=t("help.common.project_root_abs"))
    p_pd.add_argument("--json", action="store_true")
    _add_lang_arg(p_pd, nested=True)

    p_review_week = sub.add_parser(
        "review-week",
        help=t("help.review_week._command"),
    )
    _add_scope_args(p_review_week)
    p_review_week.add_argument("--json", action="store_true")

    p_history = sub.add_parser("history", help=t("help.history._command"))
    _add_scope_args(p_history)
    p_history.add_argument("--limit", type=int, default=30)
    p_history.add_argument("--json", action="store_true")

    p_cookbook = sub.add_parser("cookbook", help=t("help.cookbook._command"))
    p_cookbook.add_argument(
        "scenario", nargs="?", default=None,
        help=t("help.cookbook.scenario"),
    )
    p_cookbook.add_argument("--json", action="store_true")
    _add_lang_arg(p_cookbook)

    p_memory = sub.add_parser(
        "memory",
        help=t("help.memory._command"),
    )
    p_memory.add_argument("--path", action="append", dest="paths", help=t("help.memory.path"))
    p_memory.add_argument("--stale-days", type=int, default=90)
    p_memory.add_argument("--json", action="store_true")
    _add_lang_arg(p_memory)

    p_demo = sub.add_parser(
        "demo",
        help=t("help.demo._command"),
    )
    p_demo.add_argument(
        "--dir", dest="demo_dir", default=None,
        help=t("help.demo.dir"),
    )
    p_demo.add_argument("--json", action="store_true")
    _add_lang_arg(p_demo)

    p_ics = sub.add_parser("ics", help=t("help.ics._command"))
    _add_scope_args(p_ics)
    p_ics.add_argument("--out", default="docsweep-due.ics", help=t("help.ics.out"))

    return parser
