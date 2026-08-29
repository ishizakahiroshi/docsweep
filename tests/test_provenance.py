from __future__ import annotations

import csv
import os
from pathlib import Path

import yaml
import pytest

from docsweep.config import load_config
from docsweep.provenance import (
    SESSION_LOG_FIELD,
    AIMetadata,
    _context_execution_refs,
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
        "  actor_key: ishizaka\n"
        "work_dir: docs/local\n"
        "work_policy: private\n",
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
    assert "| C | 種別 | 内容 | 備考/注意点 | AI実行 | 実行モデル |" in body
    assert started["execution_id"] in body
    assert "implementation: openai / unknown / unknown" in body
    assert _context_execution_refs(body) == {started["execution_id"]}

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


def test_context_execution_model_columns_follow_execution_order(tmp_path: Path):
    project, config = _config(tmp_path)
    doc = new_doc("plan", "model-order", project_dir=project, config=config, offset_days={})
    initialize_document(doc.path, project_dir=project, config=config, metadata=_metadata())

    implementation = start_execution(
        doc.path,
        project_dir=project,
        config=config,
        contexts=["C1"],
        role="implementation",
        metadata=_metadata(),
    )
    review_metadata = AIMetadata.resolve(
        agent="claude",
        runtime="claude-code",
        provider="anthropic",
        model_id="opus-5",
        model_display="Opus 5",
        reasoning_profile="high",
        model_source="runtime",
        actor_key="ishizaka",
    )
    review = start_execution(
        doc.path,
        project_dir=project,
        config=config,
        contexts=["C1"],
        role="review",
        metadata=review_metadata,
    )

    body = doc.path.read_text(encoding="utf-8")
    row = next(line for line in body.splitlines() if line.startswith("| C1 |"))
    cells = [part.strip() for part in row.strip().strip("|").split("|")]

    assert cells[4] == f"{implementation['execution_id']}; {review['execution_id']}"
    assert cells[5] == "implementation: openai / unknown / unknown; review: anthropic / opus-5 / high"
    assert _context_execution_refs(body) == {
        implementation["execution_id"],
        review["execution_id"],
    }


def test_context_execution_model_uses_unknown_for_empty_values(tmp_path: Path):
    project, config = _config(tmp_path)
    doc = new_doc("plan", "model-unknown", project_dir=project, config=config, offset_days={})
    initialize_document(doc.path, project_dir=project, config=config, metadata=_metadata())

    started = start_execution(
        doc.path,
        project_dir=project,
        config=config,
        contexts=["C1"],
        role="implementation",
        metadata=AIMetadata(
            provider="",
            model_id="",
            reasoning_profile="",
        ),
    )

    body = doc.path.read_text(encoding="utf-8")
    assert "implementation: unknown / unknown / unknown" in body
    assert _context_execution_refs(body) == {started["execution_id"]}


def test_context_execution_model_is_added_after_existing_ai_column(tmp_path: Path):
    project, config = _config(tmp_path)
    doc = new_doc("plan", "existing-ai-column", project_dir=project, config=config, offset_days={})
    initialize_document(doc.path, project_dir=project, config=config, metadata=_metadata())
    body = doc.path.read_text(encoding="utf-8")
    body = body.replace(
        "| C | 種別 | 内容 | 備考/注意点 |\n"
        "|---|---|---|---|\n"
        "| C1 | planned | <TODO> | — |",
        "| C | 種別 | 内容 | 備考/注意点 | AI実行 |\n"
        "|---|---|---|---|---|\n"
        "| C1 | planned | <TODO> | — | |",
    )
    doc.path.write_text(body, encoding="utf-8")

    start_execution(
        doc.path,
        project_dir=project,
        config=config,
        contexts=["C1"],
        role="implementation",
        metadata=_metadata(),
    )

    updated = doc.path.read_text(encoding="utf-8")
    assert "| C | 種別 | 内容 | 備考/注意点 | AI実行 | 実行モデル |" in updated


def test_context_execution_model_pads_executions_recorded_before_the_column(tmp_path: Path):
    """列の導入前に記録された実行の分を埋め、位置で対応づけて読めるようにする。

    埋めないと 1 件目のモデル情報が 2 件目の実行 ID のものとして誤読される（欠落より悪い）。
    """
    project, config = _config(tmp_path)
    doc = new_doc("plan", "legacy-ai-column", project_dir=project, config=config, offset_days={})
    initialize_document(doc.path, project_dir=project, config=config, metadata=_metadata())
    body = doc.path.read_text(encoding="utf-8")
    legacy_id = "AIX-20260101T000000000-deadbeef"
    body = body.replace(
        "| C | 種別 | 内容 | 備考/注意点 |\n"
        "|---|---|---|---|\n"
        "| C1 | planned | <TODO> | — |",
        "| C | 種別 | 内容 | 備考/注意点 | AI実行 |\n"
        "|---|---|---|---|---|\n"
        f"| C1 | planned | <TODO> | — | {legacy_id} |",
    )
    doc.path.write_text(body, encoding="utf-8")

    started = start_execution(
        doc.path,
        project_dir=project,
        config=config,
        contexts=["C1"],
        role="implementation",
        metadata=_metadata(),
    )

    updated = doc.path.read_text(encoding="utf-8")
    row = next(line for line in updated.splitlines() if line.startswith("| C1 |"))
    cells = [part.strip() for part in row.strip().strip("|").split("|")]
    ai_refs = [part.strip() for part in cells[4].split(";") if part.strip()]
    model_refs = [part.strip() for part in cells[5].split(";") if part.strip()]

    assert ai_refs == [legacy_id, started["execution_id"]]
    assert len(model_refs) == len(ai_refs)
    assert model_refs[0] == "unknown: unknown / unknown / unknown"
    assert model_refs[1].startswith("implementation: ")


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


def test_session_log_is_not_written_for_unenforced_legacy_private_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """旧設定の nominal private は、絶対 session path を frontmatter に漏らさない。"""
    project, config = _config(tmp_path)
    _fake_claude_transcript(tmp_path, monkeypatch)
    config.work_dir_explicit = False
    config.work_policy_explicit = False
    config.loaded_from_config = True
    doc = new_doc("plan", "legacy-private", project_dir=project, config=config, offset_days={})

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


# --- provenance が有効なのに AI 情報が既定値のまま（plan_provenance-new-guard） ----


def _unresolved() -> AIMetadata:
    """``--ai-*`` も環境変数も無いときに resolve が返す形。"""
    return AIMetadata.resolve(actor_default="ishizaka")


def test_unresolved_metadata_is_detected_without_hardcoding_unknown() -> None:
    assert _unresolved().is_unresolved() is True
    assert _metadata().is_unresolved() is False


def test_guard_warns_only_when_provenance_is_enabled(tmp_path: Path, capsys) -> None:
    from docsweep.provenance_hint import warn_if_unresolved

    _project, config = _config(tmp_path)
    assert warn_if_unresolved(_unresolved(), config=config, command="new") is True
    err = capsys.readouterr().err
    assert "unknown" in err
    assert "--ai-agent" in err
    assert "DOCSWEEP_AI_AGENT" in err

    # 実値が取れているときは黙る。
    assert warn_if_unresolved(_metadata(), config=config, command="new") is False
    assert capsys.readouterr().err == ""


def test_guard_is_silent_when_provenance_is_disabled(tmp_path: Path, capsys) -> None:
    from docsweep.provenance_hint import warn_if_unresolved

    global_path = tmp_path / "plain.yaml"
    global_path.write_text("roots: []\n", encoding="utf-8")
    project = tmp_path / "repo2"
    (project / ".git").mkdir(parents=True)
    config = load_config(project_dir=project, global_path=global_path)

    assert warn_if_unresolved(_unresolved(), config=config, command="new") is False
    assert capsys.readouterr().err == ""


def _ledger_rows(config) -> list[dict]:
    with Path(config.provenance_ledger).open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_init_update_repairs_ledger_and_frontmatter_together(tmp_path: Path) -> None:
    """``unknown`` で作った work を、台帳と frontmatter の両方まとめて実値へ直す。"""
    project, config = _config(tmp_path)
    path = new_doc("plan", "unknown-author", project_dir=project, config=config, offset_days={}).path
    initialize_document(path, project_dir=project, config=config, metadata=_unresolved())

    before = [r for r in _ledger_rows(config) if r["role"] == "authoring"][0]
    assert before["agent"] == "unknown"

    result = initialize_document(
        path,
        project_dir=project,
        config=config,
        metadata=_metadata(),
        update=True,
    )

    assert result["status"] == "updated"
    assert result["changed"] is True

    after = [r for r in _ledger_rows(config) if r["role"] == "authoring"][0]
    assert after["agent"] == "codex"
    assert after["provider"] == "openai"
    assert after["model_display"] == "GPT-5"
    # 変えてはいけない列。
    assert after["execution_id"] == before["execution_id"]
    assert after["work_id"] == before["work_id"]
    assert after["started_at"] == before["started_at"]
    assert after["role"] == before["role"]
    assert after["work_path"] == before["work_path"]
    # 訂正したことが読み取れる。
    assert "ai-metadata-corrected" in after["notes"]

    data = read_frontmatter(path)
    assert data["ai_author_agent"] == "codex"
    assert data["ai_author_model_display"] == "GPT-5"
    assert check_document(path, project_dir=project, config=config)["valid"] is True


def test_init_without_update_still_reports_existing(tmp_path: Path) -> None:
    project, config = _config(tmp_path)
    path = new_doc("plan", "left-alone", project_dir=project, config=config, offset_days={}).path
    initialize_document(path, project_dir=project, config=config, metadata=_unresolved())

    result = initialize_document(
        path, project_dir=project, config=config, metadata=_metadata()
    )

    assert result["status"] == "existing"
    assert result["changed"] is False
    assert read_frontmatter(path)["ai_author_agent"] == "unknown"


def test_init_update_without_an_authoring_row_is_an_explicit_error(tmp_path: Path) -> None:
    from docsweep.provenance import ProvenanceError

    project, config = _config(tmp_path)
    path = new_doc("plan", "no-authoring-row", project_dir=project, config=config, offset_days={}).path
    initialize_document(path, project_dir=project, config=config, metadata=_unresolved())
    # 台帳だけ失う（別マシンから持ってきた md 等）。
    Path(config.provenance_ledger).unlink()

    with pytest.raises(ProvenanceError):
        initialize_document(
            path, project_dir=project, config=config, metadata=_metadata(), update=True
        )


# --- ai_session_logs の追記（plan_agent-session-log-in-work-md C3） --------------


def _with_session_log(tmp_path: Path, monkeypatch) -> str:
    """存在する transcript を 1 本用意し、env から解決できる状態にする。"""
    log = tmp_path / "transcript.jsonl"
    log.write_text('{"type":"session_meta"}\n', encoding="utf-8")
    monkeypatch.setenv("DOCSWEEP_AI_SESSION_LOG", str(log))
    return str(log)


def test_start_appends_the_session_log_of_a_later_session(tmp_path: Path, monkeypatch) -> None:
    project, config = _config(tmp_path)
    doc = new_doc("plan", "session-log-append", project_dir=project, config=config, offset_days={})

    first = _with_session_log(tmp_path, monkeypatch)
    initialize_document(
        doc.path,
        project_dir=project,
        config=config,
        metadata=AIMetadata.resolve(agent="claude", model_source="runtime"),
    )
    assert read_frontmatter(doc.path)[SESSION_LOG_FIELD] == [first]

    # 別セッションが同じ md で C 実行を開始する。
    second = str(tmp_path / "second.jsonl")
    Path(second).write_text('{"type":"session_meta"}\n', encoding="utf-8")
    monkeypatch.setenv("DOCSWEEP_AI_SESSION_LOG", second)
    start_execution(
        doc.path,
        project_dir=project,
        config=config,
        contexts=["C1"],
        role="implementation",
        metadata=AIMetadata.resolve(agent="codex", model_source="runtime"),
    )

    assert read_frontmatter(doc.path)[SESSION_LOG_FIELD] == [first, second]


def test_start_twice_in_one_session_does_not_duplicate(tmp_path: Path, monkeypatch) -> None:
    project, config = _config(tmp_path)
    doc = new_doc("plan", "session-log-dedupe", project_dir=project, config=config, offset_days={})
    log = _with_session_log(tmp_path, monkeypatch)
    initialize_document(
        doc.path,
        project_dir=project,
        config=config,
        metadata=AIMetadata.resolve(agent="claude", model_source="runtime"),
    )

    for _ in range(2):
        start_execution(
            doc.path,
            project_dir=project,
            config=config,
            contexts=["C1"],
            role="implementation",
            metadata=AIMetadata.resolve(agent="claude", model_source="runtime"),
        )

    assert read_frontmatter(doc.path)[SESSION_LOG_FIELD] == [log]


def test_start_writes_no_session_log_in_a_shared_queue(tmp_path: Path, monkeypatch) -> None:
    """絶対パスは OS ユーザー名を含むので、private queue 以外へは書かない。"""
    global_path = tmp_path / "shared.yaml"
    global_path.write_text(
        "provenance:\n  enabled: true\n  manager: docsweep\n"
        "  ledger: provenance/ai-executions.csv\n  actor_key: ishizaka\n"
        "work_dir: docs/shared\n"
        "work_policy: shared\n",
        encoding="utf-8",
    )
    project = tmp_path / "shared-repo"
    (project / ".git").mkdir(parents=True)
    config = load_config(project_dir=project, global_path=global_path)
    doc = new_doc("plan", "shared-queue", project_dir=project, config=config, offset_days={})
    _with_session_log(tmp_path, monkeypatch)

    initialize_document(
        doc.path,
        project_dir=project,
        config=config,
        metadata=AIMetadata.resolve(agent="claude", model_source="runtime"),
    )
    start_execution(
        doc.path,
        project_dir=project,
        config=config,
        contexts=["C1"],
        role="implementation",
        metadata=AIMetadata.resolve(agent="claude", model_source="runtime"),
    )

    assert SESSION_LOG_FIELD not in (read_frontmatter(doc.path) or {})


def test_start_without_a_resolvable_log_leaves_the_field_alone(tmp_path: Path, monkeypatch) -> None:
    project, config = _config(tmp_path)
    doc = new_doc("plan", "no-session-log", project_dir=project, config=config, offset_days={})
    monkeypatch.delenv("DOCSWEEP_AI_SESSION_LOG", raising=False)

    initialize_document(
        doc.path,
        project_dir=project,
        config=config,
        metadata=AIMetadata.resolve(agent="claude", model_source="runtime"),
    )
    start_execution(
        doc.path,
        project_dir=project,
        config=config,
        contexts=["C1"],
        role="implementation",
        metadata=AIMetadata.resolve(agent="claude", model_source="runtime"),
    )

    assert SESSION_LOG_FIELD not in (read_frontmatter(doc.path) or {})
