"""文書を移したとき、provenance 台帳の work_path も移した先へ付け替わること。

archive（sweep / promote / apply / release close / Web）と ``docsweep mv`` は
``archive.archive_file`` を、``docsweep undo`` は ``services.archive.undo_last_batch`` を通る。
以前は台帳の ``work_path`` が移す前のパスのまま残り、移した文書に ``provenance check`` を
かけるたびに「台帳のwork_pathが現在pathと異なります」が出ていた。すでにずれた台帳は
``provenance check --fix-work-path`` で直す。

台帳はテスト用の場所（tmp_path 配下）だけを使う。global 設定は conftest が実在しない
tmp のパスへ向けているので、provenance は project の ``.docsweep.yaml`` で有効にする。
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from docsweep import provenance
from docsweep.cli import main
from docsweep.config import load_config
from docsweep.engine import auto_sweep
from docsweep.provenance import (
    LEDGER_FIELDS,
    AIMetadata,
    check_document,
    finish_execution,
    initialize_document,
    start_execution,
)
from docsweep.services.archive import archive_done, undo_last_batch
from docsweep.services.frontmatter import read_frontmatter

_H1 = {"done": "完了", "in-progress": "実行中"}


def _directory_link(link: Path, target: Path, kind: str) -> None:
    """queue を repo の外へ逃がす構成。junction（実環境の docs/local と同じ）は Windows だけ。"""
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("junction は Windows だけ")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
        )
        if result.returncode != 0:
            pytest.skip("directory junction creation is unavailable")
        return
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink creation is unavailable")


def _project(
    base: Path, *, linked_to: Path | None = None, link_kind: str = "junction", git: bool = False
) -> tuple[Path, Path]:
    """provenance を有効にした project と、その作業 queue（docs/local）を返す。"""
    project = base / "repo"
    project.mkdir(parents=True)
    if git:
        subprocess.run(["git", "init", "-q", str(project)], check=True, capture_output=True)
    else:
        (project / ".git").mkdir()
    ledger = base / "home" / "provenance" / "ai-executions.csv"
    (project / ".docsweep.yaml").write_text(
        "provenance:\n"
        "  enabled: true\n"
        "  manager: docsweep\n"
        f'  ledger: "{ledger.as_posix()}"\n'
        "  actor_key: tester\n",
        encoding="utf-8",
    )
    queue = project / "docs" / "local"
    if linked_to is None:
        queue.mkdir(parents=True)
    else:
        (project / "docs").mkdir()
        linked_to.mkdir(parents=True)
        _directory_link(queue, linked_to, link_kind)
    return project, queue


def _cfg(tmp_path: Path, project: Path, *, root: Path | None = None):
    cfg = load_config(project_dir=project, explicit_roots=[str(root or tmp_path)])
    assert cfg.provenance_enabled is True
    # 本物の ~/.docsweep の台帳へ書かないことを、毎回ここで確かめる。
    assert cfg.provenance_ledger.is_relative_to(tmp_path)
    return cfg


def _doc(path: Path, *, state: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "type: plan\n"
        "status: draft\n"
        f"docsweep_state: {state}\n"
        "---\n"
        f"# [{_H1[state]}] {path.stem}\n"
        "\n"
        "## 概要\n"
        "\n"
        "合成データ\n"
        "\n"
        "## context配分\n"
        "\n"
        "| C | 種別 | 内容 | 備考/注意点 |\n"
        "|---|---|---|---|\n"
        "| C1 | 実装 | 合成データ | - |\n",
        encoding="utf-8",
        newline="",
    )
    return path


def _metadata() -> AIMetadata:
    return AIMetadata.resolve(
        agent="codex",
        runtime="many-ai-cli",
        provider="openai",
        model_id="unknown",
        model_display="GPT-5",
        reasoning_profile="unknown",
        model_source="orchestrator",
        actor_key="tester",
    )


def _track(path: Path, project: Path, cfg) -> list[str]:
    """作成（authoring）と C1 の実装を記録し、台帳の execution ID を返す。"""
    initialized = initialize_document(path, project_dir=project, config=cfg, metadata=_metadata())
    started = start_execution(
        path,
        project_dir=project,
        config=cfg,
        contexts=["C1"],
        role="implementation",
        metadata=_metadata(),
    )
    finish_execution(started["execution_id"], config=cfg, result="completed")
    return [initialized["execution_id"], started["execution_id"]]


def _rows(cfg) -> list[dict[str, str]]:
    with cfg.provenance_ledger.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _add_row(cfg, **fields: str) -> None:
    """既存の 1 行を写して、指定した列だけ変えた合成行を足す。"""
    row = dict(_rows(cfg)[0])
    row.update(fields)
    with cfg.provenance_ledger.open("a", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, fieldnames=LEDGER_FIELDS, lineterminator="\n").writerow(row)


def _expected(before: list[dict[str, str]], refs: list[str], work_path: str) -> list[dict[str, str]]:
    """``refs`` の行の work_path だけを ``work_path`` にした、ほかは before と同じ行。"""
    return [
        {**row, "work_path": work_path} if row["execution_id"] in refs else row
        for row in before
    ]


def _check(path: Path, project: Path, cfg) -> dict:
    result = check_document(path, project_dir=project, config=cfg)
    assert result["errors"] == []
    return result


# ---- 移動に台帳が追従する ------------------------------------------------------------


def test_sweep_moves_the_ledger_work_path_with_the_document(tmp_path: Path) -> None:
    project, queue = _project(tmp_path)
    cfg = _cfg(tmp_path, project)
    moved = _doc(queue / "plan_moved.md", state="done")
    kept = _doc(queue / "plan_kept.md", state="in-progress")
    refs = _track(moved, project, cfg)
    _track(kept, project, cfg)
    # 別 project に同じ project 相対パスの文書がある（台帳は全 project 共通）。
    _add_row(
        cfg,
        execution_id="AIX-synthetic-other-project",
        project_id="other-project",
        work_id="WK-synthetic-other-project",
        work_path="docs/local/plan_moved.md",
    )
    before = _rows(cfg)
    assert _check(moved, project, cfg)["warnings"] == []

    result = auto_sweep(cfg)

    archived = queue / "archive" / "plan_moved.md"
    assert result.failed == []
    assert archived.is_file() and not moved.exists()
    assert _rows(cfg) == _expected(before, refs, "docs/local/archive/plan_moved.md")
    checked = _check(archived, project, cfg)
    assert checked["valid"] is True
    assert checked["warnings"] == []


def test_archive_done_and_undo_move_the_work_path_both_ways(tmp_path: Path) -> None:
    project, queue = _project(tmp_path)
    cfg = _cfg(tmp_path, project)
    moved = _doc(queue / "plan_moved.md", state="done")
    kept = _doc(queue / "plan_kept.md", state="in-progress")
    refs = _track(moved, project, cfg)
    _track(kept, project, cfg)
    before = _rows(cfg)

    archived_result = archive_done(config=cfg, paths=[str(moved)])

    archived = queue / "archive" / "plan_moved.md"
    assert archived_result.failed == [] and len(archived_result.moved) == 1
    assert _rows(cfg) == _expected(before, refs, "docs/local/archive/plan_moved.md")
    assert _check(archived, project, cfg)["warnings"] == []

    undone = undo_last_batch(config=cfg)

    assert undone.failed == []
    assert moved.is_file() and not archived.exists()
    assert _rows(cfg) == before
    assert _check(moved, project, cfg)["warnings"] == []


def test_mv_and_undo_move_the_work_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    project, queue = _project(tmp_path, git=True)
    cfg = _cfg(tmp_path, project)
    moved = _doc(queue / "plan_moved.md", state="in-progress")
    kept = _doc(queue / "plan_kept.md", state="in-progress")
    refs = _track(moved, project, cfg)
    _track(kept, project, cfg)
    before = _rows(cfg)
    global_path = tmp_path / "global.yaml"
    global_path.write_text(f'roots: ["{tmp_path.as_posix()}"]\n', encoding="utf-8")
    monkeypatch.chdir(project)

    code = main(
        [
            "mv", "docs/local/plan_moved.md", "--to", "docs/local/app-a",
            "--project-dir", str(project), "--config", str(global_path),
        ]
    )

    target = queue / "app-a" / "plan_moved.md"
    assert code == 0
    assert target.is_file() and not moved.exists()
    assert _rows(cfg) == _expected(before, refs, "docs/local/app-a/plan_moved.md")
    assert _check(target, project, cfg)["warnings"] == []
    capsys.readouterr()

    code = main(["undo", "--root", str(tmp_path), "--config", str(global_path), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["failed"] == []
    assert moved.is_file()
    assert _rows(cfg) == before
    assert _check(moved, project, cfg)["warnings"] == []


@pytest.mark.parametrize("link_kind", ["junction", "symlink"])
def test_linked_queue_keeps_the_repo_relative_route(tmp_path: Path, link_kind: str) -> None:
    """queue が junction / symlink で repo の外にあっても、台帳には repo 相対の経路を書く（実体パスを書かない）。"""
    workspace = tmp_path / "ws"
    project, queue = _project(
        workspace, linked_to=tmp_path / "external" / "local", link_kind=link_kind
    )
    cfg = _cfg(tmp_path, project, root=workspace)
    moved = _doc(queue / "plan_moved.md", state="done")
    refs = _track(moved, project, cfg)
    before = _rows(cfg)
    assert {row["work_path"] for row in before} == {"docs/local/plan_moved.md"}

    result = auto_sweep(cfg)

    archived = queue / "archive" / "plan_moved.md"
    assert result.failed == []
    assert (tmp_path / "external" / "local" / "archive" / "plan_moved.md").is_file()
    assert _rows(cfg) == _expected(before, refs, "docs/local/archive/plan_moved.md")
    assert _check(archived, project, cfg)["warnings"] == []

    undone = undo_last_batch(config=cfg)

    assert undone.failed == []
    assert moved.is_file()
    assert _rows(cfg) == before
    assert _check(moved, project, cfg)["warnings"] == []


# ---- 何もしない・失敗しても移動は成功する ------------------------------------------------


def test_dry_run_leaves_the_ledger_alone(tmp_path: Path) -> None:
    project, queue = _project(tmp_path)
    cfg = _cfg(tmp_path, project)
    moved = _doc(queue / "plan_moved.md", state="done")
    _track(moved, project, cfg)
    before = cfg.provenance_ledger.read_bytes()

    auto_sweep(cfg, dry_run=True)

    assert moved.is_file()
    assert cfg.provenance_ledger.read_bytes() == before


def test_move_succeeds_without_a_ledger(tmp_path: Path, capsys) -> None:
    project, queue = _project(tmp_path)
    cfg = _cfg(tmp_path, project)
    moved = _doc(queue / "plan_moved.md", state="done")
    _track(moved, project, cfg)
    cfg.provenance_ledger.unlink()
    capsys.readouterr()

    result = auto_sweep(cfg)

    assert result.failed == []
    assert (queue / "archive" / "plan_moved.md").is_file()
    assert not cfg.provenance_ledger.exists()
    assert not list(cfg.provenance_ledger.parent.glob("*.lock"))
    assert "--fix-work-path" not in capsys.readouterr().err


def test_ledger_write_failure_does_not_fail_the_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    project, queue = _project(tmp_path)
    cfg = _cfg(tmp_path, project)
    moved = _doc(queue / "plan_moved.md", state="done")
    refs = _track(moved, project, cfg)
    before_bytes = cfg.provenance_ledger.read_bytes()
    before = _rows(cfg)
    capsys.readouterr()

    def broken_write(path: Path, rows: list[dict[str, str]]) -> None:
        raise OSError("synthetic ledger write failure")

    # monkeypatch.undo() は conftest の遮断（global 設定の差し替え）まで戻すので使わない。
    with monkeypatch.context() as patch:
        patch.setattr(provenance, "_write_ledger", broken_write)
        result = auto_sweep(cfg)

    archived = queue / "archive" / "plan_moved.md"
    assert result.failed == []
    assert archived.is_file() and not moved.exists()
    assert cfg.provenance_ledger.read_bytes() == before_bytes
    assert not list(cfg.provenance_ledger.parent.glob("*.lock"))
    err = capsys.readouterr().err
    assert "synthetic ledger write failure" in err
    assert "--fix-work-path" in err

    # 付け替えられなかった行は、あとから check --fix-work-path で直せる。
    repaired = check_document(archived, project_dir=project, config=cfg, fix_work_path=True)
    assert [item["execution_id"] for item in repaired["work_path_fixed"]] == refs
    assert repaired["warnings"] == []
    assert _rows(cfg) == _expected(before, refs, "docs/local/archive/plan_moved.md")


# ---- すでにずれた台帳を直す（check --fix-work-path） -------------------------------------


def test_fix_work_path_repairs_only_rows_that_match_the_document(tmp_path: Path, capsys) -> None:
    project, queue = _project(tmp_path)
    cfg = _cfg(tmp_path, project)
    drifted = _doc(queue / "plan_drifted.md", state="in-progress")
    kept = _doc(queue / "plan_kept.md", state="in-progress")
    refs = _track(drifted, project, cfg)
    _track(kept, project, cfg)
    work_id = read_frontmatter(drifted)["work_id"]
    # 別 project の同じ相対パスの行と、同じ work_id だが ai_execution_refs に無い行は直さない。
    _add_row(
        cfg,
        execution_id="AIX-synthetic-other-project",
        project_id="other-project",
        work_id="WK-synthetic-other-project",
        work_path="docs/local/plan_drifted.md",
    )
    _add_row(
        cfg,
        execution_id="AIX-synthetic-not-in-refs",
        work_id=work_id,
        work_path="docs/local/plan_drifted.md",
    )
    # 台帳が追従しない版（または手作業）で移した状態を作る。
    target = queue / "app-a" / "plan_drifted.md"
    target.parent.mkdir()
    shutil.move(str(drifted), str(target))
    before = _rows(cfg)
    assert len(_check(target, project, cfg)["warnings"]) == len(refs)
    capsys.readouterr()

    args = [
        "provenance", "check", "--path", "docs/local/app-a/plan_drifted.md",
        "--project-dir", str(project), "--fix-work-path", "--json",
    ]
    code = main(args)

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["valid"] is True
    assert payload["warnings"] == []
    assert payload["changed"] is True
    assert payload["work_path_fixed"] == [
        {"execution_id": ref, "from": "docs/local/plan_drifted.md", "to": "docs/local/app-a/plan_drifted.md"}
        for ref in refs
    ]
    assert payload["work_path_skipped"] == []
    assert _rows(cfg) == _expected(before, refs, "docs/local/app-a/plan_drifted.md")

    # 2 回目は直すものが無い。
    assert main(args) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["work_path_fixed"] == [] and again["changed"] is False


def test_fix_work_path_leaves_rows_whose_recorded_file_still_exists(
    tmp_path: Path, capsys
) -> None:
    """記録された場所に今もファイルがある（複製）なら、どちらが正しいか決められないので直さない。"""
    project, queue = _project(tmp_path)
    cfg = _cfg(tmp_path, project)
    original = _doc(queue / "plan_copied.md", state="in-progress")
    refs = _track(original, project, cfg)
    copy = queue / "app-a" / "plan_copied.md"
    copy.parent.mkdir()
    shutil.copy2(original, copy)
    before = _rows(cfg)
    capsys.readouterr()

    code = main(
        [
            "provenance", "check", "--path", "docs/local/app-a/plan_copied.md",
            "--project-dir", str(project), "--fix-work-path",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0  # 警告だけで、エラーではない
    assert out.count("未修正") == len(refs)
    assert "修正:" not in out.replace("未修正:", "")
    assert _rows(cfg) == before
    result = check_document(copy, project_dir=project, config=cfg, fix_work_path=True)
    assert result["work_path_fixed"] == []
    assert [item["execution_id"] for item in result["work_path_skipped"]] == refs
    assert {item["reason"] for item in result["work_path_skipped"]} == {"recorded_path_exists"}
