"""エンジン本体（グループ A）の文言が表示言語で切り替わること。

各モジュールで 1 つ以上、英語の表示言語で英語が出ることを確かめる。もとが英語だけだった
``raise`` は、日本語の表示言語で日本語になることも確かめる。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from docsweep.i18n import use_lang


def test_config_work_dir_outside_project_in_english(tmp_path: Path) -> None:
    from docsweep.config import resolve_work_dir

    with use_lang("en"), pytest.raises(ValueError) as excinfo:
        resolve_work_dir(tmp_path, "../outside")

    assert str(excinfo.value) == "work_dir points outside the project"


def test_config_unknown_setting_key_in_english(tmp_path: Path) -> None:
    from docsweep.config import get_user_setting

    with use_lang("en"), pytest.raises(ValueError) as excinfo:
        get_user_setting("user.nope", global_path=tmp_path / "config.yaml")

    assert str(excinfo.value).startswith("Unknown setting key: user.nope (allowed: [")


def test_engine_due_expired_requires_watching_in_english(tmp_path: Path) -> None:
    from docsweep.config import Config
    from docsweep.engine import promote_state

    config = Config(roots=[tmp_path], loaded_from_config=True)
    with use_lang("en"), pytest.raises(ValueError) as excinfo:
        promote_state(config, from_state="done", due_expired_only=True)

    assert str(excinfo.value) == "--due-expired can only be used when promoting from watching"


def test_release_label_errors_follow_the_language() -> None:
    from docsweep.release import ReleaseTrackingError, validate_release_label

    with use_lang("en"), pytest.raises(ReleaseTrackingError) as excinfo:
        validate_release_label("")
    assert str(excinfo.value) == "release label cannot be empty"

    with use_lang("ja"), pytest.raises(ReleaseTrackingError) as excinfo:
        validate_release_label("")
    assert str(excinfo.value) == "リリースラベル は空にできません"


def test_move_refs_missing_document_in_english(tmp_path: Path) -> None:
    from docsweep.move_refs import revert_ref_rewrite

    missing = tmp_path / "plan_missing.md"
    with use_lang("en"), pytest.raises(FileNotFoundError) as excinfo:
        revert_ref_rewrite({"src": str(missing), "field": "related"})

    assert str(excinfo.value).startswith(
        "Cannot find the document whose references should be restored:"
    )


def test_workspace_migration_manifest_errors_in_english() -> None:
    from docsweep.workspace_migration import apply_manifest

    with use_lang("en"), pytest.raises(ValueError) as excinfo:
        apply_manifest({"schema_version": 2})

    assert str(excinfo.value) == "Unsupported release migration manifest"


def test_workspace_migration_queue_error_keeps_reason_and_follows_language(
    tmp_path: Path,
) -> None:
    """もとが英語だけだった WorkQueueError は、理由コードを保ったまま表示言語で出る。"""
    from docsweep.config import load_config
    from docsweep.workspace_migration import WorkQueueError, _resolve_configured_queue

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    config = load_config(
        project_dir=repo, explicit_roots=[str(repo)], global_path=tmp_path / "none.yaml"
    )

    with use_lang("en"), pytest.raises(WorkQueueError) as english:
        _resolve_configured_queue(repo, config)
    with use_lang("ja"), pytest.raises(WorkQueueError) as japanese:
        _resolve_configured_queue(repo, config)

    assert english.value.reason == japanese.value.reason == "configured_work_queue_unavailable"
    assert str(english.value).startswith(
        "configured work queue does not exist or is not a directory:"
    )
    assert str(japanese.value).startswith(
        "設定済みの作業 queue が存在しないか、ディレクトリではありません:"
    )


def test_templates_gen_unknown_type_in_english() -> None:
    from docsweep.templates_gen import okf_frontmatter

    with use_lang("en"), pytest.raises(ValueError) as excinfo:
        okf_frontmatter("nope")

    assert str(excinfo.value) == "Unknown type 'nope' (plan|bugfix|pending)"


def test_archive_destination_exists_follows_the_language(tmp_path: Path) -> None:
    from docsweep.archive import archive_file

    project = tmp_path / "project"
    source = project / "docs" / "local" / "plan_a.md"
    source.parent.mkdir(parents=True)
    source.write_text("# [完了] a\n", encoding="utf-8")
    (project / "archive").mkdir()
    (project / "archive" / "plan_a.md").write_text("existing\n", encoding="utf-8")

    def move() -> None:
        archive_file(
            src=source,
            project_dir=project,
            archive_dir="archive",
            root=tmp_path,
            project="project",
            status="done",
            strict_collision=True,
        )

    with use_lang("en"), pytest.raises(FileExistsError) as english:
        move()
    with use_lang("ja"), pytest.raises(FileExistsError) as japanese:
        move()

    assert str(english.value).startswith("archive destination already exists:")
    assert str(japanese.value).startswith("archive 先に同名のファイルが既にあります:")
    assert source.is_file()


def test_work_queue_outside_project_in_english(tmp_path: Path) -> None:
    from docsweep.config import Config
    from docsweep.work_queue import check_work_queue

    project = tmp_path / "project"
    project.mkdir()
    config = Config(roots=[tmp_path], loaded_from_config=True)

    with use_lang("en"):
        result = check_work_queue(
            config=config, project_dir=project, target_dir=tmp_path / "elsewhere"
        )

    assert result.errors == ["The work queue must be inside the project"]


def test_states_missing_labels_in_english() -> None:
    from docsweep.states import build_state_model

    with use_lang("en"), pytest.raises(ValueError) as excinfo:
        build_state_model([{"key": "custom"}])

    assert str(excinfo.value) == "state 'custom' has no labels"


def test_scan_stale_row_warning_in_english(tmp_path: Path, capsys) -> None:
    from docsweep import index as db
    from docsweep.config import load_config
    from docsweep.scan import sync_index

    workspace = tmp_path / "dev"
    plan = workspace / "alpha" / "docs" / "local" / "plan_one.md"
    plan.parent.mkdir(parents=True)
    (workspace / "alpha" / "pyproject.toml").write_text("[project]\nname='alpha'\n", encoding="utf-8")
    plan.write_text("# [計画] one\n\n## 概要\n\nfoo\n", encoding="utf-8")
    db_file = tmp_path / "idx.db"
    # 旧採番（スキャンルート単位）の行を置く。
    with db.connect(db_file) as conn:
        db.upsert_project(conn, "dev", str(workspace), None, "2026-07-26T00:00:00+00:00")
        db.upsert_file(
            conn, project_id="dev", rel_path="alpha/docs/local/plan_one.md",
            type_="plan", status="planned", review_status=None, owner=None,
            last_reviewed=None, claimed_at=None, mtime=1.0, body_sha="stale",
            abs_path=plan.as_posix(),
        )
        conn.commit()
    config = load_config(explicit_roots=[str(workspace)], global_path=tmp_path / "no.yaml")
    config.search_paths = [str(workspace)]

    with use_lang("en"):
        sync_index(config, db_path_override=db_file)

    err = capsys.readouterr().err
    assert "warning: removed index rows that no longer match their files: dev (1 rows)" in err
    assert "docsweep index-sync --prune-projects" in err


def test_index_schema_error_follows_the_language(tmp_path: Path) -> None:
    from docsweep import index as db

    conn = sqlite3.connect(tmp_path / "broken.db")
    try:
        conn.execute("CREATE TABLE files (file_id INTEGER)")
        with use_lang("en"), pytest.raises(db.IndexSchemaError) as english:
            db.init_schema(conn)
        with use_lang("ja"), pytest.raises(db.IndexSchemaError) as japanese:
            db.init_schema(conn)
    finally:
        conn.close()

    assert str(english.value) == "index database is partially initialized (meta table is missing)"
    assert str(japanese.value) == (
        "索引データベースの初期化が途中で止まっています（meta テーブルがありません）"
    )


def test_excluded_invalid_settings_in_english(tmp_path: Path) -> None:
    from docsweep.excluded import ExcludedConfigError, load_excluded

    path = tmp_path / "excluded.json"
    path.write_text(json.dumps({"excluded": "not-a-list"}), encoding="utf-8")

    with use_lang("en"), pytest.raises(ExcludedConfigError) as excinfo:
        load_excluded(path=path)

    assert str(excinfo.value) == "excluded in the excluded settings is not a list"
