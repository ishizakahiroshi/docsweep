"""資料に書いた MCP の露出範囲が実装とズレていないかを機械で見張る。

2026-08-25 の仕様点検で、README と docs/ai-agent-integration.md が「3 tool 限定」と
書いたまま実装は 24 tool を露出していた。MCP 登録の可否はユーザーが権限を渡す判断なので、
資料が実態より小さく見えるのは危険。tool を足したらこのテストが落ち、資料の更新を強制する。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "ai-agent-integration.md"
SERVER = REPO / "docsweep" / "mcp_server.py"

_TOOL_RE = re.compile(r"@mcp\.tool\(\)\s*\n\s*def\s+([a-z_]+)")
_COUNT_RE = re.compile(r"MCP が露出する tool（[^）]*?(\d+) 個）")


def _registered_tools() -> list[str]:
    return _TOOL_RE.findall(SERVER.read_text(encoding="utf-8"))


def test_every_mcp_tool_is_documented():
    doc = DOC.read_text(encoding="utf-8")
    missing = [name for name in _registered_tools() if name not in doc]
    assert not missing, f"docs/ai-agent-integration.md に未記載の MCP tool: {missing}"


def test_documented_tool_count_matches_implementation():
    tools = _registered_tools()
    # 正規表現が実装の書き方に追随できなくなった時に 0 件で素通りしないよう検査する。
    assert tools, "mcp_server.py から tool を 1 つも拾えていない（検出パターンが古い?）"
    doc = DOC.read_text(encoding="utf-8")
    m = _COUNT_RE.search(doc)
    assert m, "露出 tool 数の見出しが見つからない（資料側の書式が変わった?）"
    assert int(m.group(1)) == len(tools)
