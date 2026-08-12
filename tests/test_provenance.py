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


_SESSION_ID = "11111111-2222-4333-8444-555555555555"


def _fake_claude_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Claude Code の transcript を模した実ファイルを作り、env をそこへ向ける。

    ディレクトリ名は cwd 由来だが、``docsweep new --project-dir`` は別リポの md を
    作れるため、解決は glob で行う。ここでも project_dir とは無関係な名前を使い、
    cwd からの機械変換に依存していないことを担保する。
    """
    claude_dir = tmp_path / "claude"
    (claude_dir / "projects" / "D--dev-example").mkdir(parents=True)
    transcript = claude_dir / "projects" / "D--dev-example" / f"{_SESSION_ID}.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _SESSION_ID)
    return transcript


def test_session_log_is_recorded_from_claude_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project, config = _config(tmp_path)
    transcript = _fake_claude_transcript(tmp_path, monkeypatch)
    doc = new_doc("plan", "session-log", project_dir=project, config=config, offset_days={})

    initialize_document(doc.path, project_dir=project, config=config, metadata=_metadata())

    frontmatter = read_frontmatter(doc.path) or {}
    assert frontmatter["ai_session_logs"] == [str(transcript)]


def test_session_log_is_absent_when_transcript_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """パスは組めてもファイルが無ければ書かない（存在しない証跡を残さない）。"""
    project, config = _config(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _SESSION_ID)
    doc = new_doc("plan", "missing-log", project_dir=project, config=config, offset_days={})

    initialize_document(doc.path, project_dir=project, config=config, metadata=_metadata())

    assert "ai_session_logs" not in (read_frontmatter(doc.path) or {})


def test_session_log_is_not_written_to_a_shared_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """絶対パスは OS ユーザー名を含むため private queue に限る。"""
    project, config = _config(tmp_path)
    _fake_claude_transcript(tmp_path, monkeypatch)
    doc = new_doc("plan", "shared-queue", project_dir=project, config=config, offset_days={})
    config.work_policy = "shared"

    initialize_document(doc.path, project_dir=project, config=config, metadata=_metadata())

    assert "ai_session_logs" not in (read_frontmatter(doc.path) or {})


def test_explicit_session_log_wins_over_claude_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Claude 以外の CLI は明示指定で入れる（自動解決の逃げ道）。"""
    project, config = _config(tmp_path)
    _fake_claude_transcript(tmp_path, monkeypatch)
    explicit = tmp_path / "rollout-2026-08-12.jsonl"
    explicit.write_text("{}\n", encoding="utf-8")
    doc = new_doc("plan", "explicit-log", project_dir=project, config=config, offset_days={})
    metadata = AIMetadata.resolve(
        agent="codex",
        runtime="many-ai-cli",
        provider="openai",
        model_source="orchestrator",
        actor_key="ishizaka",
        session_log=str(explicit),
    )

    initialize_document(doc.path, project_dir=project, config=config, metadata=metadata)

    assert (read_frontmatter(doc.path) or {})["ai_session_logs"] == [str(explicit)]


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
