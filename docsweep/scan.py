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
            # .md / .html を対象拡張子とする。type 判定は filename pattern（config.match_type）
            # で行うため、命名規約に合わない .md/.html（LICENSE・README・LP 等）は自然に除外される。
            if not (fn.endswith(".md") or fn.endswith(".html") or fn.endswith(".htm")):
                continue
            fpath = cur / fn
            rel = fpath.relative_to(root).as_posix()
            if _is_ignored(rel, fn, base_patterns):
                continue
            type_def = config.match_type(fn)
            # 命名規約（plan_*.md / mockup_*.html 等の type パターン）に一致しないファイルは
            # docsweep の管理対象外。拾わない（プロジェクトの LP や README を巻き込まない）。
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
    if type_def is not None and type_def.summary_section:
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
        docsweep_policy=det.docsweep_policy,
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


def _project_remote_url(project_root: Path) -> str | None:
    """``git remote get-url origin`` の URL を返す（取れなければ None）。

    ``projects.remote_url`` を埋めるためだけの補助。**project_id の採番には使わない**
    — 索引の project 単位は ``FileRecord.project``（= ``project_root.name``）に揃えてある。
    索引なしフォールバック経路（``r.project == project``）と同じ意味論にしないと、
    ``--project <リポ名>`` が索引の有無で別物を指してしまうため。

    ``.git`` を持たないディレクトリでは git を呼ばない。呼ぶと ``git -C`` が上位へ
    遡って親リポジトリの remote を拾い、monorepo 内のサブプロジェクトに親の URL が
    付いてしまう。
    """
    if not (project_root / ".git").exists():
        return None

    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


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


