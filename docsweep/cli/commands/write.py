"""CLI command handlers: write."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from ...config import DEFAULT_PROJECT_MARKERS, load_config
from ...engine import apply_action, auto_sweep, run_scan
from ..parser import _build_config

def cmd_fix_conflict(args: argparse.Namespace) -> int:
    """conflict 修理（UX W2 / P37）。"""
    from ...fix_conflict import fix_conflicts, list_conflicts

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
        from ...aggregate_index import write_index

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
    from ...engine import promote_state

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


def cmd_capture(args: argparse.Namespace) -> int:
    """会話履歴から plan / bugfix / pending 草案を抽出 (heuristic / LLM)。"""
    from ...capture import extract_drafts, save_drafts

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
        result = apply_suggestions(cfg, decisions, dry_run=getattr(args, "dry_run", False))
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    return 2


def cmd_new(args: argparse.Namespace) -> int:
    from ...scan import detect_project_root
    from ...secrets_guard import format_warnings, scan_secrets
    from ...similar_guard import find_similar_open
    from ...templates_gen import new_doc, new_split_plans

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
        print(f"  {marker} {p.doc_type:<8} status={p.status:<11} {p.path}{mode_note}")
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
        print("fix-related: 対称化が必要な参照はありません")
        return 0
    verb = "適用" if getattr(args, "apply", False) else "予定"
    print(f"fix-related {verb}: {len(result.fixes)} ファイルに追記")
    for fix in result.fixes:
        marker = "[適用済]" if fix.path in result.applied else "[予定]"
        print(f"  {marker} {fix.path}  + related: [{', '.join(fix.additions)}]")
    return 0


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
