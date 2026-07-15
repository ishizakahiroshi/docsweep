"""``docsweep new`` の --project-dir 省略時の自動検出。

cwd がリポジトリ内のサブディレクトリ（例: web/）のとき、--project-dir を渡さずに
呼ぶと従来は cwd をそのままプロジェクトルート扱いしてしまい、docs/local/ ではなく
サブディレクトリ直下に md が生成されていた（実運用で観測: many-ai-cli の web/ 配下に
誤生成）。.git 等の project marker を上へ遡って検出するよう cli.cmd_new を修正した
ことの回帰防止テスト。
"""

from __future__ import annotations

from pathlib import Path

from docsweep.cli import main


def test_new_without_project_dir_detects_git_root_from_subdir(tmp_path: Path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / "docs" / "local").mkdir(parents=True)
    subdir = proj / "web"
    subdir.mkdir()

    monkeypatch.chdir(subdir)
    rc = main(["new", "bugfix", "some-topic", "--no-due"])
    assert rc == 0

    generated = list((proj / "docs" / "local").glob("bugfix_some-topic_*.md"))
    assert len(generated) == 1
    # サブディレクトリ側には作られていないこと。
    assert not list(subdir.glob("bugfix_some-topic_*.md"))


def test_new_with_explicit_project_dir_still_wins(tmp_path: Path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()
    other = tmp_path / "other"
    other.mkdir()
    subdir = proj / "web"
    subdir.mkdir()

    monkeypatch.chdir(subdir)
    rc = main(["new", "bugfix", "explicit-topic", "--no-due", "--project-dir", str(other)])
    assert rc == 0

    generated = list(other.glob("bugfix_explicit-topic_*.md"))
    assert len(generated) == 1
