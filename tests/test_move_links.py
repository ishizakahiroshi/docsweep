"""移動に伴う本文の Markdown 相対リンクの書き換え（archive と ``docsweep mv``）と、その undo。

queue に残る文書から移した文書へのリンクと、移した文書自身から周りへのリンクの両方が、
移した後も同じ先を指すこと。コード・URL・元から切れていたリンクは触らないこと。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from docsweep.cli import main
from docsweep.config import load_config
from docsweep.engine import auto_sweep
from docsweep.move_refs import Move, rewrite_refs
from docsweep.services.archive import archive_done, undo_last_batch

_H1 = {"done": "完了", "in-progress": "実行中"}
_FENCE = "`" * 3


def _text(stem: str, body: list[str], *, state: str = "in-progress", newline: str = "\n") -> str:
    head = [
        "---", "type: plan", "status: draft", f"docsweep_state: {state}", "related: []", "---",
        f"# [{_H1[state]}] {stem}", "",
    ]
    return newline.join(head + body) + newline


def _write(path: Path, body: list[str], *, state: str = "in-progress", newline: str = "\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_text(path.stem, body, state=state, newline=newline), encoding="utf-8", newline="")
    return path


def _read(path: Path) -> str:
    return path.open("r", encoding="utf-8", newline="").read()


def _cfg(tmp_path: Path):
    global_path = tmp_path / "global.yaml"
    if not global_path.exists():
        global_path.write_text("roots: []\n", encoding="utf-8")
    return load_config(explicit_roots=[str(tmp_path)], global_path=global_path)


# ---- archive ----------------------------------------------------------------------

_DONE_BODY = [
    "[残る計画](plan_stay.md#c1)",
    "![図](images/fig.png)",
    '[上の資料](../guide.md "資料")',
    "[古い計画](archive/plan_old.md)",
    "[自分](plan_done.md#top)",
    "[節](#top)",
    "[切れていた](plan_missing.md)",
    "[外部](https://example.com/plan_stay.md)",
    "`[コード](plan_stay.md)`",
    f"{_FENCE}md",
    "[フェンス](plan_stay.md)",
    _FENCE,
    "",
    '[ref]: ./plan_stay.md "タイトル"',
]
_DONE_AFTER = [
    "[残る計画](../plan_stay.md#c1)",
    "![図](../images/fig.png)",
    '[上の資料](../../guide.md "資料")',
    "[古い計画](plan_old.md)",
    "[自分](plan_done.md#top)",
    "[節](#top)",
    "[切れていた](plan_missing.md)",
    "[外部](https://example.com/plan_stay.md)",
    "`[コード](plan_stay.md)`",
    f"{_FENCE}md",
    "[フェンス](plan_stay.md)",
    _FENCE,
    "",
    '[ref]: ../plan_stay.md "タイトル"',
]
_STAY_BODY = [
    "[完了した計画](plan_done.md#c2)",
    "[同じ計画](./plan_done.md)",
    '[題付き](plan_done.md "題")',
    "[角括弧](<plan_done.md>)",
    "[回り道](../local/plan_done.md?x=1#y)",
    "`[コード](plan_done.md)` と plan_done.md（名前だけ）",
    "[外部](https://example.com/plan_done.md)",
]
_STAY_AFTER = [
    "[完了した計画](archive/plan_done.md#c2)",
    "[同じ計画](./archive/plan_done.md)",
    '[題付き](archive/plan_done.md "題")',
    "[角括弧](<archive/plan_done.md>)",
    "[回り道](archive/plan_done.md?x=1#y)",
    "`[コード](plan_done.md)` と plan_done.md（名前だけ）",
    "[外部](https://example.com/plan_done.md)",
]
_SUB_BODY = ["[親の完了](../plan_done.md)", "[参照][r]", "", "[r]: ../plan_done.md#c3"]
_SUB_AFTER = ["[親の完了](../archive/plan_done.md)", "[参照][r]", "", "[r]: ../archive/plan_done.md#c3"]


def _archive_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    queue = project / "docs" / "local"
    (queue / "images").mkdir(parents=True)
    (queue / "images" / "fig.png").write_bytes(b"png")
    (project / "docs" / "guide.md").write_text("# guide\n", encoding="utf-8")
    return {
        "done": _write(queue / "plan_done.md", _DONE_BODY, state="done"),
        "stay": _write(queue / "plan_stay.md", _STAY_BODY),
        "sub": _write(queue / "app-a" / "plan_sub.md", _SUB_BODY),
        # archive の中の文書は今の走査範囲の外（書き換えない）。
        "old": _write(queue / "archive" / "plan_old.md", ["[完了](../plan_done.md)"], state="done"),
    }


def _body_counts(ref_updates: list[dict]) -> dict[str, int]:
    return {Path(u["path"]).name: u["count"] for u in ref_updates if u["field"] == "body"}


def test_sweep_rewrites_links_to_and_from_the_archived_doc(tmp_path: Path) -> None:
    docs = _archive_project(tmp_path)
    old_before = docs["old"].read_bytes()

    result = auto_sweep(_cfg(tmp_path))

    archived = docs["done"].parent / "archive" / "plan_done.md"
    assert not docs["done"].exists() and archived.is_file()
    assert _read(archived) == _text("plan_done", _DONE_AFTER, state="done")
    assert _read(docs["stay"]) == _text("plan_stay", _STAY_AFTER)
    assert _read(docs["sub"]) == _text("plan_sub", _SUB_AFTER)
    assert docs["old"].read_bytes() == old_before
    assert _body_counts(result.ref_updates) == {"plan_done.md": 5, "plan_stay.md": 5, "plan_sub.md": 2}
    assert result.ref_failed == []


def test_sweep_dry_run_plans_link_rewrites_without_writing(tmp_path: Path) -> None:
    docs = _archive_project(tmp_path)
    before = {path: path.read_bytes() for path in docs.values()}

    result = auto_sweep(_cfg(tmp_path), dry_run=True)

    assert {path: path.read_bytes() for path in before} == before
    assert not (docs["done"].parent / "archive" / "plan_done.md").exists()
    assert not (tmp_path / ".docsweep" / "moves.jsonl").exists()
    assert _body_counts(result.ref_updates) == {"plan_done.md": 5, "plan_stay.md": 5, "plan_sub.md": 2}
    stay = next(u for u in result.ref_updates if Path(u["path"]).name == "plan_stay.md")
    assert stay["before"] == [
        "plan_done.md", "./plan_done.md", "plan_done.md", "plan_done.md", "../local/plan_done.md",
    ]
    assert stay["after"] == [
        "archive/plan_done.md", "./archive/plan_done.md", "archive/plan_done.md",
        "archive/plan_done.md", "archive/plan_done.md",
    ]


def test_undo_restores_every_linked_doc_byte_for_byte(tmp_path: Path) -> None:
    docs = _archive_project(tmp_path)
    before = {path: path.read_bytes() for path in docs.values()}
    cfg = _cfg(tmp_path)

    archived = archive_done(config=cfg, paths=[str(docs["done"])])
    assert _body_counts(archived.ref_updates) == {
        "plan_done.md": 5, "plan_stay.md": 5, "plan_sub.md": 2,
    }

    undone = undo_last_batch(config=cfg)

    assert undone.failed == []
    assert {path: path.read_bytes() for path in before} == before
    assert not (docs["done"].parent / "archive" / "plan_done.md").exists()
    assert sorted(Path(r["path"]).name for r in undone.refs_restored if r["field"] == "body") == [
        "plan_done.md", "plan_stay.md", "plan_sub.md",
    ]
    again = undo_last_batch(config=cfg)
    assert again.batch_id is None and again.refs_restored == []


def test_undo_keeps_a_link_changed_after_the_archive(tmp_path: Path) -> None:
    docs = _archive_project(tmp_path)
    done_before = docs["done"].read_bytes()
    cfg = _cfg(tmp_path)
    archive_done(config=cfg, paths=[str(docs["done"])])
    edited = _read(docs["stay"]).replace("(archive/plan_done.md#c2)", "(plan_other.md#c2)")
    docs["stay"].write_text(edited, encoding="utf-8", newline="")

    undone = undo_last_batch(config=cfg)

    assert _read(docs["stay"]) == edited  # 人が変えた箇所は上書きしない
    assert [(Path(f["path"]).name, f["field"]) for f in undone.failed] == [("plan_stay.md", "body")]
    assert docs["done"].read_bytes() == done_before  # 移した文書自身は元どおり
    assert _read(docs["sub"]) == _text("plan_sub", _SUB_BODY)


# ---- 書き方の細部 ------------------------------------------------------------------


def test_escaped_and_crlf_links_keep_their_style(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    queue = project / "docs" / "local"
    moved = _write(queue / "plan x.md", ["本文"])
    referrer = _write(
        queue / "plan_ref.md",
        [
            "[エスケープ](plan%20x.md#c1)",
            "[角括弧](<plan x.md>)",
            "[フォルダ](./app-a/)",
            "[空白で切れる](plan x.md)",
        ],
        newline="\r\n",
    )
    (queue / "app-a").mkdir()
    dst = queue / "archive" / "plan x.md"
    dst.parent.mkdir()
    os.replace(moved, dst)

    result = rewrite_refs(
        [Move(moved, dst)], project_root=project, config=_cfg(tmp_path), root=tmp_path,
        project="repo", batch_id="b1",
    )

    assert result.failed == []
    assert _read(referrer) == _text(
        "plan_ref",
        [
            "[エスケープ](archive/plan%20x.md#c1)",
            "[角括弧](<archive/plan x.md>)",
            "[フォルダ](./app-a/)",  # 移していない先は、移していない文書からは触らない
            "[空白で切れる](plan x.md)",  # Markdown のリンクとして閉じていない
        ],
        newline="\r\n",
    )


def test_moved_doc_keeps_its_folder_links_and_trailing_slash(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    queue = project / "docs" / "local"
    (queue / "app-a").mkdir(parents=True)
    moved = _write(queue / "plan_m.md", ["[フォルダ](./app-a/)", "[親](../)"])
    dst = queue / "archive" / "plan_m.md"
    dst.parent.mkdir()
    os.replace(moved, dst)

    rewrite_refs(
        [Move(moved, dst)], project_root=project, config=_cfg(tmp_path), root=tmp_path,
        project="repo", batch_id="b1",
    )

    assert _read(dst) == _text("plan_m", ["[フォルダ](../app-a/)", "[親](../../)"])


def _directory_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlink creation is unavailable")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
    )
    if result.returncode != 0:
        pytest.skip("directory junction creation is unavailable")


def test_linked_queue_resolves_links_from_the_repo_side(tmp_path: Path) -> None:
    """queue が repo の外の実体でも、リンクは repo から見た場所（``docs/local/``）で解決する。"""
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    (project / "docs").mkdir()
    (project / "docs" / "guide.md").write_text("# guide\n", encoding="utf-8")
    external = tmp_path / "external" / "local"
    external.mkdir(parents=True)
    _directory_link(project / "docs" / "local", external)
    _write(external / "plan_done.md", ["[上の資料](../guide.md)"], state="done")
    stay = _write(external / "plan_stay.md", ["[完了](plan_done.md)"])

    auto_sweep(_cfg(tmp_path))

    assert _read(external / "archive" / "plan_done.md") == _text(
        "plan_done", ["[上の資料](../../guide.md)"], state="done"
    )
    assert _read(stay) == _text("plan_stay", ["[完了](archive/plan_done.md)"])


def test_body_true_does_not_rewrite_a_link_twice(tmp_path: Path) -> None:
    """queue が repo 直下だと、文書相対のリンクと repo 相対のパスが同じ文字列になる。

    相対リンクとして解決できた箇所は、repo 相対パスの置き換えで二重に書き換えない
    （移した文書の自分へのリンク ``./plan_a.md`` は、移した後もそのままで正しい）。
    """
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    (project / ".docsweep.yaml").write_text("work_dir: .\n", encoding="utf-8")
    moved = _write(project / "plan_a.md", ["[自分](./plan_a.md#top)", "[メモ](plan_note.md)"])
    note = _write(project / "plan_note.md", ["[A](./plan_a.md)", "./plan_a.md を読む"])
    dst = project / "app-a" / "plan_a.md"
    dst.parent.mkdir()
    os.replace(moved, dst)

    result = rewrite_refs(
        [Move(moved, dst)], project_root=project, config=_cfg(tmp_path), root=tmp_path,
        project="repo", batch_id="b1", body=True,
    )

    assert result.failed == []
    assert _read(dst) == _text("plan_a", ["[自分](./plan_a.md#top)", "[メモ](../plan_note.md)"])
    assert _read(note) == _text("plan_note", ["[A](./app-a/plan_a.md)", "./app-a/plan_a.md を読む"])


# ---- docsweep mv ------------------------------------------------------------------


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)


@pytest.fixture
def git_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs" / "local").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "docsweep-test")
    (repo / ".gitignore").write_text("docs/local/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-qm", "init")
    (tmp_path / "global.yaml").write_text(f"roots: [{tmp_path.as_posix()}]\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    return repo


def _args(project: Path, *extra: str) -> list[str]:
    return [*extra, "--project-dir", str(project), "--config", str(project.parent / "global.yaml")]


_A_BODY = ["[メモ](plan_note.md)", "詳細は docs/local/plan_note.md を読む。", "[自分](plan_a.md#top)"]
_NOTE_BODY = [
    "[A の計画](plan_a.md#c1)",
    "詳細は docs/local/plan_a.md を読む。",
    "[repo 相対](docs/local/plan_a.md)",
]


def _mv_docs(project: Path) -> tuple[Path, Path]:
    queue = project / "docs" / "local"
    return _write(queue / "plan_a.md", _A_BODY), _write(queue / "plan_note.md", _NOTE_BODY)


def test_mv_rewrites_links_and_exact_paths_once(git_project: Path, capsys) -> None:
    _target, note = _mv_docs(git_project)

    code = main(_args(git_project, "mv", "docs/local/plan_a.md", "--to", "docs/local/app-a", "--json"))

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    moved = git_project / "docs" / "local" / "app-a" / "plan_a.md"
    assert _read(moved) == _text(
        "plan_a",
        ["[メモ](../plan_note.md)", "詳細は docs/local/plan_note.md を読む。", "[自分](plan_a.md#top)"],
    )
    assert _read(note) == _text(
        "plan_note",
        [
            "[A の計画](app-a/plan_a.md#c1)",
            "詳細は docs/local/app-a/plan_a.md を読む。",
            "[repo 相対](docs/local/app-a/plan_a.md)",
        ],
    )
    assert _body_counts(payload["ref_updates"]) == {"plan_a.md": 1, "plan_note.md": 3}


def test_mv_dry_run_writes_nothing(git_project: Path, capsys) -> None:
    target, note = _mv_docs(git_project)
    before = {path: path.read_bytes() for path in (target, note)}

    code = main(_args(git_project, "mv", "docs/local/plan_a.md", "--to", "docs/local/app-a", "--dry-run", "--json"))

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert {path: path.read_bytes() for path in before} == before
    assert _body_counts(payload["ref_updates"]) == {"plan_a.md": 1, "plan_note.md": 3}


def test_mv_no_body_leaves_links_alone(git_project: Path) -> None:
    target, note = _mv_docs(git_project)
    target_before, note_before = target.read_bytes(), note.read_bytes()

    code = main(_args(git_project, "mv", "docs/local/plan_a.md", "--to", "docs/local/app-a", "--no-body"))

    assert code == 0
    assert (git_project / "docs" / "local" / "app-a" / "plan_a.md").read_bytes() == target_before
    assert note.read_bytes() == note_before


def test_mv_undo_restores_links_byte_for_byte(git_project: Path, capsys) -> None:
    target, note = _mv_docs(git_project)
    before = {path: path.read_bytes() for path in (target, note)}
    assert main(_args(git_project, "mv", "docs/local/plan_a.md", "--to", "docs/local/app-a")) == 0
    capsys.readouterr()

    code = main([
        "undo", "--root", str(git_project.parent),
        "--config", str(git_project.parent / "global.yaml"), "--json",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["failed"] == []
    assert {path: path.read_bytes() for path in before} == before
    assert not (git_project / "docs" / "local" / "app-a" / "plan_a.md").exists()
