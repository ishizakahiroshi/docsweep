"""移動に伴う参照の書き換え（``docsweep_parent`` / パス形式の ``related`` / 本文中のパス）。

archive への移送や ``docsweep mv`` で文書の場所が変わると、他の文書に書かれた
その文書へのパスが古くなる。ここでは「旧パス → 新パス」の対応を受け取り、
同じ project の queue にある文書（archive の中は除く）と、同じ操作で動いた文書の
参照を新しいパスへ書き換える。

- ``docsweep_parent``: repo 相対の値が旧パスと一致したら新パスへ
- ``related``: ``/`` を含むパス形式の値だけ（repo 相対・絶対）。bare name は basename で
  解決されるので触らない。``./`` ``../`` で始まる文書相対の値も触らない
- 本文の Markdown の相対リンク（``body=False`` のとき以外）: インラインリンク ``[text](x)``・
  画像 ``![alt](x)``・参照定義 ``[id]: x`` のリンク先のうち相対パスのもの。文書の場所から
  解決して、移した文書を指していれば移動先へ、移した文書自身のリンクは移した後の場所から
  見た相対パスへ直す。``#fragment`` / ``?query``・``<...>`` の囲み・タイトルはそのまま残す。
  コードフェンスとインラインコードの中、URL・絶対パス、移す前から切れていたリンクは触らない
- 本文の repo 相対パス（``body=True`` のときだけ）: repo 相対の旧パスの完全一致だけ。
  basename だけの言及や、前にパスの文字が続く記述（絶対パスの末尾・``./`` 付き）は触らない。
  相対リンクとして解決できた箇所は、こちらでは書き換えない（同じ箇所を二重に書き換えない）

書き換えは移動ログへ ``op: "ref_rewrite"`` で残し、``undo`` で :func:`revert_ref_rewrite`
を通して戻す。本文の書き換え（相対リンクと repo 相対パス）は、1 文書につき 1 行の
``field: "body"``（置き換えた位置 ``offsets`` 付き）にまとめる。
"""

from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from urllib.parse import quote, unquote

from .archive import _move_log_lock, _now_iso, append_move_log
from .atomic import ConflictError, write_atomic
from .config import Config, archive_dir_for_project, project_work_settings, resolve_work_dir
from .detect import mask_code_fences, mask_inline_code
from .i18n import t
from .models import MoveLogEntry
from .scan import ALWAYS_SKIP_DIRS
from .services.frontmatter import read_frontmatter_text, update_frontmatter_field

REF_FIELDS = ("docsweep_parent", "related")

# 本文中のパスの前後に続くと「別のパスの一部」になる文字。
_PATH_CHARS = r"A-Za-z0-9_\-./\\"

# インラインリンク・画像のリンク先の始まり（``[text](`` の ``](``）。
_INLINE_OPEN_RE = re.compile(r"\]\(")
# 参照定義 ``[id]: target "title"``（脚注 ``[^1]:`` は除く）。タイトルは同じ行にあるものだけ。
_REF_DEF_RE = re.compile(
    r"^[ ]{0,3}\[(?!\^)(?:[^\[\]\\\n]|\\.)+\]:[ \t]*"
    r"(?:<(?P<angle>[^<>\n]*)>|(?P<bare>[^\s<]\S*))"
    r"(?:[ \t]+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?[ \t]*\r?$",
    re.MULTILINE,
)
# ``http:`` ``mailto:`` などのスキーム（``C:`` のようなドライブ名もここで除く）。
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
# ``%E6`` のような、ASCII 以外の文字をエスケープした書き方。
_ENCODED_NON_ASCII_RE = re.compile(r"%[89A-Fa-f][0-9A-Fa-f]")


@dataclass(frozen=True)
class Move:
    """1 文書の移動（どちらも絶対パス）。"""

    src: Path
    dst: Path


@dataclass
class RefUpdate:
    """1 文書の 1 フィールドの書き換え（予定または実施済み）。"""

    path: str
    field: str
    before: str | list[str]
    after: str | list[str]
    offsets: list[int] | None = None

    def to_dict(self) -> dict:
        data: dict[str, object] = {
            "path": self.path,
            "field": self.field,
            "before": self.before,
            "after": self.after,
        }
        if self.field == "body":
            data["count"] = len(self.offsets or self.before)
        return data


