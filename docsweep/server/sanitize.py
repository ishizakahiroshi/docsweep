"""Markdown → HTML 描画の出力サニタイズ（許可リスト方式・標準ライブラリのみ）。

docsweep は横断スキャン対象（third-party clone を含みうる）の信頼できない .md を Web で
プレビューする。Python-Markdown は生 HTML（``<script>`` / ``onerror=`` 等）や
``javascript:`` URL を素通しするため、`{{ html|safe }}` 描画前に必ずここを通して
危険なタグ・属性・スキームを落とす。

注: これは新規依存を増やさないための標準ライブラリ実装の防御策。より堅牢な保証が必要なら
専用サニタイザ（nh3 / bleach）の導入を検討する（plan の進言事項を参照）。
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

# 描画を許可するタグ（Markdown が生成する安全な構造のみ）。
ALLOWED_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "pre", "code",
    "table", "thead", "tbody", "tr", "th", "td",
    "a", "img", "em", "strong", "b", "i", "del", "ins", "sup", "sub", "kbd",
    "span", "div",
}
# 内容ごと丸ごと捨てるタグ（テキストも出さない）。
DROP_WITH_CONTENT = {"script", "style"}
# 終了タグを持たない（void）要素。
VOID_TAGS = {"br", "hr", "img"}
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
_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*):")
_CTRL_RE = re.compile(r"[\x00-\x20\x7f]")


def _safe_url(value: str) -> str | None:
    """javascript:/data: 等の危険スキームを弾く。安全なら値を、危険なら None を返す。"""
    cleaned = _CTRL_RE.sub("", value)  # 制御文字/空白でスキームを隠す細工を無効化
    low = cleaned.lower()
    m = _SCHEME_RE.match(low)
    if not m:
        return value  # スキームなし＝相対/アンカー → 許可
    return value if m.group(1) in SAFE_URL_SCHEMES else None


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._drop_depth = 0  # script/style の内側にいる深さ

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROP_WITH_CONTENT:
            self._drop_depth += 1
            return
        if self._drop_depth or tag not in ALLOWED_TAGS:
            return
        self.out.append(self._open(tag, attrs))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROP_WITH_CONTENT or self._drop_depth or tag not in ALLOWED_TAGS:
            return
        self.out.append(self._open(tag, attrs))

    def handle_endtag(self, tag: str) -> None:
        if tag in DROP_WITH_CONTENT:
            if self._drop_depth:
                self._drop_depth -= 1
            return
        if self._drop_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._drop_depth:
            return
        self.out.append(escape(data))

    def handle_comment(self, data: str) -> None:  # コメントは落とす
        return

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        allowed = ALLOWED_ATTRS.get(tag, set())
        parts = [tag]
        for raw_k, v in attrs:
            k = raw_k.lower()
            if k not in allowed:
                continue
            if v is None:
                parts.append(k)
                continue
            if k in ("href", "src"):
                safe = _safe_url(v)
                if safe is None:
                    continue
                v = safe
            parts.append(f'{k}="{escape(v, quote=True)}"')
        joined = " ".join(parts)
        if tag in VOID_TAGS:
            return f"<{joined}>"
        return f"<{joined}>"


def _stdlib_sanitize(html: str) -> str:
    p = _Sanitizer()
    p.feed(html)
    p.close()
    return "".join(p.out)


def sanitize_html(html: str) -> str:
    """信頼できない Markdown 由来 HTML から危険なタグ・属性・スキームを除去する。

    堅牢性のため nh3（導入時）を優先し、未導入や想定外エラー時は検証済みの標準ライブラリ
    実装にフォールバックする。どちらの経路でも script/style・イベントハンドラ・危険スキームを落とす。
    """
    try:
        import nh3
    except ImportError:
        return _stdlib_sanitize(html)
    try:
        return nh3.clean(
            html,
            tags=ALLOWED_TAGS,
            clean_content_tags={"script", "style"},
            attributes=ALLOWED_ATTRS,
            url_schemes=SAFE_URL_SCHEMES,
        )
    except Exception:  # noqa: BLE001 - nh3 異常時は安全側で自前サニタイザへ退避
        return _stdlib_sanitize(html)
