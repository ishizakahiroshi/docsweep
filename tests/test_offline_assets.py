"""Web UI が外部ネットワークなしで完結することを守るテスト。

docsweep は「手元で完結するローカルツール」として配っている。テンプレートが
外部 CDN からスクリプト・スタイルを読むと、ネットワークの無い環境で壊れるうえ、
利用のたびに外部サーバーへアクセス記録が渡る。

2026-08-10 まで graph ページだけが unpkg から cytoscape.js を実行時取得していた
（`pending_bundle-cytoscape-offline.md`・期日 24 日超過）。v0.4.0 で同梱へ切り替えた。
本テストは同じ形の依存が再び入るのを止める。
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "docsweep" / "server" / "templates"
STATIC = Path(__file__).resolve().parent.parent / "docsweep" / "server" / "static"

# <script src="http(s)://..."> と <link ... href="http(s)://..."> だけを見る。
# 本文中の <a href="https://...">（出典リンク等）は対象外。
EXTERNAL_SCRIPT = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']https?://", re.IGNORECASE)
EXTERNAL_LINK = re.compile(r"<link\b[^>]*\bhref\s*=\s*[\"']https?://", re.IGNORECASE)


def test_templates_have_no_external_script_or_stylesheet() -> None:
    offenders: list[str] = []
    for tpl in sorted(TEMPLATES.rglob("*.html")):
        text = tpl.read_text(encoding="utf-8", errors="replace")
        for pattern, kind in ((EXTERNAL_SCRIPT, "script"), (EXTERNAL_LINK, "stylesheet")):
            for m in pattern.finditer(text):
                line = text[: m.start()].count("\n") + 1
                offenders.append(f"{tpl.name}:{line} 外部 {kind}")
    assert not offenders, (
        "テンプレートが外部リソースを参照しています。static へ同梱し、NOTICES.md に"
        f"ライセンスを追記してください: {offenders}"
    )


def test_cytoscape_is_bundled() -> None:
    """graph ページが使う cytoscape.js が実体として同梱されている。"""
    bundled = STATIC / "cytoscape.min.js"
    assert bundled.is_file(), "cytoscape.min.js が同梱されていません"
    assert bundled.stat().st_size > 100_000, "同梱ファイルが小さすぎます（取得失敗の可能性）"

    graph = (TEMPLATES / "graph.html").read_text(encoding="utf-8")
    assert "/static/cytoscape.min.js" in graph
    assert "unpkg.com" not in graph


def test_bundled_assets_are_listed_in_notices() -> None:
    """static 直下の外部由来 JS が NOTICES.md に記録されている。"""
    notices = (Path(__file__).resolve().parent.parent / "NOTICES.md").read_text(encoding="utf-8")
    for name in ("htmx.min.js", "cytoscape.min.js"):
        assert name in notices, f"{name} が NOTICES.md に記録されていません"
