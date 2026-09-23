"""表示言語の決め方と文言の辞書（docsweep.i18n）。"""

from __future__ import annotations

import string
from pathlib import Path
from types import SimpleNamespace

import pytest

from docsweep import i18n
from docsweep.cli import main

# conftest は OS の表示言語を ja に固定する。本物の判定はここで先に持っておく。
REAL_DETECT_OS_LANG = i18n.detect_os_lang


def _placeholders(template: str) -> set[str]:
    return {name for _text, name, _spec, _conv in string.Formatter().parse(template) if name}


# ---- 決め方の順序 ----------------------------------------------------------------


def test_flag_wins_over_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SimpleNamespace(lang="ja", lang_explicit=True)

    assert i18n.resolve_lang("en", config=config, environ={"DOCSWEEP_LANG": "ja"}) == "en"


def test_env_wins_over_config_and_os() -> None:
    config = SimpleNamespace(lang="ja", lang_explicit=True)

    assert i18n.resolve_lang(config=config, environ={"DOCSWEEP_LANG": "en"}) == "en"


def test_explicit_config_wins_over_os() -> None:
    config = SimpleNamespace(lang="en", lang_explicit=True)

    assert i18n.resolve_lang(config=config, environ={}) == "en"


def test_config_default_is_ignored_when_not_explicit() -> None:
    """``Config.lang`` の既定値 ja は、書いていない限り表示言語を決めない。"""
    config = SimpleNamespace(lang="ja", lang_explicit=False)

    assert i18n.resolve_lang(config=config, environ={}) == "ja"  # conftest が OS を ja に固定


def test_falls_back_to_english_when_nothing_decides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n, "detect_os_lang", lambda **_kwargs: None)

    assert i18n.resolve_lang(environ={}) == "en"


def test_unknown_values_are_skipped() -> None:
    assert i18n.resolve_lang("fr", environ={"DOCSWEEP_LANG": "de"}) == "ja"  # OS（固定）へ落ちる


# ---- OS の表示言語 ----------------------------------------------------------------


def test_windows_uses_the_ui_language_not_lang(monkeypatch: pytest.MonkeyPatch) -> None:
    """Git Bash の LANG=en_US.UTF-8 で、日本語の Windows の利用者が英語にならない。"""
    monkeypatch.setattr(i18n, "_windows_ui_lang", lambda: "ja")

    assert REAL_DETECT_OS_LANG(environ={"LANG": "en_US.UTF-8"}, platform="win32") == "ja"


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"LANG": "ja_JP.UTF-8"}, "ja"),
        ({"LANG": "en_US.UTF-8"}, "en"),
        ({"LANG": "fr_FR.UTF-8"}, "en"),
        ({"LANG": "C.UTF-8"}, "en"),
        ({"LC_ALL": "ja_JP.UTF-8", "LANG": "en_US.UTF-8"}, "ja"),
        ({"LC_MESSAGES": "en_US.UTF-8", "LANG": "ja_JP.UTF-8"}, "en"),
        ({}, None),
    ],
)
def test_posix_locale_order(environ: dict[str, str], expected: str | None) -> None:
    assert REAL_DETECT_OS_LANG(environ=environ, platform="linux") == expected


# ---- t() -----------------------------------------------------------------------


def test_t_returns_the_current_language() -> None:
    with i18n.use_lang("en"):
        assert i18n.t("target.not_found_in_scan") == "Target not found (outside the scan roots?)"
    with i18n.use_lang("ja"):
        assert i18n.t("target.not_found_in_scan") == "対象が見つかりません（スキャン範囲外?）"


def test_t_fills_placeholders() -> None:
    text = i18n.t("work_queue.privacy_downgraded", lang="en", error="X")

    assert text.startswith("X (reported as a warning")


def test_t_accepts_a_placeholder_named_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(i18n.MESSAGES, "test.key", {"ja": "キー {key}", "en": "key {key}"})

    assert i18n.t("test.key", lang="en", key="roots") == "key roots"


def test_t_rejects_unknown_keys() -> None:
    with pytest.raises(KeyError):
        i18n.t("no.such.key")


def test_use_lang_restores_the_previous_language() -> None:
    with i18n.use_lang("en"):
        with i18n.use_lang("ja"):
            assert i18n.current_lang() == "ja"
        assert i18n.current_lang() == "en"


# ---- 辞書 ------------------------------------------------------------------------


