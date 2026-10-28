"""CLI command handlers: write."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from ...atomic import write_atomic
from ...config import Config, archive_route_for_project, load_config
from ...engine import apply_action, auto_sweep, doc_for_path
from ..interactive import stdin_is_interactive
from ..parser import _build_config


def _new_release_setup_allowed() -> bool:
    if os.environ.get("CI", "").strip().lower() not in {"", "0", "false", "no", "off"}:
        return False
    return stdin_is_interactive()


def _new_prompt(message: str) -> str:
    try:
        return input(message).strip()
    except (EOFError, KeyboardInterrupt):
        return "q"


def _persist_release_tracking_choice(
    project_dir: Path,
    *,
    mode: str,
    default_target: str | None = None,
    archive_group_by: str = "minor",
    archive_dir: str | None = None,
) -> None:
    """Persist only the first-run release choice while preserving other YAML."""
    from ...i18n import t

    config_path = project_dir / ".docsweep.yaml"
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(t("cli_write.config_root_not_map", path=config_path))
    else:
        raw = {}
    tracking = raw.get("release_tracking")
    tracking = dict(tracking) if isinstance(tracking, dict) else {}
    tracking["mode"] = mode
    if mode == "enabled":
        from ...release import validate_release_label
        from ...workspace_migration import _safe_archive_root

        if not default_target:
            raise ValueError(t("cli_write.enable_needs_default_target"))
        if archive_group_by not in {"patch", "minor", "major"}:
            raise ValueError(t("cli_write.unsupported_group_by", value=archive_group_by))
        if not archive_dir or not _safe_archive_root(archive_dir):
            raise ValueError(t("cli_write.unsafe_archive_dir"))
        tracking.update(
            {
                "default_target": validate_release_label(
                    default_target, field="default_target"
                ),
                "archive_group_by": archive_group_by,
            }
        )
        raw["archive_partition"] = "release"
        raw["archive_dir"] = archive_dir
    raw["release_tracking"] = tracking
    write_atomic(
        config_path,
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
    )


def _first_run_release_setup(
    project_dir: Path,
    cfg: Config,
    explicit_target: str | None,
) -> tuple[str, str | None]:
    """Return (decision, target): enabled / disabled / cancelled."""
    from ...i18n import t

    answer = _new_prompt(t("cli_write.prompt_setup_release")).lower()
    if answer in {"q", "quit", "cancel", "c", ""}:
        return "cancelled", None
    if answer in {"n", "no", "0", "skip", "disabled"}:
        _persist_release_tracking_choice(project_dir, mode="disabled")
        return "disabled", None
    if answer not in {"y", "yes", "1", "enable", "enabled"}:
        raise ValueError(t("cli_write.invalid_setup_answer"))

    from ...release import validate_release_label

    target: str | None = None
    if explicit_target:
        explicit_target = validate_release_label(
            explicit_target, field="target_release"
        )
        value = _new_prompt(
            t("cli_write.prompt_default_target_with_current", target=explicit_target)
        )
        if value.lower() in {"q", "quit", "cancel", "c", ""}:
            if value:
                return "cancelled", None
            target = explicit_target
        else:
            target = validate_release_label(value, field="default_target")
    while not target:
        value = _new_prompt(t("cli_write.prompt_default_target"))
        if value.lower() in {"q", "quit", "cancel", "c", ""}:
            return "cancelled", None
        try:
            target = validate_release_label(value, field="default_target")
        except ValueError as exc:
            print(t("cli_write.invalid_default_target", error=exc), file=sys.stderr)

    group = _new_prompt(
        t(
            "cli_write.prompt_archive_grouping",
            default=cfg.release_tracking.archive_group_by,
        )
    ).lower()
    if group in {"q", "quit", "cancel", "c"}:
        return "cancelled", None
    group = group or cfg.release_tracking.archive_group_by or "minor"
    if group not in {"patch", "minor", "major"}:
        raise ValueError(t("cli_write.invalid_archive_grouping"))
    default_archive = archive_route_for_project(project_dir, cfg).archive_dir
    archive_dir = _new_prompt(t("cli_write.prompt_archive_dir", default=default_archive))
    if archive_dir.lower() in {"q", "quit", "cancel", "c"}:
        return "cancelled", None
    archive_dir = archive_dir or default_archive
    _persist_release_tracking_choice(
        project_dir,
        mode="enabled",
        default_target=target,
        archive_group_by=group,
        archive_dir=archive_dir,
    )
    return "enabled", target


def _rollback_generated_documents(
    created: list,
    *,
    ledger_path: Path | None = None,
    ledger_before: bytes | None = None,
) -> list[str]:
    """Remove only files created by one ``new`` invocation and restore its ledger.

    ``new_doc`` allocates a fresh path (with a suffix on collision), so these
    paths are safe rollback targets.  The ledger snapshot makes a split command
    all-or-nothing even when a later document fails provenance registration.
    """
    errors: list[str] = []
    for doc in reversed(created):
        path = getattr(doc, "path", None)
        if not path or not getattr(doc, "created", False):
            continue
        try:
            if path.is_file():
                path.unlink()
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    if ledger_path is not None:
        try:
            if ledger_before is None:
                if ledger_path.is_file():
                    ledger_path.unlink()
            else:
                write_atomic(ledger_path, ledger_before.decode("utf-8"), encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{ledger_path}: {exc}")
    return errors


def cmd_fix_conflict(args: argparse.Namespace) -> int:
    """conflict 修理（UX W2 / P37）。"""
    from ...fix_conflict import _conflict_rows, fix_conflicts
    from ...i18n import t

    cfg = _build_config(args)
    if getattr(args, "list", False):
        target_paths = getattr(args, "target_paths", None)
        rows, unmatched = _conflict_rows(cfg, target_paths)
        if unmatched:
            print(
                t("cli_write.fix_conflict_unmatched", count=len(unmatched)),
                file=sys.stderr,
            )
        if getattr(args, "json", False):
            print(json.dumps({"conflicts": rows, "unmatched": unmatched}, ensure_ascii=False, indent=2))
        else:
            if not rows:
                print(t("cli_write.no_conflicts"))
            for r in rows:
                print(f"{r.get('state_label')} {r.get('path')} (source={r.get('state_source')})")
        # --path の不一致は JSON の unmatched で機械可読に返す。既存 CLI の「対象 0 件は
        # 成功」規約を維持し、終了コードだけで空結果と誤指定を混同させない。
        return 0
    res = fix_conflicts(
        cfg,
        prefer=getattr(args, "prefer", "h1") or "h1",
        # ``--path`` の dest は positional のスキャンルート（args.paths）と分けてある。
        paths=getattr(args, "target_paths", None),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    else:
        if not res.items:
            print(t("cli_write.no_conflicts_to_fix"))
        for it in res.items:
            mark = "ok" if it.fixed else "ng"
            print(f"[{mark}] {it.path}: {it.detail}")
    return 0 if all(i.fixed for i in res.items) or not res.items else 1


_ARCHIVE_ACTIONS = frozenset({"discard", "promote"})


def _queue_root_routes(routes: list[dict]) -> list[dict]:
    """``engine._archive_routes`` の結果のうち、root が作業 queue 自身と判定されたもの。"""
    return [route for route in routes if route.get("root_is_queue")]


def _scan_queue_root_routes(cfg: Config, *, project: str | None) -> list[dict]:
    """移す前に 1 回スキャンし、sweep の dry-run と同じ判定で queue 自身の root を返す（何も書かない）。"""
    from ... import engine

    result = engine.run_scan(cfg)
    return _queue_root_routes(engine._archive_routes(result, cfg, project=project))


def _queue_root_fields(route: dict, cfg: Config) -> dict[str, str]:
    from ... import engine

    project_root = str(route["project_root"])
    work_dir = engine._root_is_queue(Path(project_root), cfg) or ""
    return {
        "project_root": project_root,
        "work_dir": work_dir,
        "archive": f"{project_root.rstrip('/')}/{route['archive_dir']}",
    }


def _warn_queue_roots(command: str, routes: list[dict], cfg: Config) -> None:
    """dry-run で、本実行なら止まることを標準エラーへ 1 行ずつ出す（--json でも出す）。"""
    from ...i18n import t

    for route in routes:
        print(
            t("cli_write.root_is_queue_dry", command=command, **_queue_root_fields(route, cfg)),
            file=sys.stderr,
        )


def _refuse_queue_roots(
    command: str, routes: list[dict], cfg: Config, *, as_json: bool
) -> int:
    """root が作業 queue 自身のとき、何も移さずに終了コード 2 で止める（逃げ道のオプションは無い）。"""
    from ...i18n import t

    fields = [_queue_root_fields(route, cfg) for route in routes]
    messages = [t("cli_write.root_is_queue_refused", command=command, **item) for item in fields]
    for message in messages:
        print(message, file=sys.stderr)
    if as_json:
        print(json.dumps({
            "moved": [],
            "failed": [],
            "archive_routes": routes,
            "error": {
                "code": "root_is_queue",
                "message": "\n".join(messages),
                "project_roots": [item["project_root"] for item in fields],
            },
        }, ensure_ascii=False, indent=2))
    return 2


def cmd_apply(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    # An explicit single-file action must not scan every configured root first.
    # Keep the lexical path so linked queues retain their owning project.
    doc = doc_for_path(Path(args.path), cfg)
    if doc is None:
        from ...i18n import t

        print(f"{t('target.not_found_in_scan')}: {args.path}", file=sys.stderr)
        return 2
    if (
        args.action in _ARCHIVE_ACTIONS
        and args.action in doc.record.allowed_actions
        and doc.record.docsweep_policy != "never_archive"
    ):
        # archive へ実際に移す action だけ（許されない action・never_archive は従来のエラーを先に出す）。
        # 判定は sweep の dry-run と同じ engine._root_is_queue。
        project_root = doc.record.project_root
        from ... import engine

        if engine._root_is_queue(Path(project_root), cfg) is not None:
            route = {
                "project": doc.record.project,
                "project_root": project_root,
                "archive_dir": archive_route_for_project(Path(project_root), cfg).archive_dir,
                "root_is_queue": True,
            }
            if args.dry_run:
                _warn_queue_roots("apply", [route], cfg)
            else:
                # apply には --json が無く、ほかのエラーと同じく標準エラーだけに出す。
                return _refuse_queue_roots("apply", [route], cfg, as_json=False)
    report: dict = {}
    try:
        entry = apply_action(
            doc,
            args.action,
            cfg,
            to=args.to,
            dry_run=args.dry_run,
            watching_days=getattr(args, "watching_days", None),
            ref_scope=getattr(args, "ref_scope", None),
            report=report,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    payload = entry.to_dict()
    # 参照の書き換えと検証は、repo の範囲を使ったときだけ足す（従来の 1 行 JSON を保つ）。
    if "verify" in report:
        payload.update(report)
    print(json.dumps(payload, ensure_ascii=False))
    verify = report.get("verify")
    if verify and not verify.get("ok", True):
        _print_verify(verify)
        return 1
    return 1 if report.get("ref_failed") and "verify" in report else 0


def _print_moves_summary(moved, cfg, *, action: str, dry_run: bool) -> None:
    """移送/昇格ログの末尾に「合計・状態別・プロジェクト別」を出す。

    フラット出力では俯瞰できないため、走査直後に確認用の集計をまとめて見せる。
    JSON 出力には混ぜない（機械可読を汚さない）。``action`` は ``"sweep"``（移送）/
    ``"promote"``（昇格）。dry-run 時は「予定」の見出しにする。
    """
    if not moved:
        return
    from collections import Counter

    from ...i18n import current_lang, t

    sm = cfg.state_model
    # 集計の状態名は画面に出す文言なので表示言語で出す（文書の言語 cfg.lang ではない）
    lang = current_lang()

    def _label(k: str | None) -> str:
        s = sm.by_key(k) if k else None
        return s.label(lang) if s else (k or t("common.none"))

    by_state = Counter(_label(m.status) for m in moved)
    by_proj = Counter(m.project for m in moved)
    total_key = {
        ("sweep", False): "cli_write.summary_total_sweep",
        ("sweep", True): "cli_write.summary_total_sweep_dry",
        ("promote", False): "cli_write.summary_total_promote",
        ("promote", True): "cli_write.summary_total_promote_dry",
    }[(action, dry_run)]
    print()
    print(t(total_key, count=len(moved), projects=len(by_proj)))
    print(
        t(
            "cli_write.summary_by_state",
            states=" / ".join(f"{k} {v}" for k, v in by_state.most_common()),
        )
    )
    print(t("cli_write.summary_by_project"))
    for proj, n in by_proj.most_common():
        print(t("cli_write.summary_project_row", project=proj, count=n))


def cmd_sweep(args: argparse.Namespace) -> int:
    from ...i18n import t

    cfg = _build_config(args)
    project = getattr(args, "project", None)
    as_json = bool(getattr(args, "json", False))
    if not args.dry_run:
        # 本実行は移す前に止める。auto_sweep はスキャンと移送を 1 回で行うため、
        # dry-run の archive_routes と同じ判定をここで先に通す。
        queue_routes = _scan_queue_root_routes(cfg, project=project)
        if queue_routes:
            return _refuse_queue_roots("sweep", queue_routes, cfg, as_json=as_json)
    moved = auto_sweep(
        cfg, project=project, dry_run=args.dry_run,
        ref_scope=getattr(args, "ref_scope", None),
    )
    if not args.dry_run and cfg.roots:
        from ...aggregate_index import write_index

        write_index(cfg)
    routes = getattr(moved, "routes", [])
    if args.dry_run and as_json:
        # 文字の出力では下の route_warning が標準エラーへ出る。--json でも見えるようにする。
        _warn_queue_roots("sweep", _queue_root_routes(routes), cfg)
    if as_json:
        payload: list | dict = [m.to_dict() for m in moved]
        if moved.failed or routes or moved.ref_updates or moved.ref_failed or moved.held:
            payload = {"moved": payload, "failed": moved.failed, "archive_routes": routes}
            payload.update(_ref_payload(moved))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        moved_key = "cli_write.moved_dry" if args.dry_run else "cli_write.moved"
        if not moved:
            print(t("cli_write.sweep_nothing"))
        for m in moved:
            print(t(moved_key, src=m.src, dst=m.dst))
        _print_ref_updates(moved, dry_run=args.dry_run)
        for route in routes:
            if route.get("warning"):
                print(
                    t("cli_write.route_warning", project=route["project"], warning=route["warning"]),
                    file=sys.stderr,
                )
        for failure in moved.failed:
            print(
                t(
                    "cli_write.failed",
                    path=failure.get("path") or t("cli_write.failed_scan"),
                    error=failure.get("error"),
                ),
                file=sys.stderr,
            )
        _print_moves_summary(moved, cfg, action="sweep", dry_run=args.dry_run)
    return _batch_exit_code(moved)


def _batch_exit_code(moved) -> int:
    """移送の失敗・参照の書き換えの失敗・検証で問題が見つかったときは 1。止めた文書は 0。"""
    verify = getattr(moved, "verify", None)
    if moved.failed or moved.ref_failed or (verify and not verify.get("ok", True)):
        return 1
    return 0


def _ref_payload(moved) -> dict:
    """移送に伴う参照の書き換え（予定）を JSON に足す。失敗は無いときは出さない。

    ``held``（移す前に止めた文書）と ``verify``（移した後の検証）は、repo の範囲を
    使ったときだけ出る。
    """
    data: dict = {"ref_updates": moved.ref_updates}
    if moved.ref_failed:
        data["ref_failed"] = moved.ref_failed
    if getattr(moved, "held", None):
        data["held"] = moved.held
    if getattr(moved, "verify", None) is not None:
        data["verify"] = moved.verify
    return data


def _print_verify(verify: dict) -> None:
    from ...i18n import t

    # dry-run の見込み（preview）は、まだ起きていないことと分かる文言にする。
    stale_key = "cli_write.verify_stale_preview" if verify.get("preview") else "cli_write.verify_stale"
    for item in verify.get("stale_refs", []):
        print(t(stale_key, path=item["path"], ref=item["ref"]), file=sys.stderr)
    for item in verify.get("missing_targets", []):
        print(
            t("cli_write.verify_missing", path=item["path"] or "-", ref=item["ref"]),
            file=sys.stderr,
        )
    for item in verify.get("broken_links", []):
        print(t("cli_write.verify_broken", path=item["path"], link=item["link"]), file=sys.stderr)


def _print_ref_updates(moved, *, dry_run: bool) -> None:
    from ...i18n import t

    for item in getattr(moved, "held", []):
        print(
            t(
                "cli_write.held",
                path=item["path"],
                message=item["message"],
                referrers=", ".join(item["referrers"]) or "-",
            )
        )
    verify = getattr(moved, "verify", None)
    if verify is not None:
        if verify.get("ok", True):
            print(t("cli_write.verify_ok_preview" if verify.get("preview") else "cli_write.verify_ok"))
        else:
            _print_verify(verify)

    body_key = "cli_write.ref_body_dry" if dry_run else "cli_write.ref_body"
    field_key = "cli_write.ref_field_dry" if dry_run else "cli_write.ref_field"
    for update in moved.ref_updates:
        if update["field"] == "body":
            print(t(body_key, path=update["path"], count=update["count"]))
        else:
            print(
                t(
                    field_key,
                    path=update["path"],
                    field=update["field"],
                    before=update["before"],
                    after=update["after"],
                )
            )
    for failure in moved.ref_failed:
        print(
            t(
                "cli_write.ref_failed",
                path=failure.get("path") or t("cli_write.failed_project"),
                error=failure.get("error"),
            ),
            file=sys.stderr,
        )


def cmd_promote(args: argparse.Namespace) -> int:
    from ...engine import promote_state

    from ...bulk_confirm import BulkConfirmRequired, phrase_for
    from ...bulk_confirm import require as bulk_require
    from ...i18n import t

    cfg = _build_config(args)
    due_expired_only = getattr(args, "due_expired", False)
    # sweep の dry-run と同じ判定。本実行は下見と --yes の確認より前に止める。
    queue_routes = _scan_queue_root_routes(cfg, project=args.project)
    if queue_routes:
        if not args.dry_run:
            return _refuse_queue_roots(
                "promote", queue_routes, cfg, as_json=bool(getattr(args, "json", False))
            )
        _warn_queue_roots("promote", queue_routes, cfg)
    if not args.dry_run:
        # 実行前に同じ条件で下見して件数を数える。しきい値以上なら --yes を要求する
        # （UX W4 / P59）。非対話が不変条件なのでプロンプトは出さない。
        try:
            preview = promote_state(
                cfg, from_state=args.state, to_state=args.to,
                project=args.project, dry_run=True,
                due_expired_only=due_expired_only,
                ref_scope=getattr(args, "ref_scope", None),
            )
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        supplied = phrase_for("promote") if getattr(args, "yes", False) else None
        try:
            bulk_require("promote", len(preview), cfg.bulk_confirm_threshold, supplied)
        except BulkConfirmRequired as exc:
            preview_cmd = "docsweep promote --due-expired --dry-run" if due_expired_only else "docsweep promote --dry-run"
            print(
                t(
                    "cli_write.promote_needs_yes",
                    count=exc.count,
                    threshold=exc.threshold,
                    preview=preview_cmd,
                ),
                file=sys.stderr,
            )
            return 2
    try:
        moved = promote_state(
            cfg, from_state=args.state, to_state=args.to, project=args.project,
            dry_run=args.dry_run, due_expired_only=due_expired_only,
            ref_scope=getattr(args, "ref_scope", None),
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        payload: list | dict = [m.to_dict() for m in moved]
        if moved.failed or moved.ref_updates or moved.ref_failed or moved.held or moved.verify:
            payload = {"moved": payload, "failed": moved.failed}
            payload.update(_ref_payload(moved))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not moved:
            # 失敗が 1 件でもあるときに「対象なし」と言ってはいけない。対象はあったが
            # 全部落ちた状態と、そもそも対象が無い状態は、利用者にとって意味が違う。
            if moved.failed:
                print(t("cli_write.promote_all_failed", count=len(moved.failed)))
            elif due_expired_only:
                print(t("cli_write.promote_nothing_due", state=args.state))
            else:
                print(t("cli_write.promote_nothing", state=args.state))
        for m in moved:
            print(t("cli_write.promoted", src=m.src, dst=m.dst))
        _print_ref_updates(moved, dry_run=args.dry_run)
        for failure in moved.failed:
            print(
                t(
                    "cli_write.failed",
                    path=failure.get("path") or t("cli_write.failed_scan"),
                    error=failure.get("error"),
                ),
                file=sys.stderr,
            )
        _print_moves_summary(moved, cfg, action="promote", dry_run=args.dry_run)
    return _batch_exit_code(moved)


def cmd_capture(args: argparse.Namespace) -> int:
    """会話履歴から plan / bugfix / pending 草案を抽出 (heuristic / LLM)。"""
    from ...capture import extract_drafts, save_drafts
    from ...i18n import t
    from ...work_queue import resolve_work_target

    cfg = _build_config(args)

    # 入力ソース解決
    source = getattr(args, "source", "clipboard")
    if source == "clipboard":
        text = _read_clipboard()
    elif source == "file":
        fpath = getattr(args, "file", None)
        if not fpath:
            print(t("cli_write.capture_file_required"), file=sys.stderr)
            return 2
        text = Path(fpath).read_text(encoding="utf-8", errors="replace")
    elif source == "-":
        text = sys.stdin.read()
    else:
        text = ""

    if not text.strip():
        print(t("cli_write.capture_empty_input"), file=sys.stderr)
        return 2

    try:
        drafts = extract_drafts(
            text,
            config=cfg,
            project=getattr(args, "project", None),
            max_drafts=int(getattr(args, "max", 5)),
            use_llm=bool(getattr(args, "llm", False)),
            allow_sensitive=bool(getattr(args, "allow_sensitive", False)),
        )
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not drafts:
        if getattr(args, "json", False):
            print(json.dumps({"drafts": [], "saved": []}, ensure_ascii=False, indent=2))
        else:
            print(t("cli_write.capture_no_drafts"))
        return 0

    saved: list[Path] = []
    if getattr(args, "save_all", False):
        explicit_dir = Path(args.out_dir) if getattr(args, "out_dir", None) else None
        try:
            project_root, out_dir = resolve_work_target(
                cfg,
                project=getattr(args, "project", None),
                explicit_dir=explicit_dir,
            )
            saved = save_drafts(
                drafts,
                config=cfg,
                target_dir=out_dir,
                project_dir=project_root,
                allow_sensitive=bool(getattr(args, "allow_sensitive", False)),
            )
        except PermissionError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    if getattr(args, "json", False):
        print(json.dumps({
            "drafts": [d.to_dict() for d in drafts],
            "saved": [str(p) for p in saved],
        }, ensure_ascii=False, indent=2))
        return 0

    print(t("cli_write.capture_drafts", count=len(drafts)))
    for d in drafts:
        print(f"  [{d.id}] {d.kind:7s} {d.suggested_filename}")
        print(f"          {d.title}")
    if saved:
        print(t("cli_write.capture_saved", count=len(saved)))
        for p in saved:
            print(f"  {p}")
    elif not getattr(args, "save_all", False):
        print(t("cli_write.capture_save_hint"))
    return 0


def _read_clipboard() -> str:
    """OS クリップボードから text を取得。失敗時は空文字。"""
    import codecs

    def _decode_clipboard(raw: bytes, *, windows: bool = False) -> str:
        if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            return raw.decode("utf-16")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            if windows:
                # Old Windows PowerShell can still ignore OutputEncoding when
                # stdout is redirected.  cp932 is a lossless fallback for the
                # ja-JP console; undecodable bytes remain an explicit failure.
                return raw.decode("cp932")
            raise

    try:
        import subprocess
        if sys.platform == "win32":
            r = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    (
                        "$utf8 = New-Object System.Text.UTF8Encoding($false); "
                        "[Console]::OutputEncoding = $utf8; "
                        "Get-Clipboard -Raw"
                    ),
                ],
                capture_output=True,
                text=False,
                timeout=3,
            )
            if r.returncode == 0:
                return _decode_clipboard(r.stdout, windows=True)
        elif sys.platform == "darwin":
            r = subprocess.run(["pbpaste"], capture_output=True, text=False, timeout=3)
            if r.returncode == 0:
                return _decode_clipboard(r.stdout)
        else:
            for cmd in (["xclip", "-selection", "clipboard", "-o"], ["xsel", "-b"], ["wl-paste"]):
                try:
                    r = subprocess.run(cmd, capture_output=True, text=False, timeout=3)
                    if r.returncode == 0:
                        return _decode_clipboard(r.stdout)
                except FileNotFoundError:
                    continue
    except (OSError, UnicodeError, subprocess.SubprocessError):
        pass
    return ""


def _resolve_out_dir(cfg) -> Path:
    """後方互換用の capture 保存先 helper（実際の境界検査は service 層）。"""
    from ...work_queue import resolve_work_target

    _root, target = resolve_work_target(cfg)
    return target


def cmd_auto_triage(args: argparse.Namespace) -> int:
    """状態遷移提案 / 適用（C5）。"""
    from ...auto_triage import apply_suggestions, suggest_transitions
    from ...i18n import t

    cfg = _build_config(args)
    if getattr(args, "suggest", False):
        result = suggest_transitions(cfg, target=getattr(args, "file", None))
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    apply_arg = getattr(args, "apply", None)
    if apply_arg:
        try:
            decisions = json.loads(Path(apply_arg).read_text(encoding="utf-8"))
        except FileNotFoundError:
            print(t("cli_write.auto_triage_json_missing", path=apply_arg), file=sys.stderr)
            return 2
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(
                t("cli_write.auto_triage_json_invalid", path=apply_arg, error=e),
                file=sys.stderr,
            )
            return 2
        if isinstance(decisions, dict):
            decisions = decisions.get("decisions") or decisions.get("suggestions") or []
        apply_result = apply_suggestions(cfg, decisions, dry_run=getattr(args, "dry_run", False))
        print(json.dumps(apply_result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    return 2


def cmd_new(args: argparse.Namespace) -> int:
    from ...i18n import t
    from ...provenance import AIMetadata, initialize_document
    from ...provenance_hint import warn_if_unresolved
    from ...similar_guard import find_similar_open
    from ...templates_gen import new_doc, new_split_plans
    from ...work_queue import find_project_dir

    if getattr(args, "project_dir", None):
        project_dir = Path(args.project_dir)
    else:
        project_dir = find_project_dir(cwd=Path.cwd())
    # ``.docsweep.yaml`` の ``due:`` ブロックから default_offset_days を読む。
    # --no-due 指定時は空 dict を渡してオフセット計算自体を無効化する（嘘の日付防止）。
    cfg = load_config(
        project_dir=project_dir,
        global_path=Path(args.config) if getattr(args, "config", None) else None,
    )
    target_release = getattr(args, "target_release", None)
    if cfg.release_tracking.mode is None:
        if _new_release_setup_allowed():
            try:
                decision, selected_target = _first_run_release_setup(
                    project_dir,
                    cfg,
                    target_release,
                )
            except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
                print(t("cli_write.release_setup_aborted", error=exc), file=sys.stderr)
                return 2
            if decision == "cancelled":
                print(t("cli_write.release_setup_cancelled"), file=sys.stderr)
                return 2
            if selected_target is not None and target_release is None:
                target_release = selected_target
            cfg = load_config(
                project_dir=project_dir,
                global_path=(
                    Path(args.config) if getattr(args, "config", None) else None
                ),
            )
        else:
            print(t("cli_write.release_not_configured"), file=sys.stderr)
    if target_release is not None and cfg.release_tracking.mode == "disabled":
        print(t("cli_write.target_release_disabled"), file=sys.stderr)
        return 2
    if getattr(args, "work_dir", None):
        cfg.work_dir = str(args.work_dir)
        cfg.work_dir_explicit = True
    if getattr(args, "work_policy", None):
        cfg.work_policy = str(args.work_policy)
        cfg.work_policy_explicit = True
    if getattr(args, "secret_policy", None):
        cfg.secret_policy = str(args.secret_policy)
    # 類似ガード（現役 open）
    try:
        sim = find_similar_open(cfg, topic=args.topic)
        if sim:
            print(t("cli_write.similar_docs"), file=sys.stderr)
            for s in sim[:3]:
                print(f"  - {s.get('state_label')} {s.get('path')}", file=sys.stderr)
    except Exception:
        pass
    offsets: dict[str, int] = {} if getattr(args, "no_due", False) else cfg.due_default_offset_days
    delegate = bool(getattr(args, "delegate", False))
    if delegate and args.type != "plan":
        print(t("cli_write.delegate_plan_only"), file=sys.stderr)
        delegate = False
    metadata = AIMetadata.resolve(
        actor_default=cfg.provenance_actor_key,
        agent=getattr(args, "ai_agent", None),
        runtime=getattr(args, "ai_runtime", None),
        provider=getattr(args, "ai_provider", None),
        model_id=getattr(args, "ai_model_id", None),
        model_display=getattr(args, "ai_model_display", None),
        reasoning_profile=getattr(args, "ai_reasoning", None),
        model_source=getattr(args, "ai_model_source", None),
        actor_key=getattr(args, "actor_key", None),
        session_log=getattr(args, "ai_session_log", None),
    )
    warn_if_unresolved(metadata, config=cfg, command="new")

    def register_provenance(path: Path) -> dict | None:
        if not cfg.provenance_enabled and cfg.provenance_manager != "repo":
            return None
        return initialize_document(
            path,
            project_dir=project_dir,
            config=cfg,
            metadata=metadata,
        )

    provenance_active = bool(cfg.provenance_enabled or cfg.provenance_manager == "repo")

    def ledger_snapshot() -> bytes | None:
        if not provenance_active:
            return None
        if not cfg.provenance_ledger.is_file():
            return None
        return cfg.provenance_ledger.read_bytes()

    split_n = int(getattr(args, "split", 0) or 0)
    if split_n > 0:
        if args.type != "plan":
            print(t("cli_write.split_plan_only"), file=sys.stderr)
            return 2
        try:
            created = new_split_plans(
                args.topic,
                n=split_n,
                project_dir=project_dir,
                title=args.title,
                due=getattr(args, "due", None),
                offset_days=offsets,
                config=cfg,
                allow_sensitive=bool(getattr(args, "allow_sensitive", False)),
                delegate=delegate,
                child_titles=[
                    part.strip()
                    for part in (getattr(args, "titles", None) or "").split(",")
                    if part.strip()
                ] or None,
                target_release=target_release,
            )
        except (OSError, ValueError) as exc:
            print(t("cli_write.save_aborted", error=exc), file=sys.stderr)
            return 2
        try:
            before = ledger_snapshot()
        except OSError as exc:
            rollback_errors = _rollback_generated_documents(created)
            print(t("cli_write.ledger_read_failed", error=exc), file=sys.stderr)
            for error in rollback_errors:
                print(t("cli_write.rollback_failed", error=error), file=sys.stderr)
            return 2
        try:
            provenance_results = [register_provenance(doc.path) for doc in created]
        except Exception as exc:  # noqa: BLE001 - new must rollback every registration failure
            rollback_errors = _rollback_generated_documents(
                created,
                ledger_path=cfg.provenance_ledger if provenance_active else None,
                ledger_before=before,
            )
            print(t("cli_write.provenance_register_failed", error=exc), file=sys.stderr)
            for error in rollback_errors:
                print(t("cli_write.rollback_failed", error=error), file=sys.stderr)
            return 2
        for d in created:
            if d.due:
                print(t("cli_write.created_due", path=d.path, due=d.due))
            else:
                print(t("cli_write.created", path=d.path))
        if any(result and result.get("status") == "delegated" for result in provenance_results):
            print(t("cli_write.provenance_delegated_repo"))
        return 0
    try:
        doc = new_doc(
            args.type, args.topic,
            project_dir=project_dir, title=args.title,
            due=getattr(args, "due", None),
            offset_days=offsets,
            config=cfg,
            allow_sensitive=bool(getattr(args, "allow_sensitive", False)),
            delegate=delegate,
            target_release=target_release,
        )
    except (OSError, ValueError) as exc:
        print(t("cli_write.save_aborted", error=exc), file=sys.stderr)
        return 2
    try:
        before = ledger_snapshot()
    except OSError as exc:
        rollback_errors = _rollback_generated_documents([doc])
        print(t("cli_write.ledger_read_failed", error=exc), file=sys.stderr)
        for error in rollback_errors:
            print(t("cli_write.rollback_failed", error=error), file=sys.stderr)
        return 2
    try:
        provenance_result = register_provenance(doc.path)
    except Exception as exc:  # noqa: BLE001 - new must rollback every registration failure
        rollback_errors = _rollback_generated_documents(
            [doc],
            ledger_path=cfg.provenance_ledger if provenance_active else None,
            ledger_before=before,
        )
        print(t("cli_write.provenance_register_failed", error=exc), file=sys.stderr)
        for error in rollback_errors:
            print(t("cli_write.rollback_failed", error=error), file=sys.stderr)
        return 2
    if doc.due:
        print(t("cli_write.created_due", path=doc.path, due=doc.due))
    else:
        print(t("cli_write.created", path=doc.path))
    if provenance_result and provenance_result.get("status") == "delegated":
        skill = provenance_result.get("delegate_skill") or t("cli_write.repo_skill")
        print(t("cli_write.provenance_delegated", skill=skill))
    return 0


def cmd_target_release_set(args: argparse.Namespace) -> int:
    """既存 MD の ``target_release`` だけを更新する。設定ファイルは変更しない。"""
    import os

    from ...i18n import t
    from ...release import validate_release_label
    from ...services.frontmatter import update_frontmatter_field
    from ...work_queue import find_project_dir

    raw_path = str(args.path)
    if any(part == ".." for part in Path(raw_path).parts):
        print(t("cli_write.target_release_dotdot"), file=sys.stderr)
        return 2
    lexical_path = Path(raw_path)
    if not lexical_path.is_absolute():
        base = (
            Path(args.project_dir)
            if getattr(args, "project_dir", None)
            else Path.cwd()
        )
        lexical_path = base / lexical_path
    lexical_path = Path(os.path.abspath(os.path.normpath(os.fspath(lexical_path))))
    project_dir = (
        Path(args.project_dir).resolve()
        if getattr(args, "project_dir", None)
        else find_project_dir(cwd=lexical_path.parent)
    )
    cfg = load_config(
        project_dir=project_dir,
        explicit_roots=[str(project_dir)],
        global_path=Path(args.config) if getattr(args, "config", None) else None,
    )
    if cfg.release_tracking.mode == "disabled":
        print(t("cli_write.target_release_tracking_disabled"), file=sys.stderr)
        return 2
    try:
        target = validate_release_label(args.to)
        # This also validates the type/filename/project scope while retaining
        # the lexical path needed for an explicitly configured junction queue.
        doc = doc_for_path(lexical_path, cfg)
        if doc is None:
            raise ValueError(t("cli_write.target_md_not_found"))
        result = update_frontmatter_field(
            lexical_path,
            "target_release",
            target,
            expected_mtime=doc.record.mtime,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"target-release: {exc}", file=sys.stderr)
        return 2
    payload = {
        **result.to_dict(),
        "release_tracking": cfg.release_tracking.mode,
        "project": project_dir.resolve().as_posix(),
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(t("cli_write.target_release_set", path=result.path, target=target))
    return 0


def cmd_migrate_frontmatter(args: argparse.Namespace) -> int:
    """既存 md に OKF frontmatter を非破壊的に挿入する。"""
    from ...i18n import t
    from ...migrate import apply_migration, plan_migration

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
        print(t("cli_write.migrate_nothing"))
        return 0
    summary_key = "cli_write.migrate_summary_apply" if apply else "cli_write.migrate_summary_plan"
    print(t(summary_key, planned=len(result.planned), skipped=len(result.skipped)))
    for p in result.planned:
        marker = (
            t("cli_write.marker_applied")
            if (apply and p.path in result.applied)
            else t("cli_write.marker_planned")
        )
        mode_note = t("cli_write.migrate_note_upgrade") if p.mode == "upgrade" else ""
        legacy_note = t("cli_write.migrate_note_legacy") if p.legacy_status_migration else ""
        print(
            f"  {marker} {p.doc_type:<8} docsweep_state={p.status:<11} "
            f"{p.path}{mode_note}{legacy_note}"
        )
        if not apply and p.diff:
            print(p.diff, end="" if p.diff.endswith("\n") else "\n")
    for p in result.skipped:
        print(f"  {t('cli_write.marker_skipped')} {p.path}  ({p.skipped_reason})")
    return 0


def cmd_fix_related(args: argparse.Namespace) -> int:
    """片側参照 related: [B] を B 側にも追記して対称化する。"""
    from ...i18n import t
    from ...related import apply_fix_related, plan_fix_related

    cfg = _build_config(args)
    if getattr(args, "apply", False):
        result = apply_fix_related(cfg)
    else:
        result = plan_fix_related(cfg)
    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if not result.fixes:
        if not result.failed:
            print(t("cli_write.fix_related_nothing"))
            return 0
    summary_key = (
        "cli_write.fix_related_summary_apply"
        if getattr(args, "apply", False)
        else "cli_write.fix_related_summary_plan"
    )
    print(t(summary_key, count=len(result.fixes)))
    for fix in result.fixes:
        marker = (
            t("cli_write.marker_applied")
            if fix.path in result.applied
            else t("cli_write.marker_planned")
        )
        print(f"  {marker} {fix.path}  + related: [{', '.join(fix.additions)}]")
    for failure in result.failed:
        print(
            t("cli_write.fix_related_failed", path=failure.get("path"), error=failure.get("error")),
            file=sys.stderr,
        )
    return 1 if result.failed else 0


def cmd_claim(args: argparse.Namespace) -> int:
    """frontmatter の owner を現ユーザーで上書き / unclaim。"""
    from ...claim import claim
    from ...i18n import t
    from ...services.frontmatter import FrontmatterValidationError

    path = Path(args.file)
    try:
        result = claim(path, unclaim=getattr(args, "unclaim", False))
    except FileNotFoundError:
        print(t("cli_write.claim_file_not_found", path=args.file), file=sys.stderr)
        return 2
    except OSError as exc:
        print(t("cli_write.claim_write_failed", error=exc), file=sys.stderr)
        return 2
    except FrontmatterValidationError as e:
        print(t("cli_write.claim_frontmatter_failed", error=e), file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if getattr(args, "unclaim", False):
        print(t("cli_write.unclaimed", path=result.path))
    else:
        print(
            t(
                "cli_write.claimed",
                owner=result.owner,
                claimed_at=result.claimed_at,
                path=result.path,
            )
        )
    return 0
