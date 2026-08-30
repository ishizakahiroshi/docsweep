"""Read-only closeout inspection for a parent plan and its child plans.

The closeout service deliberately does not call any write or archive service.  It
answers one question only: which mechanical conditions and human gates remain
before a parent/child plan may be moved to a requested state?
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config, DEFAULT_PROJECT_MARKERS, load_config
from .detect import Detection, detect_status, mask_code_fences
from .linkcheck import _extract_files_from_section, _extract_section
from .scan import ALWAYS_SKIP_DIRS, _is_ignored, _read_gitignore
from .services.frontmatter import read_frontmatter_text


class CloseoutInputError(ValueError):
    """Raised when the explicit closeout target cannot be inspected safely."""


_HEADING_RE = re.compile(r"^(?P<marks>#{2,6})[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?\[(?P<mark>[ xX])\]\s*(?P<body>.*)$")
_TODO_RE = re.compile(r"(?:<\s*TODO[^>]*>|\bTODO\b|\bTBD\b|未記入|未定)", re.IGNORECASE)
# 失敗語そのもの。``continue-on-error`` のような識別子の一部を拾わないよう、
# 前後をハイフン・語構成文字で挟まれた出現は除く。
_FAILED_RE = re.compile(
    r"(?:失敗|エラー|(?<![-\w])(?:failed|failure|failures|error|errors)(?![-\w]))",
    re.IGNORECASE,
)
# 失敗語に件数が結び付いた形。``0 failed`` / ``failed: 0`` / ``失敗 3 件`` を数値ごと拾う。
# ここで 0 と正の数を分けるのが分類の要で、``failed`` の出現有無だけでは決めない。
_FAILURE_COUNT_RE = re.compile(
    r"(?:"
    r"(?P<pre>\d+)\s*(?:件)?\s*(?:の)?\s*"
    r"(?:失敗|エラー|(?<![-\w])(?:failed|failures?|errors?)(?![-\w]))"
    r"|"
    r"(?:失敗|エラー|(?<![-\w])(?:failed|failures?|errors?)(?![-\w]))"
    r"\s*(?:数)?\s*[:=]?\s*(?P<post>\d+)\s*(?:件)?"
    r")",
    re.IGNORECASE,
)
# 件数を書かずに「無い」と言う形。``0 件`` 単独や ``未検出`` のような曖昧語は含めない
# （それらは成功の証跡ではなく manual review へ回す）。
_ZERO_FAILURE_RE = re.compile(
    r"(?:"
    r"(?:失敗|エラー)\s*(?:は)?\s*(?:なし|無し|ありません|無い)"
    r"|no\s+(?:failed|failures?|errors?)(?![-\w])"
    r"|(?<![-\w])(?:failed|failures?|errors?)(?![-\w])\s*[:=]?\s*none"
    r")",
    re.IGNORECASE,
)
_NOT_RUN_RE = re.compile(r"(?:未実施|未検証|not[ _-]?run|not[ _-]?tested|未確認)", re.IGNORECASE)
_AUTO_RE = re.compile(
    r"(?:自動(?:確認|検証|テスト)?|automated?|\bpytest\b|\bruff\b|\bmypy\b|\bunittest\b|\bCI\b|静的(?:検査|確認))",
    re.IGNORECASE,
)
_MANUAL_RE = re.compile(
    r"(?:手動(?:確認|受入|検証)?|manual|ブラウザ|browser|目視|実機|本番|production|Obsidian|Google Drive|Drive)",
    re.IGNORECASE,
)
_SUCCESS_RE = re.compile(r"(?:成功|通過|合格|完了|確認済|済み|\bpass(?:ed)?\b|\bok\b|\bdone\b|\[x\])", re.IGNORECASE)
_COMPLETION_RE = re.compile(r"(?:完了条件|completion[ _-]?criteria|definition[ _-]?of[ _-]?done)", re.IGNORECASE)
_VERIFICATION_RE = re.compile(r"(?:検証|verification|verify|test(?:ing)?)", re.IGNORECASE)
_ACCEPTANCE_RE = re.compile(r"(?:受入|受け入れ|acceptance)", re.IGNORECASE)
_CHANGED_FILES_RE = re.compile(r"(?:変更予定ファイル|変更ファイル|changed?[ _-]?files?)", re.IGNORECASE)
_LEGACY_CHILD_RE_TEMPLATE = r"^{parent}_c\d+(?:_|$)"

# The standard state order is intentionally local to this read-only service.
# Custom state models still work for parsing, while unsupported closeout target
# states are reported as blockers instead of being guessed.
_STATE_RANK = {
    "pending": 0,
    "planned": 1,
    "in-progress": 2,
    "watching": 3,
    "done": 4,
    "discarded": 5,
}
_CLOSEOUT_TYPES = {"plan", "bugfix"}


@dataclass
class _CloseoutDoc:
    path: Path
    text: str
    data: dict[str, Any] | None
    detection: Detection
    type_name: str | None
    mtime: float
    sections: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return _path_key(self.path)


def _lexical_path(value: str | Path, *, base: Path | None = None) -> Path:
    """Normalize a path without resolving junction targets.

    ``docs/local`` is allowed to be a junction.  Keeping the lexical path here
    lets the service enforce the repository boundary while still reading the
    junction target through the normal filesystem API.
    """
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base or Path.cwd()) / path
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.fspath(path)))


def _is_under(path: Path, root: Path) -> bool:
    try:
        _lexical_path(path).relative_to(_lexical_path(root))
        return True
    except ValueError:
        return False


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _find_project_root(parent: Path, explicit: Path | None, markers: list[str]) -> Path:
    if explicit is not None:
        root = _lexical_path(explicit)
        if not root.is_dir():
            raise CloseoutInputError(f"project-dir がディレクトリではありません: {root}")
        if not _is_under(parent, root):
            raise CloseoutInputError(
                f"親 plan が project-dir の外側です: {parent} (project-dir={root})"
            )
        return root

    cur = parent.parent
    while True:
        if any((cur / marker).exists() for marker in markers):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent

    # Small temporary fixtures often omit a project marker.  The conventional
    # docs/local shape gives a bounded, deterministic fallback.
    cur = parent.parent
    while cur != cur.parent:
        if cur.name.lower() == "docs":
            return cur.parent
        cur = cur.parent
    return parent.parent


def _sections(text: str) -> list[tuple[str, str, str]]:
    """Return ``(title, body, full_heading)`` for level 2+ Markdown sections."""
    masked = mask_code_fences(text)
    matches = list(_HEADING_RE.finditer(masked))
    result: list[tuple[str, str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        title = match.group("title").strip().rstrip("\r")
        body = text[match.end():end]
        result.append((title, body, match.group(0).rstrip("\r")))
    return result


def _read_doc(path: Path, config: Config) -> _CloseoutDoc:
    try:
        text = path.open("r", encoding="utf-8", newline="").read()
    except (OSError, UnicodeError) as exc:
        raise CloseoutInputError(f"plan を読み込めません: {path}: {exc}") from exc
    data, body = read_frontmatter_text(text)
    fm = data if body != text and isinstance(data, dict) else None
    type_def = config.match_type(path.name)
    detection = detect_status(
        text=text,
        filename=path.name,
        sm=config.state_model,
        _type=type_def,
    )
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        raise CloseoutInputError(f"plan の stat を取得できません: {path}: {exc}") from exc
    return _CloseoutDoc(
        path=path,
        text=text,
        data=fm,
        detection=detection,
        type_name=type_def.name if type_def else None,
        mtime=mtime,
        sections=_sections(text),
    )


def _frontmatter_parent(doc: _CloseoutDoc) -> tuple[list[str], str | None]:
    if not doc.data or "docsweep_parent" not in doc.data:
        return [], None
    raw = doc.data.get("docsweep_parent")
    if isinstance(raw, (list, tuple)):
        values = [str(value).strip() for value in raw if str(value).strip()]
        return values, "multiple" if values else None
    if raw is None or not str(raw).strip():
        return [], None
    if not isinstance(raw, str):
        return [str(raw).strip()], "invalid"
    return [raw.strip()], "explicit"


def _related_values(doc: _CloseoutDoc) -> list[str]:
    if not doc.data:
        return list(doc.detection.related)
    raw = doc.data.get("related")
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(value).strip() for value in raw if str(value).strip()]
    return list(doc.detection.related)


def _iter_plan_paths(project_root: Path, anchor: Path, config: Config) -> list[Path]:
    """Find plan candidates inside the project, including ignored docs/local.

    The project boundary is the hard limit.  The anchor is walked separately so
    a docs/local junction is not lost by ``os.walk``'s default no-follow-links
    behavior.  Archive and VCS/dependency trees are pruned.
    """
    roots: list[Path] = [project_root]
    if _path_key(anchor) != _path_key(project_root) and anchor.is_dir():
        roots.append(anchor)
    found: dict[str, Path] = {}
    archive_names = {str(config.archive_dir or "archive").replace("\\", "/").rstrip("/").split("/")[-1]}
    archive_names.update(
        str(t.archive_dir).replace("\\", "/").rstrip("/").split("/")[-1]
        for t in config.types
        if t.archive_dir
    )
    ignore_patterns = list(config.ignore)
    if config.use_gitignore:
        ignore_patterns.extend(_read_gitignore(project_root))
    anchor = _lexical_path(anchor)

    def _anchor_scope(candidate: Path) -> bool:
        return _is_under(anchor, candidate) or _is_under(candidate, anchor)

    for start in roots:
        if not start.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
            current = Path(dirpath)
            rel_dir = _relative(current, project_root)
            kept_dirs: list[str] = []
            for name in dirnames:
                child = current / name
                rel = f"{rel_dir}/{name}" if rel_dir != "." else name
                if name in ALWAYS_SKIP_DIRS or name in archive_names:
                    continue
                if _is_ignored(rel, name, ignore_patterns) and not _anchor_scope(child):
                    continue
                kept_dirs.append(name)
            dirnames[:] = kept_dirs
            for filename in filenames:
                type_def = config.match_type(filename)
                if type_def is None or type_def.name != "plan":
                    continue
                path = current / filename
                rel = path.relative_to(project_root).as_posix()
                if _is_ignored(rel, filename, ignore_patterns) and not _is_under(current, anchor):
                    continue
                found.setdefault(_path_key(path), path)
    return sorted(found.values(), key=lambda path: path.as_posix().lower())


def _resolve_ref(
    ref: str,
    source: _CloseoutDoc,
    *,
    project_root: Path,
    docs: dict[str, _CloseoutDoc],
) -> list[_CloseoutDoc]:
    """Resolve a repo-relative, source-relative, absolute, or basename ref."""
    value = ref.strip()
    if not value:
        return []
    candidates: list[Path] = []
    raw_path = Path(value)
    if raw_path.is_absolute():
        candidates.append(_lexical_path(raw_path))
    else:
        candidates.append(_lexical_path(raw_path, base=project_root))
        candidates.append(_lexical_path(raw_path, base=source.path.parent))
    result: dict[str, _CloseoutDoc] = {}
    for candidate in candidates:
        doc = docs.get(_path_key(candidate))
        if doc:
            result[doc.key] = doc
    if result:
        return list(result.values())

    basename = raw_path.name
    for doc in docs.values():
        if doc.path.name == basename:
            result[doc.key] = doc
    return list(result.values())


def _resolve_parent_ref(
    ref: str,
    *,
    project_root: Path,
    docs: dict[str, _CloseoutDoc],
) -> tuple[list[_CloseoutDoc], str | None]:
    """Resolve the direction-specific parent key as a repo-relative path only."""
    value = ref.strip()
    if not value:
        return [], "outside_parent"
    raw_path = Path(value)
    if raw_path.is_absolute():
        return [], "outside_parent"
    candidate = _lexical_path(raw_path, base=project_root)
    if not _is_under(candidate, project_root):
        return [], "outside_parent"
    doc = docs.get(_path_key(candidate))
    return ([doc] if doc else []), None


def _legacy_child_name(child: _CloseoutDoc, parent: _CloseoutDoc) -> bool:
    pattern = re.compile(
        _LEGACY_CHILD_RE_TEMPLATE.format(parent=re.escape(parent.path.stem)),
        re.IGNORECASE,
    )
    return bool(pattern.match(child.path.stem))


def _related_points_to(
    child: _CloseoutDoc,
    parent: _CloseoutDoc,
    *,
    project_root: Path,
    docs: dict[str, _CloseoutDoc],
) -> tuple[bool, bool]:
    """Return ``(points_to_parent, ambiguous)`` for legacy inference."""
    matched = False
    ambiguous = False
    for ref in _related_values(child):
        resolved = _resolve_ref(ref, child, project_root=project_root, docs=docs)
        if len(resolved) > 1:
            ambiguous = True
        if any(doc.key == parent.key for doc in resolved):
            matched = True
    return matched, ambiguous


def _section_kind(title: str) -> str | None:
    if _COMPLETION_RE.search(title):
        return "completion"
    if _ACCEPTANCE_RE.search(title):
        return "acceptance"
    if _CHANGED_FILES_RE.search(title):
        return "changed_files"
    if _VERIFICATION_RE.search(title):
        return "verification"
    return None


def _relevant_sections(doc: _CloseoutDoc) -> dict[str, list[tuple[str, str]]]:
    result: dict[str, list[tuple[str, str]]] = {
        "completion": [], "verification": [], "acceptance": [], "changed_files": [],
    }
    for title, body, _heading in doc.sections:
        kind = _section_kind(title)
        if kind:
            result[kind].append((title, body))
    return result


def _line_items(body: str) -> list[str]:
    return [line.strip() for line in body.splitlines() if line.strip()]


def _failure_counts(line: str) -> list[int]:
    """行の中で失敗語に結び付いている件数を全て返す。"""
    counts: list[int] = []
    for m in _FAILURE_COUNT_RE.finditer(line):
        raw = m.group("pre") or m.group("post")
        if raw is None:
            continue
        try:
            counts.append(int(raw))
        except ValueError:
            continue
    return counts


def _classify_verification(section: str, line: str) -> str:
    """検証行を ``not_run`` / ``failed`` / ``passed`` / ``claimed`` へ分類する。

    判定の優先順位は次のとおり。``failed`` を最初に見る旧実装は ``pytest: 0 failed``
    や ``エラーなし`` という **成功の証跡** を失敗と読み、closeout を止めていた。

    1. 明示的な未実施（``未検証`` / ``not tested``）
    2. 正の失敗件数（``1 failed`` / ``errors: 2``）
    3. 件数ゼロの失敗 summary（``0 failed`` / ``エラーなし``）— 検証 section の
       機械結果としてだけ ``passed`` へ昇格する
    4. 件数の無い失敗語（``failed`` / ``エラー`` 単独）
    5. 明示的な成功
    6. それ以外は ``claimed``

    ``未検出`` / ``空集合`` / ``0件`` / ``vulnerability 0`` のような曖昧な散文は
    どれにも当たらず ``claimed`` に落ちる。自動 pass へ昇格させない境界は動かさない。
    """
    if _NOT_RUN_RE.search(line):
        return "not_run"

    counts = _failure_counts(line)
    if any(c > 0 for c in counts):
        return "failed"

    zero_failure = bool(counts) or bool(_ZERO_FAILURE_RE.search(line))
    if zero_failure:
        # ゼロ失敗 summary は「機械が出した検証結果」のときだけ成功と見なす。
        if _section_kind(section) == "verification" and _AUTO_RE.search(line):
            return "passed"
        return "claimed" if not _SUCCESS_RE.search(line) else "passed"

    if _FAILED_RE.search(line):
        return "failed"
    if _SUCCESS_RE.search(line):
        return "passed"
    return "claimed"


def _verification_entry(path: Path, section: str, line: str) -> dict[str, str]:
    status = _classify_verification(section, line)
    if _MANUAL_RE.search(line):
        kind = "manual"
    elif _AUTO_RE.search(line):
        kind = "automatic"
    else:
        kind = "unspecified"
    return {
        "path": path.as_posix(),
        "section": section,
        "evidence": line,
        "kind": kind,
        "status": status,
    }


def _manual_check(path: Path, section: str, line: str, *, reason: str) -> dict[str, str]:
    return {
        "path": path.as_posix(),
        "section": section,
        "description": line or section,
        "reason": reason,
        "status": "pending",
    }


def _inspect_document(
    doc: _CloseoutDoc,
    *,
    target_state: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    sections = _relevant_sections(doc)
    blockers: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    if doc.type_name != "plan":
        blockers.append({
            "code": "not_plan",
            "path": doc.path.as_posix(),
            "message": "親子 closeout の対象は type: plan です",
        })
    if doc.detection.parse_error or doc.detection.state_key is None:
        blockers.append({
            "code": "state_unresolved",
            "path": doc.path.as_posix(),
            "message": "H1 / docsweep_state から作業状態を解決できません",
        })
    if doc.detection.conflict or any("作業状態" in warning for warning in doc.detection.frontmatter_warnings):
        blockers.append({
            "code": "state_conflict",
            "path": doc.path.as_posix(),
            "message": "H1 と frontmatter の作業状態が一致しません",
        })
    if doc.detection.type_conflict:
        blockers.append({
            "code": "type_conflict",
            "path": doc.path.as_posix(),
            "message": "filename と frontmatter の type が一致しません",
        })
    if doc.detection.due_parse_error:
        blockers.append({
            "code": "due_invalid",
            "path": doc.path.as_posix(),
            "message": "due を YYYY-MM-DD として解釈できません",
        })

    required = ["completion", "verification"]
    if target_state == "done":
        required.append("acceptance")
    for kind in required:
        if not sections[kind]:
            label = {
                "completion": "完了条件（子 plan は子 plan 完了条件を含む）",
                "verification": "検証",
                "acceptance": "受入条件",
            }[kind]
            blockers.append({
                "code": "missing_section",
                "path": doc.path.as_posix(),
                "section": kind,
                "message": f"必須セクションがありません: {label}",
            })

    for kind in ("completion", "verification", "acceptance"):
        for title, body in sections[kind]:
            lines = _line_items(body)
            if not lines:
                manual.append(_manual_check(doc.path, title, "", reason="section_empty"))
                continue
            for line in lines:
                checkbox = _CHECKBOX_RE.match(line)
                checked = bool(checkbox and checkbox.group("mark").lower() == "x")
                unchecked = bool(checkbox and checkbox.group("mark") == " ")
                if unchecked:
                    blockers.append({
                        "code": "unchecked_checkbox",
                        "path": doc.path.as_posix(),
                        "section": title,
                        "evidence": line,
                        "message": "未完了 checkbox が残っています",
                    })
                if _TODO_RE.search(line):
                    blockers.append({
                        "code": "evidence_missing",
                        "path": doc.path.as_posix(),
                        "section": title,
                        "evidence": line,
                        "message": "完了条件または検証に TODO/TBD が残っています",
                    })
                if kind == "verification":
                    entry = _verification_entry(doc.path, title, line)
                    evidence.append(entry)
                    if entry["status"] in {"failed", "not_run"}:
                        blockers.append({
                            "code": entry["status"],
                            "path": doc.path.as_posix(),
                            "section": title,
                            "evidence": line,
                            "message": "検証の失敗または未実施が明示されています",
                        })

                if checked:
                    if _MANUAL_RE.search(line) and not _SUCCESS_RE.search(line):
                        manual.append(_manual_check(doc.path, title, line, reason="manual_gate"))
                    continue
                if _AUTO_RE.search(line) and _SUCCESS_RE.search(line) and not _MANUAL_RE.search(line):
                    continue
                # Plain bullets are deliberately not treated as completed.  A
                # skill or human must decide whether the prose is evidence.
                manual.append(_manual_check(doc.path, title, line, reason="prose_not_machine_proof"))

    if not evidence and sections["verification"]:
        blockers.append({
            "code": "verification_evidence_missing",
            "path": doc.path.as_posix(),
            "message": "検証セクションに証跡がありません",
        })
    if not any(entry["kind"] == "automatic" for entry in evidence) and sections["verification"]:
        manual.append(_manual_check(doc.path, "検証", "自動検証の証跡", reason="automatic_evidence_not_explicit"))

    for title, body in sections["acceptance"]:
        if _MANUAL_RE.search(title) and not _line_items(body):
            manual.append(_manual_check(doc.path, title, "", reason="manual_gate"))

    changed_files: list[str] = []
    for _title, body in sections["changed_files"]:
        for raw_line in _line_items(body):
            value = raw_line
            code = re.search(r"`([^`]+)`", value)
            if code:
                value = code.group(1)
            link = re.search(r"\([^)]*\)", value)
            if link and value.startswith("["):
                value = link.group(0).strip("()")
            value = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", value)
            value = re.split(r"\s+(?:—|–|-|#)\s+|（", value, maxsplit=1)[0].strip()
            if value:
                changed_files.append(value)

    return blockers, manual, warnings, {
        "sections": {
            kind: [title for title, _body in values]
            for kind, values in sections.items()
        },
        "verification": evidence,
        "changed_files": changed_files,
    }


def _item(doc: _CloseoutDoc, project_root: Path, *, relation: str | None = None) -> dict[str, Any]:
    detection = doc.detection
    h1 = detection.title
    if detection.state_label:
        h1 = f"{detection.state_label} {detection.title or ''}".strip()
    parents, parent_kind = _frontmatter_parent(doc)
    return {
        "path": doc.path.as_posix(),
        "relative_path": _relative(doc.path, project_root),
        "name": doc.path.name,
        "type": doc.type_name,
        "h1": h1,
        "state": detection.state_key,
        "state_label": detection.state_label,
        "state_source": detection.source,
        "docsweep_state": detection.docsweep_state,
        "status": detection.okf_status,
        "okf_status": detection.okf_status,
        "docsweep_parent": parents[0] if len(parents) == 1 else parents,
        "docsweep_parent_kind": parent_kind,
        "due": detection.due,
        "review_status": detection.review_status,
        "mtime": doc.mtime,
        "relation": relation,
    }


def _git_dirty(project_root: Path) -> tuple[list[str], str | None]:
    git_dir = project_root / ".git"
    if not git_dir.exists():
        return [], "project-dir に .git がありません"
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"git status を取得できません: {exc}"
    if result.returncode != 0:
        return [], (result.stderr or "git status failed").strip()
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        raw = line[3:].strip().strip('"')
        if " -> " in raw:
            paths.extend(part.strip().strip('"') for part in raw.split(" -> "))
        elif raw:
            paths.append(raw)
    return paths, None


def _planned_path(raw: str, project_root: Path) -> str | None:
    value = raw.strip().strip("`").strip()
    if not value or value.startswith("<"):
        return None
    path = _lexical_path(value, base=project_root) if not Path(value).is_absolute() else _lexical_path(value)
    if not _is_under(path, project_root):
        return None
    return _path_key(path)


def _linkcheck_details(doc: _CloseoutDoc, project_root: Path) -> dict[str, Any]:
    """Return linkcheck's declared-file view without treating Git history as proof.

    The existing linkcheck parser is reused for the plan section.  Commit touch
    counts are deliberately not called here: a commit can be unrelated to the
    plan, while an uncommitted implementation can be valid evidence elsewhere.
    """
    section = _extract_section(doc.text)
    if not section:
        return {
            "progress_hint": "no_section",
            "declared_files": [],
            "commit_touch_used_as_evidence": False,
        }
    raw_files = _extract_files_from_section(section)
    declared: list[dict[str, Any]] = []
    for raw in raw_files:
        value = raw.strip().strip("`")
        candidate = (
            _lexical_path(value, base=project_root)
            if not Path(value).is_absolute()
            else _lexical_path(value)
        )
        declared.append({
            "path": value,
            "resolved_path": candidate.as_posix(),
            "exists": candidate.exists(),
            "inside_project": _is_under(candidate, project_root),
        })
    if not declared:
        hint = "no_files_declared"
    elif all(item["exists"] and item["inside_project"] for item in declared):
        hint = "declared_files_exist"
    else:
        hint = "declared_files_missing_or_outside"
    return {
        "progress_hint": hint,
        "declared_files": declared,
        "commit_touch_used_as_evidence": False,
    }


@dataclass
class CloseoutResult:
    parent: dict[str, Any]
    children: list[dict[str, Any]]
    target_state: str
    verdict: str
    blockers: list[dict[str, Any]] = field(default_factory=list)
    manual_checks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    suggested_order: list[str] = field(default_factory=list)
    project_dir: str | None = None
    git: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent": self.parent,
            "children": self.children,
            "target_state": self.target_state,
            "verdict": self.verdict,
            "blockers": self.blockers,
            "manual_checks": self.manual_checks,
            "warnings": self.warnings,
            "suggested_order": self.suggested_order,
            "project_dir": self.project_dir,
            "git": self.git,
        }


def check_closeout(
    parent_path: str | Path,
    *,
    target_state: str = "watching",
    project_dir: str | Path | None = None,
    config: Config | None = None,
) -> CloseoutResult:
    """Inspect one parent plan without changing any file, Git state, or archive."""
    if target_state not in {"watching", "done"}:
        raise CloseoutInputError("--to は watching または done を指定してください")

    parent = _lexical_path(parent_path)
    if not parent.is_file():
        raise CloseoutInputError(f"親 plan がファイルではありません: {parent}")
    project = _find_project_root(
        parent,
        _lexical_path(project_dir) if project_dir is not None else None,
        (config.project_markers if config else DEFAULT_PROJECT_MARKERS),
    )
    if not _is_under(parent, project):
        raise CloseoutInputError(f"親 plan が project 境界の外側です: {parent}")
    cfg = config or load_config(project_dir=project, explicit_roots=[project.as_posix()])
    parent_doc = _read_doc(parent, cfg)
    if parent_doc.type_name != "plan":
        raise CloseoutInputError(f"指定 path は plan_*.md ではありません: {parent}")

    paths = _iter_plan_paths(project, parent.parent, cfg)
    if _path_key(parent) not in {_path_key(path) for path in paths}:
        paths.append(parent)
    docs: dict[str, _CloseoutDoc] = {}
    load_errors: list[dict[str, Any]] = []
    for path in paths:
        try:
            doc = parent_doc if _path_key(path) == parent_doc.key else _read_doc(path, cfg)
        except CloseoutInputError as exc:
            # Do not silently turn an unreadable plan into an empty success.  We
            # cannot safely decide whether it is a child without its metadata,
            # so the closeout remains blocked until it is readable.
            load_errors.append({
                "code": "plan_read_error",
                "path": path.as_posix(),
                "message": str(exc),
            })
            continue
        docs[doc.key] = doc
    docs[parent_doc.key] = parent_doc

    blockers: list[dict[str, Any]] = []
    manual_checks: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    blockers.extend(load_errors)
    inspected: dict[str, dict[str, Any]] = {}
    for doc in docs.values():
        b, m, w, details = _inspect_document(doc, target_state=target_state)
        inspected[doc.key] = details
        if doc.key == parent_doc.key:
            blockers.extend(b)
            manual_checks.extend(m)
        else:
            # Candidate plans become part of closeout diagnostics only if they
            # have a parent relation or match the narrow legacy child pattern.
            pass
        warnings.extend(w)

    explicit_edges: dict[str, list[_CloseoutDoc]] = {}
    relation_errors: list[dict[str, Any]] = []
    for doc in docs.values():
        refs, kind = _frontmatter_parent(doc)
        if not refs and kind is None:
            continue
        if kind in {"multiple", "invalid"} or len(refs) != 1:
            relation_errors.append({
                "code": "ambiguous_parent",
                "path": doc.path.as_posix(),
                "message": "docsweep_parent は 1 件の repo-relative path に限定されます",
            })
            continue
        resolved, relation_error = _resolve_parent_ref(
            refs[0], project_root=project, docs=docs
        )
        explicit_edges[doc.key] = resolved
        if relation_error:
            relation_errors.append({
                "code": relation_error,
                "path": doc.path.as_posix(),
                "reference": refs[0],
                "message": "docsweep_parent は project 境界内の repo-relative path だけを指定できます",
            })
        elif len(resolved) == 0:
            relation_errors.append({
                "code": "unresolved_parent",
                "path": doc.path.as_posix(),
                "reference": refs[0],
                "message": "docsweep_parent の参照先が project 境界内に見つかりません",
            })
        elif len(resolved) > 1:
            relation_errors.append({
                "code": "ambiguous_parent",
                "path": doc.path.as_posix(),
                "reference": refs[0],
                "message": "docsweep_parent の basename が複数候補に解決されます",
            })

    child_docs: list[tuple[_CloseoutDoc, str]] = []
    for doc in docs.values():
        if doc.key == parent_doc.key:
            continue
        explicit = explicit_edges.get(doc.key, [])
        if explicit:
            if len(explicit) == 1 and explicit[0].key == parent_doc.key:
                child_docs.append((doc, "explicit"))
            continue
        if not _legacy_child_name(doc, parent_doc):
            points, _ambiguous = _related_points_to(doc, parent_doc, project_root=project, docs=docs)
            if points:
                warnings.append({
                    "code": "generic_related_not_child",
                    "path": doc.path.as_posix(),
                    "reference_from": doc.path.as_posix(),
                    "message": "related だけでは child と確定しません（legacy child filename 不一致）",
                })
            continue
        points, ambiguous = _related_points_to(doc, parent_doc, project_root=project, docs=docs)
        if ambiguous:
            relation_errors.append({
                "code": "ambiguous_parent",
                "path": doc.path.as_posix(),
                "message": "legacy related 参照が複数候補に解決されます",
            })
        if points:
            child_docs.append((doc, "inferred"))
        else:
            warnings.append({
                "code": "legacy_child_not_confirmed",
                "path": doc.path.as_posix(),
                "message": "filename は child 候補ですが、related が親を指していないため child 確定しません",
            })

    # A parent-side related link is diagnostic only.  It must not promote a
    # generic audit/report reference into a child relation.
    for ref in _related_values(parent_doc):
        resolved = _resolve_ref(ref, parent_doc, project_root=project, docs=docs)
        for candidate in resolved:
            if candidate.key == parent_doc.key:
                relation_errors.append({
                    "code": "self_reference",
                    "path": parent_doc.path.as_posix(),
                    "reference": ref,
                    "message": "親 plan 自身への参照があります",
                })
            elif not any(child.key == candidate.key for child, _kind in child_docs):
                warnings.append({
                    "code": "generic_related_not_child",
                    "path": candidate.path.as_posix(),
                    "reference_from": parent_doc.path.as_posix(),
                    "message": "親の related だけでは child と確定しません",
                })

    # Parent itself must remain a top-level plan.  A nested relation is shown as
    # a grandchild warning, while unresolved/cyclic relations block closeout.
    parent_parent = explicit_edges.get(parent_doc.key, [])
    if parent_parent:
        if len(parent_parent) == 1 and parent_parent[0].key == parent_doc.key:
            relation_errors.append({
                "code": "self_reference",
                "path": parent_doc.path.as_posix(),
                "message": "親 plan の docsweep_parent が自身を指しています",
            })
        else:
            warnings.append({
                "code": "grandchild_detected",
                "path": parent_doc.path.as_posix(),
                "message": "指定された親 plan 自体に親があり、標準の 1 階層を越えています",
            })

    # Cycle detection over explicit parent edges.  Only cycles touching the
    # requested parent are blockers; unrelated plans cannot veto this check.
    edge_keys = {
        key: values[0].key
        for key, values in explicit_edges.items()
        if len(values) == 1
    }
    cycle_nodes: set[str] = set()
    for start in [parent_doc.key, *(child.key for child, _kind in child_docs)]:
        seen: dict[str, int] = {}
        path_keys: list[str] = []
        current = start
        while current in edge_keys and current not in seen:
            seen[current] = len(path_keys)
            path_keys.append(current)
            current = edge_keys[current]
        if current in seen:
            cycle_nodes.update(path_keys[seen[current]:])
    if cycle_nodes:
        for key in sorted(cycle_nodes):
            cycle_doc = docs.get(key)
            if cycle_doc:
                relation_errors.append({
                    "code": "cycle",
                    "path": cycle_doc.path.as_posix(),
                    "message": "親子関係が循環しています",
                })

    children: list[dict[str, Any]] = []
    for doc, relation in sorted(child_docs, key=lambda pair: pair[0].path.as_posix().lower()):
        b, m, _w, details = _inspect_document(doc, target_state=target_state)
        details["linkcheck"] = _linkcheck_details(doc, project)
        item = _item(doc, project, relation=relation)
        item["details"] = details
        children.append(item)
        blockers.extend(b)
        manual_checks.extend(m)

    if relation_errors:
        for error in relation_errors:
            target_related = error.get("path") == parent_doc.path.as_posix() or error.get("path") in {
                child.path.as_posix() for child, _kind in child_docs
            }
            if target_related or error["code"] in {
                "cycle", "ambiguous_parent", "unresolved_parent", "outside_parent",
            }:
                blockers.append(error)

    parent_details = inspected[parent_doc.key]
    parent_details["linkcheck"] = _linkcheck_details(parent_doc, project)
    parent_item = _item(parent_doc, project, relation="parent")
    parent_item["details"] = parent_details

    # State and target transition checks.
    target_state_available = cfg.state_model.by_key(target_state) is not None
    if not target_state_available:
        blockers.append({
            "code": "target_state_unavailable",
            "path": parent_doc.path.as_posix(),
            "message": f"設定された state model に {target_state!r} がありません",
        })
    for doc, _relation in [(parent_doc, "parent"), *child_docs]:
        if doc.detection.state_key in {"done", "discarded"} and target_state == "watching":
            warnings.append({
                "code": "already_terminal",
                "path": doc.path.as_posix(),
                "message": "既に終端状態なので watching への順序変更対象から除外します",
            })
        if doc.type_name not in _CLOSEOUT_TYPES:
            blockers.append({
                "code": "unsupported_type_transition",
                "path": doc.path.as_posix(),
                "message": "親子 closeout は plan / bugfix のみを対象にします",
            })
        elif target_state == "done" and doc.type_name not in {"plan", "bugfix"}:
            blockers.append({
                "code": "target_state_not_allowed",
                "path": doc.path.as_posix(),
                "message": f"{doc.type_name} は {target_state} へ進められません",
            })

    parent_rank = _STATE_RANK.get(parent_doc.detection.state_key or "")
    for child, _relation in child_docs:
        child_rank = _STATE_RANK.get(child.detection.state_key or "")
        if parent_rank is not None and child_rank is not None and child_rank < parent_rank:
            blockers.append({
                "code": "state_order_conflict",
                "path": child.path.as_posix(),
                "message": "child plan が parent plan より前段の状態です",
                "parent_state": parent_doc.detection.state_key,
                "child_state": child.detection.state_key,
            })

    dirty, git_error = _git_dirty(project)
    planned_keys: set[str] = set()
    for details in [parent_details, *(inspected[doc.key] for doc, _kind in child_docs)]:
        planned_keys.update(
            planned_key for raw in details["changed_files"]
            if (planned_key := _planned_path(raw, project)) is not None
        )
    dirty_keys = {
        _path_key(_lexical_path(raw, base=project))
        for raw in dirty
        if not Path(raw).is_absolute()
    }
    dirty_keys.update(_path_key(_lexical_path(raw)) for raw in dirty if Path(raw).is_absolute())
    overlap = sorted(
        raw for raw in dirty
        if _path_key(_lexical_path(raw, base=project) if not Path(raw).is_absolute() else _lexical_path(raw)) in planned_keys
    )
    unrelated = sorted(raw for raw in dirty if raw not in overlap)
    git_payload: dict[str, Any] = {
        "dirty": dirty,
        "dirty_overlap": overlap,
        "dirty_unrelated": unrelated,
    }
    if git_error:
        git_payload["error"] = git_error
        warnings.append({"code": "git_status_unavailable", "message": git_error})
    if overlap:
        manual_checks.append({
            "path": parent_doc.path.as_posix(),
            "section": "Git dirty overlap",
            "description": ", ".join(overlap),
            "reason": "planned_file_is_dirty",
            "status": "pending",
        })
        warnings.append({
            "code": "dirty_overlap",
            "paths": overlap,
            "message": "変更予定ファイルと現在の dirty file が重なっています",
        })
    if unrelated:
        warnings.append({
            "code": "dirty_unrelated",
            "paths": unrelated,
            "message": "plan と直接関係しない dirty file があります（blocker にはしません）",
        })

    suggested = [
        child.path.as_posix() for child, _kind in sorted(child_docs, key=lambda pair: pair[0].path.as_posix().lower())
        if child.detection.state_key not in {"done", "discarded"}
    ]
    if parent_doc.detection.state_key not in {"done", "discarded"}:
        suggested.append(parent_doc.path.as_posix())

    verdict = "not_ready" if blockers else ("manual_review_required" if manual_checks else "ready")
    return CloseoutResult(
        parent=parent_item,
        children=children,
        target_state=target_state,
        verdict=verdict,
        blockers=blockers,
        manual_checks=manual_checks,
        warnings=warnings,
        suggested_order=suggested,
        project_dir=project.as_posix(),
        git=git_payload,
    )


# Descriptive aliases keep the service discoverable for callers that prefer a
# verb over the CLI-shaped name.
run_closeout_check = check_closeout
closeout_check = check_closeout