@dataclass
class RefRewriteResult:
    updates: list[RefUpdate] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)

    def extend(self, other: RefRewriteResult) -> None:
        self.updates.extend(other.updates)
        self.failed.extend(other.failed)


class ProjectPaths:
    """repo 相対パスと実体パスの対応（queue が junction でも repo 相対で表す）。"""

    def __init__(self, project_root: Path, config: Config) -> None:
        self.root = Path(project_root).resolve()
        work_dir = project_work_settings(self.root, config)[0]
        self.queue_logical = resolve_work_dir(self.root, work_dir)
        self.queue_real = self.queue_logical.resolve()
        self.queue_rel = self.queue_logical.relative_to(self.root).as_posix()
        self.archive_names = _archive_names(self.root, config)

    def queue_parts(self, path: Path) -> tuple[str, ...] | None:
        """queue 内なら queue から見た相対パスの要素、queue の外なら ``None``。"""
        try:
            return Path(path).resolve().relative_to(self.queue_real).parts
        except ValueError:
            return None

    def repo_rel(self, path: Path) -> str | None:
        real = Path(path).resolve()
        try:
            inner = real.relative_to(self.queue_real)
        except ValueError:
            pass
        else:
            return posixpath.join(self.queue_rel, inner.as_posix()) if inner.parts else self.queue_rel
        try:
            return real.relative_to(self.root).as_posix()
        except ValueError:
            return None


def _archive_names(project_root: Path, config: Config) -> set[str]:
    """queue を歩くときに刈り込む archive ディレクトリ名（``scan`` と同じく basename で判定）。"""
    names = {"archive"}
    for value in (
        config.archive_dir,
        archive_dir_for_project(project_root, config),
        *(type_def.archive_dir for type_def in config.types),
    ):
        parts = [part for part in str(value or "").replace("\\", "/").split("/") if part]
        if parts:
            names.add(parts[-1])
    return names


def _rel_key(value: str) -> str | None:
    text = value.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if not text:
        return None
    norm = posixpath.normpath(text)
    return norm.casefold() if os.name == "nt" else norm


def _is_absolute(value: str) -> bool:
    return Path(value).is_absolute() or bool(PureWindowsPath(value).drive) or value.startswith("/")


def _abs_key(value: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(Path(value).resolve())))


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


class _MoveTable:
    def __init__(self, moves: list[Move], paths: ProjectPaths) -> None:
        self.by_rel: dict[str, str] = {}
        self.by_abs: dict[str, str] = {}
        self.body_pairs: list[tuple[str, str]] = []
        for move in moves:
            old_rel = paths.repo_rel(move.src)
            new_rel = paths.repo_rel(move.dst)
            if old_rel is None or new_rel is None or old_rel == new_rel:
                continue
            key = _rel_key(old_rel)
            if key is None:
                continue
            self.by_rel[key] = new_rel
            self.by_abs[_abs_key(move.src)] = (paths.root / new_rel).as_posix()
            self.body_pairs.append((old_rel, new_rel))

    def __bool__(self) -> bool:
        return bool(self.by_rel)

    def parent(self, value: object) -> str | None:
        if not isinstance(value, str) or _is_absolute(value.strip()):
            return None
        key = _rel_key(value)
        return self.by_rel.get(key) if key else None

    def related_item(self, value: str) -> str | None:
        text = value.strip()
        if "/" not in text and "\\" not in text:
            return None
        if _is_absolute(text):
            return self.by_abs.get(_abs_key(text))
        if text.startswith(("./", "../", ".\\", "..\\")):
            return None
        key = _rel_key(text)
        return self.by_rel.get(key) if key else None

    def body_pattern(self) -> re.Pattern[str] | None:
        if not self.body_pairs:
            return None
        olds = sorted({old for old, _new in self.body_pairs}, key=len, reverse=True)
        alternation = "|".join(re.escape(old) for old in olds)
        return re.compile(
            rf"(?<![{_PATH_CHARS}])(?:{alternation})(?![A-Za-z0-9_\-/\\]|\.[A-Za-z0-9_])"
        )

    def body_new(self, old: str) -> str:
        return next(new for candidate, new in self.body_pairs if candidate == old)


