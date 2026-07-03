"""再帰スキャン: 除外ルール（.gitignore 準拠＋独自 ignore グロブ＋archive 自身は常に除外）。

完全な .gitignore セマンティクスではなく、行パターンを相対 POSIX パス／basename に
fnmatch する best-effort 実装（v0.1.0）。プロジェクト＝スキャンルート直下のディレクトリ。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from .config import Config, TypeDef
from .detect import Detection, detect_status, extract_summary
from .models import FileRecord

# 常に除外するディレクトリ名。
# docsweep 自身の生成物（INDEX.md / moves.jsonl）を再スキャンしないよう .docsweep も除外。
ALWAYS_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".ruff_cache", ".pytest_cache", ".docsweep"}


@dataclass
class ScannedDoc:
    record: FileRecord
    detection: Detection
    type_def: TypeDef | None
    text: str


# frontmatter 矛盾 warning のプロセス内 dedup（(path, message) 単位で 1 回だけ stderr へ）。
_WARNED_ONCE: set[tuple[str, str]] = set()


def _read_gitignore(root: Path) -> list[str]:
    gi = root / ".gitignore"
    if not gi.is_file():
        return []
    patterns: list[str] = []
    for line in gi.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        patterns.append(line.rstrip("/"))
    return patterns


def _is_ignored(rel_posix: str, name: str, patterns: list[str]) -> bool:
    for pat in patterns:
        p = pat.lstrip("/")
        if fnmatch(name, p) or fnmatch(rel_posix, p) or fnmatch(rel_posix, f"{p}/*") or fnmatch(rel_posix, f"*/{p}/*"):
            return True
    return False


def _age_days(mtime: float) -> int:
    now = datetime.now(timezone.utc).timestamp()
    return max(0, int((now - mtime) // 86400))


def scan_root(root: Path, config: Config) -> list[ScannedDoc]:
    """1 つのスキャンルート配下を走査し ScannedDoc のリストを返す。"""
    root = root.resolve()
    if not root.is_dir():
        return []

    # 枝刈り対象 archive ディレクトリ名。グローバル＋全 type 別 archive_dir の「末尾セグメント」を
    # 集合化する。ネスト指定（例 "docs/archive"）は末尾 "archive" だけを刈り、中間の "docs"
    # ツリー全体を誤って消さない。各プロジェクトの archive/ は任意の深さに出るため basename 判定。
    archive_names = set()
    for ad in (config.archive_dir, *(t.archive_dir for t in config.types)):
        if ad:
            seg = ad.strip("/").split("/")
            if seg and seg[-1]:
                archive_names.add(seg[-1])
    base_patterns = list(config.ignore)
    if config.use_gitignore:
        base_patterns += _read_gitignore(root)

    docs: list[ScannedDoc] = []
    proj_cache: dict[Path, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        cur = Path(dirpath)
        rel_dir = cur.relative_to(root).as_posix()

        # ディレクトリ枝刈り（archive・常時除外・ignore）。
        pruned: list[str] = []
        for d in dirnames:
            if d in ALWAYS_SKIP_DIRS or d in archive_names:
                continue
            child_rel = f"{rel_dir}/{d}".lstrip("/") if rel_dir != "." else d
            if _is_ignored(child_rel, d, base_patterns):
                continue
            pruned.append(d)
        dirnames[:] = pruned

        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            fpath = cur / fn
            rel = fpath.relative_to(root).as_posix()
            if _is_ignored(rel, fn, base_patterns):
                continue
            type_def = config.match_type(fn)
            # 命名規約（plan_/bugfix_/pending_ 等の type パターン）に一致しない .md は
            # docsweep の管理対象外（LICENSE・README・依存ライブラリの .md 等）。拾わない。
            if type_def is None:
                continue
            project_root = detect_project_root(cur, root, config.project_markers, proj_cache)
            doc = _build_doc(fpath, root, config, type_def, project_root)
            if doc is not None:
                docs.append(doc)
    return docs


def detect_project_root(
    start_dir: Path, root: Path, markers: list[str], cache: dict[Path, Path]
) -> Path:
    """プロジェクト境界を判定する。

    ファイルのフォルダから上へ辿り、``markers``（既定 .git/.docsweep.yaml/package.json/
    pyproject.toml）のいずれかを持つ最寄りの祖先をプロジェクトとする。スキャンルートより
    上へは辿らない。見つからなければルート直下の先頭セグメントへフォールバック。

    フォルダ構成（docs/local 等）を一切決め打ちせず、開発者が既に定義済みの実体で判定する。
    """
    if start_dir in cache:
        return cache[start_dir]

    chain: list[Path] = []
    cur = start_dir
    found: Path | None = None
    while True:
        chain.append(cur)
        if any((cur / m).exists() for m in markers):
            found = cur
            break
        if cur == root:
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent

    if found is None:
        rel = start_dir.relative_to(root)
        found = (root / rel.parts[0]) if rel.parts else root

    for d in chain:
        cache[d] = found
    return found


def _build_doc(
    fpath: Path, root: Path, config: Config, type_def: TypeDef | None, project_root: Path
) -> ScannedDoc | None:
    try:
        text = fpath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    sm = config.state_model
    det = detect_status(text=text, filename=fpath.name, sm=sm, _type=type_def)

    summary = None
    if type_def is not None:
        summary = extract_summary(text, type_def.summary_section)

    stat = fpath.stat()
    age = _age_days(stat.st_mtime)
    state = sm.by_key(det.state_key) if det.state_key else None

    # 型矛盾は warn として stderr へ出す（自動上書きしない）。
    # plan_okf-adoption_2026-06-29.md C1 の方針: 矛盾を可視化するが直さない。
    # 同一 (path, warning) はプロセス内 1 回だけ出す。Web UI (serve) は描画のたびに
    # run_scan を呼ぶため、毎回出すと同じ warning がログを埋める（2026-07-03 実測）。
    # 矛盾自体は needs_fix フラグとして UI にも出続けるので、抑制しても見落とさない。
    if det.frontmatter_warnings:
        import sys
        for w in det.frontmatter_warnings:
            key = (fpath.resolve().as_posix(), w)
            if key in _WARNED_ONCE:
                continue
            _WARNED_ONCE.add(key)
            print(f"warning: {fpath}: {w}", file=sys.stderr)

    record = FileRecord(
        path=fpath.resolve().as_posix(),
        project=project_root.name,
        project_root=project_root.resolve().as_posix(),
        type=type_def.name if type_def else None,
        state=det.state_key,
        state_label=det.state_label,
        state_source=det.source,
        title=det.title,
        summary=summary,
        mtime=stat.st_mtime,
        age_days=age,
        archivable=bool(state and state.archive),
        auto_movable=bool(state and state.auto_move),
        due=det.due,
        due_parse_error=det.due_parse_error,
        tags=list(det.tags),
        owner=det.owner,
        review_status=det.review_status,
        related=list(det.related),
        last_reviewed=det.last_reviewed,
    )
    return ScannedDoc(record=record, detection=det, type_def=type_def, text=text)


def scan(config: Config) -> list[ScannedDoc]:
    docs: list[ScannedDoc] = []
    seen: set[str] = set()
    for root in config.roots:
        for d in scan_root(root, config):
            if d.record.path in seen:
                continue
            seen.add(d.record.path)
            docs.append(d)
    return docs


# ===================================================================
# C1 (wings): SQLite 索引への差分同期
# ===================================================================


@dataclass
class SyncStats:
    """sync_index の戻り値。run/JSON 出力にそのまま使える形式。"""

    projects: int = 0
    files_total: int = 0
    files_added: int = 0
    files_updated: int = 0
    files_unchanged: int = 0
    files_deleted: int = 0
    # C4 (bloat-mitigation): --prune-projects で削除された孤児 projects の件数。
    # フラグ無し時は常に 0（一時的な search_paths 変更で誤削除しない安全側のため）。
    projects_removed: int = 0


def _resolve_project_id(root: Path) -> tuple[str, str | None]:
    """プロジェクト識別子と remote_url を返す。

    優先順: ``git remote get-url origin`` の repo 名（.git を除く最後の segment）→ ディレクトリ名。
    git remote が無い／git でない場合は単にディレクトリ名を ID とする。
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            if url:
                tail = url.rstrip("/").split("/")[-1]
                if tail.endswith(".git"):
                    tail = tail[:-4]
                return (tail or root.name, url)
    except (OSError, subprocess.SubprocessError):
        pass
    return (root.name, None)


