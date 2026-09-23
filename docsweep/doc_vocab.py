"""作業文書（plan / bugfix / pending）の見出し・表の列名・記入欄を引く口。

語そのものはソースに持たず、言語別の JSON（``docsweep/i18n/locales/<言語>/terms.json`` の
``doc.heading.*`` / ``doc.column.*`` / ``doc.placeholder.*``）に置く。

文書へ書き込む語なので、表示言語ではなく文書の言語（``Config.document_lang()``）で選ぶ。
解析する側は、どの言語で書かれた文書も読めるように全言語の表記を受け付ける
（``heading_variants`` / ``variants``）。``docsweep new`` のテンプレート・capture・demo・
provenance・linkcheck・closeout-check が同じ表記を使う。

配布物の pre-commit hook（``templates/.githooks/``）は docsweep を import しないため、
同じ語を自分の JSON に持つ（テストが突き合わせる）。
"""

from __future__ import annotations

from .i18n import MESSAGES, SUPPORTED_LANGS, t
from .i18n import variants as _all_variants

_HEADING = "doc.heading."
_COLUMN = "doc.column."
_PLACEHOLDER = "doc.placeholder."

# 見出しのキー（``doc.heading.<key>``）。JSON に足した見出しも自動で入る。
HEADING_KEYS: tuple[str, ...] = tuple(
    key[len(_HEADING):] for key in MESSAGES if key.startswith(_HEADING)
)


def heading(key: str, lang: str) -> str:
    """見出しを ``lang`` の表記で返す（``## `` や ``#### `` は付けない）。"""
    return t(_HEADING + key, lang=lang)


def column(key: str, lang: str) -> str:
    return t(_COLUMN + key, lang=lang)


def placeholder(key: str, lang: str) -> str:
    return t(_PLACEHOLDER + key, lang=lang)


def heading_variants(key: str) -> tuple[str, ...]:
    """見出しの全言語の表記（重複なし）。"""
    return _all_variants(_HEADING + key)


def column_variants(key: str) -> tuple[str, ...]:
    return _all_variants(_COLUMN + key)


def heading_by_lang(key: str) -> dict[str, str]:
    """言語コード -> 見出しの表記。"""
    return {lang: heading(key, lang) for lang in SUPPORTED_LANGS}


def variants(text: str) -> tuple[str, ...]:
    """見出し ``text``（どれかの言語の表記）と同じ見出しの全言語の表記。

    語彙に無い見出し（利用者が ``types:`` で定義したもの等）は ``(text,)`` をそのまま返す。
    """
    for key in HEADING_KEYS:
        names = heading_variants(key)
        if text in names:
            return names
    return (text,)
