from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from docsweep.cli import main
from docsweep.inject.api import InjectResult, _write_managed_file, eject
from docsweep.inject.blocks import _strip_managed_blocks


def test_web_dependencies_keep_security_floors():
    project = Path(__file__).parents[1]
    pyproject = (project / "pyproject.toml").read_text(encoding="utf-8")

    assert '"starlette>=1.3.1"' in pyproject
    assert '"python-multipart>=0.0.30"' in pyproject


def test_inject_does_not_rewrite_non_utf8_file(tmp_path: Path):
    path = tmp_path / "CLAUDE.md"
    original = b"user text\x80\n"
    path.write_bytes(original)
    result = InjectResult(project="sample")

    _write_managed_file(path, "generated", {"blocks": {}}, result, dry_run=False)

    assert path.read_bytes() == original
    assert any("UTF-8" in warning for warning in result.warnings)


def test_eject_does_not_rewrite_non_utf8_file(tmp_path: Path):
    path = tmp_path / "AGENTS.md"
    original = b"user text\x80\n"
    path.write_bytes(original)
    result = SimpleNamespace(warnings=[])

    assert _strip_managed_blocks(path, None, result, dry_run=False) is False
    assert path.read_bytes() == original
    assert any("UTF-8" in warning for warning in result.warnings)


def test_eject_does_not_purge_yaml_after_non_utf8_failure(tmp_path: Path):
    project = tmp_path / "repo"
    project.mkdir()
    (project / "CLAUDE.md").write_bytes(b"user text\x80\n")
    yaml_path = project / ".docsweep.yaml"
    yaml_path.write_text("lang: ja\n", encoding="utf-8")

    result = eject(project, purge=True)

    assert result.purged_yaml is False
    assert yaml_path.exists()
    assert any("purge" in warning for warning in result.warnings)


def test_new_rolls_back_all_generated_docs_when_provenance_fails(
    tmp_path: Path, monkeypatch
):
    import docsweep.provenance as provenance

    project = tmp_path / "repo"
    project.mkdir()
    (project / ".git").mkdir()
    global_config = tmp_path / "home" / "config.yaml"
    global_config.parent.mkdir()
    global_config.write_text(
        "provenance:\n"
        "  enabled: true\n"
        "  manager: docsweep\n"
        "  ledger: provenance/ai-executions.csv\n"
        "  actor_key: ishizaka\n",
        encoding="utf-8",
    )

    real_initialize = provenance.initialize_document
    calls = 0

    def fail_on_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("ledger locked")
        return real_initialize(*args, **kwargs)

    monkeypatch.setattr(provenance, "initialize_document", fail_on_second)

    rc = main(
        [
            "new", "plan", "rollback-audit",
            "--split", "2",
            "--work-policy", "shared",
            "--config", str(global_config),
            "--project-dir", str(project),
        ]
    )

    assert rc == 2
    assert not list((project / "docs" / "local").glob("plan_rollback-audit*.md"))
    assert not (tmp_path / "home" / "provenance" / "ai-executions.csv").exists()
