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


# <script> の開きタグと、その中身。src 付き（外部読み込み）と
# type="application/json"（実行されないデータ島）は対象外。
SCRIPT_TAG = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
EVENT_ATTR = re.compile(r"\son(?:click|change|submit|input|load|error|key\w+|focus|blur)\s*=", re.IGNORECASE)


def test_templates_have_no_inline_script() -> None:
    """template に inline script / inline イベント属性を書かない。

    サーバーは全レスポンスに `script-src 'self'`（`'unsafe-inline'` なし）の CSP を
    付けている。inline で書くとブラウザが実行を拒否するが、**画面には何のエラーも
    出ない**ため、ページが黙って機能しなくなる。

    2026-08-10 の v0.4.0 リリース直前に、graph / brief / capture の 3 ページが
    この状態だった（graph は cytoscape を同梱した後も描画されず、capture は
    フォームが完全に無反応だった）。外部 js + `data-action` + データ島へ移して解消。
    """
    offenders: list[str] = []
    for tpl in sorted(TEMPLATES.rglob("*.html")):
        text = tpl.read_text(encoding="utf-8", errors="replace")
        for m in SCRIPT_TAG.finditer(text):
            attrs, body = m.group(1), m.group(2)
            if "src=" in attrs.lower():
                continue
            if "application/json" in attrs.lower():
                continue
            if not body.strip():
                continue
            line = text[: m.start()].count("\n") + 1
            offenders.append(f"{tpl.name}:{line} inline script")
        for m in EVENT_ATTR.finditer(text):
            line = text[: m.start()].count("\n") + 1
            offenders.append(f"{tpl.name}:{line} inline イベント属性")
    assert not offenders, (
        "CSP (script-src 'self') が inline を拒否するため、これらは実行されません。"
        f"/static の js へ出し、data-action とデータ島で渡してください: {offenders}"
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
