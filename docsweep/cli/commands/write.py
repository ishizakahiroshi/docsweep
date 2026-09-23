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
from ...engine import apply_action, auto_sweep, doc_for_path, run_scan
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
    config_path = project_dir / ".docsweep.yaml"
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"project config のルートがmapではありません: {config_path}")
    else:
        raw = {}
    tracking = raw.get("release_tracking")
    tracking = dict(tracking) if isinstance(tracking, dict) else {}
    tracking["mode"] = mode
    if mode == "enabled":
        from ...release import validate_release_label
        from ...workspace_migration import _safe_archive_root

        if not default_target:
            raise ValueError("release trackingをenableするにはdefault targetが必要です")
        if archive_group_by not in {"patch", "minor", "major"}:
            raise ValueError(f"archive_group_byが未対応です: {archive_group_by}")
        if not archive_dir or not _safe_archive_root(archive_dir):
            raise ValueError("archive_dirは安全なプロジェクト相対パスで指定してください")
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
    answer = _new_prompt(
        "release trackingが未設定です。設定しますか? "
        "[y=enable / n=以後skip / q=cancel]: "
    ).lower()
    if answer in {"q", "quit", "cancel", "c", ""}:
        return "cancelled", None
    if answer in {"n", "no", "0", "skip", "disabled"}:
        _persist_release_tracking_choice(project_dir, mode="disabled")
        return "disabled", None
    if answer not in {"y", "yes", "1", "enable", "enabled"}:
        raise ValueError("y（enable）/ n（以後skip）/ q（cancel）を指定してください")

    from ...release import validate_release_label

    target: str | None = None
    if explicit_target:
        explicit_target = validate_release_label(
            explicit_target, field="target_release"
        )
        value = _new_prompt(
            "今後のdefault target_release "
            f"（空欄で今回の {explicit_target}、qでcancel）: "
        )
        if value.lower() in {"q", "quit", "cancel", "c", ""}:
            if value:
                return "cancelled", None
            target = explicit_target
        else:
            target = validate_release_label(value, field="default_target")
    while not target:
        value = _new_prompt("default target_release（必須、qでcancel）: ")
        if value.lower() in {"q", "quit", "cancel", "c", ""}:
            return "cancelled", None
        try:
            target = validate_release_label(value, field="default_target")
        except ValueError as exc:
            print(f"default target_releaseが不正です: {exc}", file=sys.stderr)

    group = _new_prompt(
        f"archive grouping [patch/minor/major]（既定 {cfg.release_tracking.archive_group_by}）: "
    ).lower()
    if group in {"q", "quit", "cancel", "c"}:
        return "cancelled", None
    group = group or cfg.release_tracking.archive_group_by or "minor"
    if group not in {"patch", "minor", "major"}:
        raise ValueError("archive groupingはpatch/minor/majorのいずれかです")
    default_archive = archive_route_for_project(project_dir, cfg).archive_dir
    archive_dir = _new_prompt(
        f"archive_dir（既定 {default_archive}、qでcancel）: "
    )
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

    cfg = _build_config(args)
    if getattr(args, "list", False):
        target_paths = getattr(args, "target_paths", None)
        rows, unmatched = _conflict_rows(cfg, target_paths)
        if unmatched:
            print(
                f"warning: --path の指定 {len(unmatched)} 件は conflict 一覧に一致しませんでした",
                file=sys.stderr,
            )
        if getattr(args, "json", False):
            print(json.dumps({"conflicts": rows, "unmatched": unmatched}, ensure_ascii=False, indent=2))
        else:
            if not rows:
                print("conflict なし")
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
            print("修理対象の conflict なし")
        for it in res.items:
            mark = "ok" if it.fixed else "ng"
            print(f"[{mark}] {it.path}: {it.detail}")
    return 0 if all(i.fixed for i in res.items) or not res.items else 1


