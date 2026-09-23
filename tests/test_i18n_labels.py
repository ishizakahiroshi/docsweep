"""人向け出力の見出し・項目名・接頭辞が表示言語で出ること（ソースに英語を固定しない）。

合成のプロジェクトに対して主なコマンドを ``--lang en`` と ``--lang ja`` で流し、

- 英語の出力に日本語が混ざらない（文書のデータ由来の文字は合成データを英語にして避ける）
- 日本語の出力では、辞書へ移した見出し・接頭辞が日本語になる

を確かめる。パスは実行環境で日本語を含みうるので、比較の前に取り除く。
"""

from __future__ import annotations

import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

from docsweep import excluded as excluded_module
from docsweep.cli import main
from docsweep.i18n import SUPPORTED_LANGS, t, use_lang

JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """英語の見出しだけで書いた合成プロジェクト（日本語は出力側の文言だけから来る）。"""
    root = tmp_path / "dev"
    project = root / "sample-app"
    (project / ".git").mkdir(parents=True)
    queue = project / "docs" / "local"
    _write(queue / "pending_cache-ideas.md", "# [Pending] cache ideas\n\n## Summary\n\nlater\n")
    _write(queue / "plan_sample-flow.md", "# [Planned] sample flow\n\n## Summary\n\nsteps\n")
    config = _write(tmp_path / "config.yaml", f"roots:\n  - {root.as_posix()}\n")
    monkeypatch.setattr(excluded_module, "EXCLUDED_PATH", tmp_path / "excluded.json")
    # 朝の入口の記録（~/.docsweep/streak.json）を実環境へ書かない
    monkeypatch.setenv("DOCSWEEP_METRICS", "0")
    monkeypatch.delenv("DOCSWEEP_HINTS", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    return {"tmp": tmp_path, "root": root, "project": project, "config": config}


def _run(capsys, argv: list[str]) -> str:
    main(argv)
    captured = capsys.readouterr()
    return captured.out + captured.err


def _strip_paths(text: str, *paths: Path) -> str:
    """環境依存のパス（利用者名等で日本語を含みうる）を比較から外す。"""
    for path in (*paths, Path.home()):
        for form in {str(path), path.as_posix(), str(path).replace("\\", "/")}:
            text = text.replace(form, "<path>")
    return text


def _commands(ws: dict[str, Path]) -> dict[str, list[str]]:
    root = str(ws["root"])
    return {
        "scan": ["scan", "--root", root, "--all"],
        "triage": ["triage", "--root", root, "--show", "owner"],
        "doctor": ["doctor", "--config", str(ws["config"]), "--project-dir", str(ws["project"])],
        "cookbook": ["cookbook"],
        "day open": ["day", "open", "--root", root],
        "day close": ["day", "close", "--root", root],
        "review-week": ["review-week", "--root", root],
        "project list": ["project", "list", "--root", root],
    }


@pytest.mark.parametrize(
    "name", ["scan", "triage", "doctor", "cookbook", "day open", "day close", "review-week", "project list"]
)
def test_english_output_has_no_japanese(workspace, capsys, name: str) -> None:
    argv = _commands(workspace)[name]
    text = _strip_paths(_run(capsys, [*argv, "--lang", "en"]), workspace["tmp"])

    assert text.strip()
    assert not JAPANESE.search(text), text


def test_english_write_flow_has_no_japanese(workspace, capsys) -> None:
    """書き込むコマンド（new / closeout-check / mv / sweep / undo）も、実際に動かした出力を見る。"""
    project, root = workspace["project"], str(workspace["root"])
    queue = project / "docs" / "local"
    (project / ".git").rmdir()
    _git_repo(project)  # mv は Git の追跡状態を確かめられないと動かさない
    _write(queue / "plan_finished-work.md", "# [Done] finished work\n\n## Summary\n\ndone\n")
    in_project = ["--project-dir", str(project), "--config", str(workspace["config"])]
    steps = [
        ["new", "plan", "english-flow", *in_project],
        ["closeout-check", "--path", str(queue / "plan_sample-flow.md"), "--project-dir", str(project)],
        ["mv", str(queue / "pending_cache-ideas.md"), "--to", str(queue / "later"), *in_project],
        ["sweep", "--root", root],
        ["undo", "--root", root],  # 直前の sweep を戻す
    ]
    outputs = {}
    for argv in steps:
        text = _strip_paths(_run(capsys, [*argv, "--lang", "en"]), workspace["tmp"])
        assert text.strip(), argv
        assert not JAPANESE.search(text), (argv, text)
        outputs[argv[0]] = text

    # 何もしない経路の出力だけを見て通っていないことを確かめる
    assert (queue / "plan_english-flow.md").read_text(encoding="utf-8").startswith("---")
    assert "pending_cache-ideas.md" in outputs["mv"]
    assert (queue / "later" / "pending_cache-ideas.md").exists()
    assert "plan_finished-work.md" in outputs["sweep"]
    assert (queue / "plan_finished-work.md").exists(), outputs["undo"]  # undo で戻っている


def test_doctor_table_follows_the_language(workspace, capsys) -> None:
    argv = _commands(workspace)["doctor"]

    en = _run(capsys, [*argv, "--lang", "en"])
    ja = _run(capsys, [*argv, "--lang", "ja"])

    assert "STATUS" in en and "CHECK" in en and "DETAIL" in en
    header = next(line for line in ja.splitlines() if line.startswith("状態"))
    assert "項目" in header and "詳細" in header
    assert "STATUS" not in ja
    assert "スキャン root" in ja
    assert "fix:" not in ja


def test_day_and_review_week_labels_in_japanese(workspace, capsys) -> None:
    commands = _commands(workspace)

    day_open = _run(capsys, [*commands["day open"], "--lang", "ja"])
    day_close = _run(capsys, [*commands["day close"], "--lang", "ja"])
    review = _run(capsys, [*commands["review-week"], "--lang", "ja"])

    assert day_open.startswith("1 日の始まり | ")
    assert "期限切れ:" in day_open and "overdue:" not in day_open
    assert day_close.startswith("1 日の締め | ")
    assert "今日触った:" in day_close
    assert review.startswith("週次レビュー\n")
    assert "  次の一手: " in review and "  next: " not in review


def test_project_list_marks_follow_the_language(workspace, capsys) -> None:
    argv = _commands(workspace)["project list"]

    en = _run(capsys, [*argv, "--lang", "en"])
    ja = _run(capsys, [*argv, "--lang", "ja"])

    assert en.startswith("[ON ] sample-app")
    assert ja.startswith("[有効] sample-app")


def test_project_usage_follows_the_language(capsys) -> None:
    assert main(["project", "--lang", "ja"]) == 2
    assert capsys.readouterr().err.strip() == "使い方: docsweep project list|enable|disable"

    assert main(["project", "--lang", "en"]) == 2
    assert capsys.readouterr().err.strip() == "usage: docsweep project list|enable|disable"


def test_scan_hint_prefix_follows_the_language(workspace, capsys) -> None:
    argv = _commands(workspace)["scan"]

    main([*argv, "--lang", "ja"])
    ja_err = capsys.readouterr().err
    main([*argv, "--lang", "en"])
    en_err = capsys.readouterr().err

    assert ja_err.startswith("ヒント: ")
    assert en_err.startswith("hint: ")


def _git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "docsweep-test"], check=True)
    _write(path / "README.md", "test\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)


def test_workspace_review_labels_follow_the_language(tmp_path: Path, capsys, monkeypatch) -> None:
    workspace_root = tmp_path / "ws"
    repo = workspace_root / "repo"
    repo.mkdir(parents=True)
    _git_repo(repo)
    _write(
        repo / "docs" / "local" / "plan_release-flow.md",
        "---\ntype: plan\nstatus: done\n---\n# [Done] release flow\n\n## Summary\n\nbody\n",
    )
    global_config = _write(tmp_path / "global.yaml", "")
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    argv = [
        "workspace", "migrate-release-tracking",
        "--root", str(workspace_root), "--config", str(global_config), "--review",
    ]

    en = _run(capsys, [*argv, "--lang", "en"])
    ja = _run(capsys, [*argv, "--lang", "ja"])

    assert en.startswith("workspace release tracking migration\n")
    assert "  repositories: 1" in en
    assert not JAPANESE.search(_strip_paths(en, tmp_path)), en
    assert ja.startswith("ワークスペースのリリース追跡の移行\n")
    assert "  リポジトリ: 1 件" in ja
    assert "repositories:" not in ja


# ---- 画面を通らない経路（関数を直接呼ぶ） ----------------------------------------------


def test_doc_hint_prefixes_follow_the_language() -> None:
    from docsweep.doc_links import doc_hint

    with use_lang("ja"):
        ja = doc_hint("config.yaml_parse")
    with use_lang("en"):
        en = doc_hint("config.yaml_parse")

    assert ja is not None and en is not None
    assert ja.startswith("ヒント: ") and "(ヘルプ ID: config.yaml_parse)" in ja
    assert en.startswith("hint: ") and "(help id: config.yaml_parse)" in en
    assert not JAPANESE.search(en)


def test_timeline_heading_follows_the_language() -> None:
    from docsweep.timeline import TimelineResult, render_timeline

    with use_lang("ja"):
        assert render_timeline(TimelineResult(topic="x"), fmt="markdown").startswith("# タイムライン: x")
    with use_lang("en"):
        assert render_timeline(TimelineResult(topic="x"), fmt="markdown").startswith("# timeline: x")


def test_release_close_categories_are_translated() -> None:
    categories = (
        "movable", "moved", "watching", "incomplete", "target_mismatch", "target_unset",
        "never_archive", "tag_missing", "disabled", "released_in_conflict", "collision", "failed",
    )
    with use_lang("ja"):
        labels = [t(f"cli_release.category.{key}") for key in categories]
    assert all(JAPANESE.search(label) for label in labels)


def test_lang_choices_come_from_the_locale_folders(capsys) -> None:
    """--lang の選択肢と init の質問文は locales/ のフォルダから作る（コードに一覧を持たない）。"""
    from docsweep.cli.parser import build_parser

    parser = build_parser()
    choices = {
        tuple(action.choices)
        for sub in parser._subparsers._group_actions  # type: ignore[union-attr]
        for sub_parser in sub.choices.values()
        for action in sub_parser._actions
        if "--lang" in action.option_strings
    }
    assert choices == {SUPPORTED_LANGS}

    with use_lang("en"):
        assert t("init_cmd.prompt_lang", langs="/".join(SUPPORTED_LANGS)) == (
            "Language " + "/".join(SUPPORTED_LANGS)
        )
