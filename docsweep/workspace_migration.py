"""Inventory and safe, manifest-driven migration for release tracking.

The inventory intentionally records metadata only.  It never puts document
body text, frontmatter values other than the release fields, or Git diffs into
the manifest/journal.  Applying a manifest is explicit and is performed one
repository at a time with exact-path rollback on failure.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

import yaml

from .atomic import write_atomic
from .config import (
    Config,
    archive_route_for_project,
    load_config,
    project_work_settings,
    resolve_work_dir,
)
from .release import (
    ReleaseTrackingError,
    git_tags,
    parse_release_tag,
    validate_release_label,
)
from .services.frontmatter import _format_value, _replace_or_insert, read_frontmatter_text


DEFAULT_WORKSPACE_EXCLUDES: tuple[str, ...] = (
    "**/.many-ai-cli/worktrees/**",
    "**/NEXTCLOUD_SOURCES_off/**",
    "**/vendor/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/cache/**",
    "**/__pycache__/**",
)
_MANAGED_FILE_RE = re.compile(r"^(?:plan|bugfix|pending)_.*\.(?:md|markdown)$", re.IGNORECASE)
_VERSION_BUCKET_RE = re.compile(
    r"^(?:v)?\d+(?:\.\d+)?(?:\.x)?$|^(?:v)?\d+\.x$", re.IGNORECASE
)
_QUARTER_BUCKET_RE = re.compile(r"^\d{4}-Q[1-4]$", re.IGNORECASE)
_JOURNAL_ROOT = Path.home() / ".docsweep" / "release-migrations"


@dataclass(frozen=True)
class RepositoryCandidate:
    root: Path
    excluded: bool = False
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class WorkQueue:
    """The logical queue route and the physical directory it resolves to."""

    logical_root: Path
    physical_root: Path
    is_reparse_root: bool


class WorkQueueError(ValueError):
    """A configured queue cannot be used safely for migration."""

    def __init__(self, reason: str, message: str, *, path: Path | None = None):
        super().__init__(message)
        self.reason = reason
        self.path = _lexical(path) if path is not None else None


class ManifestPreflightError(ReleaseTrackingError):
    """A manifest no longer describes the repository it is about to change."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _is_under(path: Path, root: Path) -> bool:
    try:
        _lexical(path).relative_to(_lexical(root))
    except ValueError:
        return False
    return True


def _path_crosses_reparse(
    path: Path,
    root: Path,
    *,
    allowed_reparse_roots: tuple[Path, ...] = (),
) -> bool:
    """Return true when any component between root and path is a reparse point."""
    lexical_path = _lexical(path)
    lexical_root = _lexical(root)
    allowed = {_lexical(item) for item in allowed_reparse_roots}
    try:
        relative = lexical_path.relative_to(lexical_root)
    except ValueError:
        return True
    current = lexical_root
    for part in relative.parts:
        current /= part
        if _is_reparse_point(current) and current not in allowed:
            return True
    return False


def _is_reparse_point(path: Path) -> bool:
    """Return true for symlink/junction-like directories that leave the repo."""
    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        return bool(isjunction and isjunction(path))
    except OSError:
        return True


def _reparse_points_between(path: Path, root: Path) -> list[Path]:
    """Return reparse points on the lexical route from ``root`` to ``path``."""
    lexical_path = _lexical(path)
    lexical_root = _lexical(root)
    try:
        relative = lexical_path.relative_to(lexical_root)
    except ValueError:
        return [lexical_path]
    points: list[Path] = []
    current = lexical_root
    for part in relative.parts:
        current /= part
        if _is_reparse_point(current):
            points.append(current)
    return points


def _nested_reparse_points(root: Path) -> list[Path]:
    """Find reparse-point directories below an already accepted queue root."""
    points: list[Path] = []
    errors: list[OSError] = []

    def onerror(error: OSError) -> None:
        errors.append(error)

    for dirpath, dirnames, _filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=onerror
    ):
        current = _lexical(Path(dirpath))
        keep: list[str] = []
        for name in dirnames:
            child = current / name
            if _is_reparse_point(child):
                points.append(_lexical(child))
            else:
                keep.append(name)
        dirnames[:] = keep
    if errors:
        raise WorkQueueError(
            "configured_work_queue_unreadable",
            f"configured work queue cannot be read: {root}: {errors[0]}",
            path=root,
        )
    return points


def _resolve_configured_queue(repo: Path, config: Config) -> WorkQueue:
    """Resolve and validate the one configured queue allowed to cross a link."""
    try:
        work_dir, _policy, _secret = project_work_settings(repo, config)
        logical_root = resolve_work_dir(repo, work_dir)
    except (OSError, ValueError) as exc:
        raise WorkQueueError(
            "configured_work_queue_unavailable",
            f"configured work queue cannot be resolved for {repo}: {exc}",
        ) from exc

    logical_root = _lexical(logical_root)
    if not logical_root.is_dir():
        raise WorkQueueError(
            "configured_work_queue_unavailable",
            f"configured work queue does not exist or is not a directory: {logical_root}",
            path=logical_root,
        )

    points = _reparse_points_between(logical_root, repo)
    unexpected = [point for point in points if point != logical_root]
    if unexpected:
        raise WorkQueueError(
            "queue_parent_reparse_point",
            f"configured work queue path crosses an unapproved reparse point: {unexpected[0]}",
            path=unexpected[0],
        )
    try:
        physical_root = logical_root.resolve(strict=True)
        if not physical_root.is_dir():
            raise OSError("resolved path is not a directory")
        with os.scandir(logical_root):
            pass
    except (OSError, RuntimeError) as exc:
        raise WorkQueueError(
            "configured_work_queue_unreadable",
            f"configured work queue cannot be read: {logical_root}: {exc}",
            path=logical_root,
        ) from exc

    nested = _nested_reparse_points(logical_root)
    if nested:
        raise WorkQueueError(
            "nested_reparse_point",
            f"configured work queue contains an unapproved nested reparse point: {nested[0]}",
            path=nested[0],
        )
    return WorkQueue(
        logical_root=logical_root,
        physical_root=_lexical(physical_root),
        is_reparse_root=_is_reparse_point(logical_root),
    )