def _logical(paths: ProjectPaths, path: Path) -> str | None:
    """repo の下の見かけの絶対パス（queue が junction でも、リンクは repo から見た場所で解決する）。"""
    rel = paths.repo_rel(path)
    if rel is None:
        return None
    return os.path.normpath(os.path.join(str(paths.root), *rel.split("/")))


class _LinkTable:
    """本文の相対リンクを解決するための、移動前後の場所（どちらも repo の下の見かけの絶対パス）。"""

    def __init__(self, moves: list[Move], paths: ProjectPaths, *, dry_run: bool) -> None:
        self.paths = paths
        # 移動元（normcase したキー）→ 移動先
        self.moved_to: dict[str, str] = {}
        # 移動先（normcase したキー）。移す前には無かった場所なので、ここを指すリンクは元から切れている
        self.new_places: set[str] = set()
        # 今その文書がある実体パス（dry-run は移動元・本実行は移動先）→ (移動元, 移動先)
        self.docs: dict[str, tuple[str, str]] = {}
        for move in moves:
            old = _logical(paths, move.src)
            new = _logical(paths, move.dst)
            if old is None or new is None or os.path.normcase(old) == os.path.normcase(new):
                continue
            self.moved_to[os.path.normcase(old)] = new
            self.new_places.add(os.path.normcase(new))
            self.docs[_abs_key(move.src if dry_run else move.dst)] = (old, new)

    def dirs_for(self, path: Path) -> tuple[str, str, bool] | None:
        """文書の (移す前のフォルダ, 移した後のフォルダ, 別のフォルダへ移した文書か)。"""
        moved = self.docs.get(_abs_key(path))
        if moved is not None:
            before, after = os.path.dirname(moved[0]), os.path.dirname(moved[1])
            return before, after, os.path.normcase(before) != os.path.normcase(after)
        here = _logical(self.paths, path)
        if here is None:
            return None
        folder = os.path.dirname(here)
        return folder, folder, False

    def target(self, before_dir: str, target: str, *, moved: bool) -> str | None:
        """リンク先の、移動後の場所。このリンクを書き換えの対象にしないときは ``None``。

        移した文書を指すリンクは、どの文書からでも移動先へ。移した文書自身のリンクは、
        移す前に実在した先だけ（移動先の場所は移す前には無かったので除く）。
        """
        resolved = os.path.normpath(os.path.join(before_dir, target))
        key = os.path.normcase(resolved)
        if key in self.moved_to:
            return self.moved_to[key]
        if not moved or key in self.new_places:
            return None
        return resolved if os.path.exists(resolved) else None


@dataclass(frozen=True)
class _Edit:
    """本文の 1 か所の置き換え（``start``/``end`` は置き換え前の全文での位置）。"""

    start: int
    end: int
    old: str
    new: str


def _escaped(text: str, index: int) -> bool:
    count = 0
    while index - count > 0 and text[index - count - 1] == "\\":
        count += 1
    return count % 2 == 1


def _closes_link_text(masked: str, close: int, floor: int) -> bool:
    """``masked[close]`` の ``]`` が、同じ段落の ``[`` と対になるか（リンクのテキストの終わりか）。"""
    if _escaped(masked, close):
        return False
    limit = max(floor, masked.rfind("\n\n", 0, close), masked.rfind("\n\r\n", 0, close))
    depth = 0
    for index in range(close - 1, limit - 1, -1):
        char = masked[index]
        if char not in "[]" or _escaped(masked, index):
            continue
        if char == "]":
            depth += 1
        elif depth:
            depth -= 1
        else:
            return True
    return False


