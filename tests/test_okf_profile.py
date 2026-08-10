"""OKF profile loading and read-only conformance checks."""

from __future__ import annotations

import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

import pytest

from docsweep.okf import OkfProfileError, load_okf_profile
from docsweep.okf_check import check_bundle


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _custom_profile() -> dict:
    return {
        "format": "okf",
        "spec_version": "9.9",
        "lifecycle_default": "working",
        "lifecycle_values": ["working", "published"],
        "required_frontmatter": ["type"],
        "reserved_files": {"index.md": "directory_index", "log.md": "update_log"},
        "unknown_types": "allow",
        "unknown_fields": "preserve",
        "broken_links": "warning",
        "missing_optional_fields": "allow",
        "docsweep_status_map": {
            "planned": "working",
            "in-progress": "working",
            "done": "published",
        },
    }


def test_bundled_profile_is_offline_and_data_driven():
    profile = load_okf_profile()
    assert profile.spec_version == "0.2"
    assert profile.required_frontmatter == ("type",)
    assert profile.status_for_state("done") == "stable"
    assert profile.status_for_state("watching") == "draft"


def test_local_profile_and_sha256_are_supported(tmp_path: Path):
    profile_path = tmp_path / "okf-9.9.json"
    raw = json.dumps(_custom_profile(), ensure_ascii=False, indent=2).encode("utf-8")
    profile_path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()

    profile = load_okf_profile(source=profile_path, sha256=digest)
    assert profile.spec_version == "9.9"
    assert profile.lifecycle_default == "working"
    assert profile.status_for_state("done") == "published"

    with pytest.raises(OkfProfileError, match="SHA-256"):
        load_okf_profile(source=profile_path, sha256="0" * 64)
    with pytest.raises(OkfProfileError, match="64 桁"):
        load_okf_profile(source=profile_path, sha256="not-a-digest")


def test_explicit_remote_profile_can_be_pinned_and_cached(tmp_path: Path, monkeypatch):
    raw = json.dumps(_custom_profile()).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    calls: list[str] = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()
            return False

    def fake_urlopen(request, timeout=0):
        calls.append(request.full_url)
        assert timeout == 15
        return Response(raw)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    profile = load_okf_profile(
        source="https://raw.githubusercontent.com/example/repo/abc/okf.json",
        sha256=digest,
        cache_dir=tmp_path / "cache",
    )
    assert profile.spec_version == "9.9"
    assert calls
    assert (tmp_path / "cache" / f"{digest}.json").is_file()


def test_okf_check_allows_unknown_type_fields_and_broken_links(tmp_path: Path):
    bundle = tmp_path / "bundle"
    _write(
        bundle / "index.md",
        "---\nokf_version: \"0.2\"\n---\n# Bundle\n\n* [note](note.md)\n",
    )
    _write(
        bundle / "note.md",
        "---\n"
        "type: experiment\n"
        "status: draft\n"
        "producer_extra: kept\n"
        "---\n"
        "# Note\n\n[missing](missing.md)\n",
    )
    result = check_bundle(bundle, load_okf_profile())
    assert result.ok
    assert any(issue.code == "broken_link" for issue in result.warnings)
    assert not any(issue.severity == "error" for issue in result.issues)


def test_okf_check_reports_structural_errors_without_writing(tmp_path: Path):
    bundle = tmp_path / "bundle"
    _write(bundle / "bad.md", "# no frontmatter\n")
    _write(bundle / "nested" / "index.md", "---\ntype: index\n---\n# Nested\n")
    _write(bundle / "log.md", "## not-a-date\n\nentry\n")
    before = {
        path: path.read_bytes()
        for path in bundle.rglob("*.md")
    }

    result = check_bundle(bundle, load_okf_profile())

    assert not result.ok
    codes = {issue.code for issue in result.errors}
    assert {"missing_frontmatter", "reserved_index_frontmatter", "log_date_heading"} <= codes
    assert {path: path.read_bytes() for path in bundle.rglob("*.md")} == before


def test_exported_zip_is_checkable_as_okf(tmp_path: Path):
    """The checker accepts the root index and normalized concepts in a Bundle."""
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "index.md",
            "---\nokf_version: \"0.2\"\n---\n# Bundle\n\n* [note](note.md)\n",
        )
        archive.writestr("note.md", "---\ntype: note\nstatus: draft\n---\n# Note\n")

    result = check_bundle(zip_path, load_okf_profile())
    assert result.ok
    assert result.files_checked == 2


def test_okf_check_cli_json(tmp_path: Path, capsys):
    from docsweep import cli

    bundle = tmp_path / "bundle"
    _write(bundle / "note.md", "---\ntype: note\nstatus: draft\n---\n# Note\n")

    assert cli.main(["okf-check", str(bundle), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["profile"]["spec_version"] == "0.2"
