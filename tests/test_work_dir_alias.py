"""work_dir を junction / symlink でリポジトリ外へ逃がした構成の project 帰属テスト。

2026-08-10 に実環境で発生した症状の回帰テスト。``docs/local`` を junction 化すると
``Path.resolve()`` がリポジトリの外へ抜け、実体側から上へ辿っても project marker が
見つからず、``detect_project_root`` の fallback が「スキャンルート直下の先頭セグメント」を
project にしていた。結果、``docsweep brief`` が **エラーも警告も出さずに 0 件** を返した
（実際には 65 件の未完 md があった）。同じ配置を採った 20 リポジトリすべてが同じ状態だった。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep.config import load_config
from docsweep.scan import (
    build_work_dir_aliases,
    clear_work_dir_alias_cache,
    detect_project_for_path,
    scan_root,
)

PLAN_BODY = """---
type: plan
status: draft
docsweep_state: planned
---

# [計画] ダミー計画

## 概要

テスト用。
"""


@pytest.fixture(autouse=True)
def _clear_alias_cache():
    clear_work_dir_alias_cache()
    yield
    clear_work_dir_alias_cache()


def _make_repo(root: Path, name: str) -> Path:
    repo = root / name
    (repo / ".git").mkdir(parents=True)
    return repo


def _link_dir(link: Path, target: Path) -> bool:
    """``link`` を ``target`` へのディレクトリリンクにする。作れなければ False。

    Windows でシンボリックリンクを作るには開発者モードか管理者権限が要るため、
    権限が無い環境ではテストごと skip する（CI・他 OS では通る）。
    """
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return True


def _cfg(root: Path, tmp_path: Path):
    return load_config(explicit_roots=[str(root)], global_path=tmp_path / "no-such-global.yaml")


def test_linked_work_dir_is_attributed_to_declaring_project(tmp_path: Path) -> None:
    """work_dir の実体がリポ外にあっても、宣言元リポジトリの持ち物として扱う。"""
    root = tmp_path / "dev"
    repo = _make_repo(root, "myrepo")
    central = root / "central" / "myrepo" / "local"
    central.mkdir(parents=True)
    (central / "plan_dummy.md").write_text(PLAN_BODY, encoding="utf-8")

    if not _link_dir(repo / "docs" / "local", central):
        pytest.skip("ディレクトリリンクを作成できない環境")

    cfg = _cfg(root, tmp_path)

    # 実体パスから引いても宣言元リポジトリへ戻る
    got = detect_project_for_path(central / "plan_dummy.md", cfg)
    assert got is not None
    assert got.name == "myrepo"

    # 走査結果の project も同じ
    docs = scan_root(root, cfg)
    projects = {d.record.project for d in docs}
    assert projects == {"myrepo"}, projects


def test_no_alias_when_work_dir_is_inside_project(tmp_path: Path) -> None:
    """通常構成（docs/local が実体）では alias を作らない。"""
    root = tmp_path / "dev"
    repo = _make_repo(root, "plainrepo")
    (repo / "docs" / "local").mkdir(parents=True)
    (repo / "docs" / "local" / "plan_dummy.md").write_text(PLAN_BODY, encoding="utf-8")

    cfg = _cfg(root, tmp_path)
    assert build_work_dir_aliases(root, cfg) == {}

    docs = scan_root(root, cfg)
    assert {d.record.project for d in docs} == {"plainrepo"}


def test_conflicting_alias_is_dropped_instead_of_picking_one(tmp_path: Path) -> None:
    """同じ実体を 2 リポジトリが work_dir に宣言したら、黙って片方を採らない。

    どちらが正しいか機械には決められない。誤った project へ静かに入れるくらいなら
    alias を作らず、既存の marker 判定 / fallback に委ねる。
    """
    root = tmp_path / "dev"
    repo_a = _make_repo(root, "repo-a")
    repo_b = _make_repo(root, "repo-b")
    central = root / "central" / "shared" / "local"
    central.mkdir(parents=True)

    if not _link_dir(repo_a / "docs" / "local", central):
        pytest.skip("ディレクトリリンクを作成できない環境")
    if not _link_dir(repo_b / "docs" / "local", central):
        pytest.skip("ディレクトリリンクを作成できない環境")

    cfg = _cfg(root, tmp_path)
    aliases = build_work_dir_aliases(root, cfg)
    assert central.resolve() not in aliases


def test_explicit_work_dir_setting_is_honoured(tmp_path: Path) -> None:
    """`.docsweep.yaml` で work_dir を変えた場合も、その実体を引き戻す。"""
    root = tmp_path / "dev"
    repo = _make_repo(root, "customrepo")
    (repo / ".docsweep.yaml").write_text("work_dir: docs/ai\n", encoding="utf-8")
    central = root / "central" / "customrepo" / "ai"
    central.mkdir(parents=True)
    (central / "plan_dummy.md").write_text(PLAN_BODY, encoding="utf-8")

    if not _link_dir(repo / "docs" / "ai", central):
        pytest.skip("ディレクトリリンクを作成できない環境")

    cfg = _cfg(root, tmp_path)
    got = detect_project_for_path(central / "plan_dummy.md", cfg)
    assert got is not None
    assert got.name == "customrepo"