def _inline_dest(masked: str, pos: int) -> tuple[int, int, bool] | None:
    """``](`` の直後 ``pos`` からリンク先を読み、(開始, 終了, ``<...>`` 囲みか) を返す。

    タイトル（``"..."`` / ``'...'`` / ``(...)``）を挟んで ``)`` で閉じていなければ、リンクとは
    見なさず ``None``。
    """
    size = len(masked)
    index = pos
    while index < size and masked[index] in " \t":
        index += 1
    if index < size and masked[index] == "<":
        close = index + 1
        while close < size and masked[close] not in "<>\n":
            close += 1
        if close >= size or masked[close] != ">":
            return None
        dest = (index + 1, close, True)
        index = close + 1
    else:
        depth = 0
        end = index
        while end < size:
            char = masked[end]
            if char == "\\" and end + 1 < size:
                end += 2
                continue
            if char.isspace():
                break
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            end += 1
        if depth:
            return None
        dest = (index, end, False)
        index = end
    after_dest = index
    while index < size and masked[index] in " \t":
        index += 1
    if index > after_dest and index < size and masked[index] in "\"'(":
        closer = ")" if masked[index] == "(" else masked[index]
        close = masked.find(closer, index + 1)
        if close < 0 or "\n" in masked[index:close]:
            return None
        index = close + 1
        while index < size and masked[index] in " \t":
            index += 1
    if index >= size or masked[index] != ")":
        return None
    return dest


def _link_targets(masked: str, start: int) -> list[tuple[int, int, bool]]:
    """本文（``start`` から後ろ）の Markdown リンク先の (開始, 終了, ``<...>`` 囲みか)。"""
    found: list[tuple[int, int, bool]] = []
    for match in _INLINE_OPEN_RE.finditer(masked, start):
        if not _closes_link_text(masked, match.start(), start):
            continue
        dest = _inline_dest(masked, match.end())
        if dest is not None:
            found.append(dest)
    for match in _REF_DEF_RE.finditer(masked, start):
        group = "angle" if match.group("angle") is not None else "bare"
        found.append((match.start(group), match.end(group), group == "angle"))
    return sorted(found)


def _relative_path_part(raw: str) -> str | None:
    """リンク先のうち、書き換えの対象になる相対パスの部分（``#`` ``?`` より前）。

    空・``#`` だけ・``?`` だけ・``/`` で始まる（``//`` を含む）・スキーム付き（URL・ドライブ名）・
    ``\\`` を含む（Markdown ではエスケープ）ものは対象にしない。
    """
    if not raw or raw.startswith(("#", "?", "/")) or "\\" in raw or _SCHEME_RE.match(raw):
        return None
    cuts = [index for index in (raw.find("#"), raw.find("?")) if index >= 0]
    path = raw[:min(cuts)] if cuts else raw
    return path or None


def _encode_like(original: str, new: str, *, angle: bool) -> str | None:
    """新しいパスを、元のリンク先の書き方（``%`` エスケープ・``<...>`` 囲み）に合わせる。"""
    if angle:
        return None if any(char in "<>\r\n" for char in new) else new
    escape_percent = "%" in original
    escape_non_ascii = bool(_ENCODED_NON_ASCII_RE.search(original))
    pieces: list[str] = []
    for char in new:
        if (
            char.isspace()
            or char in "<>"
            or (escape_percent and char == "%")
            or (escape_non_ascii and ord(char) > 0x7F)
        ):
            pieces.append(quote(char, safe=""))
        else:
            pieces.append(char)
    return "".join(pieces)


def _new_link_path(
    old: str, links: _LinkTable, place: tuple[str, str, bool], *, angle: bool
) -> str | None:
    """相対リンクのパス部分 ``old`` の書き換え後。対象外なら ``None``（変わらなければ ``old``）。"""
    before_dir, after_dir, moved = place
    decoded = unquote(old) if "%" in old else old
    new_abs = links.target(before_dir, decoded, moved=moved)
    if new_abs is None:
        return None
    try:
        rel = os.path.relpath(new_abs, after_dir).replace(os.sep, "/")
    except ValueError:  # 別のドライブ
        return None
    if old.endswith("/") and not rel.endswith("/"):
        rel += "/"
    if old.startswith("./") and not rel.startswith(("./", "../")) and rel not in (".", ".."):
        rel = "./" + rel
    return _encode_like(old, rel, angle=angle)


