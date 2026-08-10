"""Read-only OKF Bundle conformance checks.

The checker intentionally implements only the structural requirements that
OKF makes normative.  Unknown types, producer extensions, optional families,
and broken cross-links are surfaced as warnings (or accepted) rather than
turning an otherwise usable Bundle into a hard failure.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

import yaml

from .okf import OkfProfile

_FRONTMATTER_RE = re.compile(
    r"^---[ \t]*\r?\n(?P<body>.*?)(?:\r?\n)---[ \t]*(?:\r?\n|$)",
    re.DOTALL,
)
_DATE_HEADING_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$")
_LINK_RE = re.compile(r"!?(?:\[[^\]]*\])\(([^)]+)\)")


@dataclass(frozen=True)
class OkfIssue:
    path: str
    severity: str  # error | warning
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class OkfCheckResult:
    path: str
    profile: dict
    files_checked: int
    issues: list[OkfIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[OkfIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[OkfIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "ok": self.ok,
            "files_checked": self.files_checked,
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
            "profile": self.profile,
        }


@dataclass(frozen=True)
class _BundleFile:
    name: str
    text: str
    read_error: str | None = None


def _read_frontmatter(text: str) -> tuple[dict | None, str | None]:
    """Return (mapping, error code) for a concept frontmatter block."""
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return None, "missing_frontmatter"
    try:
        data = yaml.safe_load(match.group("body"))
    except yaml.YAMLError:
        return None, "invalid_yaml"
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return None, "frontmatter_not_mapping"
    return data, None


def _issue(issues: list[OkfIssue], path: str, severity: str, code: str, message: str) -> None:
    issues.append(OkfIssue(path=path, severity=severity, code=code, message=message))


def _check_index(
    file: _BundleFile, *, is_root: bool, profile: OkfProfile, issues: list[OkfIssue]
) -> None:
    data, error = _read_frontmatter(file.text)
    if not is_root:
        if error != "missing_frontmatter":
            _issue(
                issues,
                file.name,
                "error",
                "reserved_index_frontmatter",
                "nested index.md は frontmatter を持てません",
            )
    elif error not in {None, "missing_frontmatter"}:
        _issue(issues, file.name, "error", error, "bundle root の index.md frontmatter が不正です")
    elif data:
        extra = set(data) - {"okf_version"}
        if extra:
            _issue(
                issues,
                file.name,
                "error",
                "reserved_index_keys",
                f"bundle root の index.md で許可されるキー以外があります: {sorted(extra)}",
            )
        declared = data.get("okf_version")
        if declared is not None and str(declared).strip() != profile.spec_version:
            _issue(
                issues,
                file.name,
                "error",
                "okf_version_mismatch",
                f"index.md の okf_version={declared!r} と profile={profile.spec_version!r} が不一致です",
            )
    if not re.search(r"^#\s+\S", file.text, re.MULTILINE) or not re.search(
        r"^\s*[*-]\s+\[[^]]+\]\([^)]*\)", file.text, re.MULTILINE
    ):
        _issue(
            issues,
            file.name,
            "warning",
            "index_structure",
            "index.md に見出しと Markdown link の一覧が見つかりません",
        )


def _check_log(file: _BundleFile, issues: list[OkfIssue]) -> None:
    _, error = _read_frontmatter(file.text)
    if error != "missing_frontmatter":
        _issue(
            issues,
            file.name,
            "error",
            "reserved_log_frontmatter",
            "log.md は frontmatter を持てません",
        )
    headings = re.findall(r"^##\s+(.+?)\s*$", file.text, re.MULTILINE)
    invalid_dates = []
    for heading in headings:
        match = _DATE_HEADING_RE.fullmatch(f"## {heading}")
        if match is None:
            invalid_dates.append(heading)
            continue
        try:
            date.fromisoformat(match.group(1))
        except ValueError:
            invalid_dates.append(heading)
    if invalid_dates:
        _issue(
            issues,
            file.name,
            "error",
            "log_date_heading",
            "log.md の ## 見出しは YYYY-MM-DD 形式である必要があります",
        )
    if not headings:
        _issue(
            issues,
            file.name,
            "warning",
            "log_structure",
            "log.md に日付単位の ## 見出しがありません",
        )


def _check_concept(file: _BundleFile, profile: OkfProfile, issues: list[OkfIssue]) -> None:
    data, error = _read_frontmatter(file.text)
    if error:
        messages = {
            "missing_frontmatter": "非予約 Markdown は YAML frontmatter が必要です",
            "invalid_yaml": "frontmatter の YAML を解析できません",
            "frontmatter_not_mapping": "frontmatter の root は mapping である必要があります",
        }
        _issue(issues, file.name, "error", error, messages.get(error, "frontmatter が不正です"))
        return
    assert data is not None
    for key in profile.required_frontmatter:
        value = data.get(key)
        if key == "type" and not isinstance(value, str):
            _issue(issues, file.name, "error", "missing_type", "type は空でない文字列が必要です")
        elif isinstance(value, str) and not value.strip():
            _issue(issues, file.name, "error", "missing_type", "type は空でない文字列が必要です")
        elif value is None:
            _issue(issues, file.name, "error", "missing_type", f"必須 frontmatter {key!r} がありません")

    status = data.get("status")
    if status is not None and not profile.is_lifecycle_status(status):
        _issue(
            issues,
            file.name,
            "warning",
            "nonstandard_status",
            f"status={status!r} は profile の lifecycle 値ではありません（文書は reject しません）",
        )


def _link_target(source_name: str, target: str) -> str | None:
    target = target.strip().strip("<>").split("#", 1)[0].split("?", 1)[0]
    target = unquote(target)
    if not target or target.startswith(("#", "mailto:", "data:")):
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    if target.startswith("/"):
        candidate = PurePosixPath(target.lstrip("/"))
    else:
        candidate = PurePosixPath(source_name).parent / target
    normalized = PurePosixPath(*candidate.parts)
    if ".." in normalized.parts:
        # A link escaping the bundle is not an internal link to validate.
        return None
    return normalized.as_posix()


def _check_links(
    files: list[_BundleFile], profile: OkfProfile, issues: list[OkfIssue]
) -> None:
    link_policy = profile.broken_links.strip().lower()
    if link_policy in {"allow", "ignore", "accepted"}:
        return
    severity = "error" if link_policy in {"error", "reject"} else "warning"
    names = {f.name for f in files}
    directories = {str(PurePosixPath(n).parent) for n in names}
    for file in files:
        for match in _LINK_RE.finditer(file.text):
            target = _link_target(file.name, match.group(1))
            if target is None:
                continue
            candidates = {target}
            if not target.lower().endswith(".md"):
                candidates.add(f"{target}.md")
            if target.endswith("/"):
                candidates.add(target.rstrip("/"))
            if not (candidates & names or target.rstrip("/") in directories):
                _issue(
                    issues,
                    file.name,
                    severity,
                    "broken_link",
                    f"Bundle 内にリンク先がありません: {target}",
                )


def _reserved_role(name: str, profile: OkfProfile) -> str | None:
    """Resolve a reserved-file role by basename or bundle-relative path."""
    normalized_name = name.lower()
    basename = PurePosixPath(name).name.lower()
    for raw_name, role in profile.reserved_files.items():
        candidate = PurePosixPath(str(raw_name).replace("\\", "/")).as_posix().lower()
        if candidate == normalized_name or PurePosixPath(candidate).name == basename:
            return role.lower()
    return None


def _iter_directory(root: Path) -> list[_BundleFile]:
    files: list[_BundleFile] = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            files.append(_BundleFile(name=name, text="", read_error="invalid_utf8"))
            continue
        except OSError:
            files.append(_BundleFile(name=name, text="", read_error="read_error"))
            continue
        files.append(_BundleFile(name=name, text=text))
    return files


def _iter_zip(path: Path) -> list[_BundleFile]:
    files: list[_BundleFile] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for name in sorted(n for n in archive.namelist() if n.lower().endswith(".md") and not n.endswith("/")):
                try:
                    text = archive.read(name).decode("utf-8")
                except UnicodeDecodeError:
                    files.append(_BundleFile(name=name.replace("\\", "/"), text="", read_error="invalid_utf8"))
                    continue
                except KeyError:
                    files.append(_BundleFile(name=name.replace("\\", "/"), text="", read_error="read_error"))
                    continue
                files.append(_BundleFile(name=name.replace("\\", "/"), text=text))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"OKF Bundle zip を読めません: {path}") from exc
    return files


def check_bundle(path: Path, profile: OkfProfile) -> OkfCheckResult:
    """Check a directory or zip without modifying it."""
    path = Path(path).expanduser().resolve()
    if path.is_dir():
        files = _iter_directory(path)
    elif path.is_file() and path.suffix.lower() == ".zip":
        files = _iter_zip(path)
    else:
        raise ValueError(f"OKF Bundle のディレクトリまたは zip がありません: {path}")

    issues: list[OkfIssue] = []
    for file in files:
        if file.read_error:
            code = file.read_error
            message = (
                "Markdown は UTF-8 である必要があります"
                if code == "invalid_utf8"
                else "Bundle 内のファイルを読み取れません"
            )
            _issue(issues, file.name, "error", code, message)
            continue
        role = _reserved_role(file.name, profile)
        if role == "directory_index":
            is_root = PurePosixPath(file.name).parent == PurePosixPath(".")
            _check_index(file, is_root=is_root, profile=profile, issues=issues)
        elif role == "update_log":
            _check_log(file, issues)
        elif role is not None:
            # Unknown reserved roles are profile declarations, not concepts.
            # A future profile can add a validator without making old code
            # reject the file as a normal concept.
            continue
        else:
            _check_concept(file, profile, issues)
    _check_links(files, profile, issues)
    return OkfCheckResult(
        path=path.as_posix(),
        profile=profile.to_dict(),
        files_checked=len(files),
        issues=issues,
    )
