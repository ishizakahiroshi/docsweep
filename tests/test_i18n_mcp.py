"""MCP の tool の説明（description）が表示言語の JSON から来ること。

説明は ``docsweep/i18n/locales/<言語>/mcp.json`` の ``mcp.tool.<tool 名>`` に置き、
サーバーを作る時点の表示言語で引く。docstring には開発者向けの 1 行しか書かない。
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from docsweep import i18n
from docsweep.config import load_config
from docsweep.i18n import use_lang

pytest.importorskip("mcp")

JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
PREFIX = "mcp.tool."


def _build(root: Path):
    from docsweep.mcp_server import build_server

    return build_server(load_config(explicit_roots=[str(root)], global_path=root / "no.yaml"))


def _descriptions(server) -> dict[str, str]:
    """MCP クライアントが tools/list で受け取る説明（tool 名 -> description）。"""
    tools = asyncio.run(server.list_tools())
    return {tool.name: tool.description or "" for tool in tools}


def _langs() -> str:
    return " / ".join(i18n.SUPPORTED_LANGS)


def test_every_tool_has_a_message_key_and_no_key_is_orphaned(tmp_path: Path) -> None:
    names = set(_descriptions(_build(tmp_path)))
    keys = {key[len(PREFIX):] for key in i18n.MESSAGES if key.startswith(PREFIX)}

    assert names, "tool を 1 つも拾えていない"
    assert sorted(names - keys) == [], "mcp.json に説明が無い tool"
    assert sorted(keys - names) == [], "登録されていない tool の説明が mcp.json に残っている"
    for name in names:
        assert set(i18n.MESSAGES[PREFIX + name]) == set(i18n.SUPPORTED_LANGS), name


def test_descriptions_are_english_under_use_lang_en(tmp_path: Path) -> None:
    with use_lang("en"):
        descriptions = _descriptions(_build(tmp_path))

    for name, text in descriptions.items():
        assert text.strip(), name
        assert not JAPANESE.search(text), f"{name}: {text!r}"
        assert text == i18n.t(PREFIX + name, lang="en", langs=_langs())


def test_descriptions_are_japanese_under_use_lang_ja(tmp_path: Path) -> None:
    with use_lang("ja"):
        descriptions = _descriptions(_build(tmp_path))

    for name, text in descriptions.items():
        assert JAPANESE.search(text), f"{name}: {text!r}"
        assert text == i18n.t(PREFIX + name, lang="ja", langs=_langs())


def test_descriptions_are_fixed_when_the_server_is_built(tmp_path: Path) -> None:
    """説明は作った時点の表示言語で決まり、後から言語を変えても混ざらない。"""
    with use_lang("en"):
        server = _build(tmp_path)
    with use_lang("ja"):
        descriptions = _descriptions(server)

    assert not any(JAPANESE.search(text) for text in descriptions.values())


def test_language_list_comes_from_the_locale_folders(tmp_path: Path) -> None:
    """対応言語は説明に書き込まず、言語フォルダの一覧から埋める。"""
    for lang in i18n.SUPPORTED_LANGS:
        path = i18n.LOCALES_DIR / lang / "mcp.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "{langs}" in raw[PREFIX + "inject"], lang
        assert "{langs}" in raw[PREFIX + "inject_global"], lang
    with use_lang("en"):
        descriptions = _descriptions(_build(tmp_path))
    for name in ("inject", "inject_global"):
        assert f"lang ({_langs()})" in descriptions[name]
        assert "{" not in descriptions[name]


def test_docstrings_are_only_short_developer_notes(tmp_path: Path) -> None:
    """説明の正本は mcp.json。docstring は無いか 1 行だけで、説明の出どころにしない。"""
    server = _build(tmp_path)
    for name, tool in server._tool_manager._tools.items():
        doc = (tool.fn.__doc__ or "").strip()
        assert "\n" not in doc, name
        assert tool.description != doc, name