def _link_edits(
    text: str, start: int, links: _LinkTable, path: Path
) -> tuple[list[_Edit], list[tuple[int, int]]]:
    """本文の相対リンクの置き換えと、相対リンクとして解決できた箇所（書き換えなしも含む）。"""
    place = links.dirs_for(path)
    if place is None:
        return [], []
    masked = text[:start] + mask_inline_code(mask_code_fences(text[start:]))
    edits: list[_Edit] = []
    claimed: list[tuple[int, int]] = []
    for begin, end, angle in _link_targets(masked, start):
        raw = text[begin:end]
        if masked[begin:end] != raw:
            continue
        old = _relative_path_part(raw)
        if old is None:
            continue
        new = _new_link_path(old, links, place, angle=angle)
        if new is None:
            continue
        claimed.append((begin, begin + len(old)))
        if new != old:
            edits.append(_Edit(begin, begin + len(old), old, new))
    return edits, claimed


def _path_edits(text: str, start: int, table: _MoveTable) -> list[_Edit]:
    """本文の repo 相対の旧パス（完全一致）の置き換え。"""
    pattern = table.body_pattern()
    if pattern is None:
        return []
    return [
        _Edit(match.start(), match.end(), match.group(0), table.body_new(match.group(0)))
        for match in pattern.finditer(text, start)
    ]


def _overlaps(edit: _Edit, spans: Iterable[tuple[int, int]]) -> bool:
    return any(edit.start < end and begin < edit.end for begin, end in spans)


def _iter_candidates(paths: ProjectPaths, extra: Iterable[Path]) -> list[Path]:
    """queue の ``.md``（archive を除く）と、同じ操作で動いた文書。"""
    found: dict[str, Path] = {}
    if paths.queue_real.is_dir():
        for dirpath, dirnames, filenames in os.walk(paths.queue_real):
            dirnames[:] = sorted(
                d for d in dirnames if d not in ALWAYS_SKIP_DIRS and d not in paths.archive_names
            )
            current = Path(dirpath)
            for filename in sorted(filenames):
                if filename.lower().endswith(".md"):
                    candidate = current / filename
                    found.setdefault(_path_key(candidate), candidate)
    for candidate in extra:
        path = Path(candidate)
        if path.is_file() and path.name.lower().endswith(".md"):
            found.setdefault(_path_key(path), path)
    return list(found.values())


def _frontmatter_updates(path: Path, text: str, table: _MoveTable) -> list[RefUpdate]:
    data, _body = read_frontmatter_text(text)
    if not data:
        return []
    updates: list[RefUpdate] = []
    parent = data.get("docsweep_parent")
    new_parent = table.parent(parent)
    if isinstance(parent, str) and new_parent and new_parent != parent.strip():
        updates.append(RefUpdate(path.as_posix(), "docsweep_parent", parent, new_parent))
    related = data.get("related")
    if isinstance(related, list):
        before = [str(item) for item in related if item is not None]
        after: list[str] = []
        changed = False
        for item in before:
            new_item = table.related_item(item)
            if new_item and new_item != item:
                after.append(new_item)
                changed = True
            else:
                after.append(item)
        if changed:
            updates.append(RefUpdate(path.as_posix(), "related", before, after))
    return updates