def _expand_search_paths(config: Config) -> list[Path]:
    """``projects.search_paths`` のグロブパターンを展開し実在ディレクトリのみ返す。

    search_paths 未設定なら ``roots`` をフォールバックとして使う（後方互換）。
    """
    import glob

    raw: list[str] = list(config.search_paths) if config.search_paths else []
    expanded: list[Path] = []
    seen: set[Path] = set()

    for pat in raw:
        # 環境変数 / ~ を展開してからグロブ展開
        p = os.path.expandvars(os.path.expanduser(str(pat)))
        for hit in glob.glob(p):
            cand = Path(hit).resolve()
            if cand.is_dir() and cand not in seen:
                seen.add(cand)
                expanded.append(cand)

    # フォールバック: search_paths 未設定なら roots を使う
    if not expanded:
        for r in config.roots:
            cand = Path(r).resolve()
            if cand.is_dir() and cand not in seen:
                seen.add(cand)
                expanded.append(cand)

    # exclude グロブで除外
    if config.search_exclude:
        filtered: list[Path] = []
        for p in expanded:
            posix = p.as_posix()
            if any(fnmatch(posix, pat) for pat in config.search_exclude):
                continue
            filtered.append(p)
        expanded = filtered

    return expanded


def _body_sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def sync_index(
    config: Config,
    *,
    full: bool = False,
    db_path_override: Path | None = None,
    prune_projects: bool = False,
) -> SyncStats:
    """``search_paths`` 配下を走査し SQLite 索引へ差分同期する。

    Args:
        config: ロード済み Config
        full: True で全件再構築（DB の files を一旦 truncate してから挿入）
        db_path_override: テスト用に DB パスを上書き
        prune_projects: True で「DB にあるが今回の search_paths 展開結果に無い projects」を
            CASCADE 削除する。既定 False（一時的な search_paths 変更で誤削除しないよう保護）。

    Returns:
        SyncStats — 同期件数の集計
    """
    import json as _json
    from datetime import datetime, timezone

    from . import index as db
    from .engine import classify

    stats = SyncStats()
    roots = _expand_search_paths(config)

    with db.connect(db_path_override) as conn:
        if full:
            # 全再構築: files を空にする（projects と tags/related は ON DELETE CASCADE で連鎖）
            conn.execute("DELETE FROM files")
            conn.commit()

        # C4: 孤児プロジェクト掃除 — DB にあるが今回 search_paths から外れた project を CASCADE 削除。
        # files / tags / related は ON DELETE CASCADE で連鎖削除される。
        if prune_projects:
            current_ids = {_resolve_project_id(root)[0] for root in roots}
            existing_ids = {
                r["project_id"]
                for r in conn.execute("SELECT project_id FROM projects").fetchall()
            }
            orphan_ids = existing_ids - current_ids
            for orphan in orphan_ids:
                conn.execute("DELETE FROM projects WHERE project_id=?", (orphan,))
                stats.projects_removed += 1
            if orphan_ids:
                conn.commit()

        now_iso = datetime.now(timezone.utc).isoformat()

        for root in roots:
            project_id, remote_url = _resolve_project_id(root)
            db.upsert_project(conn, project_id, str(root), remote_url, now_iso)
            stats.projects += 1

            existing = db.known_files(conn, project_id)
            seen_rel: set[str] = set()

            docs = scan_root(root, config)
            for doc in docs:
                # classify を呼んで flags / allowed_actions を FileRecord に充填
                classify(doc, config)
                rec = doc.record
                rel_path = Path(rec.path).relative_to(root).as_posix()
                seen_rel.add(rel_path)
                stats.files_total += 1

                mtime = rec.mtime
                # 差分判定: 既知 mtime と一致なら skip
                prev = existing.get(rel_path)
                if not full and prev and prev[0] is not None and abs(prev[0] - mtime) < 1e-6:
                    stats.files_unchanged += 1
                    continue

                sha = _body_sha(doc.text)
                if not full and prev and prev[1] == sha:
                    # mtime 変わっても body 未変化（touch のみ等）→ mtime だけ更新
                    conn.execute(
                        "UPDATE files SET mtime=? WHERE project_id=? AND rel_path=?",
                        (mtime, project_id, rel_path),
                    )
                    stats.files_unchanged += 1
                    continue

                file_id = db.upsert_file(
                    conn,
                    project_id=project_id,
                    rel_path=rel_path,
                    type_=rec.type,
                    status=rec.state,
                    review_status=rec.review_status,
                    owner=rec.owner,
                    last_reviewed=rec.last_reviewed,
                    claimed_at=None,
                    mtime=mtime,
                    body_sha=sha,
                    title=rec.title,
                    summary=rec.summary,
                    state_label=rec.state_label,
                    state_source=rec.state_source,
                    flags=_json.dumps(rec.flags, ensure_ascii=False),
                    allowed_actions=_json.dumps(rec.allowed_actions, ensure_ascii=False),
                    due=rec.due,
                    due_parse_error=rec.due_parse_error,
                    archivable=rec.archivable,
                    auto_movable=rec.auto_movable,
                    project_root=rec.project_root,
                    abs_path=rec.path,
                )
                db.replace_tags(conn, file_id, list(rec.tags or []))
                db.replace_related(conn, file_id, list(rec.related or []))

                if prev is None:
                    stats.files_added += 1
                else:
                    stats.files_updated += 1

            # DB にあるが今回見つからなかったファイル = 削除済み
            for stale_rel in set(existing.keys()) - seen_rel:
                db.delete_file(conn, project_id, stale_rel)
                stats.files_deleted += 1

        conn.commit()

    return stats