def cmd_apply(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    result = run_scan(cfg)
    target = Path(args.path).resolve().as_posix()
    doc = next((d for d in result.docs if d.record.path == target), None)
    if doc is None:
        # `docs/local` is commonly gitignored.  An explicitly named write target
        # is still valid and must not be reported as a scan-scope success/empty.
        doc = doc_for_path(Path(args.path), cfg)
    if doc is None:
        print(f"対象が見つかりません（スキャン範囲外?）: {args.path}", file=sys.stderr)
        return 2
    try:
        entry = apply_action(
            doc,
            args.action,
            cfg,
            to=args.to,
            dry_run=args.dry_run,
            watching_days=getattr(args, "watching_days", None),
        )
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
        from ...aggregate_index import write_index

        write_index(cfg)
    routes = getattr(moved, "routes", [])
    if getattr(args, "json", False):
        payload: list | dict = [m.to_dict() for m in moved]
        if moved.failed or routes:
            payload = {"moved": payload, "failed": moved.failed, "archive_routes": routes}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        verb = "移送予定" if args.dry_run else "移送"
        if not moved:
            print("自動移送対象なし（done/discarded のラベル確定ファイルが無い）")
        for m in moved:
            print(f"{verb}: {m.src} -> {m.dst}")
        for route in routes:
            if route.get("warning"):
                print(f"warning: {route['project']}: {route['warning']}", file=sys.stderr)
        for failure in moved.failed:
            print(f"失敗: {failure.get('path') or '(scan)'}: {failure.get('error')}", file=sys.stderr)
        _print_moves_summary(moved, cfg, action="移送", dry_run=args.dry_run)
    return 1 if moved.failed else 0


def cmd_promote(args: argparse.Namespace) -> int:
    from ...engine import promote_state

    from ...bulk_confirm import BulkConfirmRequired, phrase_for
    from ...bulk_confirm import require as bulk_require

    cfg = _build_config(args)
    due_expired_only = getattr(args, "due_expired", False)
    if not args.dry_run:
        # 実行前に同じ条件で下見して件数を数える。しきい値以上なら --yes を要求する
        # （UX W4 / P59）。非対話が不変条件なのでプロンプトは出さない。
        try:
            preview = promote_state(
                cfg, from_state=args.state, to_state=args.to,
                project=args.project, dry_run=True,
                due_expired_only=due_expired_only,
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
                f"promote: {exc.count} 件はしきい値 {exc.threshold} 件以上です。"
                "内容を確認して --yes を付けて再実行してください"
                f"（下見: {preview_cmd}）",
                file=sys.stderr,
            )
            return 2
    try:
        moved = promote_state(
            cfg, from_state=args.state, to_state=args.to, project=args.project,
            dry_run=args.dry_run, due_expired_only=due_expired_only,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        payload: list | dict = [m.to_dict() for m in moved]
        if moved.failed:
            payload = {"moved": payload, "failed": moved.failed}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not moved:
            # 失敗が 1 件でもあるときに「対象なし」と言ってはいけない。対象はあったが
            # 全部落ちた状態と、そもそも対象が無い状態は、利用者にとって意味が違う。
            if moved.failed:
                print(f"昇格できたファイルなし（対象 {len(moved.failed)} 件がすべて失敗。詳細は下記）")
            elif due_expired_only:
                print(f"昇格対象なし（{args.state} の due 到来ファイルが無い）")
            else:
                print(f"昇格対象なし（{args.state} のファイルが無い）")
        for m in moved:
            print(f"昇格→archive: {m.src} -> {m.dst}")
        for failure in moved.failed:
            print(f"失敗: {failure.get('path') or '(scan)'}: {failure.get('error')}", file=sys.stderr)
        _print_moves_summary(moved, cfg, action="昇格", dry_run=args.dry_run)
    return 1 if moved.failed else 0


def cmd_capture(args: argparse.Namespace) -> int:
    """会話履歴から plan / bugfix / pending 草案を抽出 (heuristic / LLM)。"""
    from ...capture import extract_drafts, save_drafts
    from ...work_queue import resolve_work_target

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
            print("草案候補は見つかりませんでした (heuristic マーカー未検出)")
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
            print(f"apply 対象の JSON が見つかりません: {apply_arg}", file=sys.stderr)
            return 2
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"apply 対象の JSON を読み込めません: {apply_arg}: {e}", file=sys.stderr)
            return 2
        if isinstance(decisions, dict):
            decisions = decisions.get("decisions") or decisions.get("suggestions") or []
        apply_result = apply_suggestions(cfg, decisions, dry_run=getattr(args, "dry_run", False))
        print(json.dumps(apply_result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    return 2


def cmd_new(args: argparse.Namespace) -> int:
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
                print(f"release trackingの初回設定を中止しました: {exc}", file=sys.stderr)
                return 2
            if decision == "cancelled":
                print("release trackingの初回設定をキャンセルしました", file=sys.stderr)
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
            print(
                "warning: release trackingが未設定です。非対話実行では確認せず、"
                "従来形式で生成します。workspace migrate-release-tracking --review "
                "またはprojectの.docsweep.yamlで設定してください",
                file=sys.stderr,
            )
    if target_release is not None and cfg.release_tracking.mode == "disabled":
        print(
            "release tracking が disabled のため --target-release は指定できません",
            file=sys.stderr,
        )
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
            print("類似の現役ドキュメントがあります（重複防止ヒント）:", file=sys.stderr)
            for s in sim[:3]:
                print(f"  - {s.get('state_label')} {s.get('path')}", file=sys.stderr)
    except Exception:
        pass
    offsets: dict[str, int] = {} if getattr(args, "no_due", False) else cfg.due_default_offset_days
    delegate = bool(getattr(args, "delegate", False))
    if delegate and args.type != "plan":
        print("warning: --delegate は plan のみ対応のため無視します", file=sys.stderr)
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
            print("--split は plan のみ対応です", file=sys.stderr)
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
            print(f"保存を中止しました: {exc}", file=sys.stderr)
            return 2
        try:
            before = ledger_snapshot()
        except OSError as exc:
            rollback_errors = _rollback_generated_documents(created)
            print(f"provenance 台帳の事前読み取りに失敗しました。生成物をロールバックしました: {exc}", file=sys.stderr)
            for error in rollback_errors:
                print(f"ロールバック失敗: {error}", file=sys.stderr)
            return 2
        try:
            provenance_results = [register_provenance(doc.path) for doc in created]
        except Exception as exc:  # noqa: BLE001 - new must rollback every registration failure
            rollback_errors = _rollback_generated_documents(
                created,
                ledger_path=cfg.provenance_ledger if provenance_active else None,
                ledger_before=before,
            )
            print(f"provenance 登録に失敗しました。生成物をロールバックしました: {exc}", file=sys.stderr)
            for error in rollback_errors:
                print(f"ロールバック失敗: {error}", file=sys.stderr)
            return 2
        for d in created:
            print(f"生成しました: {d.path}" + (f" (due={d.due})" if d.due else ""))
        if any(result and result.get("status") == "delegated" for result in provenance_results):
            print("provenance: repo固有skillへ委譲しました（汎用台帳は未変更）")
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
        print(f"保存を中止しました: {exc}", file=sys.stderr)
        return 2
    try:
        before = ledger_snapshot()
    except OSError as exc:
        rollback_errors = _rollback_generated_documents([doc])
        print(f"provenance 台帳の事前読み取りに失敗しました。生成物をロールバックしました: {exc}", file=sys.stderr)
        for error in rollback_errors:
            print(f"ロールバック失敗: {error}", file=sys.stderr)
        return 2
    try:
        provenance_result = register_provenance(doc.path)
    except Exception as exc:  # noqa: BLE001 - new must rollback every registration failure
        rollback_errors = _rollback_generated_documents(
            [doc],
            ledger_path=cfg.provenance_ledger if provenance_active else None,
            ledger_before=before,
        )
        print(f"provenance 登録に失敗しました。生成物をロールバックしました: {exc}", file=sys.stderr)
        for error in rollback_errors:
            print(f"ロールバック失敗: {error}", file=sys.stderr)
        return 2
    if doc.due:
        print(f"生成しました: {doc.path} (due={doc.due})")
    else:
        print(f"生成しました: {doc.path}")
    if provenance_result and provenance_result.get("status") == "delegated":
        skill = provenance_result.get("delegate_skill") or "repo固有skill"
        print(f"provenance: {skill} へ委譲しました（汎用台帳は未変更）")
    return 0


def cmd_target_release_set(args: argparse.Namespace) -> int:
    """既存 MD の ``target_release`` だけを更新する。設定ファイルは変更しない。"""
    import os

    from ...release import validate_release_label
    from ...services.frontmatter import update_frontmatter_field
    from ...work_queue import find_project_dir

    raw_path = str(args.path)
    if any(part == ".." for part in Path(raw_path).parts):
        print("target-release: path に '..' を含む指定はできません", file=sys.stderr)
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
        print("target-release: release tracking が disabled です", file=sys.stderr)
        return 2
    try:
        target = validate_release_label(args.to)
        # This also validates the type/filename/project scope while retaining
        # the lexical path needed for an explicitly configured junction queue.
        doc = doc_for_path(lexical_path, cfg)
        if doc is None:
            raise ValueError("対象 MD が project root 配下の管理対象として見つかりません")
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
        print(f"target_release を設定しました: {result.path} -> {target}")
    return 0


def cmd_migrate_frontmatter(args: argparse.Namespace) -> int:
    """既存 md に OKF frontmatter を非破壊的に挿入する。"""
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
        print("migrate-frontmatter: 対象なし")
        return 0
    verb = "適用" if apply else "予定"
    print(f"migrate-frontmatter {verb}: {len(result.planned)} 件 / skipped {len(result.skipped)} 件")
    for p in result.planned:
        marker = "[適用済]" if (apply and p.path in result.applied) else "[予定]"
        mode_note = "（既存frontmatterへ不足キー追記）" if p.mode == "upgrade" else ""
        legacy_note = "（旧 status を分離）" if p.legacy_status_migration else ""
        print(
            f"  {marker} {p.doc_type:<8} docsweep_state={p.status:<11} "
            f"{p.path}{mode_note}{legacy_note}"
        )
        if not apply and p.diff:
            print(p.diff, end="" if p.diff.endswith("\n") else "\n")
    for p in result.skipped:
        print(f"  [skip] {p.path}  ({p.skipped_reason})")
    return 0


def cmd_fix_related(args: argparse.Namespace) -> int:
    """片側参照 related: [B] を B 側にも追記して対称化する。"""
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
            print("fix-related: 対称化が必要な参照はありません")
            return 0
    verb = "適用" if getattr(args, "apply", False) else "予定"
    print(f"fix-related {verb}: {len(result.fixes)} ファイルに追記")
    for fix in result.fixes:
        marker = "[適用済]" if fix.path in result.applied else "[予定]"
        print(f"  {marker} {fix.path}  + related: [{', '.join(fix.additions)}]")
    for failure in result.failed:
        print(f"  [失敗] {failure.get('path')}: {failure.get('error')}", file=sys.stderr)
    return 1 if result.failed else 0


def cmd_claim(args: argparse.Namespace) -> int:
    """frontmatter の owner を現ユーザーで上書き / unclaim。"""
    from ...claim import claim
    from ...services.frontmatter import FrontmatterValidationError

    path = Path(args.file)
    try:
        result = claim(path, unclaim=getattr(args, "unclaim", False))
    except FileNotFoundError:
        print(f"ファイルが見つかりません: {args.file}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"claim: 書き換えに失敗しました: {exc}", file=sys.stderr)
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