def _excluded(path: Path, roots: list[Path], patterns: list[str]) -> str | None:
    path = _lexical(path)
    for root in roots:
        root = _lexical(root)
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if not rel:
            continue
        for raw_pattern in patterns:
            pattern = str(raw_pattern).replace("\\", "/").strip().strip("/")
            if not pattern:
                continue
            if (
                fnmatch.fnmatch(rel, pattern)
                or fnmatch.fnmatch(rel + "/", pattern)
                or fnmatch.fnmatch(rel, pattern.rstrip("/") + "/**")
                or any(fnmatch.fnmatch(part, pattern) for part in Path(rel).parts)
            ):
                return pattern
    return None


def discover_repositories(
    roots: list[Path], *, excludes: list[str] | None = None
) -> tuple[list[RepositoryCandidate], list[dict]]:
    """Find Git candidates without reading repository contents."""
    normalized_roots = [_lexical(root) for root in roots if _lexical(root).is_dir()]
    patterns = list(DEFAULT_WORKSPACE_EXCLUDES) + list(excludes or [])
    found: dict[str, RepositoryCandidate] = {}
    excluded: list[dict] = []
    for root in normalized_roots:
        for dirpath, dirnames, _filenames in os.walk(root, topdown=True):
            current = _lexical(Path(dirpath))
            keep: list[str] = []
            for name in dirnames:
                child = current / name
                reason = _excluded(child, normalized_roots, patterns)
                if reason:
                    excluded.append({"path": child.as_posix(), "reason": reason})
                    continue
                if _is_reparse_point(child):
                    excluded.append({"path": child.as_posix(), "reason": "reparse-point"})
                    continue
                keep.append(name)
            dirnames[:] = keep
            marker = current / ".git"
            if not marker.exists():
                continue
            key = current.as_posix().casefold()
            found[key] = RepositoryCandidate(root=current)
            # The .git directory itself is not a candidate to descend into.
            dirnames[:] = [name for name in dirnames if name != ".git"]
    return sorted(found.values(), key=lambda item: item.root.as_posix().casefold()), excluded


