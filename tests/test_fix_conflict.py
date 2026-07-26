"""fix-conflict の対象絞り込み（`--path`）— dest 衝突とパス表記ゆれの回帰テスト。

背景: `--path` の dest が `_add_scope_args` の positional `paths` と衝突しており、
positional 側の既定値 `[]` が append 結果を潰していた。結果 `fix_conflicts()` の
`want` が `None` になり、**1 件だけ指定したつもりで全 conflict が処理対象になる**。
`--prefer` の向きが合っていない conflict まで巻き込むと、完了済み plan が未着手へ
戻る誤修正になる。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from docsweep.cli import main
from docsweep.cli.parser import build_parser
from docsweep.config import load_config
from docsweep.fix_conflict import fix_conflicts, list_conflicts

# frontmatter は done なのに H1 は [計画]（= conflict）。
# prefer="frontmatter" で H1 が [完了] に書き換わるので、変更の有無が目で見える。
CONFLICT_MD = """---
type: plan
status: done
---
# [計画] {title}

## 概要

{title} の概要
"""

CLEAN_MD = """---
type: plan
status: planned
---
# [計画] {title}

## 概要

{title} の概要
"""


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """conflict 2 件 + 非 conflict 1 件を持つプロジェクト。"""
    root = tmp_path / "dev"
    proj = root / "proj"
    _write(proj / "pyproject.toml", "[project]\nname='proj'\n")
    _write(proj / "docs" / "local" / "plan_a.md", CONFLICT_MD.format(title="a"))
    _write(proj / "docs" / "local" / "plan_b.md", CONFLICT_MD.format(title="b"))
    _write(proj / "docs" / "local" / "plan_clean.md", CLEAN_MD.format(title="clean"))
    return root


def _cfg(root: Path):
    return load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")


def _h1(p: Path) -> str:
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line
    return ""


def test_parser_path_dest_does_not_collide_with_scan_roots() -> None:
    """``--path`` は positional の scan root（args.paths）と別 dest に入る。"""
    args = build_parser().parse_args(["fix-conflict", "--path", "x.md", "--path", "y.md"])
    assert args.target_paths == ["x.md", "y.md"]
    # positional は空のまま = _build_config がスキャンルートとして拾わない
    assert args.paths == []


def test_workspace_has_two_conflicts(workspace: Path) -> None:
    """前提確認: conflict は 2 件（絞り込みの有無を判定できる状態）。"""
    rows = list_conflicts(_cfg(workspace))
    assert {Path(r["path"]).name for r in rows} == {"plan_a.md", "plan_b.md"}


def test_paths_filter_touches_only_the_specified_file(workspace: Path) -> None:
    """1 件だけ指定したら、その 1 件だけが変更され他は元のまま。"""
    a = workspace / "proj" / "docs" / "local" / "plan_a.md"
    b = workspace / "proj" / "docs" / "local" / "plan_b.md"

    res = fix_conflicts(_cfg(workspace), prefer="frontmatter", paths=[str(a)])

    assert [Path(i.path).name for i in res.items] == ["plan_a.md"]
    assert all(i.fixed for i in res.items)
    assert _h1(a).startswith("# [完了]")
    assert _h1(b).startswith("# [計画]")  # 巻き添えで書き換わっていない


def test_paths_filter_normalizes_separators(workspace: Path) -> None:
    """``..`` を含む非正規なパス表記でも一致する（表記ゆれ吸収）。"""
    local = workspace / "proj" / "docs" / "local"
    denormalized = local / ".." / "local" / "plan_a.md"

    res = fix_conflicts(_cfg(workspace), prefer="frontmatter", paths=[str(denormalized)])

    assert [Path(i.path).name for i in res.items] == ["plan_a.md"]
    assert _h1(local / "plan_a.md").startswith("# [完了]")


@pytest.mark.skipif(os.name != "nt", reason="バックスラッシュ区切りは Windows のパス表記")
def test_paths_filter_accepts_backslash_paths(workspace: Path) -> None:
    """Windows ユーザーが自然に渡すバックスラッシュ表記でも一致する。

    ``FileRecord.path`` はスラッシュ区切りの posix 絶対パスなので、正規化しないと
    素の集合比較では一致しない。
    """
    a = workspace / "proj" / "docs" / "local" / "plan_a.md"
    backslashed = str(a).replace("/", "\\")
    assert "\\" in backslashed

    res = fix_conflicts(_cfg(workspace), prefer="frontmatter", paths=[backslashed])

    assert [Path(i.path).name for i in res.items] == ["plan_a.md"]
    assert _h1(a).startswith("# [完了]")


def test_unmatched_path_warns_on_stderr(workspace: Path, capsys) -> None:
    """存在しないパスを渡したら黙って 0 件終了せず stderr に出す。"""
    missing = workspace / "proj" / "docs" / "local" / "plan_nope.md"

    res = fix_conflicts(_cfg(workspace), prefer="frontmatter", paths=[str(missing)])

    assert res.items == []
    err = capsys.readouterr().err
    assert "plan_nope.md" in err


def test_non_conflict_path_warns_on_stderr(workspace: Path, capsys) -> None:
    """実在するが conflict でないパスも「一致しなかった」として警告する。"""
    clean = workspace / "proj" / "docs" / "local" / "plan_clean.md"

    res = fix_conflicts(_cfg(workspace), prefer="frontmatter", paths=[str(clean)])

    assert res.items == []
    assert "plan_clean.md" in capsys.readouterr().err


def test_no_paths_processes_all_conflicts(workspace: Path) -> None:
    """``--path`` 無指定なら従来どおり全 conflict が対象（絞り込みの既定を壊さない）。"""
    res = fix_conflicts(_cfg(workspace), prefer="frontmatter", dry_run=True)
    assert {Path(i.path).name for i in res.items} == {"plan_a.md", "plan_b.md"}


def test_cli_path_option_filters_end_to_end(workspace: Path) -> None:
    """CLI 経由（parser → cmd_fix_conflict → fix_conflicts）でも絞り込みが効く。"""
    a = workspace / "proj" / "docs" / "local" / "plan_a.md"
    b = workspace / "proj" / "docs" / "local" / "plan_b.md"

    rc = main([
        "fix-conflict", "--root", str(workspace),
        "--prefer", "frontmatter", "--path", str(a),
    ])

    assert rc == 0
    assert _h1(a).startswith("# [完了]")
    assert _h1(b).startswith("# [計画]")


def test_list_path_is_filtered_and_json_reports_unmatched(workspace: Path, capsys) -> None:
    """--list でも --path を守り、誤指定は JSON で機械可読に返す。"""
    a = workspace / "proj" / "docs" / "local" / "plan_a.md"
    missing = workspace / "proj" / "docs" / "local" / "missing.md"

    rc = main([
        "fix-conflict", "--root", str(workspace), "--list", "--json",
        "--path", str(a), "--path", str(missing),
    ])

    import json
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [Path(row["path"]).name for row in payload["conflicts"]] == ["plan_a.md"]
    assert payload["unmatched"] == [str(missing)]


def test_index_records_are_limited_to_requested_root(tmp_path: Path, monkeypatch) -> None:
    """索引が全 project を持っていても --root 外の conflict は修理しない。"""
    from docsweep.scan import sync_index

    root = tmp_path / "dev"
    a = _write(root / "alpha" / "pyproject.toml", "[project]\nname='alpha'\n").parent
    b = _write(root / "beta" / "pyproject.toml", "[project]\nname='beta'\n").parent
    a_doc = _write(a / "docs" / "local" / "plan_a.md", CONFLICT_MD.format(title="a"))
    b_doc = _write(b / "docs" / "local" / "plan_b.md", CONFLICT_MD.format(title="b"))
    cfg_all = _cfg(root)
    cfg_all.search_paths = [str(root)]
    db_file = tmp_path / "idx.db"
    sync_index(cfg_all, db_path_override=db_file)
    monkeypatch.setenv("DOCSWEEP_INDEX_DB", str(db_file))

    res = fix_conflicts(_cfg(a), prefer="frontmatter")

    assert [Path(item.path).name for item in res.items] == ["plan_a.md"]
    assert _h1(a_doc).startswith("# [完了]")
    assert _h1(b_doc).startswith("# [計画]")
