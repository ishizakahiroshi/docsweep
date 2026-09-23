"""``docsweep mv`` — queue 内の移動と、参照（frontmatter・本文）の書き換えと undo。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from docsweep.cli import main
from docsweep.services.frontmatter import read_frontmatter


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs" / "local").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "docsweep-test")
    (repo / ".gitignore").write_text("docs/local/\n", encoding="utf-8")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "README.md")
    _git(repo, "commit", "-qm", "init")
    (tmp_path / "global.yaml").write_text(f"roots: [{tmp_path.as_posix()}]\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    return repo


def _args(project: Path, *extra: str) -> list[str]:
    return [*extra, "--project-dir", str(project), "--config", str(project.parent / "global.yaml")]


def _doc(path: Path, *, parent: str | None = None, body: str = "本文") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: plan", "status: draft", "docsweep_state: in-progress"]
    if parent:
        lines.append(f"docsweep_parent: {parent}")
    lines += ["related: []", "---", f"# [実行中] {path.stem}", "", "## 概要", "", body, ""]
    path.write_text("\n".join(lines), encoding="utf-8", newline="")
    return path


def _setup_docs(project: Path) -> tuple[Path, Path, Path]:
    queue = project / "docs" / "local"
    target = _doc(queue / "plan_a.md")
    child = _doc(queue / "plan_a_c1_x.md", parent="docs/local/plan_a.md")
    note = _doc(
        queue / "plan_note.md",
        body=(
            "詳細は docs/local/plan_a.md を読む。\n"
            "[リンク](docs/local/plan_a.md#c1) と plan_a.md（名前だけ）。\n"
            "別物: docs/local/plan_a.md.bak / x/docs/local/plan_a.md"
        ),
    )
    return target, child, note


def test_mv_moves_the_file_and_rewrites_refs_and_exact_body_paths(project: Path, capsys) -> None:
    target, child, note = _setup_docs(project)

    code = main(_args(project, "mv", "docs/local/plan_a.md", "--to", "docs/local/app-a", "--json"))

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    moved = project / "docs" / "local" / "app-a" / "plan_a.md"
    assert moved.is_file() and not target.exists()
    assert read_frontmatter(child)["docsweep_parent"] == "docs/local/app-a/plan_a.md"
    body = note.read_text(encoding="utf-8")
    assert "詳細は docs/local/app-a/plan_a.md を読む。" in body
    assert "[リンク](docs/local/app-a/plan_a.md#c1)" in body
    assert "と plan_a.md（名前だけ）" in body
    assert "docs/local/plan_a.md.bak / x/docs/local/plan_a.md" in body
    assert {(u["field"], Path(u["path"]).name) for u in payload["ref_updates"]} == {
        ("docsweep_parent", "plan_a_c1_x.md"),
        ("body", "plan_note.md"),
    }
    body_update = next(u for u in payload["ref_updates"] if u["field"] == "body")
    assert body_update["count"] == 2


def test_mv_dry_run_changes_nothing(project: Path, capsys) -> None:
    target, child, note = _setup_docs(project)
    before = {p: p.read_bytes() for p in (target, child, note)}

    code = main(_args(project, "mv", "docs/local/plan_a.md", "--to", "docs/local/app-a", "--dry-run", "--json"))

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["dry_run"] is True
    assert [Path(m["to"]).parent.name for m in payload["moved"]] == ["app-a"]
    assert len(payload["ref_updates"]) == 2
    assert {p: p.read_bytes() for p in before} == before
    assert not (project / "docs" / "local" / "app-a").exists()


def test_mv_no_body_keeps_body_paths(project: Path) -> None:
    _target, child, note = _setup_docs(project)
    body_before = note.read_bytes()

    assert main(_args(project, "mv", "docs/local/plan_a.md", "--to", "docs/local/app-a", "--no-body")) == 0

    assert note.read_bytes() == body_before
    assert read_frontmatter(child)["docsweep_parent"] == "docs/local/app-a/plan_a.md"


def test_undo_restores_file_frontmatter_and_body(project: Path, capsys) -> None:
    target, child, note = _setup_docs(project)
    before = {p: p.read_bytes() for p in (target, child, note)}
    assert main(_args(project, "mv", "docs/local/plan_a.md", "--to", "docs/local/app-a")) == 0
    capsys.readouterr()

    code = main(["undo", "--root", str(project.parent), "--config", str(project.parent / "global.yaml"), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["failed"] == []
    assert len(payload["restored"]) == 1
    assert {r["field"] for r in payload["refs_restored"]} == {"docsweep_parent", "body"}
    assert {p: p.read_bytes() for p in before} == before
    assert not (project / "docs" / "local" / "app-a" / "plan_a.md").exists()


def test_mv_refuses_git_tracked_files_and_moves_nothing(project: Path, capsys) -> None:
    tracked = _doc(project / "docs" / "plan_public.md")
    queue_doc = _doc(project / "docs" / "local" / "plan_b.md")
    (project / ".docsweep.yaml").write_text("work_dir: docs\n", encoding="utf-8")
    _git(project, "add", "docs/plan_public.md")

    code = main(_args(project, "mv", "docs/plan_public.md", "docs/local/plan_b.md", "--to", "docs/app-a"))

    assert code == 2
    assert "git で追跡されている" in capsys.readouterr().err
    assert tracked.is_file() and queue_doc.is_file()
    assert not (project / "docs" / "app-a").exists()


@pytest.mark.parametrize(
    ("sources", "to", "message"),
    [
        (["docs/local/plan_a.md"], "docs/elsewhere", "作業 queue の外"),
        (["docs/local/plan_a.md"], "docs/local/archive/app-a", "archive の中"),
        (["docs/local/archive/plan_old.md"], "docs/local/app-a", "archive の中のファイル"),
        (["docs/local/plan_a.md"], "docs/local", "すでに移動先"),
        (["docs/local/missing.md"], "docs/local/app-a", "見つかりません"),
    ],
)
def test_mv_rejects_invalid_requests(project: Path, capsys, sources, to, message) -> None:
    target, _child, _note = _setup_docs(project)
    _doc(project / "docs" / "local" / "archive" / "plan_old.md")

    code = main(_args(project, "mv", *sources, "--to", to))

    assert code == 2
    assert message in capsys.readouterr().err
    assert target.is_file()


def test_mv_stops_on_name_collision(project: Path, capsys) -> None:
    target, _child, _note = _setup_docs(project)
    existing = _doc(project / "docs" / "local" / "app-a" / "plan_a.md", body="既存")

    code = main(_args(project, "mv", "docs/local/plan_a.md", "--to", "docs/local/app-a"))

    assert code == 2
    assert "同じ名前のファイルがあります" in capsys.readouterr().err
    assert target.is_file()
    assert "既存" in existing.read_text(encoding="utf-8")


def test_mv_help_is_registered(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["mv", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--to" in out and "--no-body" in out and "--dry-run" in out
