"""``archive_refs.scope: repo`` — repo 全体の参照の書き換え・移す前に止める仕組み・移した後の検証。

git 管理下の ``docs/local`` と、``docs/obsidian``・html・json・コードの注釈に参照を持つ repo を
合成して、1 回の ``sweep`` で「移すもの・移さないもの・理由」と、移した後の検証 3 点が 0 に
なることを確かめる。block-style の ``related:`` は ``scope`` に関係なく書き換えられること。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from docsweep.archive_guard import (
    archive_ref_settings,
    snapshot_links,
    verify_moves,
)
from docsweep.cli import main
from docsweep.config import load_config
from docsweep.engine import auto_sweep
from docsweep.move_refs import Move, matches_any
from docsweep.services.archive import undo_last_batch

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git が無い")

_H1 = {"done": "完了", "in-progress": "実行中", "discarded": "廃止"}


def _doc(path: Path, *, state: str = "done", body: list[str] | None = None,
         related: list[str] | None = None, block_related: bool = False,
         parent: str | None = None) -> Path:
    head = ["---", "type: plan", "status: draft", f"docsweep_state: {state}"]
    if parent:
        head.append(f"docsweep_parent: {parent}")
    if block_related and related:
        head.append("related:")
        head.extend(f"  - {item}" for item in related)
    else:
        head.append(f"related: [{', '.join(related or [])}]")
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


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """合成 repo。コミットした後に、未コミットの変更と ignore を足す。"""
    root = tmp_path / "repo"
    q = root / "docs" / "local"
    _put(root / ".docsweep.yaml", "\n".join([
        "archive_refs:",
        "  scope: repo",
        "  exclude:",
        "    - docs/obsidian/evidence/**",
        "    - docs/local/reply/**",
        "",
    ]))
    _put(root / ".gitignore", ".docsweep/\n")
    # 移すもの
    _doc(q / "plan_done.md", body=[
        "[残る計画](plan_keep.md)",
        "docs/local/plan_keep.md を参照",
        "[元から切れていた](plan_gone.md)",
    ])
    # 残る（参照を直される側）。related は block-style
    _doc(q / "plan_keep.md", state="in-progress", related=["docs/local/plan_done.md"],
         block_related=True, body=[
             "[完了した計画](plan_done.md)",
             "path 形式: docs/local/plan_done.md。",
             "別の名前: docs/local/plan_done.md.bak と docs/local/plan_done.mdx",
         ])
    # コードから参照されている
    _doc(q / "plan_coderef.md")
    _put(root / "src" / "app.py", "# 仕様は docs/local/plan_coderef.md を見る\n")
    # 系統（子 1 本が実行中で残る）
    _doc(q / "plan_fam.md")
    _doc(q / "plan_fam_c1_ui.md")
    _doc(q / "plan_fam_c2_api.md", state="in-progress")
    # 未コミット
    _doc(q / "plan_dirty.md")
    # 未コミットのファイルから参照される
    _doc(q / "plan_refd.md")
    _put(root / "docs" / "notes.md", "メモ\n")
    # ignore を外れてしまう
    _doc(q / "plan_secret.md")
    # repo の他の場所からの参照
    _put(root / "docs" / "obsidian" / "note.md",
         "docs/local/plan_done.md と [リンク](../local/plan_done.md)\n")
    _put(root / "docs" / "obsidian" / "evidence" / "e.md", "当時: docs/local/plan_done.md\n")
    _put(q / "reply" / "old.md", "送った: docs/local/plan_done.md\n")
    _put(root / "docs" / "report.html",
         '<p>docs/local/plan_done.md</p><a href="local/plan_done.md">計画</a>\n')
    _put(root / "data.json", '{"ref": "docs/local/plan_done.md"}\n')
    _git(root, "init", "-q")
    _put(root / ".git" / "info" / "exclude", "docs/local/plan_secret.md\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    # コミットの後の変更（未コミット）
    _put(q / "plan_dirty.md", _read(q / "plan_dirty.md") + "編集中\n")
    _put(root / "docs" / "notes.md", "メモ: docs/local/plan_refd.md\n")
    return root


def _sweep_json(repo: Path, tmp_path: Path, *extra: str, capsys) -> tuple[int, dict]:
    code = main([
        "sweep", "--root", str(repo), "--project-dir", str(repo),
        "--config", str(_global(tmp_path)), "--json", *extra,
    ])
    out = capsys.readouterr().out
    return code, json.loads(out)


def _names(items: list[dict], key: str = "src") -> set[str]:
    return {Path(item[key]).name for item in items}


def test_repo_scope_sweep_moves_rewrites_holds_and_verifies(repo: Path, tmp_path: Path, capsys):
    q = repo / "docs" / "local"
    evidence_before = _read(repo / "docs" / "obsidian" / "evidence" / "e.md")
    reply_before = _read(q / "reply" / "old.md")

    code, payload = _sweep_json(repo, tmp_path, capsys=capsys)

    assert code == 0, payload
    assert _names(payload["moved"]) == {"plan_done.md"}
    held = {(Path(item["path"]).name, item["reason"]): item for item in payload["held"]}
    assert set(held) == {
        ("plan_coderef.md", "referenced_by_code"),
        ("plan_fam.md", "family_incomplete"),
        ("plan_fam_c1_ui.md", "family_incomplete"),
        ("plan_dirty.md", "uncommitted"),
        ("plan_refd.md", "referenced_by_uncommitted"),
        ("plan_secret.md", "ignore_lost"),
    }
    assert held[("plan_coderef.md", "referenced_by_code")]["referrers"] == ["src/app.py"]
    assert held[("plan_refd.md", "referenced_by_uncommitted")]["referrers"] == ["docs/notes.md"]
    assert "docs/local/plan_fam_c2_api.md" in held[("plan_fam.md", "family_incomplete")]["referrers"]
    assert payload["verify"]["ok"] is True, payload["verify"]

    # 止めた文書はそのまま
    for name in ("plan_coderef.md", "plan_fam.md", "plan_dirty.md", "plan_refd.md", "plan_secret.md"):
        assert (q / name).is_file()
    assert (q / "archive" / "plan_done.md").is_file()

    keep = _read(q / "plan_keep.md")
    assert "related:\n  - docs/local/archive/plan_done.md\n" in keep  # block-style のまま
    assert "[完了した計画](archive/plan_done.md)" in keep
    assert "path 形式: docs/local/archive/plan_done.md。" in keep
    assert "docs/local/plan_done.md.bak と docs/local/plan_done.mdx" in keep  # 別の名前は触らない
    moved = _read(q / "archive" / "plan_done.md")
    assert "[残る計画](../plan_keep.md)" in moved
    assert "[元から切れていた](plan_gone.md)" in moved  # 元から切れていたリンクは触らない
    note = _read(repo / "docs" / "obsidian" / "note.md")
    assert note == "docs/local/archive/plan_done.md と [リンク](../local/archive/plan_done.md)\n"
    html = _read(repo / "docs" / "report.html")
    assert '<p>docs/local/archive/plan_done.md</p><a href="local/archive/plan_done.md">' in html
    assert _read(repo / "data.json") == '{"ref": "docs/local/archive/plan_done.md"}\n'
    # 履歴・旧記録とコードは書き換えない
    assert _read(repo / "docs" / "obsidian" / "evidence" / "e.md") == evidence_before
    assert _read(q / "reply" / "old.md") == reply_before
    assert _read(repo / "src" / "app.py") == "# 仕様は docs/local/plan_coderef.md を見る\n"
    assert _read(repo / "docs" / "notes.md") == "メモ: docs/local/plan_refd.md\n"


@pytest.mark.parametrize(
    ("name", "base"),
    [
        ("plan_a.md", "plan_a"),
        ("plan_a_c1.md", "plan_a"),
        ("plan_a_c1_ui.md", "plan_a"),
        ("plan_a-c1.md", "plan_a"),
        ("PLAN_A-C12.md", "plan_a"),
        # ハイフンの形は接尾辞を許さない（親子でない名前を巻き込まない）
        ("plan_a-c8-approval.md", "plan_a-c8-approval"),
        ("plan_vitamin-c.md", "plan_vitamin-c"),
    ],
)
def test_family_base_reads_underscore_and_hyphen_children(name: str, base: str):
    from docsweep.archive_guard import _family_base

    assert _family_base(name) == base


def test_hyphen_children_keep_family_together_without_docsweep_parent(
    tmp_path: Path, capsys
):
    """``-c<数字>`` の子は、``docsweep_parent`` が無くても親と同じ系統として止める。

    mer で、親（``plan_x.md``）と一部の子だけが archive へ移り、``-c<数字>`` の子 3 本が
    直下に残って親子が割れた事例の再現。
    """
    root = tmp_path / "repo"
    q = root / "docs" / "local"
    _put(root / ".docsweep.yaml", "archive_refs:\n  scope: repo\n")
    _put(root / ".gitignore", ".docsweep/\n")
    # 子 1 本が実行中で残る系統（ハイフンの形・docsweep_parent なし）
    _doc(q / "plan_hy.md")
    _doc(q / "plan_hy-c1.md")
    _doc(q / "plan_hy-c2.md", state="in-progress")
    # 全員そろって移せる系統
    _doc(q / "plan_hz.md")
    _doc(q / "plan_hz-c1.md")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")

    code, payload = _sweep_json(root, tmp_path, capsys=capsys)

    assert code == 0, payload
    assert _names(payload["moved"]) == {"plan_hz.md", "plan_hz-c1.md"}
    held = {(Path(item["path"]).name, item["reason"]) for item in payload["held"]}
    assert held == {("plan_hy.md", "family_incomplete"), ("plan_hy-c1.md", "family_incomplete")}
    for name in ("plan_hy.md", "plan_hy-c1.md", "plan_hy-c2.md"):
        assert (q / name).is_file()
    assert (q / "archive" / "plan_hz-c1.md").is_file()


def test_code_mentioning_only_the_file_name_holds_the_doc(tmp_path: Path, capsys):
    """コードの注釈が ``docs/local/`` を付けずファイル名だけで書いていても、移さない。

    mer で、``src`` の注釈が ``plan_x_c5_y.md`` とだけ書いていた文書が「参照なし」と判定され、
    移せる一覧に入った事例の再現。別の名前の一部（``old_plan_x.md``・``plan_x.md.bak``）は
    同じ名前ではないので数えない。docs の中のファイル名だけの記述では止めない。
    """
    root = tmp_path / "repo"
    q = root / "docs" / "local"
    _put(root / ".docsweep.yaml", "archive_refs:\n  scope: repo\n")
    _put(root / ".gitignore", ".docsweep/\n")
    _doc(q / "plan_bare.md")        # コードにファイル名だけで書かれている
    _doc(q / "plan_bare_end.md")    # 文末の「.」の直前（句点）で書かれている
    _doc(q / "plan_part.md")        # 別の名前の一部としてだけ書かれている
    _doc(q / "plan_other.md")       # 拡張子の後に別の名前が続く（.bak）
    _doc(q / "plan_docmention.md")  # docs の中でファイル名だけで書かれている
    _doc(q / "plan_free.md")        # どこにも書かれていない
    _put(root / "src" / "app.py", "\n".join([
        "# 根拠: plan_bare.md の案 A",
        "# 詳しくは plan_bare_end.md.",
        "# old_plan_part.md は別の文書。plan_other.md.bak も別のファイル",
        "",
    ]))
    _put(root / "docs" / "note.md", "メモ: plan_docmention.md を見る\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")

    code, payload = _sweep_json(root, tmp_path, capsys=capsys)

    assert code == 0, payload
    held = {(Path(item["path"]).name, item["reason"]): item for item in payload["held"]}
    assert set(held) == {
        ("plan_bare.md", "referenced_by_code"),
        ("plan_bare_end.md", "referenced_by_code"),
    }
    assert held[("plan_bare.md", "referenced_by_code")]["referrers"] == ["src/app.py"]
    assert _names(payload["moved"]) == {
        "plan_part.md", "plan_other.md", "plan_docmention.md", "plan_free.md",
    }
    assert (q / "plan_bare.md").is_file()


def test_repo_scope_second_sweep_does_nothing(repo: Path, tmp_path: Path, capsys):
    _sweep_json(repo, tmp_path, capsys=capsys)
    snapshot = {p: _read(p) for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts
                and ".docsweep" not in p.parts}

    code, payload = _sweep_json(repo, tmp_path, capsys=capsys)

    assert code == 0
    assert payload["moved"] == []
    assert payload["ref_updates"] == []
    after = {p: _read(p) for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts
             and ".docsweep" not in p.parts}
    assert after == snapshot


def test_repo_scope_dry_run_writes_nothing_and_reports_holds(repo: Path, tmp_path: Path, capsys):
    before = {p: _read(p) for p in (repo / "docs").rglob("*.md")}

    code, payload = _sweep_json(repo, tmp_path, "--dry-run", capsys=capsys)

    assert code == 0
    assert _names(payload["moved"]) == {"plan_done.md"}
    assert {item["reason"] for item in payload["held"]} >= {"referenced_by_code", "uncommitted"}
    assert "verify" not in payload
    assert {p: _read(p) for p in (repo / "docs").rglob("*.md")} == before
    paths = {Path(u["path"]).name for u in payload["ref_updates"]}
    assert {"plan_keep.md", "note.md", "report.html", "data.json"} <= paths


def test_repo_scope_undo_restores_block_style_and_repo_files(repo: Path, tmp_path: Path, capsys):
    q = repo / "docs" / "local"
    keep_before = _read(q / "plan_keep.md")
    note_before = _read(repo / "docs" / "obsidian" / "note.md")
    html_before = _read(repo / "docs" / "report.html")

    _sweep_json(repo, tmp_path, capsys=capsys)
    result = undo_last_batch(config=_cfg(repo, tmp_path))

    assert not result.failed, result
    assert (q / "plan_done.md").is_file()
    assert _read(q / "plan_keep.md") == keep_before
    assert _read(repo / "docs" / "obsidian" / "note.md") == note_before
    assert _read(repo / "docs" / "report.html") == html_before


def test_ref_scope_flag_overrides_setting(repo: Path, tmp_path: Path, capsys):
    """``--ref-scope queue`` なら従来どおり（止めない・queue の md の相対リンクだけ）。"""
    code, payload = _sweep_json(repo, tmp_path, "--ref-scope", "queue", "--dry-run", capsys=capsys)

    assert code == 0
    assert "held" not in payload
    assert "plan_coderef.md" in _names(payload["moved"])
    paths = {Path(u["path"]).name for u in payload["ref_updates"]}
    assert "note.md" not in paths
    assert "report.html" not in paths


def test_block_style_related_rewritten_in_queue_scope(tmp_path: Path):
    """``scope: queue``（既定）でも block-style の related は失敗しない（以前は ref_failed）。"""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    q = root / "docs" / "local"
    _doc(q / "plan_done.md")
    _doc(q / "plan_keep.md", state="in-progress", block_related=True,
         related=["docs/local/plan_done.md", "plan_other.md"])

    moved = auto_sweep(_cfg(root, tmp_path))

    assert not moved.ref_failed, moved.ref_failed
    assert moved.held == [] and moved.verify is None
    assert "related:\n  - docs/local/archive/plan_done.md\n  - plan_other.md\n" in _read(
        q / "plan_keep.md"
    )


def test_root_is_queue_warns(tmp_path: Path, capsys):
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    q = root / "docs" / "local"
    _doc(q / "plan_done.md")

    code = main([
        "sweep", "--root", str(q), "--config", str(_global(tmp_path)), "--json", "--dry-run",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    route = payload["archive_routes"][0]
    assert route.get("root_is_queue") is True
    assert "--root" in route["warning"]


def test_root_is_repo_has_no_queue_warning(tmp_path: Path, capsys):
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    _doc(root / "docs" / "local" / "plan_done.md")

    main(["sweep", "--root", str(root), "--config", str(_global(tmp_path)), "--json", "--dry-run"])
    payload = json.loads(capsys.readouterr().out)

    assert "root_is_queue" not in payload["archive_routes"][0]


def test_apply_repo_scope_refuses_held_doc(repo: Path, tmp_path: Path, capsys):
    q = repo / "docs" / "local"
    _doc(q / "plan_coderef.md", state="in-progress")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "relabel")
    before = _read(q / "plan_coderef.md")
    code = main([
        "apply", "--root", str(repo), "--project-dir", str(repo),
        "--config", str(_global(tmp_path)),
        "--path", str(q / "plan_coderef.md"), "--action", "discard",
    ])
    err = capsys.readouterr().err

    assert code == 2
    assert "src/app.py" in err
    assert _read(q / "plan_coderef.md") == before  # ラベルも変えない


def test_apply_repo_scope_moves_and_verifies(repo: Path, tmp_path: Path, capsys):
    q = repo / "docs" / "local"
    _doc(q / "plan_keep.md", state="in-progress", body=["docs/local/plan_solo.md"])
    _doc(q / "plan_solo.md", state="in-progress")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "solo")

    code = main([
        "apply", "--root", str(repo), "--project-dir", str(repo),
        "--config", str(_global(tmp_path)),
        "--path", str(q / "plan_solo.md"), "--action", "discard",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["verify"]["ok"] is True
    assert (q / "archive" / "plan_solo.md").is_file()
    assert "docs/local/archive/plan_solo.md" in _read(q / "plan_keep.md")


def test_verify_finds_stale_and_broken_after_manual_move(tmp_path: Path):
    """書き換えずに移すと、検証が旧パスの残りと新しく切れたリンクを数える（元から切れたものは数えない）。"""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    q = root / "docs" / "local"
    src = _doc(q / "plan_done.md")
    _put(root / "docs" / "obsidian" / "n.md",
         "docs/local/plan_done.md [a](../local/plan_done.md) [b](../local/plan_done_gone.md)\n")
    cfg = _cfg(root, tmp_path)
    settings = archive_ref_settings(root, cfg, scope="repo")
    dst = q / "archive" / "plan_done.md"
    moves = [Move(src, dst)]
    before = snapshot_links(moves, project_root=root, config=cfg, settings=settings)
    dst.parent.mkdir(parents=True)
    src.rename(dst)

    result = verify_moves(moves, project_root=root, config=cfg, settings=settings, before=before)

    assert result["ok"] is False
    assert result["stale_refs"] == [{"path": "docs/obsidian/n.md", "ref": "docs/local/plan_done.md"}]
    assert result["broken_links"] == [{"path": "docs/obsidian/n.md", "link": "../local/plan_done.md"}]
    assert result["missing_targets"] == []


def test_settings_reject_unknown_key_and_bad_scope(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    cfg = _cfg(root, tmp_path)
    _put(root / ".docsweep.yaml", "archive_refs:\n  scop: repo\n")
    with pytest.raises(ValueError, match="scop"):
        archive_ref_settings(root, cfg)
    _put(root / ".docsweep.yaml", "archive_refs:\n  scope: all\n")
    with pytest.raises(ValueError, match="all"):
        archive_ref_settings(root, cfg)


def test_settings_project_overrides_global_per_key(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    global_path = tmp_path / "g.yaml"
    global_path.write_text(
        "roots: []\narchive_refs:\n  scope: repo\n  exclude: [docs/archive/**]\n", encoding="utf-8"
    )
    cfg = load_config(explicit_roots=[str(root)], global_path=global_path)
    _put(root / ".docsweep.yaml", "archive_refs:\n  code_paths: [lib/**]\n")

    settings = archive_ref_settings(root, cfg)

    assert settings.scope == "repo"
    assert settings.exclude == ("docs/archive/**",)
    assert settings.code_paths == ("lib/**",)


@pytest.mark.parametrize(
    ("rel", "patterns", "expected"),
    [
        ("docs/obsidian/evidence/a/b.md", ["docs/obsidian/evidence/**"], True),
        ("docs/obsidian/note.md", ["docs/obsidian/evidence/**"], False),
        ("docs/local/reply/x.md", ["docs/local/reply/"], True),
        ("sub/CLAUDE.md.bak1", ["CLAUDE.md.bak*"], True),
        ("CLAUDE.md", ["CLAUDE.md"], True),
        ("docs/CLAUDE.md", ["/CLAUDE.md"], False),
    ],
)
def test_matches_any(rel: str, patterns: list[str], expected: bool):
    assert matches_any(rel, patterns) is expected
