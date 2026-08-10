from __future__ import annotations

import csv
import os
from pathlib import Path

import yaml
import pytest

from docsweep.config import load_config
from docsweep.provenance import (
    AIMetadata,
    check_document,
    finish_execution,
    initialize_document,
    start_execution,
)
from docsweep.services.frontmatter import read_frontmatter
from docsweep.templates_gen import new_doc


def _config(tmp_path: Path):
    global_path = tmp_path / "home" / "config.yaml"
    global_path.parent.mkdir()
    global_path.write_text(
        "provenance:\n"
        "  enabled: true\n"
        "  manager: docsweep\n"
        "  ledger: provenance/ai-executions.csv\n"
        "  actor_key: ishizaka\n",
        encoding="utf-8",
    )
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".git").mkdir()
    return project, load_config(project_dir=project, global_path=global_path)


def _metadata() -> AIMetadata:
    return AIMetadata.resolve(
        agent="codex",
        runtime="many-ai-cli",
        provider="openai",
        model_id="unknown",
        model_display="GPT-5",
        reasoning_profile="unknown",
        model_source="orchestrator",
        actor_key="ishizaka",
    )


def test_initialize_start_finish_and_check(tmp_path: Path):
    project, config = _config(tmp_path)
    doc = new_doc("plan", "provenance", project_dir=project, config=config, offset_days={})

    initialized = initialize_document(
        doc.path,
        project_dir=project,
        config=config,
        metadata=_metadata(),
    )
    assert initialized["status"] == "initialized"
    frontmatter = read_frontmatter(doc.path)
    assert frontmatter is not None
    assert frontmatter["work_id"].startswith("WK-")
    assert frontmatter["ai_author_agent"] == "codex"
    assert frontmatter["ai_execution_refs"] == [initialized["execution_id"]]

    started = start_execution(
        doc.path,
        project_dir=project,
        config=config,
        contexts=["C1"],
        role="implementation",
        metadata=_metadata(),
    )
    body = doc.path.read_text(encoding="utf-8")
    assert "| C | 内容 | 種別 | AI実行 |" in body
    assert started["execution_id"] in body

    finished = finish_execution(
        started["execution_id"],
        config=config,
        result="completed",
        evidence_refs="pytest",
    )
    assert finished["status"] == "finished"
    checked = check_document(doc.path, project_dir=project, config=config)
    assert checked["valid"] is True
    assert checked["errors"] == []

    with config.provenance_ledger.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [row["role"] for row in rows] == ["authoring", "implementation"]
    assert rows[1]["result"] == "completed"
    assert rows[1]["evidence_refs"] == "pytest"


def test_repo_manager_delegates_without_creating_generic_ledger(tmp_path: Path):
    project, config = _config(tmp_path)
    (project / ".docsweep.yaml").write_text(
        "provenance:\n"
        "  manager: repo\n"
        "  delegate_skill: cpni-doc\n",
        encoding="utf-8",
    )
    config = load_config(project_dir=project, global_path=tmp_path / "home" / "config.yaml")
    doc = new_doc("plan", "delegated", project_dir=project, config=config, offset_days={})

    result = initialize_document(
        doc.path,
        project_dir=project,
        config=config,
        metadata=_metadata(),
    )
    assert result["status"] == "delegated"
    assert result["delegate_skill"] == "cpni-doc"
    assert not config.provenance_ledger.exists()
    assert "ai_author_agent" not in (read_frontmatter(doc.path) or {})


def test_provenance_config_resolves_global_and_project_paths(tmp_path: Path):
    project, config = _config(tmp_path)
    assert config.provenance_enabled is True
    assert config.provenance_manager == "docsweep"
    assert config.provenance_actor_key == "ishizaka"
    assert config.provenance_ledger == (tmp_path / "home" / "provenance" / "ai-executions.csv")

    (project / ".docsweep.yaml").write_text(
        yaml.safe_dump({"provenance": {"ledger": "local/aix.csv", "project_id": "sample"}}),
        encoding="utf-8",
    )
    config = load_config(project_dir=project, global_path=tmp_path / "home" / "config.yaml")
    assert config.provenance_ledger == (project / "local" / "aix.csv")
    assert config.provenance_project_id == "sample"


def test_project_relative_route_is_kept_when_work_dir_is_symlink(tmp_path: Path):
    project, config = _config(tmp_path)
    private_queue = tmp_path / "private-queue"
    private_queue.mkdir()
    docs = project / "docs"
    docs.mkdir()
    try:
        os.symlink(private_queue, docs / "local", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink is unavailable: {exc}")
    doc = new_doc("plan", "linked", project_dir=project, config=config, offset_days={})

    initialize_document(doc.path, project_dir=project, config=config, metadata=_metadata())

    with config.provenance_ledger.open("r", encoding="utf-8", newline="") as fh:
        row = next(csv.DictReader(fh))
    assert row["work_path"] == "docs/local/plan_linked.md"
