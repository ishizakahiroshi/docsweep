"""archive へ移す前に止める仕組みと、移した後の検証（``archive_refs.scope: repo``）。

``docs/local`` が git で追跡されている repo では、作業文書が ``docs/local`` の外（他の
docs・html・コードの注釈）からも参照される。そのまま移すと、コードから参照されている文書や、
他のセッションが編集中のファイルから参照されている文書まで動いてしまう。ここでは

- 移す前: 移すと困る文書を止め、理由（``Hold``）を返す（:func:`guard_moves`）
- 移した後: 旧パスの残り・実体のない参照・新しく切れたリンクを数える（:func:`verify_moves`）

を受け持つ。参照の見つけ方は :mod:`move_refs` の書き換えと同じ（frontmatter・相対リンク・
repo 相対パスの完全一致）。
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import move_refs
from .config import PROJECT_CONFIG_NAME, Config, _load_yaml
from .i18n import t
from .move_refs import (
    DEFAULT_REWRITE_EXTENSIONS,
    Move,
    ProjectPaths,
    RepoScope,
    matches_any,
    repo_files,
)
from .services.frontmatter import read_frontmatter_text

SCOPES = ("queue", "repo")
DEFAULT_CODE_PATHS: tuple[str, ...] = (
    ".github/**",
    ".githooks/**",
    ".claude/**",
    "src/**",
    "CLAUDE.md",
    "AGENTS.md",
)

# 止める理由（JSON の ``reason``）。文言は ``archive_guard.reason.<理由>``。
REASON_CODE = "referenced_by_code"
REASON_UNREADABLE = "referenced_by_unreadable"
REASON_UNCOMMITTED = "uncommitted"
REASON_UNCOMMITTED_REF = "referenced_by_uncommitted"
REASON_IGNORE_LOST = "ignore_lost"
REASON_FAMILY = "family_incomplete"

# 親子の系統: ファイル名から ``_c<数字>(_<名前>)?`` または旧い命名の ``-c<数字>`` を除いた
# 名前が同じものを 1 つにする。ハイフンの形は接尾辞を許さない（mer の実データで、接尾辞つきの
# ``-c<数字>-<名前>`` に親が無かったため。親子でない名前を巻き込む範囲を増やさない）。
_FAMILY_RE = re.compile(
    r"^(?P<base>.+?)(?:_c\d+(?:_[^/\\]*)?|-c\d+)\.md$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ArchiveRefSettings:
    """``archive_refs:`` の解決結果。"""

    scope: str = "queue"
    exclude: tuple[str, ...] = ()
    code_paths: tuple[str, ...] = DEFAULT_CODE_PATHS
    rewrite_extensions: tuple[str, ...] = DEFAULT_REWRITE_EXTENSIONS

    @property
    def repo(self) -> bool:
        return self.scope == "repo"

    def repo_scope(self, skip: frozenset[str] = frozenset()) -> RepoScope:
        return RepoScope(exclude=self.exclude, extensions=self.rewrite_extensions, skip=skip)


def _settings_layer(raw: object, source: object) -> dict:
    if raw is None or raw == "" or raw == {}:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(t("archive_guard.config_not_mapping", source=source))
    layer: dict = {}
    for key, value in raw.items():
        if key == "scope":
            scope = str(value or "").strip().lower()
            if scope not in SCOPES:
                raise ValueError(
                    t("archive_guard.config_bad_scope", source=source, value=value, allowed="/".join(SCOPES))
                )
            layer["scope"] = scope
        elif key in ("exclude", "code_paths", "rewrite_extensions"):
            if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
                raise ValueError(t("archive_guard.config_bad_list", source=source, key=key))
            items = [item.strip() for item in value]
            if key == "rewrite_extensions":
                items = [(item if item.startswith(".") else f".{item}").lower() for item in items]
            layer[key] = tuple(items)
        else:
            raise ValueError(t("archive_guard.config_unknown_key", source=source, key=key))
    return layer


def archive_ref_settings(
    project_dir: Path, config: Config, *, scope: str | None = None
) -> ArchiveRefSettings:
    """project の ``archive_refs`` を、global の値の上にキー単位で重ねて返す。

    ``scope`` は CLI の ``--ref-scope``（設定より優先）。壊れた設定は既定へ倒さず例外にする
    （意図しない範囲の書き換えを防ぐ）。
    """
    merged = _settings_layer(config.archive_refs_base, "~/.docsweep/config.yaml")
    project_config = Path(project_dir) / PROJECT_CONFIG_NAME
    merged.update(_settings_layer(_load_yaml(project_config).get("archive_refs"), project_config))
    if scope is not None:
        if scope not in SCOPES:
            raise ValueError(
                t("archive_guard.config_bad_scope", source="--ref-scope", value=scope, allowed="/".join(SCOPES))
            )
        merged["scope"] = scope
    return ArchiveRefSettings(**merged)


@dataclass
class Hold:
    """移さなかった文書と、その理由。"""

    path: str
    reason: str
    referrers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "reason": self.reason,
            "message": t(f"archive_guard.reason.{self.reason}"),
            "referrers": sorted(self.referrers),
        }


@dataclass
class GuardResult:
    allowed: list[Move]
    held: list[Hold]
    # 書き換えないファイル（未コミット・未追跡の変更があるもの）の照合キー
    skip: frozenset[str] = frozenset()


def _key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _git(root: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _git_toplevel(root: Path) -> Path | None:
    completed = _git(root, "rev-parse", "--show-toplevel")
    if completed is None or completed.returncode != 0 or not completed.stdout.strip():
        return None
    return Path(completed.stdout.strip())


def _git_dirty_keys(root: Path) -> set[str]:
    """未コミット・未追跡の変更があるファイルの照合キー（git でなければ空）。

    ``closeout._git_dirty`` と同じ ``git status`` だが、日本語のファイル名がエスケープされない
    ``-z`` で読む（エスケープされた名前は、実際のパスと突き合わせられない）。
    """
    top = _git_toplevel(root)
    if top is None:
        return set()
    completed = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if completed is None or completed.returncode != 0:
        return set()
    keys: set[str] = set()
    entries = completed.stdout.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        keys.add(_key(top / entry[3:]))
        if entry[0] in "RC" and index < len(entries):
            keys.add(_key(top / entries[index]))
            index += 1
    return keys


def _git_ignored(root: Path, candidates: list[Path]) -> set[str] | None:
    """``candidates`` のうち git の ignore に当たるものの照合キー（git でなければ ``None``）。"""
    if _git_toplevel(root) is None:
        return None
    rels: dict[str, Path] = {}
    for path in candidates:
        try:
            rels[Path(os.path.relpath(path, root)).as_posix()] = path
        except ValueError:
            continue
    if not rels:
        return set()
    completed = _git(root, "check-ignore", "-z", "--stdin", stdin="\0".join(rels) + "\0")
    if completed is None or completed.returncode not in (0, 1):
        return None
    return {_key(rels[rel]) for rel in completed.stdout.split("\0") if rel in rels}


def _scan_files(paths: ProjectPaths, settings: ArchiveRefSettings) -> list[Path]:
    """参照を探すファイル（repo の中と queue の md。``exclude`` を除く）。"""
    found: dict[str, Path] = {}
    for path in repo_files(paths.root):
        found.setdefault(_key(path), path)
    for path in move_refs._iter_candidates(paths, []):
        found.setdefault(_key(path), path)
    kept: list[Path] = []
    for path in found.values():
        rel = paths.repo_rel(path)
        if rel is not None and matches_any(rel, settings.exclude):
            continue
        kept.append(path)
    return kept


def _read_text(path: Path) -> tuple[str, bool]:
    """(本文, UTF-8 として読めたか)。読めないときは置き換え文字で読む（探すだけなので）。"""
    data = path.read_bytes()
    try:
        return data.decode("utf-8"), True
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), False


def _family_base(name: str) -> str:
    match = _FAMILY_RE.match(name)
    base = match.group("base") if match else (name[:-3] if name.lower().endswith(".md") else name)
    return base.casefold()


def _families(paths: ProjectPaths) -> tuple[dict[str, set[str]], dict[str, Path]]:
    """queue の md（archive を除く）を系統ごとにまとめる。

    返り値は (照合キー → 同じ系統の照合キー, 照合キー → パス)。

    名前の規則（``_c<数字>`` と ``-c<数字>``）と ``docsweep_parent`` の両方でつなぐ。
    """
    docs = move_refs._iter_candidates(paths, [])
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    by_rel: dict[str, str] = {}
    by_base: dict[str, str] = {}
    for doc in docs:
        key = _key(doc)
        parent[key] = key
        rel = paths.repo_rel(doc)
        if rel is not None:
            by_rel[rel.casefold()] = key
    for doc in docs:
        key = _key(doc)
        base = _family_base(doc.name)
        if base in by_base:
            union(by_base[base], key)
        else:
            by_base[base] = key
        try:
            text = doc.open("r", encoding="utf-8", newline="").read()
        except (OSError, UnicodeError):
            continue
        data, _body = read_frontmatter_text(text)
        value = data.get("docsweep_parent")
        if isinstance(value, str) and value.strip():
            wanted = value.strip().replace("\\", "/")
            while wanted.startswith("./"):
                wanted = wanted[2:]
            target = by_rel.get(wanted.casefold())
            if target is not None:
                union(target, key)
    groups: dict[str, set[str]] = {}
    for key in parent:
        groups.setdefault(find(key), set()).add(key)
    return {key: groups[find(key)] for key in parent}, {_key(doc): doc for doc in docs}


def guard_moves(
    moves: list[Move],
    *,
    project_root: Path,
    config: Config,
    settings: ArchiveRefSettings,
    family: bool = True,
) -> GuardResult:
    """``moves`` のうち、移すと困るものを止める（移す前に呼ぶ。何も書かない）。

    ``family=False`` は 1 本を名指しする操作（``apply``）用で、系統がそろわなくても止めない。
    """
    if not moves:
        return GuardResult([], [])
    paths = ProjectPaths(project_root, config)
    table, links = move_refs.move_tables(moves, paths, dry_run=True)
    dirty = _git_dirty_keys(paths.root)
    by_old = {paths.repo_rel(move.src): move for move in moves}
    holds: dict[tuple[str, str], Hold] = {}

    def hold(move: Move, reason: str, referrer: str | None = None) -> None:
        entry = holds.setdefault(
            (_key(move.src), reason), Hold(move.src.as_posix(), reason)
        )
        if referrer and referrer not in entry.referrers:
            entry.referrers.append(referrer)

    def moving(move: Move) -> bool:
        return not any(key[0] == _key(move.src) for key in holds)

    for move in moves:
        if _key(move.src) in dirty:
            hold(move, REASON_UNCOMMITTED)
    ignored = _git_ignored(paths.root, [p for move in moves for p in (move.src, move.dst)])
    if ignored is not None:
        for move in moves:
            if _key(move.src) in ignored and _key(move.dst) not in ignored:
                hold(move, REASON_IGNORE_LOST)

    needles = move_refs._name_needles(moves)
    moving_keys = {_key(move.src) for move in moves}
    referrers: list[tuple[Path, set[str], bool]] = []
    for path in _scan_files(paths, settings):
        if not move_refs._mentions_any(path, needles):
            continue
        try:
            text, readable = _read_text(path)
        except OSError:
            continue
        olds = move_refs.referenced_sources(path, text, table, links)
        if olds:
            referrers.append((path, olds, readable))
    scope = settings.repo_scope()
    for path, olds, readable in referrers:
        rel = paths.repo_rel(path) or path.as_posix()
        own = _key(path)
        is_code = matches_any(rel, settings.code_paths) or not scope.rewritable(path)
        is_dirty = own in dirty
        for old in olds:
            target = by_old.get(old)
            if target is None or _key(target.src) == own:
                continue
            if is_code:
                hold(target, REASON_CODE, rel)
            elif not readable:
                hold(target, REASON_UNREADABLE, rel)
            elif is_dirty and own not in moving_keys:
                hold(target, REASON_UNCOMMITTED_REF, rel)
    # 未コミットで止めた文書（動かない・書き換えない）から参照されている文書も止める
    for path, olds, _readable in referrers:
        own = _key(path)
        if own not in moving_keys or own not in dirty:
            continue
        rel = paths.repo_rel(path) or path.as_posix()
        for old in olds:
            target = by_old.get(old)
            if target is not None and _key(target.src) != own:
                hold(target, REASON_UNCOMMITTED_REF, rel)

    if family:
        families, doc_paths = _families(paths)
        move_by_key = {_key(move.src): move for move in moves}
        for move in moves:
            if not moving(move):
                continue
            members = families.get(_key(move.src), {_key(move.src)})
            staying = [
                member for member in members
                if member not in move_by_key or not moving(move_by_key[member])
            ]
            if not staying:
                continue
            for member in members:
                target = move_by_key.get(member)
                if target is not None and moving(target):
                    for other in staying:
                        other_path = doc_paths.get(other)
                        other_rel = paths.repo_rel(other_path) if other_path is not None else None
                        hold(target, REASON_FAMILY, other_rel or other)
    allowed = [move for move in moves if moving(move)]
    return GuardResult(allowed, list(holds.values()), frozenset(dirty))


def snapshot_links(
    moves: list[Move], *, project_root: Path, config: Config, settings: ArchiveRefSettings
) -> dict[str, set[str]]:
    """移す前に届いていなかったリンク（ファイルの照合キー → リンク先の書き方）。

    リンクが新しく切れうるのは、移す文書を指すファイルと、移す文書自身だけなので、
    その名前を含むファイルだけを読む。
    """
    paths = ProjectPaths(project_root, config)
    needles = move_refs._name_needles(moves)
    scope = settings.repo_scope()
    broken: dict[str, set[str]] = {}
    for path in _scan_files(paths, settings):
        if not scope.rewritable(path) or not move_refs._mentions_any(path, needles):
            continue
        try:
            text, _readable = _read_text(path)
        except OSError:
            continue
        broken[_key(path)] = move_refs.unresolved_links(path, text, paths)
    return broken


def verify_moves(
    moves: list[Move],
    *,
    project_root: Path,
    config: Config,
    settings: ArchiveRefSettings,
    before: dict[str, set[str]],
) -> dict:
    """移した後の検証。3 つの一覧（どれも空なら ``ok``）を返す。

    - ``stale_refs``: 旧パス（repo 相対）が ``exclude`` 以外のファイルに残っている
    - ``missing_targets``: 移した先のパスを指しているのに、実体がない
    - ``broken_links``: 移す前は届いていた md・html の相対リンクが、今は届かない
    """
    result: dict = {"stale_refs": [], "missing_targets": [], "broken_links": []}
    if not moves:
        result["ok"] = True
        return result
    paths = ProjectPaths(project_root, config)
    old_table, _links = move_refs.move_tables(moves, paths, dry_run=False)
    new_table, _ = move_refs.move_tables([Move(m.dst, m.src) for m in moves], paths, dry_run=False)
    src_of = {_key(move.dst): _key(move.src) for move in moves}
    needles = sorted(
        set(move_refs._name_needles(moves)) | set(move_refs._name_needles([Move(m.dst, m.src) for m in moves]))
    )
    scope = settings.repo_scope()
    for move in moves:
        if not move.dst.exists():
            result["missing_targets"].append(
                {"path": None, "ref": paths.repo_rel(move.dst) or move.dst.as_posix()}
            )
    old_pattern = old_table.body_pattern()
    new_pattern = new_table.body_pattern()
    for path in _scan_files(paths, settings):
        if not move_refs._mentions_any(path, needles):
            continue
        try:
            text, _readable = _read_text(path)
        except OSError:
            continue
        rel = paths.repo_rel(path) or path.as_posix()
        if old_pattern is not None:
            for match in old_pattern.finditer(text):
                result["stale_refs"].append({"path": rel, "ref": match.group(0)})
        if new_pattern is not None:
            for match in new_pattern.finditer(text):
                if not (paths.root / match.group(0)).exists():
                    result["missing_targets"].append({"path": rel, "ref": match.group(0)})
        if not scope.rewritable(path):
            continue
        before_key = src_of.get(_key(path), _key(path))
        if before_key not in before:
            continue
        for link in sorted(move_refs.unresolved_links(path, text, paths) - before[before_key]):
            result["broken_links"].append({"path": rel, "link": link})
    result["ok"] = not (result["stale_refs"] or result["missing_targets"] or result["broken_links"])
    return result


def merge_verify(results: list[dict]) -> dict | None:
    """project ごとの検証結果を 1 つにまとめる（検証していなければ ``None``）。"""
    if not results:
        return None
    merged: dict = {"stale_refs": [], "missing_targets": [], "broken_links": []}
    for item in results:
        for key in ("stale_refs", "missing_targets", "broken_links"):
            merged[key].extend(item.get(key, []))
    merged["ok"] = not (merged["stale_refs"] or merged["missing_targets"] or merged["broken_links"])
    return merged
