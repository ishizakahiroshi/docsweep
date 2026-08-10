"""OKF version profiles and explicit profile loading.

The OKF specification is human-readable Markdown, but the small part that
docsweep can enforce must be deterministic.  Profiles keep that contract out
of the parser and make a new OKF version a data update when no code behaviour
has changed.

Bundled profiles are always used by default.  A local file or an HTTP(S) URL
can be supplied explicitly by a caller; normal scans and exports never fetch
the network implicitly.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any


OKF_FORMAT = "okf"
# The bundled v0.2 profile currently defines these values.  Keep this public
# constant for callers that used the old helper, but validate custom profiles
# against their own lifecycle_values below rather than against this set.
OKF_LIFECYCLE_STATUSES = frozenset({"draft", "stable", "deprecated"})
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_MAX_PROFILE_BYTES = 1024 * 1024


class OkfProfileError(ValueError):
    """Raised when an OKF profile cannot be loaded or is not valid JSON."""


@dataclass(frozen=True)
class OkfProfile:
    """Validated machine-readable contract for one OKF version."""

    format: str
    spec_version: str
    required_frontmatter: tuple[str, ...]
    reserved_files: dict[str, str]
    unknown_types: str
    unknown_fields: str
    broken_links: str
    missing_optional_fields: str
    lifecycle_values: tuple[str, ...]
    docsweep_status_map: dict[str, str]
    source: str
    sha256: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def lifecycle_default(self) -> str:
        return str(self.raw.get("lifecycle_default") or "stable").strip().lower()

    def is_lifecycle_status(self, value: object) -> bool:
        return str(value).strip().lower() in self.lifecycle_values

    def status_for_state(self, state: str | None) -> str:
        if not state:
            return self.lifecycle_default
        return self.docsweep_status_map.get(state, self.lifecycle_default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "spec_version": self.spec_version,
            "source": self.source,
            "sha256": self.sha256,
            "required_frontmatter": list(self.required_frontmatter),
            "reserved_files": dict(self.reserved_files),
            "unknown_types": self.unknown_types,
            "unknown_fields": self.unknown_fields,
            "broken_links": self.broken_links,
            "missing_optional_fields": self.missing_optional_fields,
            "lifecycle_values": list(self.lifecycle_values),
            "lifecycle_default": self.lifecycle_default,
            "docsweep_status_map": dict(self.docsweep_status_map),
        }


def _profile_path(version: str) -> Path:
    version = str(version).strip()
    if not _VERSION_RE.fullmatch(version):
        raise OkfProfileError(f"不正な OKF version: {version!r}")
    return Path(__file__).with_name("okf_profiles") / f"{version}.json"


def available_okf_profiles() -> list[str]:
    """Return bundled profile versions in deterministic order."""
    directory = Path(__file__).with_name("okf_profiles")
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.json") if _VERSION_RE.fullmatch(p.stem))


def _decode_profile(raw_bytes: bytes, *, source: str, expected_sha256: str | None) -> OkfProfile:
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if expected_sha256 and digest.lower() != expected_sha256.strip().lower():
        raise OkfProfileError(
            f"OKF profile の SHA-256 が一致しません: expected={expected_sha256} actual={digest}"
        )
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OkfProfileError(f"OKF profile の JSON 読み込みに失敗しました: {source}") from exc
    if not isinstance(payload, dict):
        raise OkfProfileError("OKF profile の root は JSON object である必要があります")

    fmt = payload.get("format")
    version = str(payload.get("spec_version") or "").strip()
    required = payload.get("required_frontmatter")
    reserved = payload.get("reserved_files")
    lifecycle = payload.get("lifecycle_values")
    status_map = payload.get("docsweep_status_map") or {}
    if fmt != OKF_FORMAT:
        raise OkfProfileError(f"OKF profile の format が不正です: {fmt!r}")
    if not _VERSION_RE.fullmatch(version):
        raise OkfProfileError(f"OKF profile の spec_version が不正です: {version!r}")
    if not isinstance(required, list) or not all(isinstance(v, str) and v.strip() for v in required):
        raise OkfProfileError("OKF profile の required_frontmatter は空でない文字列配列が必要です")
    if "type" not in required:
        raise OkfProfileError("OKF profile の required_frontmatter には type が必要です")
    if not isinstance(reserved, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in reserved.items()
    ):
        raise OkfProfileError("OKF profile の reserved_files が不正です")
    if not isinstance(lifecycle, list) or not all(isinstance(v, str) and v.strip() for v in lifecycle):
        raise OkfProfileError("OKF profile の lifecycle_values が不正です")
    if not isinstance(status_map, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in status_map.items()
    ):
        raise OkfProfileError("OKF profile の docsweep_status_map が不正です")
    lifecycle_values = tuple(v.strip().lower() for v in lifecycle)
    if not lifecycle_values or len(set(lifecycle_values)) != len(lifecycle_values):
        raise OkfProfileError("OKF profile の lifecycle_values は重複しない配列が必要です")
    default = str(payload.get("lifecycle_default") or "stable").strip().lower()
    if default not in lifecycle_values:
        raise OkfProfileError("OKF profile の lifecycle_default が lifecycle_values にありません")
    normalized_status_map = {
        str(key).strip(): str(value).strip().lower() for key, value in status_map.items()
    }
    if any(value not in lifecycle_values for value in normalized_status_map.values()):
        raise OkfProfileError(
            "OKF profile の docsweep_status_map に lifecycle_values 外の値があります"
        )

    return OkfProfile(
        format=fmt,
        spec_version=version,
        required_frontmatter=tuple(v.strip() for v in required),
        reserved_files=dict(reserved),
        unknown_types=str(payload.get("unknown_types") or "allow"),
        unknown_fields=str(payload.get("unknown_fields") or "preserve"),
        broken_links=str(payload.get("broken_links") or "warning"),
        missing_optional_fields=str(payload.get("missing_optional_fields") or "allow"),
        lifecycle_values=lifecycle_values,
        docsweep_status_map=normalized_status_map,
        source=source,
        sha256=digest,
        raw=dict(payload),
    )


def _read_local_profile(path: Path) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise OkfProfileError(f"OKF profile を読めません: {path}") from exc
    if len(raw) > _MAX_PROFILE_BYTES:
        raise OkfProfileError("OKF profile が大きすぎます")
    return raw


def _read_remote_profile(url: str) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise OkfProfileError(f"OKF profile URL が不正です: {url!r}")
    # 取得内容を固定しない使い方は「今日たまたま取れたルール」で判定することになる。
    # 拒否はしない（明示的な opt-in なので利用者の判断）が、黙って受け入れもしない。
    import sys

    if parsed.scheme == "http":
        print(
            f"warning: OKF profile を平文 HTTP で取得します（改竄を検知できません）: {url}",
            file=sys.stderr,
        )
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "docsweep-okf-profile/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - explicit opt-in URL
            raw = response.read(_MAX_PROFILE_BYTES + 1)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise OkfProfileError(f"OKF profile URL の取得に失敗しました: {url}") from exc
    if len(raw) > _MAX_PROFILE_BYTES:
        raise OkfProfileError("OKF profile が大きすぎます")
    return raw


def _cache_path(cache_dir: Path, digest: str) -> Path:
    return cache_dir / f"{digest}.json"


def load_okf_profile(
    version: str = "0.2",
    *,
    source: str | Path | None = None,
    sha256: str | None = None,
    cache_dir: Path | None = None,
) -> OkfProfile:
    """Load a bundled, local, or explicitly requested remote profile.

    ``source=None`` is deliberately offline.  A URL is fetched only when the
    caller supplied it, and a supplied digest is checked before the profile is
    accepted or cached.
    """
    source_text = str(source) if source is not None else None
    if sha256 is not None and not _SHA256_RE.fullmatch(sha256.strip()):
        raise OkfProfileError("OKF profile の SHA-256 は 64 桁の hexadecimal で指定してください")
    if not source_text or source_text == "bundled":
        path = _profile_path(version)
        raw = _read_local_profile(path)
        return _decode_profile(raw, source=f"bundled:{version}", expected_sha256=sha256)

    parsed = urllib.parse.urlparse(source_text)
    if parsed.scheme in {"http", "https"}:
        if not sha256:
            import sys

            print(
                "warning: OKF profile を SHA-256 なしで取得します。取得先が差し替わっても"
                f"検知できません（--okf-profile-sha256 で固定してください）: {source_text}",
                file=sys.stderr,
            )
        cached: bytes | None = None
        if cache_dir and sha256:
            candidate = _cache_path(Path(cache_dir), sha256.strip().lower())
            if candidate.is_file():
                try:
                    cached = _read_local_profile(candidate)
                except OkfProfileError:
                    cached = None
        raw = cached if cached is not None else _read_remote_profile(source_text)
        profile = _decode_profile(raw, source=source_text, expected_sha256=sha256)
        if cache_dir:
            directory = Path(cache_dir)
            try:
                directory.mkdir(parents=True, exist_ok=True)
                _cache_path(directory, profile.sha256).write_bytes(raw)
            except OSError:
                # Cache is an optimization; a read-only or locked cache must not
                # make an explicitly successful profile load fail.
                pass
        return profile

    path = Path(source_text).expanduser().resolve()
    raw = _read_local_profile(path)
    return _decode_profile(raw, source=path.as_posix(), expected_sha256=sha256)


@lru_cache(maxsize=4)
def bundled_okf_profile(version: str = "0.2") -> OkfProfile:
    """Load a bundled profile once for the normal docsweep parsing paths."""
    return load_okf_profile(version)


def is_okf_lifecycle_status(value: object, profile: OkfProfile | None = None) -> bool:
    """Return whether a value belongs to the selected profile's lifecycle."""
    return (profile or bundled_okf_profile()).is_lifecycle_status(value)