def _items(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else value


def test_every_message_has_all_languages_with_the_same_placeholders() -> None:
    problems: list[str] = []
    for key, entry in i18n.MESSAGES.items():
        if set(entry) != set(i18n.SUPPORTED_LANGS):
            problems.append(f"{key}: languages {sorted(entry)}")
            continue
        kinds = {type(value).__name__ for value in entry.values()}
        if len(kinds) != 1:
            problems.append(f"{key}: string in one language, list in another")
            continue
        if not all(item.strip() for value in entry.values() for item in _items(value)) or not all(
            _items(value) for value in entry.values()
        ):
            problems.append(f"{key}: empty text")
            continue
        if isinstance(entry[i18n.DEFAULT_LANG], list):
            continue  # 判定用のキーワード等。言語ごとに数が違ってよい
        expected = _placeholders(entry[i18n.DEFAULT_LANG])
        if any(_placeholders(value) != expected for value in entry.values()):
            problems.append(f"{key}: placeholders differ")
        if "lang" in expected:
            # t() の lang= は言語の指定なので、文言の値として渡せない
            problems.append(f"{key}: placeholder named lang")
    assert problems == []


def test_english_messages_contain_no_japanese() -> None:
    import re

    japanese = re.compile(r"[぀-ヿ一-鿿]")
    leaked = [
        key
        for key, entry in i18n.MESSAGES.items()
        if any(japanese.search(item) for item in _items(entry["en"]))
    ]
    assert leaked == []


# 日本語の値に日本語が 1 文字も無くてよいキー。コマンド名・フォルダ名・frontmatter の項目名・
# パス・書式など、訳すと利用者が入力や検索に使えなくなる識別子だけを載せる。
_JA_IDENTIFIER_KEYS = {
    "fix_conflict.detail_dry_run_h1_from_status",  # dry-run は CLI のフラグ名、H1 / frontmatter は文書の部位名
    "cli_inject.dry_run_tag",  # CLI のフラグ名
    "capture.marker_match",  # 設定値（substring）
    "doc_links.doc.config.yaml_parse",  # リポジトリ内のパス
    "doc_links.doc.console.encoding",
    "doc_links.doc.naming.work_md",
    "doc_links.doc.closeout.parent_only",
    "doc_links.doc.states.label",
    "ui.board_page_title",  # 製品名
    "ui.settings_project_off",  # トグルの ON / OFF
    "ui.bulk_archive_btn",  # archive/ フォルダ名（日本語の画面でも「archive へ」と書く）
    "ui.okf_heading",  # frontmatter（OKF）
    "ui.claim_btn",  # docsweep claim コマンド
    "ui.unclaim_btn",  # docsweep claim --unclaim
    "ui.settings_inject_btn",  # docsweep inject コマンド
    "ui.settings_eject_btn",  # docsweep eject
    "js.mtime_label",  # mtime は日本語の画面でも保存結果に出す項目名
    "js.date_prompt",  # 入力書式 YYYY-MM-DD
}


def test_japanese_messages_are_japanese_unless_they_are_identifiers() -> None:
    """ja の値へ英語をそのまま写すと、日本語の画面に英語が混ざる（例: 「↶ Undo」）。"""
    import re

    japanese = re.compile(r"[぀-ヿ一-鿿]")
    word = re.compile(r"[A-Za-z]{3,}")
    untranslated = sorted(
        key
        for key, entry in i18n.MESSAGES.items()
        if isinstance(entry["ja"], str)  # 判定用のキーワード一覧は英語の語も含んでよい
        and not japanese.search(entry["ja"])
        and word.search(re.sub(r"\{[^{}]*\}", "", entry["ja"]))  # {label} 等の差し込み名は除く
    )
    assert [key for key in untranslated if key not in _JA_IDENTIFIER_KEYS] == []
    assert sorted(_JA_IDENTIFIER_KEYS - set(untranslated)) == []  # 訳したら一覧からも外す


def test_argparse_messages_keep_their_percent_placeholders() -> None:
    """argparse の文言は % 形式で埋めるので、訳でも同じ名前の % プレースホルダを残す。"""
    import re

    percent = re.compile(r"%(?:\((\w+)\))?[sr]")
    for key, entry in i18n.MESSAGES.items():
        if not key.startswith("argparse."):
            continue
        msgid = key[len("argparse."):]
        assert entry["en"] == msgid, key
        for value in entry.values():
            assert sorted(percent.findall(value)) == sorted(percent.findall(msgid)), key


@pytest.mark.parametrize(
    ("lang", "usage", "options", "help_line"),
    [
        ("ja", "使い方:", "オプション:", "このヘルプを表示して終了する"),
        ("en", "usage:", "options:", "show this help message and exit"),
    ],
)
def test_argparse_help_follows_the_display_language(
    capsys, lang: str, usage: str, options: str, help_line: str
) -> None:
    import re

    with pytest.raises(SystemExit):
        main(["mv", "--help", "--lang", lang])
    # Python 3.14 の argparse は色の制御文字を付けることがあるので外して比べる
    out = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)

    assert out.startswith(usage)
    assert options in out
    assert help_line in out


