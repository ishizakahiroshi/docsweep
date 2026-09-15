"""Inventory and safe, manifest-driven migration for release tracking.

The inventory intentionally records metadata only.  It never puts document
body text, frontmatter values other than the release fields, or Git diffs into
the manifest/journal.  Applying a manifest is explicit and is performed one
repository at a time with exact-path rollback on failure.
"""

from __future__ import annotations

import fnmatch
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


def _path_crosses_reparse(path: Path, root: Path) -> bool:
    """Return true when any component between root and path is a reparse point."""
    lexical_path = _lexical(path)
    lexical_root = _lexical(root)
    try:
        relative = lexical_path.relative_to(lexical_root)
    except ValueError:
        return True
    current = lexical_root
    for part in relative.parts:
        current /= part
        if _is_reparse_point(current):
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


def _archive_buckets(repo: Path, archive_dir: str) -> list[str]:
    """List only immediate archive directory names; do not inspect file bodies."""
    base = repo / archive_dir
    try:
        if not base.is_dir() or _path_crosses_reparse(base, repo):
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


def _iter_active_docs(repo: Path, archive_dir: str, patterns: list[str]) -> list[Path]:
    archive_names = {"archive"}
    archive_parts = [part for part in archive_dir.replace("\\", "/").split("/") if part]
    if archive_parts:
        archive_names.add(archive_parts[-1])
    output: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo, topdown=True):
        current = Path(dirpath)
        dirnames[:] = [
            name
            for name in dirnames
            if name not in {".git", ".docsweep", "node_modules", "vendor", "cache"}
            and name not in archive_names
            and not _is_reparse_point(current / name)
            and _excluded(current / name, [repo], patterns) is None
        ]
        for filename in filenames:
            if _MANAGED_FILE_RE.fullmatch(filename):
                output.append(current / filename)
    return sorted(output, key=lambda path: path.as_posix().casefold())


def _iter_archived_docs(repo: Path, archive_dir: str) -> list[tuple[Path, str]]:
    """Return managed files under known release bucket directories only."""
    base = repo / archive_dir
    if not base.is_dir() or _path_crosses_reparse(base, repo):
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
            current = Path(dirpath)
            dirnames[:] = [
                name for name in dirnames if not _is_reparse_point(current / name)
            ]
            for filename in filenames:
                if _MANAGED_FILE_RE.fullmatch(filename):
                    output.append((current / filename, bucket_dir.name))
    return sorted(output, key=lambda item: item[0].as_posix().casefold())


