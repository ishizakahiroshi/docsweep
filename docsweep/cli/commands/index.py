"""CLI command handlers: index."""

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

def cmd_index(args: argparse.Namespace) -> int:
    from ...aggregate_index import write_index

    cfg = _build_config(args)
    json_path, md_path = write_index(cfg)
    print(f"INDEX を生成しました:\n  {md_path}\n  {json_path}")
    return 0


def cmd_index_sync(args: argparse.Namespace) -> int:
    """SQLite 索引へ差分同期。``projects.search_paths`` 配下を走査して mtime 差分のみ更新。"""
    from ...scan import sync_index

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
    from ... import index as db
    from ...scan import sync_index

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
    from ... import index as db

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
    from ... import index as db

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

    from ...scan import _expand_search_paths, sync_index

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
            from ... import index as db
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
