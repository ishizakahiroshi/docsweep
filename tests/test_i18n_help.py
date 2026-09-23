"""CLI の --help の表示言語（docsweep/cli/parser.py と docsweep/i18n/catalog_help.py）。"""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterator

import pytest

from docsweep.cli import main
from docsweep.cli.parser import build_parser
from docsweep.i18n import use_lang

JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


def _parsers(parser: argparse.ArgumentParser, name: str = "docsweep") -> Iterator[tuple[str, argparse.ArgumentParser]]:
    """トップレベルと、subparsers action の choices にある全サブコマンド（入れ子も）を返す。"""
    yield name, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub_name, sub_parser in action.choices.items():
                yield from _parsers(sub_parser, f"{name} {sub_name}")


def _subcommands(parser: argparse.ArgumentParser) -> list[tuple[str, argparse.ArgumentParser]]:
    return [(name, p) for name, p in _parsers(parser) if name != "docsweep"]


def test_every_subcommand_is_found() -> None:
    names = [name for name, _p in _subcommands(build_parser())]

    assert len(names) >= 60
    assert {"docsweep mv", "docsweep sweep", "docsweep release close"} <= set(names)


def test_english_help_has_no_japanese() -> None:
    with use_lang("en"):
        parsers = list(_parsers(build_parser()))
        helps = {name: p.format_help() for name, p in parsers}

    leaked = {
        name: [line for line in text.splitlines() if JAPANESE.search(line)]
        for name, text in helps.items()
        if JAPANESE.search(text)
    }
    assert leaked == {}


def test_japanese_help_builds_for_every_subcommand() -> None:
    with use_lang("ja"):
        parser = build_parser()
        helps = {name: p.format_help() for name, p in _parsers(parser)}

    assert all(text.strip() for text in helps.values())
    assert "AI 作業ドキュメントの横断スキャン・判定・archive 移送" in helps["docsweep"]
    assert "移送内容を出力するだけ" in helps["docsweep sweep"]


def test_every_subcommand_accepts_lang() -> None:
    """表示言語は main() が argv から先に読む。argparse が --lang で落ちないこと。"""
    missing = [
        name
        for name, p in _subcommands(build_parser())
        if "--lang" not in p._option_string_actions
    ]

    assert missing == []


def test_lang_is_accepted_after_a_subcommand_without_scope_args() -> None:
    args = build_parser().parse_args(["mv", "plan_a.md", "--to", "docs/local/a", "--lang", "en"])

    assert args.lang == "en"


def test_nested_subcommand_keeps_lang_given_to_the_parent() -> None:
    parser = build_parser()

    assert parser.parse_args(["release", "--lang", "en", "close", "v1.0.0"]).lang == "en"
    assert parser.parse_args(["release", "close", "v1.0.0", "--lang", "ja"]).lang == "ja"
    assert getattr(parser.parse_args(["release", "close", "v1.0.0"]), "lang", None) is None


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["mv", "--help", "--lang", "en"], "Move documents within the work queue"),
        (["sweep", "--help", "--lang", "en"], "Only print what would be moved"),
        (["--help", "--lang", "en"], "Scan, triage, and archive AI work documents across projects"),
        # 先頭のオプションは main() が scan へ回す（scan の help になる）。
        (["--lang", "en", "--help"], "Roots to scan once (no config needed)"),
    ],
)
def test_main_help_is_english_with_lang_en(argv: list[str], expected: str, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert expected in out
    assert not JAPANESE.search(out)


def test_main_help_is_japanese_by_default(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["sweep", "--help"])

    assert exc.value.code == 0
    assert "移送内容を出力するだけ" in capsys.readouterr().out


def test_type_error_message_follows_the_display_language(capsys) -> None:
    argv = ["apply", "--path", "x.md", "--action", "relabel", "--to", "watching", "--watching-days", "-1"]

    with pytest.raises(SystemExit):
        main([*argv, "--lang", "en"])
    assert "Specify an integer of 0 or more" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        main(argv)
    assert "0 以上の整数を指定してください" in capsys.readouterr().err