def _body_rewrite(
    text: str, table: _MoveTable, links: _LinkTable, path: Path, *, repo_paths: bool
) -> tuple[str, RefUpdate | None]:
    """本文の相対リンク（``repo_paths`` なら repo 相対の旧パスも）を置き換えた全文と、その記録。

    offsets は置き換え後の全文での位置。相対リンクとして解決できた箇所は、repo 相対の
    置き換えから除く（同じ箇所を二重に書き換えない）。
    """
    _data, body = read_frontmatter_text(text)
    start = len(text) - len(body)
    edits, claimed = _link_edits(text, start, links, path)
    if repo_paths:
        spans = claimed + [(edit.start, edit.end) for edit in edits]
        edits += [edit for edit in _path_edits(text, start, table) if not _overlaps(edit, spans)]
    accepted: list[_Edit] = []
    for edit in edits:
        if not _overlaps(edit, [(done.start, done.end) for done in accepted]):
            accepted.append(edit)
    if not accepted:
        return text, None
    accepted.sort(key=lambda edit: edit.start)
    pieces: list[str] = []
    before: list[str] = []
    after: list[str] = []
    offsets: list[int] = []
    cursor = 0
    length = 0
    for edit in accepted:
        pieces.append(text[cursor:edit.start])
        length += edit.start - cursor
        offsets.append(length)
        pieces.append(edit.new)
        length += len(edit.new)
        before.append(edit.old)
        after.append(edit.new)
        cursor = edit.end
    pieces.append(text[cursor:])
    return "".join(pieces), RefUpdate("", "body", before, after, offsets)


def _log(root: Path, project: str, update: RefUpdate, *, batch_id: str | None, op: str) -> None:
    with _move_log_lock(root):
        append_move_log(
            root,
            MoveLogEntry(
                ts=_now_iso(), op=op, project=project, status=None,
                src=update.path, dst=None, batch_id=batch_id,
                field=update.field, before=update.before, after=update.after,
                offsets=update.offsets,
            ),
        )


def rewrite_refs(
    moves: list[Move],
    *,
    project_root: Path,
    config: Config,
    root: Path,
    project: str,
    batch_id: str | None = None,
    dry_run: bool = False,
    body: bool | None = None,
) -> RefRewriteResult:
    """``moves`` に合わせて同じ project の文書の参照を書き換える。

    ``dry_run`` では何も書かず、予定だけを返す。書き換えた文書は 1 フィールドごとに
    移動ログへ残す（``batch_id`` があれば ``undo`` で戻せる）。

    ``body`` は本文の扱い:

    - ``None``（既定。archive へ移す経路）: Markdown の相対リンクだけ移動に追従させる
    - ``True``（``docsweep mv``）: 相対リンクに加えて、repo 相対の旧パスの完全一致も書き換える
    - ``False``（``docsweep mv --no-body``）: 本文には触らない（frontmatter の参照だけ）
    """
    result = RefRewriteResult()
    if not moves:
        return result
    try:
        paths = ProjectPaths(project_root, config)
    except (OSError, ValueError) as exc:
        result.failed.append({"path": None, "field": None, "error": str(exc)})
        return result
    table = _MoveTable(moves, paths)
    if not table:
        return result
    links = _LinkTable(moves, paths, dry_run=dry_run)
    moved_now = [move.src if dry_run else move.dst for move in moves]
    for path in _iter_candidates(paths, moved_now):
        try:
            _rewrite_one(path, table, links, result, root=root, project=project,
                         batch_id=batch_id, dry_run=dry_run, body=body)
        except (OSError, UnicodeError, ValueError, ConflictError) as exc:
            result.failed.append({"path": path.as_posix(), "field": None, "error": str(exc)})
    return result


def _rewrite_one(
    path: Path,
    table: _MoveTable,
    links: _LinkTable,
    result: RefRewriteResult,
    *,
    root: Path,
    project: str,
    batch_id: str | None,
    dry_run: bool,
    body: bool | None,
) -> None:
    text = path.open("r", encoding="utf-8", newline="").read()
    mtime = path.stat().st_mtime
    for update in _frontmatter_updates(path, text, table):
        if dry_run:
            result.updates.append(update)
            continue
        try:
            written = update_frontmatter_field(path, update.field, update.after, expected_mtime=mtime)
        except (OSError, UnicodeError, ValueError, ConflictError) as exc:
            result.failed.append({"path": update.path, "field": update.field, "error": str(exc)})
            return
        mtime = written.new_mtime
        _log(root, project, update, batch_id=batch_id, op="ref_rewrite")
        result.updates.append(update)
    if body is False:
        return
    if not dry_run:
        text = path.open("r", encoding="utf-8", newline="").read()
    new_text, body_update = _body_rewrite(text, table, links, path, repo_paths=body is True)
    if body_update is None:
        return
    body_update.path = path.as_posix()
    if not dry_run:
        try:
            write_atomic(path, new_text, expected_mtime=mtime)
        except (OSError, UnicodeError, ConflictError) as exc:
            result.failed.append({"path": body_update.path, "field": "body", "error": str(exc)})
            return
        _log(root, project, body_update, batch_id=batch_id, op="ref_rewrite")
    result.updates.append(body_update)


