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
