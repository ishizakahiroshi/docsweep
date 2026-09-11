"""owner の既定解決（`docsweep new` が frontmatter へ書く値）の単体テスト。

背景: owner を空で生成していた頃、値を決めるのが人か AI の判断になり、同一人物に
対して `ishizakahiroshi` / `ishizaka` / 日本語氏名 / OS ログイン名の 4 表記が並存した。
しかも表記はリポジトリ単位で固まっており、近くのファイルを写して伝播していた。
生成時に埋めて、判断の余地そのものを無くすのがこのテストの守備範囲。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from docsweep import config as config_module
from docsweep.cli.commands.excluded import cmd_config
from docsweep.config import get_user_setting, load_config, set_user_setting
from docsweep.services import frontmatter as frontmatter_module
from docsweep.services.frontmatter import current_owner, default_doc_owner
from docsweep.templates_gen import new_doc


def _use_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """グローバル設定をテスト専用パスへ向けて、そのパスを返す。"""
    cfg = tmp_path / "docsweep-config.yaml"
    monkeypatch.setattr(config_module, "GLOBAL_CONFIG_PATH", cfg)
    return cfg


def test_default_doc_owner_prefers_configured_user_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg = _use_config(tmp_path, monkeypatch)
    set_user_setting("user.name", "octocat", global_path=cfg)
    monkeypatch.setattr(frontmatter_module, "_git_user_name", lambda cwd=None: "Git Name")

    assert default_doc_owner() == "octocat"


def test_default_doc_owner_falls_back_to_git_user_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _use_config(tmp_path, monkeypatch)  # 未作成 = user.name 未設定
    monkeypatch.setattr(frontmatter_module, "_git_user_name", lambda cwd=None: "Git Name")

    assert default_doc_owner() == "Git Name"


def test_default_doc_owner_never_falls_back_to_os_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """OS ログイン名は「端末の都合」であって人の名前ではないので owner にしない。

    実測で `ishiz` のような端末アカウント名が owner として残っていた。claim 用の
    ``current_owner`` は従来どおり OS ログインまで落ちるが、生成時の既定は空で止める。
    """
    _use_config(tmp_path, monkeypatch)
    monkeypatch.setattr(frontmatter_module, "_git_user_name", lambda cwd=None: None)
    monkeypatch.setattr(frontmatter_module, "_os_login", lambda: "term-login")

    assert default_doc_owner() == ""
    assert current_owner() == "term-login"  # claim 側の解決順は変えていない


def test_default_doc_owner_drops_names_that_need_quoting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`:` を含む表示名は bare scalar に書けない。owner を壊すより空にする。"""
    _use_config(tmp_path, monkeypatch)
    monkeypatch.setattr(frontmatter_module, "_git_user_name", lambda cwd=None: "a: b")

    assert default_doc_owner() == ""


def test_new_doc_fills_resolved_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _use_config(tmp_path, monkeypatch)
    monkeypatch.setattr(frontmatter_module, "_git_user_name", lambda cwd=None: "octocat")
    proj = tmp_path / "proj"
    proj.mkdir()

    for doc_type in ("plan", "bugfix", "pending"):
        doc = new_doc(doc_type, f"topic-{doc_type}", project_dir=proj)
        assert "owner: octocat\n" in doc.path.read_text(encoding="utf-8")


def test_project_config_user_name_overrides_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """複数人の登録簿を持つリポジトリは、グローバルと違う owner を使えなければならない。

    グローバル 1 個の値を全リポジトリへ押し付けると、そこだけ生成のたびにずれ続ける。
    """
    cfg = _use_config(tmp_path, monkeypatch)
    set_user_setting("user.name", "octocat", global_path=cfg)
    monkeypatch.setattr(frontmatter_module, "_git_user_name", lambda cwd=None: "Git Name")

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".docsweep.yaml").write_text("user:\n  name: teamkey\n", encoding="utf-8")
    project_config = load_config(project_dir=proj, global_path=cfg)
    assert project_config.user_name == "teamkey"
    assert default_doc_owner(config=project_config) == "teamkey"

    doc = new_doc("plan", "project-owner", project_dir=proj, config=project_config)
    assert "owner: teamkey\n" in doc.path.read_text(encoding="utf-8")