def rewrite_refs_by_project(
    moves: list[tuple[Move, str, Path, str]],
    *,
    config: Config,
    batch_id: str | None = None,
    dry_run: bool = False,
    body: bool | None = None,
) -> RefRewriteResult:
    """``(move, project_root, root, project)`` の並びを project ごとにまとめて書き換える。

    ``body`` の意味は :func:`rewrite_refs` と同じ。
    """
    groups: dict[tuple[str, str, str], list[Move]] = {}
    for move, project_root, log_root, project in moves:
        groups.setdefault((project_root, str(log_root), project), []).append(move)
    result = RefRewriteResult()
    for (group_project_root, group_root, group_project), group in groups.items():
        result.extend(
            rewrite_refs(
                group, project_root=Path(group_project_root), config=config,
                root=Path(group_root), project=group_project, batch_id=batch_id,
                dry_run=dry_run, body=body,
            )
        )
    return result


# ------------------------------------------------------------------
# undo
# ------------------------------------------------------------------


def _as_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def revert_ref_rewrite(entry: dict) -> RefUpdate:
    """移動ログの ``ref_rewrite`` 1 行を元へ戻し、取り消した書き換えを返す。

    返り値は ``entry`` と同じ前後の値を持つ（``ref_restore`` の行を ``ref_rewrite`` の行と
    突き合わせて二重に戻さないため）。書き換えの後に人や別の操作がその値を変えていたら、
    上書きせずに例外で止める。
    """
    path = Path(str(entry.get("src") or ""))
    field_name = str(entry.get("field") or "")
    before = entry.get("before")
    after = entry.get("after")
    undone = RefUpdate(
        path.as_posix(), field_name, _restore_value(before), _restore_value(after),
        entry.get("offsets") if isinstance(entry.get("offsets"), list) else None,
    )
    if not path.is_file():
        raise FileNotFoundError(t("move_refs.restore_target_missing", path=path))
    text = path.open("r", encoding="utf-8", newline="").read()
    mtime = path.stat().st_mtime
    if field_name in REF_FIELDS:
        data, _body = read_frontmatter_text(text)
        current = data.get(field_name)
        if field_name == "related":
            if _as_str_list(current) != _as_str_list(after):
                raise ValueError(t("move_refs.related_changed", path=path))
            restored: str | list[str] = _as_str_list(before)
        else:
            if not isinstance(current, str) or current.strip() != str(after or "").strip():
                raise ValueError(t("move_refs.parent_changed", path=path))
            restored = str(before or "")
        update_frontmatter_field(path, field_name, restored, expected_mtime=mtime)
        return undone
    if field_name == "body":
        olds = _as_str_list(before)
        news = _as_str_list(after)
        offsets = entry.get("offsets")
        if (
            not isinstance(offsets, list)
            or len(offsets) != len(olds)
            or len(olds) != len(news)
        ):
            raise ValueError(t("move_refs.body_record_broken", path=path))
        new_text = text
        for offset, old, new in sorted(zip(offsets, olds, news, strict=True), reverse=True):
            if not isinstance(offset, int) or new_text[offset:offset + len(new)] != new:
                raise ValueError(t("move_refs.body_changed", path=path))
            new_text = new_text[:offset] + old + new_text[offset + len(new):]
        write_atomic(path, new_text, expected_mtime=mtime)
        return undone
    raise ValueError(t("move_refs.unknown_rewrite_kind", field=field_name))


def _restore_value(value: object) -> str | list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return "" if value is None else str(value)


def log_ref_restore(root: Path, project: str, update: RefUpdate, *, batch_id: str | None) -> None:
    _log(root, project, update, batch_id=batch_id, op="ref_restore")