def test_argparse_errors_follow_the_display_language(capsys) -> None:
    import re

    def err() -> str:
        return re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().err)

    with pytest.raises(SystemExit):
        main(["sweep", "--bogus", "--lang", "ja"])
    assert "エラー: 認識できない引数: --bogus" in err()

    with pytest.raises(SystemExit):
        main(["sweep", "--bogus", "--lang", "en"])
    assert "error: unrecognized arguments: --bogus" in err()


def test_argparse_is_restored_after_the_cli_returns(tmp_path: Path) -> None:
    """ライブラリとして import したアプリの argparse を巻き込まない。"""
    import argparse
    import gettext

    main(_apply_missing(tmp_path))

    assert argparse._ is gettext.gettext  # type: ignore[attr-defined]
    assert argparse.ngettext is gettext.ngettext


def test_messages_live_in_json_not_in_the_source() -> None:
    """文言と用語は locales/<言語>/*.json に置く（i18n パッケージに Python の辞書を持たない）。"""
    package = Path(i18n.__file__).parent
    assert sorted(p.name for p in package.glob("*.py")) == ["__init__.py"]
    assert set(i18n.SUPPORTED_LANGS) == {
        p.name for p in (package / "locales").iterdir() if p.is_dir()
    }
    for lang in i18n.SUPPORTED_LANGS:
        names = sorted(p.name for p in (package / "locales" / lang).glob("*.json"))
        assert names == sorted(p.name for p in (package / "locales" / "en").glob("*.json")), lang


def test_a_new_language_folder_is_picked_up(tmp_path: Path) -> None:
    """言語はフォルダを足すだけで増やせる（コードに言語の一覧を持たない）。"""
    import json

    for lang, text in (("en", "Hello"), ("fr", "Bonjour")):
        folder = tmp_path / lang
        folder.mkdir()
        (folder / "messages.json").write_text(json.dumps({"greet": text}), encoding="utf-8")

    assert i18n.load_locales(tmp_path) == {"en": {"greet": "Hello"}, "fr": {"greet": "Bonjour"}}


def test_duplicate_keys_across_json_files_are_rejected(tmp_path: Path) -> None:
    import json

    folder = tmp_path / "en"
    folder.mkdir()
    for name in ("a.json", "b.json"):
        (folder / name).write_text(json.dumps({"same.key": "x"}), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate message key: same.key"):
        i18n.load_locales(tmp_path)


# ---- CLI ------------------------------------------------------------------------


def _apply_missing(tmp_path: Path, *extra: str) -> list[str]:
    return ["apply", "--root", str(tmp_path), "--path", str(tmp_path / "missing.md"), "--action", "keep", *extra]


def test_cli_lang_en_prints_english(tmp_path: Path, capsys) -> None:
    code = main(_apply_missing(tmp_path, "--lang", "en"))

    assert code == 2
    assert "Target not found (outside the scan roots?)" in capsys.readouterr().err


def test_cli_env_var_prints_english(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCSWEEP_LANG", "en")

    main(_apply_missing(tmp_path))

    assert "Target not found" in capsys.readouterr().err


def test_cli_lang_ja_beats_the_env_var(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCSWEEP_LANG", "en")

    main(_apply_missing(tmp_path, "--lang", "ja"))

    assert "対象が見つかりません" in capsys.readouterr().err


def test_cli_reads_lang_from_project_config(tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / ".docsweep.yaml").write_text("lang: en\n", encoding="utf-8")
    monkeypatch.chdir(project)

    main(_apply_missing(tmp_path))

    assert "Target not found" in capsys.readouterr().err


def test_cli_does_not_leak_the_language_to_the_next_call(tmp_path: Path, capsys) -> None:
    main(_apply_missing(tmp_path, "--lang", "en"))
    capsys.readouterr()

    main(_apply_missing(tmp_path))

    assert "対象が見つかりません" in capsys.readouterr().err


# ---- 文言に依存しない判定 --------------------------------------------------------------


def test_privacy_downgrade_does_not_depend_on_the_message_language(tmp_path: Path) -> None:
    """private queue の Git 状態のエラーは、英語でも互換 fallback で警告へ落ちる。"""
    import subprocess

    from docsweep.config import Config
    from docsweep.work_queue import ensure_write_allowed

    project = tmp_path / "repo"
    queue = project / "docs" / "local"
    queue.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    config = Config(roots=[tmp_path], loaded_from_config=True)

    with i18n.use_lang("en"):
        result = ensure_write_allowed(config=config, project_dir=project, target_dir=queue)

    assert any("is not ignored by Git" in warning for warning in result.warnings)
    assert result.errors == ["The private work queue is not ignored by Git"]