def test_capture_drafts_use_the_same_owner_as_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """md の生まれ方（new / capture）で owner が変わらないこと。"""
    from docsweep.capture.heuristics import extract_drafts_heuristic

    cfg = _use_config(tmp_path, monkeypatch)
    set_user_setting("user.name", "octocat", global_path=cfg)

    drafts = extract_drafts_heuristic(
        "バグ: 保存ボタンを押すと落ちる\n", owner="teamkey"
    )
    assert drafts, "ヒューリスティック抽出が 1 件も返さなかった"
    assert "owner: teamkey\n" in drafts[0].body


def test_new_doc_owner_can_be_forced_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``owner=""`` は「解決しない」の明示。行は残す（OKF のフィールド一式は欠かさない）。"""
    _use_config(tmp_path, monkeypatch)
    monkeypatch.setattr(frontmatter_module, "_git_user_name", lambda cwd=None: "octocat")
    proj = tmp_path / "proj"
    proj.mkdir()

    doc = new_doc("plan", "no-owner", project_dir=proj, owner="")
    body = doc.path.read_text(encoding="utf-8")
    assert "owner: \n" in body
    assert "octocat" not in body


def test_new_doc_owner_blank_when_unresolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """解決できない環境では従来どおり空で出す（生成は owner を理由に失敗しない）。"""
    _use_config(tmp_path, monkeypatch)
    monkeypatch.setattr(frontmatter_module, "_git_user_name", lambda cwd=None: None)
    proj = tmp_path / "proj"
    proj.mkdir()

    doc = new_doc("plan", "unresolvable", project_dir=proj)
    assert "owner: \n" in doc.path.read_text(encoding="utf-8")


def test_guidance_block_shows_the_value_to_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """導線は「空で書け」ではなく実効値を見せる（手書き経路でも判断を残さない）。"""
    from docsweep.inject.api import generate_okf_block

    _use_config(tmp_path, monkeypatch)
    monkeypatch.setattr(frontmatter_module, "_git_user_name", lambda cwd=None: "octocat")

    for lang in ("ja", "en"):
        block = generate_okf_block(lang)
        assert "`owner: octocat`" in block


def test_config_from_github_freezes_the_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """gh のアカウント名を 1 回だけ解決して設定へ凍結する（生成時には gh を呼ばない）。"""
    cfg = _use_config(tmp_path, monkeypatch)
    monkeypatch.setattr(frontmatter_module, "github_login", lambda timeout=5.0: "octocat")

    args = argparse.Namespace(
        key="user.name", value=None, get_key=None, unset_key=None,
        list_all=False, from_github=True, json=False,
    )
    assert cmd_config(args) == 0
    assert get_user_setting("user.name", global_path=cfg) == "octocat"


def test_config_from_github_reports_failure_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """gh 未導入 / 未ログイン / オフラインでは書かずに失敗させる（誤った値を凍結しない）。"""
    cfg = _use_config(tmp_path, monkeypatch)
    monkeypatch.setattr(frontmatter_module, "github_login", lambda timeout=5.0: None)

    args = argparse.Namespace(
        key="user.name", value=None, get_key=None, unset_key=None,
        list_all=False, from_github=True, json=False,
    )
    assert cmd_config(args) == 1
    assert not cfg.exists()


def test_config_from_github_rejects_other_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg = _use_config(tmp_path, monkeypatch)
    monkeypatch.setattr(frontmatter_module, "github_login", lambda timeout=5.0: "octocat")

    args = argparse.Namespace(
        key="user.email", value=None, get_key=None, unset_key=None,
        list_all=False, from_github=True, json=False,
    )
    assert cmd_config(args) == 2
    assert not cfg.exists()
