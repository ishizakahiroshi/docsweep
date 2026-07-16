"""CLI command handlers: read."""

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
    from ...day import day_close, day_open

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
    from ...intent import route_intent

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


def cmd_doctor(args: argparse.Namespace) -> int:
    """環境ヘルスチェック（UX W1 / P3）。"""
    from ...doctor import format_human, run_doctor

    global_path = Path(args.config) if getattr(args, "config", None) else None
    report = run_doctor(global_path=global_path)
    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_human(report), end="")
    return 0 if report.ok else 1


def cmd_scan(args: argparse.Namespace) -> int:
    from ...engine import scan_records

    cfg = _build_config(args)
    project = getattr(args, "project", None)
    records = scan_records(cfg, project=project)
    if project:
        records = [r for r in records if r.project == project]
    if not getattr(args, "all", False):
        from ...models import Flag
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
    from ...reports import build_triage

    cfg = _build_config(args)

    # --review はインタラクティブ実行へ即委譲（JSON は出さない）。
    if getattr(args, "review", False):
        from ...interactive import run_interactive_triage
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
    from ...activity import ActivityDateError, build_activity

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
    from ...brief import build_brief

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
        from ...context import collect_context, render_context, to_clipboard
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
    from ...cross import build_cross
    from ...cross.service import explain_score

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


def cmd_linkcheck(args: argparse.Namespace) -> int:
    """plan の整合チェック（C5）。"""
    from ...linkcheck import linkcheck

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


def cmd_graph(args: argparse.Namespace) -> int:
    """関係性グラフ JSON 出力（C5）。"""
    from ...graph import build_graph

    cfg = _build_config(args)
    g = build_graph(cfg, project=getattr(args, "project", None))
    print(json.dumps(g.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_resurrect(args: argparse.Namespace) -> int:
    """archive 蘇生（C6）。embedding 未インストール時は Jaccard。"""
    from ...resurrect import find_candidates

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
    from ...aggregate_index import build_index

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
    from ...reports import render_report

    print(render_report(_build_config(args)))
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    from ...reports import render_summary

    print(render_summary(_build_config(args), project=getattr(args, "project", None)))
    return 0


def cmd_project(args: argparse.Namespace) -> int:
    """project list|enable|disable（UX W2 / P39）。"""
    from ...excluded import disable_project, enable_project, list_known_projects, load_excluded

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
    from ...history import read_history

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
    from ...cookbook import get_scenario, list_scenarios, render_cookbook

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


def cmd_list(args: argparse.Namespace) -> int:
    from ...inject import list_injected

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


def cmd_show(args: argparse.Namespace) -> int:
    """指定ファイルを参照している plan/bugfix/pending を逆参照表示。"""
    from ...engine import scan_records
    from ...related import backref_records, forward_records

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
    from ...stale import find_stale

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
    from ...context import collect_context, render_context, to_clipboard

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


def cmd_timeline(args: argparse.Namespace) -> int:
    """topic を含む plan/bugfix/pending を時系列で列挙。"""
    from ...timeline import build_timeline, render_timeline

    cfg = _build_config(args)
    result = build_timeline(cfg, args.topic)
    print(render_timeline(result, fmt=args.format))
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    """自由クエリで FileRecord を絞り込む。"""
    from ...find import FindFilters, find_records, resolve_owner_alias

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
    from ...export import run_export

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
