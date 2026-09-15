"""``docsweep new`` の --project-dir 省略時の自動検出。

cwd がリポジトリ内のサブディレクトリ（例: web/）のとき、--project-dir を渡さずに
呼ぶと従来は cwd をそのままプロジェクトルート扱いしてしまい、docs/local/ ではなく
サブディレクトリ直下に md が生成されていた（実運用で観測: many-ai-cli の web/ 配下に
誤生成）。.git 等の project marker を上へ遡って検出するよう cli.cmd_new を修正した
ことの回帰防止テスト。
"""

from __future__ import annotations

from pathlib import Path
import sys

from docsweep.cli import main


class _TTY:
    def isatty(self) -> bool:
        return True


def _isolate_global_config(tmp_path: Path, monkeypatch) -> None:
    """User-level opt-ins must not make CLI tests write to the real home ledger."""
    monkeypatch.setattr("docsweep.config.GLOBAL_CONFIG_PATH", tmp_path / "empty-config.yaml")


def test_new_without_project_dir_detects_git_root_from_subdir(tmp_path: Path, monkeypatch):
    _isolate_global_config(tmp_path, monkeypatch)
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
    _isolate_global_config(tmp_path, monkeypatch)
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

    # templates_gen._placement_dir は docs/local/ が無いプロジェクトでも自動的に
    # docs/local/ を新設して配置する（ルート直下に md をばら撒かない・家規約）。
    generated = list((other / "docs" / "local").glob("bugfix_explicit-topic_*.md"))
    assert len(generated) == 1
    # フォールバック（project_dir 直下）に落ちていないこと。
    assert not list(other.glob("bugfix_explicit-topic_*.md"))


def test_delegate_is_ignored_for_bugfix_with_one_warning(tmp_path: Path, monkeypatch, capsys):
    _isolate_global_config(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()

    rc = main([
        "new", "bugfix", "delegate-warning", "--delegate", "--no-due",
        "--project-dir", str(project),
    ])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err.count("warning: --delegate") == 1
    generated = next((project / "docs" / "local").glob("bugfix_delegate-warning_*.md"))
    body = generated.read_text(encoding="utf-8")
    assert "docsweep_delegation: external" not in body
    assert "## C 詳細" not in body


def test_delegate_cli_generates_delegated_plan(tmp_path: Path, monkeypatch, capsys):
    _isolate_global_config(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()

    rc = main([
        "new", "plan", "delegate-cli", "--delegate", "--no-due",
        "--project-dir", str(project),
    ])

    assert rc == 0
    capsys.readouterr()
    generated = project / "docs" / "local" / "plan_delegate-cli.md"
    body = generated.read_text(encoding="utf-8")
    assert "docsweep_delegation: external" in body
    assert "## C 詳細" in body


def test_new_first_run_can_enable_release_tracking(tmp_path: Path, monkeypatch):
    global_config = tmp_path / "global.yaml"
    global_config.write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    answers = iter(["y", "v1.2.x", "", ""])
    monkeypatch.setattr(sys, "stdin", _TTY())
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    rc = main(
        [
            "new",
            "plan",
            "first-release",
            "--project-dir",
            str(project),
            "--config",
            str(global_config),
            "--no-due",
        ]
    )

    assert rc == 0
    config = (project / ".docsweep.yaml").read_text(encoding="utf-8")
    body = (project / "docs/local/plan_first-release.md").read_text(encoding="utf-8")
    assert "mode: enabled" in config
    assert "default_target: v1.2.x" in config
    assert "archive_group_by: minor" in config
    assert "archive_dir: docs/local/archive" in config
    assert "target_release: v1.2.x" in body


def test_new_first_run_skip_is_persisted(tmp_path: Path, monkeypatch):
    global_config = tmp_path / "global.yaml"
    global_config.write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    monkeypatch.setattr(sys, "stdin", _TTY())
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    rc = main(
        [
            "new",
            "plan",
            "skip-release",
            "--project-dir",
            str(project),
            "--config",
            str(global_config),
            "--no-due",
        ]
    )

    assert rc == 0
    config = (project / ".docsweep.yaml").read_text(encoding="utf-8")
    body = (project / "docs/local/plan_skip-release.md").read_text(encoding="utf-8")
    assert "mode: disabled" in config
    assert "archive_dir:" not in config
    assert "target_release:" not in body


def test_new_first_run_keeps_explicit_target_separate_from_future_default(
    tmp_path: Path, monkeypatch
):
    global_config = tmp_path / "global.yaml"
    global_config.write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    answers = iter(["y", "v1.2.x", "minor", ""])
    monkeypatch.setattr(sys, "stdin", _TTY())
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    rc = main(
        [
            "new",
            "plan",
            "exact-release",
            "--project-dir",
            str(project),
            "--config",
            str(global_config),
            "--target-release",
            "v1.2.3",
            "--no-due",
        ]
    )

    assert rc == 0
    config = (project / ".docsweep.yaml").read_text(encoding="utf-8")
    body = (project / "docs/local/plan_exact-release.md").read_text(encoding="utf-8")
    assert "default_target: v1.2.x" in config
    assert "target_release: v1.2.3" in body


def test_new_first_run_cancel_writes_nothing(tmp_path: Path, monkeypatch):
    global_config = tmp_path / "global.yaml"
    global_config.write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    monkeypatch.setattr(sys, "stdin", _TTY())
    monkeypatch.setattr("builtins.input", lambda _prompt: "q")

    rc = main(
        [
            "new",
            "plan",
            "cancel-release",
            "--project-dir",
            str(project),
            "--config",
            str(global_config),
            "--no-due",
        ]
    )

    assert rc == 2
    assert not (project / ".docsweep.yaml").exists()
    assert not (project / "docs/local/plan_cancel-release.md").exists()