def _project_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _safe_mode(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return None
    value = raw.get("mode")
    if value is None:
        return None
    value = str(value).strip().lower()
    return value if value in {"enabled", "disabled"} else "invalid"


def _config_summary(cfg: Config, raw: dict, route) -> dict:
    tracking = cfg.release_tracking
    return {
        "release_tracking": {
            "mode": tracking.mode,
            "tag_pattern": tracking.tag_pattern,
            "archive_group_by": tracking.archive_group_by,
            "default_target": tracking.default_target,
            "include_prerelease": tracking.include_prerelease,
            "tag_prefix": tracking.tag_prefix,
            "source": "project" if "release_tracking" in raw else "inherited_or_default",
        },
        "archive_partition": cfg.archive_partition,
        "archive_dir": route.archive_dir,
        "archive_route_source": route.source,
        "project_config_present": (Path(cfg.project_dir or ".") / ".docsweep.yaml").is_file(),
    }


def _archive_buckets(
    repo: Path,
    archive_dir: str,
    *,
    allowed_reparse_roots: tuple[Path, ...] = (),
) -> list[str]:
    """List only immediate archive directory names; do not inspect file bodies."""
    base = repo / archive_dir
    try:
        if not base.is_dir() or _path_crosses_reparse(
            base, repo, allowed_reparse_roots=allowed_reparse_roots
        ):
            return []
        return sorted(
            child.name
            for child in base.iterdir()
            if child.is_dir() and child.name not in {".", ".."}
        )
    except OSError:
        return []


def _archive_base(archive_dir: str) -> tuple[str, str | None]:
    parts = [part for part in str(archive_dir).replace("\\", "/").split("/") if part]
    if not parts:
        return archive_dir, None
    if _VERSION_BUCKET_RE.fullmatch(parts[-1]) or _QUARTER_BUCKET_RE.fullmatch(parts[-1]):
        parent = "/".join(parts[:-1]) or "."
        return parent, parts[-1]
    return archive_dir, None


def _is_release_bucket_name(value: str) -> bool:
    return bool(_VERSION_BUCKET_RE.fullmatch(value) or _QUARTER_BUCKET_RE.fullmatch(value))


def _iter_active_docs(
    queue_root: Path,
    archive_dir: str,
    patterns: list[str],
    *,
    exclude_root: Path | None = None,
    allowed_reparse_roots: tuple[Path, ...] = (),
) -> list[Path]:
    """Return managed files from the configured queue, using logical paths."""
    archive_names = {"archive"}
    archive_parts = [part for part in archive_dir.replace("\\", "/").split("/") if part]
    if archive_parts:
        archive_names.add(archive_parts[-1])
    pattern_root = exclude_root or queue_root
    output: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(
        queue_root, topdown=True, followlinks=False
    ):
        current = _lexical(Path(dirpath))
        dirnames[:] = [
            name
            for name in dirnames
            if name not in {".git", ".docsweep", "node_modules", "vendor", "cache"}
            and name not in archive_names
            and not _is_reparse_point(current / name)
            and _excluded(current / name, [pattern_root], patterns) is None
        ]
        for filename in filenames:
            if _MANAGED_FILE_RE.fullmatch(filename):
                path = current / filename
                if not _path_crosses_reparse(
                    path,
                    queue_root,
                    allowed_reparse_roots=allowed_reparse_roots,
                ):
                    output.append(path)
    return sorted(output, key=lambda path: path.as_posix().casefold())


def _iter_archived_docs(
    repo: Path,
    archive_dir: str,
    *,
    allowed_reparse_roots: tuple[Path, ...] = (),
) -> list[tuple[Path, str]]:
    """Return managed files under known release bucket directories only."""
    base = repo / archive_dir
    if not base.is_dir() or _path_crosses_reparse(
        base, repo, allowed_reparse_roots=allowed_reparse_roots
    ):
        return []
    output: list[tuple[Path, str]] = []
    try:
        bucket_dirs = [
            child
            for child in base.iterdir()
            if child.is_dir()
            and not _is_reparse_point(child)
            and _is_release_bucket_name(child.name)
        ]
    except OSError:
        return []
    for bucket_dir in bucket_dirs:
        for dirpath, dirnames, filenames in os.walk(
            bucket_dir, topdown=True, followlinks=False
        ):
            current = _lexical(Path(dirpath))
            dirnames[:] = [
                name
                for name in dirnames
                if not _is_reparse_point(current / name)
                and not _path_crosses_reparse(
                    current / name,
                    repo,
                    allowed_reparse_roots=allowed_reparse_roots,
                )
            ]
            for filename in filenames:
                if _MANAGED_FILE_RE.fullmatch(filename):
                    path = current / filename
                    if not _path_crosses_reparse(
                        path,
                        repo,
                        allowed_reparse_roots=allowed_reparse_roots,
                    ):
                        output.append((path, bucket_dir.name))
    return sorted(output, key=lambda item: item[0].as_posix().casefold())


def _iter_queue_outliers(
    repo: Path,
    expected_queue: Path | None,
    archive_root: Path,
    patterns: list[str],
) -> list[Path]:
    """Find managed documents under ``docs`` but outside the configured queue.

    This is intentionally a diagnostic-only scan.  Its return value is never
    used to construct migration actions.
    """
    docs_root = repo / "docs"
    if not docs_root.is_dir() or _path_crosses_reparse(docs_root, repo):
        return []
    expected = _lexical(expected_queue) if expected_queue is not None else None
    archive = _lexical(archive_root)
    output: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(
        docs_root, topdown=True, followlinks=False
    ):
        current = _lexical(Path(dirpath))
        keep: list[str] = []
        for name in dirnames:
            child = current / name
            if expected is not None and _is_under(child, expected):
                continue
            if _is_under(child, archive) or _is_reparse_point(child):
                continue
            if _excluded(child, [repo], patterns) is None:
                keep.append(name)
        dirnames[:] = keep
        for filename in filenames:
            if not _MANAGED_FILE_RE.fullmatch(filename):
                continue
            path = current / filename
            if expected is not None and _is_under(path, expected):
                continue
            if _is_under(path, archive) or _path_crosses_reparse(path, repo):
                continue
            if _excluded(path, [repo], patterns) is None:
                output.append(path)
    return sorted(output, key=lambda path: path.as_posix().casefold())


def _doc_metadata(path: Path) -> dict:
    """Read only enough metadata to classify a document; never return body text."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = read_frontmatter_text(text)
    except (OSError, UnicodeError):
        return {
            "readable": False,
            "target_release": None,
            "released_in": None,
            "target_present": False,
            "released_present": False,
            "state": None,
            "never_archive": False,
        }
    target = frontmatter.get("target_release")
    released = frontmatter.get("released_in")
    state = frontmatter.get("docsweep_state") or frontmatter.get("status")
    policy = frontmatter.get("docsweep_policy")
    return {
        "readable": True,
        "target_release": str(target).strip() if target is not None and str(target).strip() else None,
        "released_in": str(released).strip() if released is not None and str(released).strip() else None,
        "target_present": "target_release" in frontmatter,
        "released_present": "released_in" in frontmatter,
        "state": str(state).strip() if state is not None and str(state).strip() else None,
        "never_archive": policy == "never_archive",
    }


def _normalized_field_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _file_precondition(path: Path, *, field: str | None = None) -> dict:
    """Capture a write precondition without storing document contents."""
    path = _lexical(path)
    if not path.is_file():
        return {
            "exists": False,
            "readable": True,
            "mtime_ns": None,
            "content_sha256": None,
            **(
                {"field_present": False, "old_value": None}
                if field is not None
                else {}
            ),
        }
    try:
        raw = path.read_bytes()
        stat = path.stat()
        result = {
            "exists": True,
            "readable": True,
            "mtime_ns": stat.st_mtime_ns,
            "content_sha256": hashlib.sha256(raw).hexdigest(),
        }
        if field is not None:
            text = raw.decode("utf-8")
            frontmatter, _body = read_frontmatter_text(text)
            result.update(
                {
                    "field_present": field in frontmatter,
                    "old_value": _normalized_field_value(frontmatter.get(field)),
                }
            )
        return result
    except (OSError, UnicodeError):
        result = {
            "exists": True,
            "readable": False,
            "mtime_ns": None,
            "content_sha256": None,
        }
        if field is not None:
            result.update({"field_present": None, "old_value": None})
        return result


def _precondition_matches(path: Path, expected: dict, *, field: str | None = None) -> tuple[bool, str]:
    """Compare a manifest precondition with the current file state."""
    if not isinstance(expected, dict):
        return False, "missing_precondition"
    actual = _file_precondition(path, field=field)
    keys = ("exists", "readable", "mtime_ns", "content_sha256")
    if any(actual.get(key) != expected.get(key) for key in keys):
        return False, "file_changed"
    if field is not None and (
        actual.get("field_present") != expected.get("field_present")
        or actual.get("old_value") != expected.get("old_value")
    ):
        return False, f"field_changed:{field}"
    return True, "ok"


def _manifest_fingerprint(manifest: dict) -> str:
    payload = {
        key: value for key, value in manifest.items() if key != "manifest_sha256"
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _safe_archive_root(value: str) -> bool:
    """Whether a migration may write the archive root into a repo config."""
    raw = str(value).replace("/", "\\")
    candidate = PureWindowsPath(raw)
    return not (
        candidate.is_absolute()
        or candidate.drive
        or ".." in candidate.parts
    )


def _queue_outlier_items(
    paths: list[Path], expected_queue: Path | None
) -> list[dict]:
    """Convert diagnostic-only queue outlier paths into manifest review items."""
    expected = _lexical(expected_queue).as_posix() if expected_queue is not None else None
    return [
        {
            "kind": "queue",
            "path": _lexical(path).as_posix(),
            "expected_dir": expected,
            "reason": "document_outside_configured_work_dir",
            "confidence": "high",
        }
        for path in paths
    ]


def _inventory_repository(
    candidate: RepositoryCandidate,
    *,
    default_target: str | None,
    excludes: list[str],
    global_path: Path | None = None,
) -> dict:
    repo = candidate.root
    project_yaml = repo / ".docsweep.yaml"
    raw = _project_yaml(project_yaml)
    config_precondition = _file_precondition(project_yaml)
    cfg = load_config(
        project_dir=repo,
        explicit_roots=[str(repo)],
        global_path=global_path,
    )
    route = archive_route_for_project(repo, cfg)
    before = _config_summary(cfg, raw, route)
    archive_dir, versioned_suffix = _archive_base(route.archive_dir)
    expected_queue: Path | None = None
    queue: WorkQueue | None = None
    queue_error: WorkQueueError | None = None
    try:
        work_dir, _policy, _secret = project_work_settings(repo, cfg)
        expected_queue = resolve_work_dir(repo, work_dir)
    except (OSError, ValueError) as exc:
        queue_error = WorkQueueError(
            "configured_work_queue_unavailable",
            f"configured work queue cannot be resolved for {repo}: {exc}",
        )
    if queue_error is None:
        try:
            queue = _resolve_configured_queue(repo, cfg)
        except WorkQueueError as exc:
            queue_error = exc
    allowed_reparse_roots = (queue.logical_root,) if queue is not None else ()
    buckets = _archive_buckets(
        repo,
        archive_dir,
        allowed_reparse_roots=allowed_reparse_roots,
    )
    effective_default = default_target or cfg.release_tracking.default_target
    if effective_default is not None:
        effective_default = validate_release_label(effective_default, field="default_target")

    docs: list[dict] = []
    if queue is not None:
        active_paths = _iter_active_docs(
            queue.logical_root,
            archive_dir,
            excludes,
            exclude_root=repo,
            allowed_reparse_roots=allowed_reparse_roots,
        )
        archived_paths = _iter_archived_docs(
            repo,
            archive_dir,
            allowed_reparse_roots=allowed_reparse_roots,
        )
    else:
        active_paths = []
        archived_paths = []
    outlier_paths = _iter_queue_outliers(
        repo,
        expected_queue,
        repo / archive_dir,
        excludes,
    )
    for path in active_paths:
        metadata = _doc_metadata(path)
        docs.append(
            {
                "path": _lexical(path).as_posix(),
                "target_release": metadata["target_release"],
                "released_in": metadata["released_in"],
                "state": metadata["state"],
                "never_archive": metadata["never_archive"],
                "readable": metadata["readable"],
            }
        )

    archived_documents: list[dict] = []
    archived_actions: list[dict] = []
    review_items: list[dict] = []
    for path, bucket in archived_paths:
        metadata = _doc_metadata(path)
        item = {
            "path": _lexical(path).as_posix(),
            "bucket": bucket,
            "target_release": metadata["target_release"],
            "released_in": metadata["released_in"],
            "readable": metadata["readable"],
        }
        archived_documents.append(item)
        if not metadata["readable"]:
            review_items.append(
                {
                    "kind": "archive-document",
                    "path": item["path"],
                    "reason": "unreadable",
                    "confidence": "none",
                }
            )
        elif not metadata["target_release"]:
            archived_actions.append(
                {
                    "path": item["path"],
                    "field": "target_release",
                    "value": bucket,
                    "scope": "archive",
                    "precondition": _file_precondition(path, field="target_release"),
                    "reason": "archive_bucket_inference",
                    "evidence": f"archive directory: {bucket}",
                    "confidence": "high",
                }
            )
        elif item["target_release"] != bucket:
            review_items.append(
                {
                    "kind": "archive-document",
                    "path": item["path"],
                    "reason": "target_release_differs_from_archive_bucket",
                    "target_release": item["target_release"],
                    "bucket": bucket,
                    "confidence": "high",
                }
            )

    tags = git_tags(repo)
    semver_tags = [tag for tag in tags if parse_release_tag(tag, cfg.release_tracking) is not None]
    missing_target = [
        item
        for item in docs
        if item["readable"] and not item["target_release"] and not item["never_archive"]
    ]
    actions: list[dict] = list(archived_actions)
    if cfg.release_tracking.mode != "disabled" and effective_default:
        for item in missing_target:
            actions.append(
                {
                    "path": item["path"],
                    "field": "target_release",
                    "value": effective_default,
                    "scope": "queue",
                    "precondition": _file_precondition(
                        Path(item["path"]), field="target_release"
                    ),
                    "reason": "repository_default_target",
                    "evidence": "repository/default_target configuration",
                    "confidence": "high",
                }
            )

    config_action: dict | None = None
    if cfg.release_tracking.mode != "disabled":
        desired_tracking: dict[str, object] = {
            "mode": "enabled",
            "tag_pattern": cfg.release_tracking.tag_pattern,
            "archive_group_by": cfg.release_tracking.archive_group_by,
        }
        if effective_default:
            desired_tracking["default_target"] = effective_default
        if cfg.release_tracking.include_prerelease:
            desired_tracking["include_prerelease"] = True
        if cfg.release_tracking.tag_prefix != "v":
            desired_tracking["tag_prefix"] = cfg.release_tracking.tag_prefix
        normalized_archive = archive_dir
        needs_config = (
            cfg.release_tracking.mode != "enabled"
            or cfg.archive_partition != "release"
            or versioned_suffix is not None
        )
        if needs_config:
            config_action = {
                "archive_partition": "release",
                "archive_dir": normalized_archive,
                "release_tracking": desired_tracking,
                "precondition": config_precondition,
                "reason": "enable_release_tracking_and_normalize_archive_route",
            }

    status = "ready"
    diagnostics: list[str] = []
    review_items.extend(_queue_outlier_items(outlier_paths, expected_queue))
    if queue_error is not None:
        status = "needs_review"
        diagnostics.append(str(queue_error))
        review_items.insert(
            0,
            {
                "kind": "queue",
                "path": queue_error.path.as_posix() if queue_error.path else None,
                "reason": queue_error.reason,
                "confidence": "none",
            },
        )
    unreadable_active = [item for item in docs if not item["readable"]]
    if unreadable_active:
        status = "needs_review"
        diagnostics.append(f"{len(unreadable_active)} active document(s) are unreadable")
        review_items.append(
            {
                "kind": "active-document",
                "count": len(unreadable_active),
                "reason": "unreadable",
                "confidence": "none",
            }
        )
    if cfg.release_tracking.mode == "disabled":
        status = "disabled"
        diagnostics.append("project release_tracking.mode is disabled")
        actions = []
        config_action = None
    if queue_error is not None:
        status = "needs_review"
        actions = []
        config_action = None
    elif missing_target and not effective_default:
        status = "needs_review"
        diagnostics.append("active documents have no target_release and no default_target is available")
        review_items.append(
            {
                "kind": "target",
                "count": len(missing_target),
                "reason": "needs_target",
                "confidence": "none",
            }
        )
    elif versioned_suffix is not None:
        diagnostics.append(f"archive_dir ends with a release bucket: {versioned_suffix}")
    if review_items and status == "ready":
        diagnostics.append(f"{len(review_items)} review item(s) require manual confirmation")
    if not _safe_archive_root(archive_dir):
        status = "needs_review"
        diagnostics.append(
            "archive_dir is absolute or parent-traversing and cannot be migrated automatically"
        )
        review_items.append(
            {
                "kind": "archive-route",
                "reason": "unsafe_archive_dir",
                "archive_dir": archive_dir,
                "confidence": "none",
            }
        )
    if (
        queue_error is None
        and not project_yaml.is_file()
        and config_action is None
        and cfg.release_tracking.mode is None
    ):
        config_action = {
            "archive_partition": "release",
            "archive_dir": archive_dir,
            "release_tracking": {
                "mode": "enabled",
                "tag_pattern": "semver",
                "archive_group_by": "minor",
                **({"default_target": effective_default} if effective_default else {}),
            },
            "precondition": config_precondition,
            "reason": "create_project_release_tracking_config",
        }

    after = {
        **before,
        "release_tracking": {
            **before["release_tracking"],
            "mode": "enabled" if cfg.release_tracking.mode != "disabled" else "disabled",
            "default_target": effective_default or before["release_tracking"]["default_target"],
            "source": "migration_manifest",
        },
        "archive_partition": "release" if cfg.release_tracking.mode != "disabled" else before["archive_partition"],
        "archive_dir": archive_dir,
    }
    return {
        "root": repo.as_posix(),
        "status": status,
        "before": before,
        "after": after,
        "inventory": {
            "active_documents": len(docs),
            "archived_documents": len(archived_documents),
            "target_release_set": sum(1 for item in docs if item["target_release"]),
            "target_release_missing": len(missing_target),
            "archived_target_release_missing": sum(
                1 for item in archived_documents if not item["target_release"]
            ),
            "released_in_set": sum(1 for item in docs if item["released_in"]),
            "archive_buckets": buckets,
            "versioned_archive_suffix": versioned_suffix,
            "git_tags": len(tags),
            "semver_tags": len(semver_tags),
            "ignored_tag_count": len(tags) - len(semver_tags),
            "configured_work_dir": expected_queue.as_posix()
            if expected_queue is not None
            else None,
            "configured_work_dir_physical": queue.physical_root.as_posix()
            if queue is not None
            else None,
            "configured_work_dir_is_reparse": queue.is_reparse_root
            if queue is not None
            else None,
            "configured_work_dir_valid": queue_error is None,
        },
        "documents": docs,
        "archived_documents": archived_documents,
        "actions": actions,
        "config_action": config_action,
        "config_precondition": config_precondition,
        "diagnostics": diagnostics,
        "review_items": review_items,
    }


def build_manifest(
    roots: list[Path],
    *,
    excludes: list[str] | None = None,
    default_target: str | None = None,
    migration_id: str | None = None,
    global_path: Path | None = None,
) -> dict:
    """Build a metadata-only migration manifest."""
    if default_target is not None:
        default_target = validate_release_label(default_target, field="default_target")
    normalized_roots = [_lexical(root) for root in roots]
    candidates, excluded = discover_repositories(normalized_roots, excludes=excludes)
    repositories: list[dict] = []
    for candidate in candidates:
        try:
            repositories.append(
                _inventory_repository(
                    candidate,
                    default_target=default_target,
                    excludes=list(excludes or []) + list(DEFAULT_WORKSPACE_EXCLUDES),
                    global_path=global_path,
                )
            )
        except Exception as exc:  # one broken repo must remain reviewable
            repositories.append(
                {
                    "root": candidate.root.as_posix(),
                    "status": "failed",
                    "before": {},
                    "after": {},
                    "inventory": {},
                    "documents": [],
                    "actions": [],
                    "config_action": None,
                    "diagnostics": [str(exc)],
                }
            )
    configured_reparse_queues = {
        _lexical(Path(str(inventory.get("configured_work_dir")))).as_posix()
        for repository in repositories
        for inventory in [repository.get("inventory") or {}]
        if inventory.get("configured_work_dir_is_reparse") is True
        and inventory.get("configured_work_dir")
    }
    if configured_reparse_queues:
        excluded = [
            item
            for item in excluded
            if not (
                item.get("reason") == "reparse-point"
                and item.get("path") in configured_reparse_queues
            )
        ]
    manifest = {
        "schema_version": 1,
        "migration_id": migration_id or f"rm-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "created_at": _utc_now(),
        "roots": [root.as_posix() for root in normalized_roots],
        "excludes": list(excludes or []),
        "default_target": default_target,
        "excluded": excluded,
        "repositories": repositories,
    }
    manifest["manifest_sha256"] = _manifest_fingerprint(manifest)
    return manifest


def _write_json(path: Path, payload: dict) -> None:
    path = _lexical(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _config_after_text(path: Path, action: dict) -> str:
    if path.is_file():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ReleaseTrackingError(
                f"project config を安全に読み直せません: {path}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise ReleaseTrackingError(
                f"project config のルートが map ではありません: {path}"
            )
    else:
        raw = {}
    current_tracking = raw.get("release_tracking")
    if isinstance(current_tracking, dict) and str(current_tracking.get("mode", "")).strip().lower() == "disabled":
        raise ReleaseTrackingError(f"project config is disabled and cannot be overwritten: {path}")
    if not isinstance(current_tracking, dict):
        current_tracking = {}
    desired_tracking = dict(current_tracking)
    desired_tracking.update(dict(action.get("release_tracking") or {}))
    raw["release_tracking"] = desired_tracking
    raw["archive_partition"] = action.get("archive_partition", "release")
    if action.get("archive_dir"):
        if not _safe_archive_root(str(action["archive_dir"])):
            raise ReleaseTrackingError(
                "archive_dir はプロジェクト相対の安全なパスだけ指定できます: "
                f"{action['archive_dir']!r}"
            )
        raw["archive_dir"] = action["archive_dir"]
    return yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)


def _target_after_text(path: Path, value: str) -> str:
    value = validate_release_label(value, field="target_release")
    text = path.open("r", encoding="utf-8", newline="").read()
    return _replace_or_insert(text, "target_release", _format_value("target_release", value))


def _manifest_action_path(
    action: dict,
    *,
    root: Path,
    queue: WorkQueue,
    archive_root: Path,
) -> Path:
    """Validate an action's logical path against the same queue boundary used by inventory."""
    path = _lexical(Path(str(action.get("path", ""))))
    if not _is_under(path, root) or path.suffix.lower() not in {".md", ".markdown"}:
        raise ReleaseTrackingError(
            f"manifest path is outside repository or not Markdown: {path}"
        )
    scope = str(action.get("scope") or "queue").strip().lower()
    if scope == "queue":
        if not _is_under(path, queue.logical_root):
            raise ReleaseTrackingError(
                f"manifest action is outside the configured work queue: {path}"
            )
    elif scope == "archive":
        if not _is_under(path, archive_root):
            raise ReleaseTrackingError(
                f"manifest action is outside the configured archive root: {path}"
            )
    elif scope == "explicit_override":
        if action.get("allow_outside_queue") is not True:
            raise ReleaseTrackingError(
                f"queue-outside action requires an explicit file override: {path}"
            )
    else:
        raise ReleaseTrackingError(f"unknown manifest action scope: {scope}")
    if _path_crosses_reparse(
        path,
        root,
        allowed_reparse_roots=(queue.logical_root,),
    ):
        raise ReleaseTrackingError(
            f"manifest path crosses an unapproved reparse point: {path}"
        )
    return path


def _apply_repository(repo: dict, *, global_path: Path | None = None) -> dict:
    """Preflight and apply all operations for one repository atomically."""
    root = _lexical(Path(str(repo.get("root", ""))))
    if not root.is_dir() or not (root / ".git").exists():
        raise ReleaseTrackingError(f"manifest repository is not a Git repository: {root}")
    if _is_reparse_point(root):
        raise ReleaseTrackingError(f"manifest repository root is a reparse point: {root}")
    cfg = load_config(
        project_dir=root,
        explicit_roots=[str(root)],
        global_path=global_path,
    )
    queue = _resolve_configured_queue(root, cfg)
    archive_route = archive_route_for_project(root, cfg)
    archive_dir, _versioned_suffix = _archive_base(archive_route.archive_dir)
    archive_root = _lexical(root / archive_dir)
    config_path = root / ".docsweep.yaml"
    expected_config = repo.get("config_precondition")
    if expected_config is None:
        config_action = repo.get("config_action")
        expected_config = (
            config_action.get("precondition")
            if isinstance(config_action, dict)
            else None
        )
    if expected_config is None:
        raise ManifestPreflightError(
            "missing_config_precondition",
            f"manifest has no project config precondition: {root}",
        )
    matches, reason = _precondition_matches(config_path, expected_config)
    if not matches:
        raise ManifestPreflightError(
            "stale_manifest",
            f"project config precondition failed for {root}: {reason}",
        )

    operations: list[tuple[Path, str]] = []
    config_action = repo.get("config_action")
    if isinstance(config_action, dict):
        operations.append((config_path, _config_after_text(config_path, config_action)))
    seen_paths = {config_path}
    for action in repo.get("actions") or []:
        if not isinstance(action, dict) or action.get("field") != "target_release":
            continue
        path = _manifest_action_path(
            action,
            root=root,
            queue=queue,
            archive_root=archive_root,
        )
        if path in seen_paths:
            raise ManifestPreflightError(
                "duplicate_operation",
                f"manifest contains duplicate operation path: {path}",
            )
        seen_paths.add(path)
        precondition = action.get("precondition")
        if not isinstance(precondition, dict):
            raise ManifestPreflightError(
                "missing_precondition",
                f"manifest document has no precondition: {path}",
            )
        matches, reason = _precondition_matches(
            path,
            precondition,
            field="target_release",
        )
        if not matches:
            raise ManifestPreflightError(
                "stale_manifest",
                f"document precondition failed for {path}: {reason}",
            )
        if not path.is_file():
            raise ManifestPreflightError(
                "document_missing",
                f"manifest document is missing: {path}",
            )
        operations.append((path, _target_after_text(path, str(action.get("value", "")))))

    if not operations:
        return {"root": root.as_posix(), "status": "skipped", "changed": 0}

    originals: dict[Path, tuple[bool, str]] = {}
    changed: list[Path] = []
    for path, _content in operations:
        existed = path.is_file()
        try:
            original = (
                path.open("r", encoding="utf-8", newline="").read()
                if existed
                else ""
            )
        except (OSError, UnicodeError) as exc:
            raise ManifestPreflightError(
                "preflight_read_failed",
                f"manifest operation cannot be read before writing: {path}: {exc}",
            ) from exc
        originals[path] = (existed, original)

    try:
        for path, content in operations:
            existed, original = originals[path]
            if existed and original == content:
                continue
            write_atomic(path, content)
            changed.append(path)
    except Exception as exc:
        rollback_errors: list[dict] = []
        for path in reversed(changed):
            existed, original = originals[path]
            try:
                if existed:
                    write_atomic(path, original)
                elif path.is_file():
                    path.unlink()
            except Exception as rollback_exc:  # preserve recovery facts for the caller
                rollback_errors.append(
                    {"path": path.as_posix(), "error": str(rollback_exc)}
                )
        raise ReleaseTrackingError(
            json.dumps(
                {
                    "reason": "rollback_failed" if rollback_errors else "apply_failed",
                    "error": str(exc),
                    "changed_paths": [path.as_posix() for path in changed],
                    "rolled_back": not rollback_errors,
                    "rollback_errors": rollback_errors,
                },
                ensure_ascii=False,
            )
        ) from exc
    return {
        "root": root.as_posix(),
        "status": "applied" if changed else "skipped",
        "changed": len(changed),
        "changed_paths": [path.as_posix() for path in changed],
    }


def _journal_cumulative(previous: dict) -> dict[str, dict]:
    """Read durable applied state, keeping it separate from attempt results."""
    raw = previous.get("cumulative")
    if isinstance(raw, dict) and isinstance(raw.get("repositories"), dict):
        return {
            str(root): dict(value)
            for root, value in raw["repositories"].items()
            if isinstance(value, dict)
        }
    legacy = {}
    for item in previous.get("repositories") or []:
        if isinstance(item, dict) and item.get("status") == "applied":
            root = str(item.get("root", ""))
            if root:
                legacy[root] = dict(item)
    return legacy


def _structured_apply_error(exc: Exception) -> dict:
    try:
        value = json.loads(str(exc))
    except (TypeError, ValueError):
        return {"error": str(exc)}
    return value if isinstance(value, dict) else {"error": str(exc)}


def apply_manifest(
    manifest: dict,
    *,
    journal_path: Path | None = None,
    global_path: Path | None = None,
) -> dict:
    """Apply a manifest with identity checks and durable, resumable state."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("未対応の release migration manifest です")
    expected_hash = manifest.get("manifest_sha256")
    actual_hash = _manifest_fingerprint(manifest)
    if not isinstance(expected_hash, str) or expected_hash != actual_hash:
        raise ValueError("manifest の内容が記録済みの fingerprint と一致しません")
    migration_id = str(manifest.get("migration_id") or "unknown")
    journal = _lexical(journal_path or (_JOURNAL_ROOT / f"{migration_id}.json"))
    previous: dict = {}
    if journal.is_file():
        try:
            previous = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReleaseTrackingError(
                f"migration journal を安全に読み込めません: {journal}: {exc}"
            ) from exc
        if not isinstance(previous, dict):
            raise ReleaseTrackingError(f"migration journal の形式が不正です: {journal}")
        journal_hash = previous.get("manifest_sha256")
        if journal_hash is None:
            old_manifest = previous.get("manifest")
            journal_hash = old_manifest.get("manifest_sha256") if isinstance(old_manifest, dict) else None
        if journal_hash != expected_hash:
            raise ReleaseTrackingError(
                "migration journal の manifest fingerprint が今回の manifest と一致しません"
            )

    cumulative = _journal_cumulative(previous)
    repo_results: list[dict] = []
    for repo in manifest.get("repositories") or []:
        if not isinstance(repo, dict):
            continue
        root = str(repo.get("root", ""))
        prior = cumulative.get(root)
        if prior and prior.get("status") == "applied":
            repo_results.append(
                {
                    "root": root,
                    "status": "skipped",
                    "reason": "already_applied",
                    "changed": 0,
                }
            )
            continue
        if repo.get("status") in {"disabled", "needs_review", "failed", "excluded"}:
            repo_results.append(
                {
                    "root": root,
                    "status": "needs_review",
                    "reason": repo.get("status"),
                    "changed": 0,
                }
            )
            continue
        try:
            applied = _apply_repository(repo, global_path=global_path)
            repo_results.append(applied)
            if applied.get("status") in {"applied", "skipped"}:
                cumulative[root] = {
                    "status": "applied",
                    "changed": int(applied.get("changed", 0)),
                    "changed_paths": list(applied.get("changed_paths", [])),
                    "completed_at": _utc_now(),
                }
        except ManifestPreflightError as exc:
            repo_results.append(
                {
                    "root": root,
                    "status": "needs_review",
                    "reason": exc.reason,
                    "changed": 0,
                    "error": str(exc),
                }
            )
        except WorkQueueError as exc:
            repo_results.append(
                {
                    "root": root,
                    "status": "needs_review",
                    "reason": exc.reason,
                    "changed": 0,
                    "error": str(exc),
                }
            )
        except Exception as exc:  # one repository must not stop the others
            details = _structured_apply_error(exc)
            repo_results.append(
                {
                    "root": root,
                    "status": "failed",
                    "changed": 0,
                    **details,
                }
            )

    result = {
        "schema_version": 1,
        "migration_id": migration_id,
        "manifest_sha256": expected_hash,
        "updated_at": _utc_now(),
        "manifest_roots": manifest.get("roots") or [],
        "repositories": repo_results,
        "counts": {
            "applied": sum(item.get("status") == "applied" for item in repo_results),
            "skipped": sum(item.get("status") == "skipped" for item in repo_results),
            "needs_review": sum(item.get("status") == "needs_review" for item in repo_results),
            "failed": sum(item.get("status") == "failed" for item in repo_results),
        },
    }
    attempts = previous.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    attempts = [*attempts, result]
    _write_json(
        journal,
        {
            "schema_version": 1,
            "migration_id": migration_id,
            "manifest_sha256": expected_hash,
            "manifest": manifest,
            "cumulative": {"repositories": cumulative},
            "attempts": attempts,
            **result,
        },
    )
    return result


def load_manifest(path: Path) -> dict:
    try:
        data = json.loads(_lexical(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifest を読み込めません: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("manifest のルートは object である必要があります")
    return data


def migrate_release_tracking(
    roots: list[Path],
    *,
    excludes: list[str] | None = None,
    default_target: str | None = None,
    manifest_path: Path | None = None,
    apply_manifest_path: Path | None = None,
    apply: bool = False,
    journal_path: Path | None = None,
    global_path: Path | None = None,
) -> dict:
    """Run inventory, optional manifest output, and optional explicit apply."""
    if apply and apply_manifest_path is not None:
        raise ValueError("--apply と --apply-manifest は同時に指定できません")
    if apply_manifest_path is not None:
        manifest = load_manifest(apply_manifest_path)
    else:
        manifest = build_manifest(
            roots,
            excludes=excludes,
            default_target=default_target,
            global_path=global_path,
        )
    if manifest_path is not None:
        _write_json(manifest_path, manifest)
    if apply or apply_manifest_path is not None:
        applied = apply_manifest(
            manifest,
            journal_path=journal_path,
            global_path=global_path,
        )
        if manifest_path is None and apply_manifest_path is not None:
            # Keep the original user-edited manifest as the source of truth;
            # the journal carries apply state separately.
            pass
        return {"mode": "apply", "manifest": manifest, "apply": applied}
    return {"mode": "dry-run", "manifest": manifest}


__all__ = [
    "DEFAULT_WORKSPACE_EXCLUDES",
    "apply_manifest",
    "build_manifest",
    "discover_repositories",
    "load_manifest",
    "migrate_release_tracking",
]
