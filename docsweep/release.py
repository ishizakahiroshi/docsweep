"""Git release metadata and release-partition helpers.

The release feature is deliberately opt-in.  This module owns the small,
shared pieces used by the CLI, scanner and archive engine so that a preview and
an apply operation cannot silently calculate different destinations.
"""

from __future__ import annotations

import re
import subprocess
from copy import copy
from dataclasses import dataclass, field
from pathlib import Path

from .config import (
    RELEASE_ARCHIVE_GROUPS,
    RELEASE_TAG_PREFIXES,
    Config,
    ReleaseTrackingConfig,
    archive_partition_for_project,
    release_tracking_for_project,
)
from .models import FileRecord
from .scan import ScannedDoc


class ReleaseTrackingError(ValueError):
    """Release metadata is invalid or insufficient for a requested operation."""


_SEMVER_RE = re.compile(
    r"^(?P<prefix>v?)(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?P<prerelease>-(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?P<build>\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_WINDOWS_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)
_RELEASE_BUCKET_PATH_RE = re.compile(
    r"^(?:v)?\d+(?:\.\d+)?(?:\.x)?$|^\d{4}-Q[1-4]$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReleaseTag:
    """A parsed release tag while preserving its exact original spelling."""

    original: str
    major: int
    minor: int
    patch: int
    prerelease: str | None = None
    prefix: str = ""

    @property
    def is_prerelease(self) -> bool:
        return self.prerelease is not None


@dataclass
class ReleaseCloseResult:
    """One deterministic preview/apply result for ``release close``."""

    tag: str
    dry_run: bool
    target_tag: dict = field(default_factory=dict)
    movable: list[dict] = field(default_factory=list)
    watching: list[dict] = field(default_factory=list)
    incomplete: list[dict] = field(default_factory=list)
    target_mismatch: list[dict] = field(default_factory=list)
    target_unset: list[dict] = field(default_factory=list)
    never_archive: list[dict] = field(default_factory=list)
    tag_missing: list[dict] = field(default_factory=list)
    disabled: list[dict] = field(default_factory=list)
    released_in_conflict: list[dict] = field(default_factory=list)
    collision: list[dict] = field(default_factory=list)
    moved: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        categories = {
            "movable": self.movable,
            "watching": self.watching,
            "incomplete": self.incomplete,
            "target_mismatch": self.target_mismatch,
            "target_unset": self.target_unset,
            "never_archive": self.never_archive,
            "tag_missing": self.tag_missing,
            "disabled": self.disabled,
            "released_in_conflict": self.released_in_conflict,
            "collision": self.collision,
            "moved": self.moved,
            "failed": self.failed,
        }
        return {
            "tag": self.tag,
            "dry_run": self.dry_run,
            "target_tag": self.target_tag,
            **categories,
            "counts": {name: len(items) for name, items in categories.items()},
        }


def validate_release_label(value: str, *, field: str = "release label") -> str:
    """Validate a label before it is used as an archive directory name.

    Labels such as ``v0.9.x`` and ``2026-Q3`` are allowed.  Path syntax,
    control characters, Windows device names and names normalized by Windows
    are rejected.  A label is returned unchanged apart from surrounding
    whitespace removal so the exact user value is retained in frontmatter.
    """
    if not isinstance(value, str):
        raise ReleaseTrackingError(f"{field} は文字列で指定してください")
    label = value.strip()
    if not label:
        raise ReleaseTrackingError(f"{field} は空にできません")
    if label in {".", ".."}:
        raise ReleaseTrackingError(f"{field} に '.' / '..' は指定できません")
    if any(ord(char) < 32 for char in label):
        raise ReleaseTrackingError(f"{field} に制御文字は指定できません")
    if any(char in label for char in ("/", "\\", ":", "<", ">", '"', "|", "?", "*")):
        raise ReleaseTrackingError(f"{field} にパスとして危険な文字は指定できません: {label!r}")
    if label.endswith((".", " ")):
        raise ReleaseTrackingError(f"{field} の末尾に '.' / 空白は指定できません")
    if label.casefold().split(".", 1)[0] in _WINDOWS_RESERVED:
        raise ReleaseTrackingError(f"{field} に Windows 予約名は指定できません: {label!r}")
    return label


def validate_git_tag(value: str, *, field: str = "Git tag") -> str:
    """Validate a Git tag without treating its legal ``/`` as a filesystem path.

    A release tag is an exact Git ref, not an archive directory name.  GitHub
    and Git itself commonly use names such as ``release/2026-09``; rejecting
    those here would make the non-SemVer diagnostic path unreachable.  The
    value is still checked against the ref characters that Git forbids before
    it is passed as an argument to a subprocess.
    """
    if not isinstance(value, str):
        raise ReleaseTrackingError(f"{field} は文字列で指定してください")
    tag = value.strip()
    if not tag:
        raise ReleaseTrackingError(f"{field} は空にできません")
    if tag in {".", ".."} or ".." in tag or "@{" in tag:
        raise ReleaseTrackingError(f"{field} に Git ref として危険な表記があります: {tag!r}")
    if any(ord(char) < 32 for char in tag) or any(
        char in tag for char in (" ", "~", "^", ":", "?", "*", "[", "\\")
    ):
        raise ReleaseTrackingError(f"{field} に Git ref として使えない文字があります: {tag!r}")
    if tag.startswith("/") or tag.endswith("/") or "//" in tag:
        raise ReleaseTrackingError(f"{field} の階層区切りが不正です: {tag!r}")
    if tag.startswith(".") or tag.endswith("."):
        raise ReleaseTrackingError(f"{field} は '.' で始めたり終えたりできません: {tag!r}")
    return tag


def release_archive_root(value: str) -> str:
    """Strip one legacy version suffix from a configured archive path."""
    parts = [part for part in str(value).replace("\\", "/").split("/") if part]
    if parts and _RELEASE_BUCKET_PATH_RE.fullmatch(parts[-1]):
        return "/".join(parts[:-1]) or "."
    return value


def parse_release_tag(
    tag: str, tracking: ReleaseTrackingConfig | None = None
) -> ReleaseTag | None:
    """Parse a configured release tag, preserving the exact tag string.

    ``None`` means the tag does not match the configured pattern.  A malformed
    tag that looks like a SemVer candidate is also treated as non-matching so
    callers can report ignored special tags without aborting a repository scan.
    """
    if not isinstance(tag, str):
        return None
    policy = tracking or ReleaseTrackingConfig(mode="enabled")
    pattern = policy.tag_pattern.strip().lower()
    if pattern not in {"semver", "semver_no_v", "semver_optional_v"}:
        # Named custom patterns are supported when they expose the three
        # numeric groups.  This keeps extension possible without interpreting
        # a pattern as a filesystem path.
        try:
            match = re.fullmatch(policy.tag_pattern, tag)
        except re.error as exc:
            raise ReleaseTrackingError(
                f"tag_pattern が正しい正規表現ではありません: {policy.tag_pattern!r}"
            ) from exc
        if match is None or not {"major", "minor", "patch"} <= set(match.groupdict()):
            return None
        try:
            return ReleaseTag(
                original=tag,
                major=int(match.group("major")),
                minor=int(match.group("minor")),
                patch=int(match.group("patch")),
                prerelease=match.groupdict().get("prerelease"),
                prefix=match.groupdict().get("prefix", ""),
            )
        except (TypeError, ValueError):
            return None

    match = _SEMVER_RE.fullmatch(tag)
    if match is None:
        return None
    prefix = match.group("prefix") or ""
    expected_prefix = "none" if pattern == "semver_no_v" else policy.tag_prefix
    if pattern == "semver_optional_v":
        expected_prefix = "optional"
    if expected_prefix == "v" and prefix != "v":
        return None
    if expected_prefix == "none" and prefix:
        return None
    if expected_prefix not in RELEASE_TAG_PREFIXES:
        raise ReleaseTrackingError(f"tag_prefix が未対応です: {expected_prefix!r}")
    return ReleaseTag(
        original=tag,
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        prerelease=match.group("prerelease"),
        prefix=prefix,
    )


def archive_bucket_for_tag(tag: ReleaseTag, group_by: str) -> str:
    """Return the release bucket for ``patch``, ``minor`` or ``major``."""
    if group_by not in RELEASE_ARCHIVE_GROUPS:
        raise ReleaseTrackingError(f"archive_group_by が未対応です: {group_by!r}")
    prefix = tag.prefix
    if group_by == "patch":
        bucket = tag.original
    elif group_by == "minor":
        bucket = f"{prefix}{tag.major}.{tag.minor}.x"
    else:
        bucket = f"{prefix}{tag.major}.x"
    # Custom tag patterns may expose a prefix containing '/'.  Never let a
    # user-supplied regex turn a release bucket into a path traversal.
    return validate_release_label(bucket, field="release archive bucket")


def release_bucket_for_record(
    record: FileRecord,
    config: Config,
    *,
    project_dir: Path | None = None,
) -> str | None:
    """Resolve the archive bucket, or ``None`` when release partitioning is off.

    ``released_in`` wins over ``target_release``.  The former is never rounded;
    its group bucket is calculated only for the destination while the original
    tag remains in frontmatter.  A missing label fails closed with a repairable
    command in the error text.
    """
    project = project_dir or Path(record.project_root)
    tracking = release_tracking_for_project(config, project)
    partition = archive_partition_for_project(config, project)
    if not tracking.enabled or partition != "release":
        return None

    if record.released_in:
        exact = validate_git_tag(record.released_in, field="released_in")
        if not release_tag_exists(project, exact):
            raise ReleaseTrackingError(
                "released_in の Git tag が対象 project に存在しません: "
                f"{exact!r} ({record.path})"
            )
        parsed = parse_release_tag(exact, tracking)
        if parsed is None:
            raise ReleaseTrackingError(
                f"released_in が設定されたタグパターンに一致しません: {exact!r}"
            )
        if parsed.is_prerelease and not tracking.include_prerelease:
            raise ReleaseTrackingError(
                f"プレリリースタグは設定で許可されていません: {exact!r}"
            )
        return archive_bucket_for_tag(parsed, tracking.archive_group_by)

    if record.target_release:
        return validate_release_label(record.target_release, field="target_release")

    raise ReleaseTrackingError(
        "target_release / released_in が無いため archive 先を決められません: "
        f"{record.path}。docsweep target-release set --path {record.path} --to <label> "
        "で補正してください"
    )


def _close_item(record: FileRecord, *, reason: str | None = None) -> dict:
    item = {
        "path": record.path,
        "project": record.project,
        "project_root": record.project_root,
        "type": record.type,
        "state": record.state,
        "target_release": record.target_release,
        "released_in": record.released_in,
    }
    if reason:
        item["reason"] = reason
    return item


def _target_matches(
    target: str, tag: str, parsed: ReleaseTag, tracking: ReleaseTrackingConfig
) -> bool:
    """Match an exact target or the configured calculated bucket."""
    if target == tag:
        return True
    try:
        return target == archive_bucket_for_tag(parsed, tracking.archive_group_by)
    except ReleaseTrackingError:
        return False


def close_release(
    config: Config,
    tag: str,
    *,
    dry_run: bool = False,
    project: str | None = None,
) -> ReleaseCloseResult:
    """Close one exact Git tag and optionally archive eligible work documents.

    The classification is completed before any write.  ``dry_run`` and apply
    therefore expose the same candidate lists; apply only performs the listed
    ``movable`` operations after the tag and target checks have passed.
    """
    from .engine import run_scan
    from .services.frontmatter import update_frontmatter_field

    exact_tag = validate_git_tag(tag, field="Git tag")
    result = ReleaseCloseResult(tag=exact_tag, dry_run=dry_run)
    scan_result = run_scan(config)
    if scan_result.errors:
        result.failed.extend(
            {"path": None, "error": error} for error in scan_result.errors
        )

    by_project: dict[str, list[ScannedDoc]] = {}
    for doc in scan_result.docs:
        if project and doc.record.project != project:
            continue
        by_project.setdefault(doc.record.project_root, []).append(doc)

    docs_to_move: list[ScannedDoc] = []
    planned_destinations: set[str] = set()
    for project_root, docs in sorted(by_project.items()):
        project_path = Path(project_root)
        tracking = release_tracking_for_project(config, project_path)
        partition = archive_partition_for_project(config, project_path)
        project_info = result.target_tag.setdefault(
            "projects", []
        )
        if not tracking.enabled or partition != "release":
            project_info.append(
                {
                    "project": project_root,
                    "mode": tracking.mode,
                    "archive_partition": partition,
                    "status": "disabled",
                }
            )
            for doc in docs:
                result.disabled.append(_close_item(doc.record, reason="tracking_disabled"))
            continue

        parsed = parse_release_tag(exact_tag, tracking)
        exists = release_tag_exists(project_path, exact_tag)
        info = {
            "project": project_root,
            "mode": tracking.mode,
            "archive_partition": partition,
            "exists": exists,
            "recognized": parsed is not None,
            "tag_pattern": tracking.tag_pattern,
        }
        if parsed is not None:
            info["bucket"] = archive_bucket_for_tag(parsed, tracking.archive_group_by)
            info["prerelease"] = parsed.is_prerelease
        project_info.append(info)

        if parsed is None:
            for doc in docs:
                result.tag_missing.append(
                    _close_item(doc.record, reason="tag_not_recognized")
                )
            continue
        if parsed.is_prerelease and not tracking.include_prerelease:
            for doc in docs:
                result.tag_missing.append(
                    _close_item(doc.record, reason="prerelease_not_allowed")
                )
            continue
        if not exists:
            for doc in docs:
                result.tag_missing.append(
                    _close_item(doc.record, reason="git_tag_not_found")
                )
            continue

        for doc in docs:
            rec = doc.record
            if rec.docsweep_policy == "never_archive":
                result.never_archive.append(_close_item(rec, reason="never_archive"))
                continue
            # released_in is the factual release record and therefore wins
            # over target_release.  A document that was already tied to this
            # exact tag can still be retried after a previous archive failure;
            # a different factual tag is a conflict before target matching.
            if rec.released_in:
                if rec.released_in != exact_tag:
                    result.released_in_conflict.append(
                        _close_item(rec, reason="released_in_differs")
                    )
                    continue
            else:
                target = (rec.target_release or "").strip()
                if not target:
                    result.target_unset.append(_close_item(rec, reason="target_unset"))
                    continue
                if not _target_matches(target, exact_tag, parsed, tracking):
                    result.target_mismatch.append(_close_item(rec, reason="target_mismatch"))
                    continue
            if rec.state == "watching":
                result.watching.append(_close_item(rec, reason="watching"))
                continue
            if rec.state not in {"done", "discarded"} or not rec.archivable:
                result.incomplete.append(_close_item(rec, reason="not_archivable"))
                continue
            # Check the exact destination during classification so dry-run and
            # apply expose the same candidate set.  This release path obeys
            # the strict collision contract; legacy flat archive operations
            # retain their historical _2 deduplication behavior.
            candidate = copy(doc)
            candidate.record = copy(rec)
            candidate.record.released_in = exact_tag
            try:
                from .engine import _archive_dir_for

                archive_dir = _archive_dir_for(candidate, config)
                destination = (project_path / archive_dir / Path(rec.path).name).resolve()
            except (OSError, UnicodeError, ValueError) as exc:
                result.failed.append({"path": rec.path, "error": str(exc)})
                continue
            destination_key = destination.as_posix().casefold()
            if destination.exists() or destination_key in planned_destinations:
                result.collision.append(
                    _close_item(rec, reason=f"destination_exists: {destination.as_posix()}")
                )
                continue
            planned_destinations.add(destination_key)
            result.movable.append(_close_item(rec, reason="release_target"))
            docs_to_move.append(doc)

    result.target_tag.setdefault("tag", exact_tag)
    if dry_run:
        return result

    for doc in docs_to_move:
        rec = doc.record
        previous_released_in = rec.released_in
        metadata_mtime: float | None = None
        try:
            if rec.released_in != exact_tag:
                metadata = update_frontmatter_field(
                    Path(rec.path), "released_in", exact_tag, expected_mtime=rec.mtime
                )
                metadata_mtime = metadata.new_mtime
                rec.released_in = exact_tag
            move = _archive_doc_for_release(doc, config)
            result.moved.append(move.to_dict())
        except (OSError, UnicodeError, ValueError) as exc:
            failure: dict[str, object] = {"path": rec.path, "error": str(exc)}
            if metadata_mtime is not None:
                try:
                    update_frontmatter_field(
                        Path(rec.path),
                        "released_in",
                        previous_released_in,
                        expected_mtime=metadata_mtime,
                    )
                    rec.released_in = previous_released_in
                    failure["released_in_rolled_back"] = True
                except (OSError, UnicodeError, ValueError) as rollback_exc:
                    failure["released_in_rollback_error"] = str(rollback_exc)
            result.failed.append(failure)
    return result


def _archive_doc_for_release(doc: ScannedDoc, config: Config):
    """Local import wrapper to keep release helpers independent of engine import time."""
    from .engine import archive_doc

    return archive_doc(doc, config, dry_run=False, strict_collision=True)


def git_tags(project_root: Path) -> list[str]:
    """List local Git tags without reading repository file contents."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "tag", "--list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def release_tag_exists(project_root: Path, tag: str) -> bool:
    """Check exact local tag existence without shell interpolation."""
    try:
        exact = validate_git_tag(tag)
    except ReleaseTrackingError:
        return False
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "rev-parse",
                "--verify",
                "--quiet",
                f"refs/tags/{exact}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def release_tracking_diagnostic(config: Config, *, project_dir: Path | None = None) -> dict:
    """Return a JSON-safe, read-only configuration diagnostic."""
    project = project_dir or config.project_dir
    tracking = (
        release_tracking_for_project(config, project)
        if project is not None
        else config.release_tracking
    )
    partition = (
        archive_partition_for_project(config, project)
        if project is not None
        else config.archive_partition
    )
    payload: dict[str, object] = {
        "mode": tracking.mode,
        "configured": tracking.mode is not None,
        "enabled": tracking.enabled,
        "archive_partition": partition,
        "tag_pattern": tracking.tag_pattern,
        "tag_prefix": tracking.tag_prefix,
        "archive_group_by": tracking.archive_group_by,
        "default_target": tracking.default_target,
        "include_prerelease": tracking.include_prerelease,
    }
    if project is not None:
        tags = git_tags(Path(project))
        recognized = sum(
            parse_release_tag(tag, tracking) is not None for tag in tags
        )
        payload.update(
            {
                "git_tag_count": len(tags),
                "recognized_tag_count": recognized,
                "ignored_tag_count": len(tags) - recognized,
            }
        )
    return payload


__all__ = [
    "ReleaseTag",
    "ReleaseTrackingError",
    "archive_bucket_for_tag",
    "git_tags",
    "parse_release_tag",
    "release_bucket_for_record",
    "release_archive_root",
    "release_tag_exists",
    "release_tracking_diagnostic",
    "validate_git_tag",
    "validate_release_label",
]
