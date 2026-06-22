"""services 層 — Web UI / MCP / CLI が共通して呼ぶ書き込み API。

「Web UI に新しい特権を持たせない」原則を構造的に守るため、書き込みの実体は
ここに集約する。MCP ツール（``mcp_server.py``）と Web UI ルート（``server/app.py``）は
このパッケージの関数を呼ぶだけの薄いラッパに留める。

不変条件（全 services 共通）:
- 物理削除を持たない（最悪でも archive 移動止まり）
- スキャンルート配下のみ書き込み可（realpath 解決後にスコープ境界チェック）
- 全 API は ``atomic.write_atomic`` 経由でアトミック書き込み（楽観ロック対応）
"""
