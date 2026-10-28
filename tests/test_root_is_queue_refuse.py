"""``--root`` に作業 queue 自身を渡したとき、archive へ移す本実行を止める。

queue 自身（``<repo>/docs/local``）をスキャンルートにすると project の目印が見つからず、
queue が project 扱いになって archive 先が ``docs/local/docs/local/archive`` と二重になる。
``sweep`` / ``promote`` / ``apply``（discard・promote）の本実行は、何も移さず・何も書かずに
終了コード 2 で止まる。dry-run は計画を最後まで出し、``--json`` でも標準エラーに警告を出す。
正しい root（repo）を渡したときは止めない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docsweep.cli import main

_H1 = {"done": "完了", "in-progress": "実行中", "watching": "様子見"}


def _doc(path: Path, *, state: str = "done") -> Path:
    lines = [
        "---", "type: plan", "status: draft", f"docsweep_state: {state}", "related: []", "---",
        f"# [{_H1[state]}] {path.stem}", "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    return path


def _global(tmp_path: Path) -> Path:
    path = tmp_path / "global.yaml"
    path.write_text("roots: []\n", encoding="utf-8")
    return path


def _repo(tmp_path: Path, name: str = "proj") -> tuple[Path, Path]:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    return root, root / "docs" / "local"


def _snapshot(base: Path) -> dict[str, bytes]:
    return {
        p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()
    }


def _assert_untouched(root: Path, q: Path, before: dict[str, bytes]) -> None:
    # ファイルの中身・場所が変わらず、移動ログ・INDEX（<root>/.docsweep）も、二重の archive も無い。
    assert _snapshot(root) == before
    assert not (q / ".docsweep").exists()
    assert not (q / "docs").exists()
    assert not (root / ".docsweep").exists()


# --- sweep --------------------------------------------------------------------------------


@pytest.mark.parametrize("as_json", [False, True])
def test_sweep_refuses_queue_root(tmp_path: Path, capsys, as_json: bool):
    root, q = _repo(tmp_path)
    _doc(q / "plan_done.md")
    before = _snapshot(root)

    argv = ["sweep", "--root", str(q), "--config", str(_global(tmp_path))]
    code = main(argv + (["--json"] if as_json else []))
    out, err = capsys.readouterr()

    assert code == 2
    _assert_untouched(root, q, before)
    assert "--root" in err and ".git" in err
    if as_json:
        payload = json.loads(out)
        assert payload["moved"] == []
        assert payload["error"]["code"] == "root_is_queue"
        assert "--root" in payload["error"]["message"]
        assert payload["archive_routes"][0]["root_is_queue"] is True


def test_sweep_refusal_message_in_english(tmp_path: Path, capsys):
    root, q = _repo(tmp_path)
    _doc(q / "plan_done.md")

    code = main(["sweep", "--root", str(q), "--config", str(_global(tmp_path)), "--lang", "en"])
    err = capsys.readouterr().err

    assert code == 2
    assert "aborted without moving anything" in err
    assert "--root" in err


def test_sweep_dry_run_json_warns_on_stderr(tmp_path: Path, capsys):
    root, q = _repo(tmp_path)
    _doc(q / "plan_done.md")
    before = _snapshot(root)

    code = main([
        "sweep", "--root", str(q), "--config", str(_global(tmp_path)), "--json", "--dry-run",
    ])
    out, err = capsys.readouterr()
    payload = json.loads(out)

    assert code == 0
    assert payload["archive_routes"][0]["root_is_queue"] is True
    assert len(payload["moved"]) == 1  # 計画は最後まで出す
    warn_lines = [line for line in err.splitlines() if "--root" in line]
    assert len(warn_lines) == 1
    assert _snapshot(root) == before


def test_sweep_dry_run_text_still_warns_once(tmp_path: Path, capsys):
    _root, q = _repo(tmp_path)
    _doc(q / "plan_done.md")

    code = main(["sweep", "--root", str(q), "--config", str(_global(tmp_path)), "--dry-run"])
    err = capsys.readouterr().err

    assert code == 0
    assert len([line for line in err.splitlines() if "--root" in line]) == 1


@pytest.mark.parametrize("with_project_dir", [False, True])
def test_sweep_repo_root_is_not_refused(tmp_path: Path, capsys, with_project_dir: bool):
    root, q = _repo(tmp_path)
    _doc(q / "plan_done.md")

    argv = ["sweep", "--root", str(root), "--config", str(_global(tmp_path)), "--json"]
    if with_project_dir:
        argv += ["--project-dir", str(root)]
    code = main(argv)
    out, err = capsys.readouterr()

    assert code == 0, err
    assert "--root" not in err
    assert not (q / "plan_done.md").exists()
    assert list((q / "archive").rglob("plan_done.md"))
    assert not (q / "docs").exists()
    assert "error" not in out


def test_sweep_project_filter_skips_other_queue_root(tmp_path: Path, capsys):
    """別の root が queue 自身でも、--project で外した project は二重にならないので止めない。"""
    good, good_q = _repo(tmp_path, "good")
    _doc(good_q / "plan_done.md")
    _bad_root, bad_q = _repo(tmp_path, "bad")
    _doc(bad_q / "plan_other.md")
    cfg = str(_global(tmp_path))
    roots = ["--root", str(good), "--root", str(bad_q)]

    main(["sweep", *roots, "--config", cfg, "--json", "--dry-run"])
    routes = json.loads(capsys.readouterr().out)["archive_routes"]
    good_project = next(r["project"] for r in routes if not r.get("root_is_queue"))
    assert any(r.get("root_is_queue") for r in routes)

    assert main(["sweep", *roots, "--config", cfg]) == 2
    capsys.readouterr()
    assert (good_q / "plan_done.md").exists()

    code = main(["sweep", *roots, "--config", cfg, "--project", good_project])
    err = capsys.readouterr().err
    assert code == 0, err
    assert not (good_q / "plan_done.md").exists()
    assert (bad_q / "plan_other.md").exists()
    assert not (bad_q / "docs").exists()


# --- promote ------------------------------------------------------------------------------


@pytest.mark.parametrize("as_json", [False, True])
def test_promote_refuses_queue_root(tmp_path: Path, capsys, as_json: bool):
    root, q = _repo(tmp_path)
    _doc(q / "plan_watch.md", state="watching")
    before = _snapshot(root)

    argv = ["promote", "--root", str(q), "--config", str(_global(tmp_path))]
    code = main(argv + (["--json"] if as_json else []))
    out, err = capsys.readouterr()

    assert code == 2
    _assert_untouched(root, q, before)
    assert "--root" in err
    if as_json:
        assert json.loads(out)["error"]["code"] == "root_is_queue"


def test_promote_dry_run_json_warns_on_stderr(tmp_path: Path, capsys):
    root, q = _repo(tmp_path)
    _doc(q / "plan_watch.md", state="watching")
    before = _snapshot(root)

    code = main([
        "promote", "--root", str(q), "--config", str(_global(tmp_path)), "--json", "--dry-run",
    ])
    out, err = capsys.readouterr()

    assert code == 0
    assert len(json.loads(out)) == 1  # 計画は最後まで出す
    assert len([line for line in err.splitlines() if "--root" in line]) == 1
    assert _snapshot(root) == before


def test_promote_repo_root_is_not_refused(tmp_path: Path, capsys):
    root, q = _repo(tmp_path)
    _doc(q / "plan_watch.md", state="watching")

    code = main(["promote", "--root", str(root), "--config", str(_global(tmp_path))])
    err = capsys.readouterr().err

    assert code == 0, err
    assert "--root" not in err
    assert not (q / "plan_watch.md").exists()
    assert list((q / "archive").rglob("plan_watch.md"))


# --- apply --------------------------------------------------------------------------------


@pytest.mark.parametrize(("action", "state"), [("discard", "in-progress"), ("promote", "watching")])
def test_apply_refuses_queue_root(tmp_path: Path, capsys, action: str, state: str):
    root, q = _repo(tmp_path)
    target = _doc(q / "plan_wip.md", state=state)
    before = _snapshot(root)

    code = main([
        "apply", "--root", str(q), "--config", str(_global(tmp_path)),
        "--path", str(target), "--action", action,
    ])
    out, err = capsys.readouterr()

    assert code == 2
    _assert_untouched(root, q, before)
    assert "--root" in err
    assert out == ""


def test_apply_dry_run_warns_on_stderr(tmp_path: Path, capsys):
    root, q = _repo(tmp_path)
    target = _doc(q / "plan_wip.md", state="in-progress")
    before = _snapshot(root)

    code = main([
        "apply", "--root", str(q), "--config", str(_global(tmp_path)),
        "--path", str(target), "--action", "discard", "--dry-run",
    ])
    out, err = capsys.readouterr()

    assert code == 0
    assert json.loads(out)["op"] == "discard"
    assert len([line for line in err.splitlines() if "--root" in line]) == 1
    assert _snapshot(root) == before


def test_apply_relabel_on_queue_root_is_not_refused(tmp_path: Path, capsys):
    """archive へ移さない action は二重にならないので止めない。"""
    _root, q = _repo(tmp_path)
    target = _doc(q / "plan_wip.md", state="in-progress")

    code = main([
        "apply", "--root", str(q), "--config", str(_global(tmp_path)),
        "--path", str(target), "--action", "relabel", "--to", "watching",
    ])
    err = capsys.readouterr().err

    assert code == 0, err
    assert "--root" not in err
    assert target.exists()


def test_apply_disallowed_action_keeps_existing_error(tmp_path: Path, capsys):
    """許されない action は、queue の判定より先に従来のエラーを出す（どちらでも何も移さない）。"""
    root, q = _repo(tmp_path)
    target = _doc(q / "plan_wip.md", state="in-progress")
    before = _snapshot(root)

    code = main([
        "apply", "--root", str(q), "--config", str(_global(tmp_path)),
        "--path", str(target), "--action", "promote",
    ])
    err = capsys.readouterr().err

    assert code == 2
    assert "promote" in err and "--root" not in err
    assert _snapshot(root) == before


def test_apply_repo_root_is_not_refused(tmp_path: Path, capsys):
    root, q = _repo(tmp_path)
    target = _doc(q / "plan_wip.md", state="in-progress")

    code = main([
        "apply", "--root", str(root), "--project-dir", str(root),
        "--config", str(_global(tmp_path)), "--path", str(target), "--action", "discard",
    ])
    err = capsys.readouterr().err

    assert code == 0, err
    assert not target.exists()
    assert list((q / "archive").rglob("plan_wip.md"))
    assert not (q / "docs").exists()
