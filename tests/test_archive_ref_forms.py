"""``archive_refs.scope: repo`` — 旧パスの書き方の幅と、dry-run の検証の見込み。

1. 本文の旧パスは ``/`` 区切りの repo 相対だけでなく、``\\`` 区切りの repo 相対・repo の中を
   指す絶対パス（``/`` と ``\\``。Windows のドライブ名の大小は区別しない）・JSON の文字列で
   エスケープした ``\\\\`` でも、書き換え・移す前に止める判定・移した後の検証で同じに扱う。
   書き換えた後も書かれていた区切り・root のままにし、``undo`` で元へ戻る。
2. dry-run でも ``verify`` に見込み（``preview: True``）を入れる。``stale_refs`` は本実行の
   検証と同じ形で、書き換えた後にも残る旧パスを数える。

合成した git repo（``tmp_path`` の中）だけを使う。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from docsweep.archive_guard import archive_ref_settings, snapshot_links, verify_moves
from docsweep.cli import main
from docsweep.config import load_config
from docsweep.engine import auto_sweep, promote_state
from docsweep.move_refs import Move
from docsweep.services.archive import undo_last_batch

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")

windows_only = pytest.mark.skipif(os.name != "nt", reason="ドライブ名のある絶対パスは Windows だけ")

_H1 = {"done": "完了", "in-progress": "実行中", "watching": "様子見"}


def _doc(path: Path, *, state: str = "done", body: list[str] | None = None,
         extra_head: list[str] | None = None) -> Path:
    head = ["---", "type: plan", "status: draft", f"docsweep_state: {state}", *(extra_head or [])]
    head += ["---", f"# [{_H1[state]}] {path.stem}", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(head + (body or [])) + "\n", encoding="utf-8", newline="")
    return path


def _put(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _read(path: Path) -> str:
    return path.open("r", encoding="utf-8", newline="").read()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "core.hooksPath=.no-hooks", "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True,
    )


def _global(tmp_path: Path) -> Path:
    path = tmp_path / "global.yaml"
    path.write_text("roots: []\n", encoding="utf-8")
    return path


def _cfg(repo: Path, tmp_path: Path):
    return load_config(explicit_roots=[str(repo)], global_path=_global(tmp_path))


def _new_repo(tmp_path: Path, *, exclude: list[str] | None = None) -> Path:
    root = tmp_path / "repo"
    lines = ["archive_refs:", "  scope: repo"]
    if exclude:
        lines.append("  exclude:")
        lines.extend(f"    - {item}" for item in exclude)
    _put(root / ".docsweep.yaml", "\n".join(lines) + "\n")
    _put(root / ".gitignore", ".docsweep/\n")
    return root


def _commit(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")


def _sweep_json(repo: Path, tmp_path: Path, *extra: str, capsys) -> tuple[int, dict]:
    code = main([
        "sweep", "--root", str(repo), "--project-dir", str(repo),
        "--config", str(_global(tmp_path)), "--json", *extra,
    ])
    return code, json.loads(capsys.readouterr().out)


def _files(repo: Path) -> dict[Path, str]:
    return {
        path: _read(path) for path in repo.rglob("*")
        if path.is_file() and ".git" not in path.parts and ".docsweep" not in path.parts
    }


def _swap_drive_case(text: str) -> str:
    return text[0].swapcase() + text[1:]


# ------------------------------------------------------------------
# 1. 書き方の幅
# ------------------------------------------------------------------


def test_backslash_repo_relative_path_is_rewritten_keeping_separator(tmp_path: Path, capsys):
    root = _new_repo(tmp_path)
    q = root / "docs" / "local"
    _doc(q / "plan_done.md")
    _put(root / "docs" / "note.md", "\n".join([
        r"A docs\local\plan_done.md を見る",
        r"B docs/local/plan_done.md",
        # 別の名前の一部・別のパスの続きは数えない
        r"C docs\local\plan_done.md.bak と docs\local\plan_done.mdx",
        r"D x\docs\local\plan_done.md と .\docs\local\plan_done.md",
        r"E docs\local\old_plan_done.md",
        "",
    ]))
    _commit(root)

    code, payload = _sweep_json(root, tmp_path, capsys=capsys)

    assert code == 0, payload
    assert payload["verify"]["ok"] is True, payload["verify"]
    assert _read(root / "docs" / "note.md") == "\n".join([
        r"A docs\local\archive\plan_done.md を見る",
        r"B docs/local/archive/plan_done.md",
        r"C docs\local\plan_done.md.bak と docs\local\plan_done.mdx",
        r"D x\docs\local\plan_done.md と .\docs\local\plan_done.md",
        r"E docs\local\old_plan_done.md",
        "",
    ])


def test_slash_absolute_path_inside_repo_is_rewritten(tmp_path: Path, capsys):
    """``/`` 区切りの絶対パス（repo の中）は、書かれた root のまま移動先へ。repo の外は触らない。"""
    root = _new_repo(tmp_path)
    q = root / "docs" / "local"
    _doc(q / "plan_done.md")
    inside = root.resolve().as_posix()
    _put(root / "docs" / "note.md", "\n".join([
        f"A {inside}/docs/local/plan_done.md",
        "B Z:/elsewhere/repo/docs/local/plan_done.md",
        f"C {inside}/docs/local/plan_done.md.bak",
        "",
    ]))
    _commit(root)

    code, payload = _sweep_json(root, tmp_path, capsys=capsys)

    assert code == 0, payload
    assert payload["verify"]["ok"] is True, payload["verify"]
    assert _read(root / "docs" / "note.md") == "\n".join([
        f"A {inside}/docs/local/archive/plan_done.md",
        "B Z:/elsewhere/repo/docs/local/plan_done.md",
        f"C {inside}/docs/local/plan_done.md.bak",
        "",
    ])


@windows_only
def test_backslash_absolute_path_and_drive_case(tmp_path: Path, capsys):
    root = _new_repo(tmp_path)
    q = root / "docs" / "local"
    _doc(q / "plan_done.md")
    win = str(root.resolve())
    swapped = _swap_drive_case(win)
    slash_swapped = _swap_drive_case(root.resolve().as_posix())
    _put(root / "docs" / "note.md", "\n".join([
        f"A {win}\\docs\\local\\plan_done.md",
        f"B {swapped}\\docs\\local\\plan_done.md",
        f"C {slash_swapped}/docs/local/plan_done.md",
        "",
    ]))
    _commit(root)

    code, payload = _sweep_json(root, tmp_path, capsys=capsys)

    assert code == 0, payload
    assert payload["verify"]["ok"] is True, payload["verify"]
    assert _read(root / "docs" / "note.md") == "\n".join([
        f"A {win}\\docs\\local\\archive\\plan_done.md",
        f"B {swapped}\\docs\\local\\archive\\plan_done.md",
        f"C {slash_swapped}/docs/local/archive/plan_done.md",
        "",
    ])


def test_json_escaped_backslash_path_is_rewritten(tmp_path: Path, capsys):
    """JSON の文字列でエスケープした ``\\\\`` 区切りも扱い、読み込んだ値が移動先を指す。"""
    root = _new_repo(tmp_path)
    q = root / "docs" / "local"
    _doc(q / "plan_done.md")
    data = {"rel": "docs\\local\\plan_done.md", "other": "docs\\local\\plan_done.md.bak"}
    if os.name == "nt":
        data["abs"] = str(root.resolve()) + "\\docs\\local\\plan_done.md"
    _put(root / "data.json", json.dumps(data) + "\n")
    _commit(root)

    code, payload = _sweep_json(root, tmp_path, capsys=capsys)

    assert code == 0, payload
    assert payload["verify"]["ok"] is True, payload["verify"]
    loaded = json.loads(_read(root / "data.json"))
    assert loaded["rel"] == "docs\\local\\archive\\plan_done.md"
    assert loaded["other"] == "docs\\local\\plan_done.md.bak"
    if os.name == "nt":
        assert loaded["abs"] == str(root.resolve()) + "\\docs\\local\\archive\\plan_done.md"


def test_undo_restores_backslash_and_absolute_forms(tmp_path: Path, capsys):
    root = _new_repo(tmp_path)
    q = root / "docs" / "local"
    _doc(q / "plan_done.md", body=[r"自分: docs\local\plan_done.md"])
    lines = [
        r"A docs\local\plan_done.md",
        f"B {root.resolve().as_posix()}/docs/local/plan_done.md",
        "C docs\\\\local\\\\plan_done.md",
    ]
    if os.name == "nt":
        lines.append(f"D {_swap_drive_case(str(root.resolve()))}\\docs\\local\\plan_done.md")
    _put(root / "docs" / "note.md", "\n".join(lines) + "\n")
    _commit(root)
    before = _files(root)

    code, payload = _sweep_json(root, tmp_path, capsys=capsys)
    assert code == 0, payload
    assert _read(root / "docs" / "note.md") != before[root / "docs" / "note.md"]
    assert r"自分: docs\local\archive\plan_done.md" in _read(q / "archive" / "plan_done.md")

    result = undo_last_batch(config=_cfg(root, tmp_path))

    assert not result.failed, result
    assert _files(root) == before


def test_backslash_reference_from_uncommitted_file_holds_the_doc(tmp_path: Path, capsys):
    """未コミットの変更があるファイルが ``\\`` 区切り・絶対パスで参照していれば、移さない。"""
    root = _new_repo(tmp_path)
    q = root / "docs" / "local"
    _doc(q / "plan_refd.md")
    _doc(q / "plan_absd.md")
    _doc(q / "plan_free.md")
    _put(root / "docs" / "notes.md", "メモ\n")
    _put(root / "docs" / "notes2.md", "メモ\n")
    _commit(root)
    _put(root / "docs" / "notes.md", "メモ: docs\\local\\plan_refd.md\n")
    _put(root / "docs" / "notes2.md", f"メモ: {root.resolve().as_posix()}/docs/local/plan_absd.md\n")

    code, payload = _sweep_json(root, tmp_path, capsys=capsys)

    assert code == 0, payload
    held = {(Path(item["path"]).name, item["reason"]): item for item in payload["held"]}
    assert set(held) == {
        ("plan_refd.md", "referenced_by_uncommitted"),
        ("plan_absd.md", "referenced_by_uncommitted"),
    }
    assert held[("plan_refd.md", "referenced_by_uncommitted")]["referrers"] == ["docs/notes.md"]
    assert held[("plan_absd.md", "referenced_by_uncommitted")]["referrers"] == ["docs/notes2.md"]
    assert {Path(item["src"]).name for item in payload["moved"]} == {"plan_free.md"}


def test_backslash_reference_from_code_holds_the_doc(tmp_path: Path, capsys):
    """コードから ``\\`` 区切り・絶対パスで参照されていれば ``referenced_by_code``。"""
    root = _new_repo(tmp_path)
    q = root / "docs" / "local"
    _doc(q / "plan_coderef.md")
    _doc(q / "plan_conf.md")
    _put(root / "src" / "app.py", "# 仕様: docs\\local\\plan_coderef.md\n")
    _put(root / "tools" / "conf.yaml", f"spec: {root.resolve().as_posix()}/docs/local/plan_conf.md\n")
    _commit(root)

    code, payload = _sweep_json(root, tmp_path, capsys=capsys)

    assert code == 0, payload
    held = {(Path(item["path"]).name, item["reason"]): item for item in payload["held"]}
    assert held[("plan_coderef.md", "referenced_by_code")]["referrers"] == ["src/app.py"]
    assert held[("plan_conf.md", "referenced_by_code")]["referrers"] == ["tools/conf.yaml"]
    assert payload["moved"] == []


def test_verify_counts_backslash_and_absolute_stale_refs(tmp_path: Path):
    """書き換えずに移すと、``\\`` 区切り・絶対パスの旧パスも ``stale_refs`` に書かれたまま出る。

    移動先を ``\\`` 区切りで指す参照は、実体があれば ``missing_targets`` に出さない。
    """
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    q = root / "docs" / "local"
    src = _doc(q / "plan_done.md")
    absolute = f"{root.resolve().as_posix()}/docs/local/plan_done.md"
    _put(root / "docs" / "obsidian" / "n.md", "\n".join([
        r"docs\local\plan_done.md",
        absolute,
        r"docs\local\plan_done.md.bak",
        r"docs\local\archive\plan_done.md",
        "",
    ]))
    cfg = _cfg(root, tmp_path)
    settings = archive_ref_settings(root, cfg, scope="repo")
    dst = q / "archive" / "plan_done.md"
    moves = [Move(src, dst)]
    before = snapshot_links(moves, project_root=root, config=cfg, settings=settings)
    dst.parent.mkdir(parents=True)
    src.rename(dst)

    result = verify_moves(moves, project_root=root, config=cfg, settings=settings, before=before)

    assert result["stale_refs"] == [
        {"path": "docs/obsidian/n.md", "ref": "docs\\local\\plan_done.md"},
        {"path": "docs/obsidian/n.md", "ref": absolute},
    ]
    assert result["missing_targets"] == []
    assert result["ok"] is False
    assert "preview" not in result


# ------------------------------------------------------------------
# 2. dry-run の検証の見込み
# ------------------------------------------------------------------


def _preview_repo(tmp_path: Path) -> Path:
    """本実行で書き換えても旧パスが残る箇所（frontmatter の参照でない欄）を持つ repo。"""
    root = _new_repo(tmp_path, exclude=["docs/obsidian/evidence/**"])
    q = root / "docs" / "local"
    _doc(q / "plan_done.md", extra_head=["source: docs/local/plan_done.md"],
         body=["自分: docs/local/plan_done.md"])
    _doc(q / "plan_keep.md", state="in-progress", extra_head=["origin: docs\\local\\plan_done.md"],
         body=["本文: docs\\local\\plan_done.md"])
    _put(root / "docs" / "obsidian" / "evidence" / "e.md", "当時: docs/local/plan_done.md\n")
    _commit(root)
    return root


def test_dry_run_verify_previews_refs_left_after_rewrite(tmp_path: Path, capsys):
    root = _preview_repo(tmp_path)
    before = _files(root)

    code, payload = _sweep_json(root, tmp_path, "--dry-run", capsys=capsys)

    assert _files(root) == before  # 何も書かない
    assert {Path(item["src"]).name for item in payload["moved"]} == {"plan_done.md"}
    preview = payload["verify"]
    assert preview["preview"] is True
    assert preview["ok"] is False
    # 本文の旧パスは書き換わる見込みなので数えない。exclude は本実行の検証と同じく数えない
    assert sorted(preview["stale_refs"], key=lambda item: item["path"]) == [
        {"path": "docs/local/archive/plan_done.md", "ref": "docs/local/plan_done.md"},
        {"path": "docs/local/plan_keep.md", "ref": "docs\\local\\plan_done.md"},
    ]
    assert preview["missing_targets"] == []
    assert preview["broken_links"] == []
    assert code == 1  # CLI は見込みの残りでも検証の失敗として 1 を返す

    real_code, real = _sweep_json(root, tmp_path, capsys=capsys)

    assert real_code == 1
    assert "preview" not in real["verify"]
    assert sorted(real["verify"]["stale_refs"], key=lambda item: item["path"]) == sorted(
        preview["stale_refs"], key=lambda item: item["path"]
    )
    assert set(real["verify"]) == {"stale_refs", "missing_targets", "broken_links", "ok"}


def test_dry_run_verify_ok_when_everything_is_rewritten(tmp_path: Path, capsys):
    root = _new_repo(tmp_path)
    q = root / "docs" / "local"
    _doc(q / "plan_done.md")
    _put(root / "docs" / "note.md", "docs\\local\\plan_done.md と docs/local/plan_done.md\n")
    _commit(root)
    before = _files(root)

    code, payload = _sweep_json(root, tmp_path, "--dry-run", capsys=capsys)

    assert code == 0
    assert _files(root) == before
    assert payload["verify"] == {
        "stale_refs": [], "missing_targets": [], "broken_links": [], "preview": True, "ok": True,
    }


def test_dry_run_text_output_says_the_verify_is_a_preview(tmp_path: Path, capsys):
    """dry-run の文字の出力は、旧パスの残りをまだ起きていない見込みとして書く。"""
    root = _preview_repo(tmp_path)
    args = ["sweep", "--root", str(root), "--project-dir", str(root),
            "--config", str(_global(tmp_path)), "--ref-scope", "repo"]

    code = main([*args, "--dry-run"])
    captured = capsys.readouterr()

    assert code == 1
    assert "検証の見込み: 本実行でも旧パスが残ります: docs/local/plan_keep.md" in captured.err
    assert "検証: 旧パスが残っています" not in captured.err

    real_code = main(args)
    real = capsys.readouterr()

    assert real_code == 1
    assert "検証: 旧パスが残っています: docs/local/plan_keep.md" in real.err
    assert "検証の見込み" not in real.err + real.out


def test_dry_run_preview_counts_files_left_unrewritten_by_skip(tmp_path: Path):
    """``skip``（未コミット）のファイルは書き換えない見込みとして数える（engine を通さず直接）。"""
    from docsweep.archive_guard import preview_verify

    root = _new_repo(tmp_path)
    q = root / "docs" / "local"
    src = _doc(q / "plan_done.md")
    note = _put(root / "docs" / "note.md", "docs\\local\\plan_done.md\n")
    _commit(root)
    cfg = _cfg(root, tmp_path)
    settings = archive_ref_settings(root, cfg)
    moves = [Move(src, q / "archive" / "plan_done.md")]
    skip = frozenset({os.path.normcase(os.path.normpath(os.path.abspath(note)))})

    rewritten = preview_verify(moves, project_root=root, config=cfg, settings=settings)
    skipped = preview_verify(moves, project_root=root, config=cfg, settings=settings, skip=skip)

    assert rewritten["ok"] is True and rewritten["stale_refs"] == []
    assert skipped["ok"] is False
    assert skipped["stale_refs"] == [{"path": "docs/note.md", "ref": "docs\\local\\plan_done.md"}]
    assert skipped["preview"] is True


def test_promote_and_engine_dry_run_carry_preview(tmp_path: Path):
    root = _new_repo(tmp_path)
    q = root / "docs" / "local"
    _doc(q / "plan_watch.md", state="watching", extra_head=["source: docs/local/plan_watch.md"])
    _doc(q / "plan_done.md", extra_head=["source: docs\\local\\plan_done.md"])
    _commit(root)
    cfg = _cfg(root, tmp_path)
    before = _files(root)

    promoted = promote_state(cfg, dry_run=True)
    swept = auto_sweep(cfg, dry_run=True)

    assert _files(root) == before
    assert promoted.verify is not None and promoted.verify["preview"] is True
    assert promoted.verify["stale_refs"] == [
        {"path": "docs/local/archive/plan_watch.md", "ref": "docs/local/plan_watch.md"},
    ]
    assert swept.verify is not None and swept.verify["preview"] is True
    assert swept.verify["stale_refs"] == [
        {"path": "docs/local/archive/plan_done.md", "ref": "docs\\local\\plan_done.md"},
    ]


def test_queue_scope_dry_run_has_no_verify(tmp_path: Path, capsys):
    """repo の範囲を使わないときは、dry-run でも従来どおり ``verify`` を出さない。"""
    root = _new_repo(tmp_path)
    q = root / "docs" / "local"
    _doc(q / "plan_done.md")
    _commit(root)

    code, payload = _sweep_json(root, tmp_path, "--dry-run", "--ref-scope", "queue", capsys=capsys)

    assert code == 0
    assert "verify" not in payload
