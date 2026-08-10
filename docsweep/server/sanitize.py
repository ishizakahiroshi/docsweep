"""Markdown → HTML 描画の出力サニタイズ（web extras 必須の nh3 を使用）。

docsweep は横断スキャン対象（third-party clone を含みうる）の信頼できない .md を Web で
プレビューする。Python-Markdown は生 HTML（``<script>`` / ``onerror=`` 等）や
``javascript:`` URL を素通しするため、`{{ html|safe }}` 描画前に必ずここを通して
危険なタグ・属性・スキームを落とす。

サニタイズは Rust 実装の nh3（ammonia バインディング）に一本化する。nh3 は
``pyproject.toml`` の web extras で必須指定されており、Web UI を起動できる環境には
必ず存在する。nh3 由来の例外は握り潰さずそのまま伝播させる（silent fallback しない）。
"""

from __future__ import annotations

import nh3

# 描画を許可するタグ（Markdown が生成する安全な構造のみ）。
ALLOWED_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "pre", "code",
    "table", "thead", "tbody", "tr", "th", "td",
    "a", "img", "em", "strong", "b", "i", "del", "ins", "sup", "sub", "kbd",
    "span", "div",
}
# タグ別に許可する属性（イベントハンドラ on* / style は一切許可しない）。
ALLOWED_ATTRS: dict[str, set[str]] = {
    "a": {"href", "title", "id"},
    "img": {"src", "alt", "title"},
    "code": {"class"},
    "pre": {"class"},
    "span": {"class"},
    "div": {"class"},
    "td": {"colspan", "rowspan", "align"},
    "th": {"colspan", "rowspan", "align"},
    "ol": {"start"},
    "h1": {"id"}, "h2": {"id"}, "h3": {"id"}, "h4": {"id"},
    "h5": {"id"}, "h6": {"id"}, "li": {"id"},
}
# href / src で許可する URL スキーム。相対 URL・アンカーも許可。
SAFE_URL_SCHEMES = {"http", "https", "mailto"}


def sanitize_html(html: str) -> str:
    """信頼できない Markdown 由来 HTML から危険なタグ・属性・スキームを除去する。

    nh3.clean を直接呼び、script/style・イベントハンドラ・javascript: 等の危険スキームを落とす。
    nh3 由来の例外はそのまま伝播させる（フォールバックしない）。
    """
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        clean_content_tags={"script", "style"},
        attributes=ALLOWED_ATTRS,
        url_schemes=SAFE_URL_SCHEMES,
    )
