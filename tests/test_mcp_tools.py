"""C7 (wings): MCP tool 登録の存在チェック。

実 MCP プロトコル経由の通信は別途 e2e でテストする想定。ここでは
``build_server`` が要件 tool を登録し、description が AI 選択に十分な内容を
持つことだけを検証する（C7 の完了条件）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep.config import load_config

# mcp extras 未インストール環境では skip
mcp = pytest.importorskip("mcp")


def _build(tmp_path):
    from docsweep.mcp_server import build_server

    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    return build_server(cfg)


def _tool_names(server) -> set[str]:
    """FastMCP のツール登録を覗いて名前集合を返す。

    FastMCP の内部 API に依存するため、変更で壊れたらここを直す。
    """
    # FastMCP は ``_tool_manager._tools`` (dict) に登録する。
    tm = getattr(server, "_tool_manager", None)
    if tm is not None and hasattr(tm, "_tools"):
        return set(tm._tools.keys())
    # 後方互換: ``server._tools`` に持つ実装
    if hasattr(server, "_tools"):
        return set(server._tools.keys())
    return set()


def test_朝の入口_3tool_registered(tmp_path):
    """C2/C3/C4 で採用された MCP 露出 3 tool が登録されている。"""
    server = _build(tmp_path)
    names = _tool_names(server)
    for required in ("brief", "cross", "capture_extract", "capture_save"):
        assert required in names, f"MCP tool '{required}' が登録されていない (現状: {sorted(names)})"


def test_既存_tools_still_registered(tmp_path):
    """C1 以前から登録されていた MCP tool が壊れていない。"""
    server = _build(tmp_path)
    names = _tool_names(server)
    # 代表的な既存 tool（全部ではなく typical なもの）
    for required in ("triage", "scan", "apply", "sweep", "promote"):
        assert required in names, f"既存 MCP tool '{required}' が消えている"


def test_朝の入口_descriptions_have_user_phrases(tmp_path):
    """brief / cross / capture の description に典型ユーザー発話例が含まれる。"""
    server = _build(tmp_path)
    tm = getattr(server, "_tool_manager", None)
    if tm is None or not hasattr(tm, "_tools"):
        pytest.skip("FastMCP 内部 API 変更で description 取得不可")

    tools = tm._tools

    def _desc(name: str) -> str:
        t = tools.get(name)
        if t is None:
            return ""
        # FastMCP の Tool は description プロパティを持つ
        return getattr(t, "description", "") or ""

    brief_desc = _desc("brief")
    cross_desc = _desc("cross")
    capture_desc = _desc("capture_extract")

    # 「典型ユーザー発話」が含まれていることを description で確認（AI 選択精度のため）
    assert "今日" in brief_desc or "朝" in brief_desc or "ブリーフ" in brief_desc
    assert "全プロジェクト" in cross_desc or "クロス" in cross_desc or "凍結" in cross_desc
    assert "plan" in capture_desc or "キャプチャ" in capture_desc or "草案" in capture_desc