def _rel_for_index(abs_path: str, root: Path) -> str:
    """索引に入れる rel_path を求める（``root`` は原則プロジェクトルート）。

    ``FileRecord.path`` は ``fpath.resolve()`` 済みなので、root 配下に junction /
    symlink があるとリンク先の実体パス（root の外）に解決される。例えば
    ``C:/dev/kb`` が別ドライブ配下への junction だと ``relative_to(C:/dev)`` は
    ValueError になり、以前は sync_index がその場で落ちて索引が不完全なまま
    残っていた。

    root 相対が取れない場合は実体の絶対パスをそのまま識別子に使う。
    ``UNIQUE(project_id, rel_path)`` の一意性は保たれ、表示や移送に使う絶対パスは
    別列 ``abs_path`` が持つため実害はない。同名 basename のリポが 2 つ同じ
    project_id に落ちた場合も、片方はこのフォールバックで絶対パス識別子になり
    互いを上書きしない。
    """
    p = Path(abs_path)
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def _is_within(path: Path, roots: list[Path]) -> bool:
    """``path`` が ``roots`` のいずれかの配下（または一致）かを返す。

    「今回のスキャンがそのプロジェクトを実際にカバーしたか」の判定に使う。
    カバーされていたのに 1 件も見つからなければ、そのプロジェクトの索引行は
    すべて消えたものとして削除できる。判定できない場合は False（＝消さない安全側）。
    """
    for r in roots:
        try:
            path.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def sync_index(
    config: Config,
    *,
    full: bool = False,
    db_path_override: Path | None = None,
    prune_projects: bool = False,
) -> SyncStats:
    """``search_paths`` 配下を走査し SQLite 索引へ差分同期する。

    索引の project 単位は **スキャンルートではなくプロジェクトルート（リポジトリ）**。
    ``projects.project_id`` には ``FileRecord.project``（= ``project_root.name``）を、
    ``projects.root_path`` には ``project_root`` を入れ、``files.rel_path`` は
    project_root 相対にする。理由は 2 つ:

    1. 索引経由の ``WHERE project_id = ?`` と、索引なしフォールバックの
       ``r.project == project`` が同じ意味論になる。以前は前者がスキャンルート単位
       だったため、索引が有効だと ``--project <リポ名>`` がエラーも警告も無く 0 件を
       返していた（「残作業なし」と誤読する事故）。
    2. basename が同じスキャンルート（``C:/dev`` と ``C:/Users/x/dev``）が同じ
       project_id に潰れ、``known_files`` → 未検出分削除の流れで互いの登録を消し合う
       事故が消える（差分同期が毎回全件書き直しになり、処理順次第で索引が空になり得た）。

    Args:
        config: ロード済み Config
        full: True で全件再構築（DB の files を一旦 truncate してから挿入）
        db_path_override: テスト用に DB パスを上書き
        prune_projects: True で「DB にあるが今回の走査で見つからなかった projects」を
            CASCADE 削除する。既定 False（一時的な search_paths 変更で誤削除しないよう保護）。

    Returns:
        SyncStats — 同期件数の集計
    """
    import json as _json
    import sys
    from datetime import datetime, timezone

    from . import index as db
    from .engine import classify

    stats = SyncStats()
    roots = _expand_search_paths(config)

    # --- 1. 走査（DB を触る前に完了させる）--------------------------------------
    # project_root ごとにまとめ直す。DB 書き込みより前に全走査を終えるのは、
    # full=True の ``DELETE FROM files`` 直後に走査で落ちると索引が空のまま残るため
    # （junction で ValueError を投げて実際に壊した実績がある）。
    groups: dict[str, tuple[Path, list[ScannedDoc]]] = {}
    seen_abs: set[str] = set()
    for root in roots:
        for doc in scan_root(root, config):
            rec = doc.record
            if rec.path in seen_abs:
                continue  # スキャンルートが入れ子でも同じファイルを二重登録しない
            seen_abs.add(rec.path)
            # classify を呼んで flags / allowed_actions を FileRecord に充填
            classify(doc, config)
            project_root = Path(rec.project_root) if rec.project_root else root
            project_id = rec.project or project_root.name
            entry = groups.get(project_id)
            if entry is None:
                groups[project_id] = (project_root, [doc])
            else:
                entry[1].append(doc)

    now_iso = datetime.now(timezone.utc).isoformat()

    with db.connect(db_path_override) as conn:
        existing_projects: dict[str, str] = {
            r["project_id"]: (r["root_path"] or "")
            for r in conn.execute("SELECT project_id, root_path FROM projects").fetchall()
        }
        current_ids = set(groups)

        if full:
            # 全再構築: files を空にする（tags/related は ON DELETE CASCADE で連鎖）
            conn.execute("DELETE FROM files")
            conn.commit()

        # C4: 孤児プロジェクト掃除 — DB にあるが今回の走査で 1 件も見つからなかった
        # project を CASCADE 削除。files / tags / related は連鎖削除される。
        pruned_ids: set[str] = set()
        if prune_projects:
            pruned_ids = set(existing_projects) - current_ids
            for orphan in pruned_ids:
                conn.execute("DELETE FROM projects WHERE project_id=?", (orphan,))
                stats.projects_removed += 1
            if pruned_ids:
                conn.commit()

        for project_id, (project_root, docs) in groups.items():
            db.upsert_project(
                conn, project_id, project_root.as_posix(),
                _project_remote_url(project_root), now_iso,
            )
            stats.projects += 1

            existing = db.known_files(conn, project_id)
            seen_rel: set[str] = set()

            for doc in docs:
                rec = doc.record
                rel_path = _rel_for_index(rec.path, project_root)
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

        # --- 2. 走査範囲内なのに 1 件も見つからなかった project の後始末 --------
        # 該当するのは 2 種類:
        #   a) 旧採番（スキャンルート単位）で登録された project 行。放置すると同じ md が
        #      旧 project_id と新 project_id の両方に載り、triage が二重に出る
        #   b) md を全部消した / archive へ移したリポ
        # どちらもファイル行は実体を失っているので自動で消す。project 行そのものは
        # 消さない（--prune-projects の担当・一時的な search_paths 変更で誤削除しない）。
        cleaned: list[tuple[str, int]] = []
        for project_id, root_path in existing_projects.items():
            if project_id in current_ids or project_id in pruned_ids:
                continue
            if not root_path or not _is_within(Path(root_path), roots):
                continue  # 走査範囲外＝今回は判断材料が無い。触らない（安全側）
            removed = 0
            for stale_rel in db.known_files(conn, project_id):
                db.delete_file(conn, project_id, stale_rel)
                removed += 1
            if removed:
                stats.files_deleted += removed
                cleaned.append((project_id, removed))

        conn.commit()

    # 実際に掃除した時だけ 1 行出す。空になった project 行が残っているだけの状態で
    # 毎回鳴らすと、常時 stderr を汚すだけで行動につながらない。
    if cleaned:
        detail = ", ".join(f"{pid}（{n} 件）" for pid, n in sorted(cleaned))
        print(
            f"warning: 実体を失った索引行を削除しました: {detail}"
            " — 旧採番（スキャンルート単位）の残骸か、md を全部消した/archive へ移した"
            "プロジェクトです。空になった project 行ごと消すなら"
            " `docsweep index-sync --prune-projects`",
            file=sys.stderr,
        )

    return stats