def _doc_metadata(path: Path) -> dict:
    """Read only enough metadata to classify a document; never return body text."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = read_frontmatter_text(text)
    except (OSError, UnicodeError):
        return {"readable": False, "target_release": None, "released_in": None, "state": None, "never_archive": False}
    target = frontmatter.get("target_release")
    released = frontmatter.get("released_in")
    state = frontmatter.get("docsweep_state") or frontmatter.get("status")
    policy = frontmatter.get("docsweep_policy")
    return {
        "readable": True,
        "target_release": str(target).strip() if target is not None and str(target).strip() else None,
        "released_in": str(released).strip() if released is not None and str(released).strip() else None,
        "state": str(state).strip() if state is not None and str(state).strip() else None,
        "never_archive": policy == "never_archive",
    }


def _safe_archive_root(value: str) -> bool:
    """Whether a migration may write the archive root into a repo config."""
    raw = str(value).replace("/", "\\")
    candidate = PureWindowsPath(raw)
    return not (
        candidate.is_absolute()
        or candidate.drive
        or ".." in candidate.parts
    )


def _queue_outliers(repo: Path, config: Config, paths: list[Path]) -> list[dict]:
    """Report docs under docs that are outside the configured work queue."""
    try:
        work_dir, _policy, _secret = project_work_settings(repo, config)
        expected = resolve_work_dir(repo, work_dir)
    except (OSError, ValueError) as exc:
        return [{"kind": "queue", "reason": str(exc), "confidence": "none"}]
    docs_root = repo / "docs"
    outliers: list[dict] = []
    for path in paths:
        if _is_under(path, docs_root) and not _is_under(path, expected):
            outliers.append(
                {
                    "kind": "queue",
                    "path": _lexical(path).as_posix(),
                    "expected_dir": _lexical(expected).as_posix(),
                    "reason": "document_outside_configured_work_dir",
                    "confidence": "high",
                }
            )
    return outliers


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
    cfg = load_config(
        project_dir=repo,
        explicit_roots=[str(repo)],
        global_path=global_path,
    )
    route = archive_route_for_project(repo, cfg)
    before = _config_summary(cfg, raw, route)
    archive_dir, versioned_suffix = _archive_base(route.archive_dir)
    buckets = _archive_buckets(repo, archive_dir)
    effective_default = default_target or cfg.release_tracking.default_target
    if effective_default is not None:
        effective_default = validate_release_label(effective_default, field="default_target")

    docs: list[dict] = []
    active_paths = _iter_active_docs(repo, archive_dir, excludes)
    archived_paths = _iter_archived_docs(repo, archive_dir)
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
                "reason": "enable_release_tracking_and_normalize_archive_route",
            }

    status = "ready"
    diagnostics: list[str] = []
    review_items.extend(_queue_outliers(repo, cfg, active_paths))
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
    if not project_yaml.is_file() and config_action is None and cfg.release_tracking.mode is None:
        config_action = {
            "archive_partition": "release",
            "archive_dir": archive_dir,
            "release_tracking": {
                "mode": "enabled",
                "tag_pattern": "semver",
                "archive_group_by": "minor",
                **({"default_target": effective_default} if effective_default else {}),
            },
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
        },
        "documents": docs,
        "archived_documents": archived_documents,
        "actions": actions,
        "config_action": config_action,
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
    return {
        "schema_version": 1,
        "migration_id": migration_id or f"rm-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "created_at": _utc_now(),
        "roots": [root.as_posix() for root in normalized_roots],
        "excludes": list(excludes or []),
        "default_target": default_target,
        "excluded": excluded,
        "repositories": repositories,
    }


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


def _apply_repository(repo: dict) -> dict:
    """Apply all operations for one repository or roll that repository back."""
    root = _lexical(Path(str(repo.get("root", ""))))
    if not root.is_dir() or not (root / ".git").exists():
        raise ReleaseTrackingError(f"manifest repository is not a Git repository: {root}")
    operations: list[tuple[Path, str, str | None]] = []
    config_action = repo.get("config_action")
    config_path = root / ".docsweep.yaml"
    if isinstance(config_action, dict):
        operations.append((config_path, _config_after_text(config_path, config_action), None))
    for action in repo.get("actions") or []:
        if not isinstance(action, dict) or action.get("field") != "target_release":
            continue
        path = _lexical(Path(str(action.get("path", ""))))
        if not _is_under(path, root) or path.suffix.lower() not in {".md", ".markdown"}:
            raise ReleaseTrackingError(f"manifest path is outside repository or not Markdown: {path}")
        if not path.is_file():
            raise FileNotFoundError(path)
        operations.append((path, _target_after_text(path, str(action.get("value", ""))), None))

    if not operations:
        return {"root": root.as_posix(), "status": "skipped", "changed": 0}

    originals: dict[Path, tuple[bool, str]] = {}
    changed: list[Path] = []
    try:
        for path, content, _ in operations:
            existed = path.is_file()
            original = path.open("r", encoding="utf-8", newline="").read() if existed else ""
            originals[path] = (existed, original)
            if existed and original == content:
                continue
            write_atomic(path, content)
            changed.append(path)
    except Exception:
        for path in reversed(changed):
            existed, original = originals[path]
            try:
                if existed:
                    write_atomic(path, original)
                elif path.is_file():
                    path.unlink()
            except OSError:
                pass
        raise
    return {
        "root": root.as_posix(),
        "status": "applied" if changed else "skipped",
        "changed": len(changed),
        "changed_paths": [path.as_posix() for path in changed],
    }


def apply_manifest(manifest: dict, *, journal_path: Path | None = None) -> dict:
    """Apply a manifest, skipping repositories already recorded as applied."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("未対応の release migration manifest です")
    migration_id = str(manifest.get("migration_id") or "unknown")
    journal = _lexical(journal_path or (_JOURNAL_ROOT / f"{migration_id}.json"))
    previous: dict = {}
    if journal.is_file():
        try:
            previous = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            previous = {}
    repo_results: list[dict] = []
    for repo in manifest.get("repositories") or []:
        if not isinstance(repo, dict):
            continue
        root = str(repo.get("root", ""))
        prior = next(
            (item for item in previous.get("repositories", []) if item.get("root") == root),
            None,
        )
        if prior and prior.get("status") == "applied":
            repo_results.append({"root": root, "status": "skipped", "reason": "already_applied", "changed": 0})
            continue
        if repo.get("status") in {"disabled", "needs_review", "failed", "excluded"}:
            repo_results.append({"root": root, "status": "needs_review", "reason": repo.get("status"), "changed": 0})
            continue
        try:
            repo_results.append(_apply_repository(repo))
        except Exception as exc:  # one repository must not stop the others
            repo_results.append({"root": root, "status": "failed", "changed": 0, "error": str(exc)})

    result = {
        "schema_version": 1,
        "migration_id": migration_id,
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
    _write_json(journal, {"manifest": manifest, **result})
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
        applied = apply_manifest(manifest, journal_path=journal_path)
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
