"""Web UI の文言の引き方と表示言語の決め方。

設計の正本: docs/local/plan_v0.2.0-english-support.md §C1

文言はソースに書かず ``docsweep/i18n/locales/<言語>/ui.json`` に置く（CLI と同じ辞書）。

- テンプレ向けは ``ui.*`` キー。``get_messages(lang)`` が接頭辞 ``ui.`` を外した解決済み dict
  ``T`` を返し、テンプレは ``{{ T.key }}`` で参照する（テンプレに言語分岐を書かない）。
- JS 向けは ``js.*`` キー。``js_messages(lang)`` が表示言語 1 つ分の表を返し、i18n.js を読む
  ページが ``<script type="application/json" id="ds-i18n">`` に埋める。JSON は実行されないので
  CSP ``script-src 'self'`` のまま渡せる（static/i18n.js は文言を持たない）。
- 対応言語は ``docsweep.i18n.SUPPORTED_LANGS``（locales/ のフォルダ）。対応外の言語は
  ``DEFAULT_LANG`` で出し、ある言語に足りないキーも ``DEFAULT_LANG`` の文言で出す。
- lang の解決順は ``?lang=`` クエリ > cookie ``docsweep_lang`` > CLI と同じ決め方
  （``DOCSWEEP_LANG`` > 明示した config の lang > OS の表示言語 > en）。
  cookie は設定モーダルの言語トグルと ``?lang=`` が書く（config.yaml は書き換えない＝ユーザー設定温存）。
"""

from __future__ import annotations

from datetime import date

from fastapi import Request

from ..i18n import DEFAULT_LANG, SUPPORTED_LANGS, normalize_lang, t, texts
from ..i18n import MESSAGES as _CATALOG

LANG_COOKIE = "docsweep_lang"

_UI = "ui."
_JS = "js."

__all__ = [
    "LANG_COOKIE",
    "SUPPORTED_LANGS",
    "absolute_title",
    "age_label",
    "get_messages",
    "js_messages",
    "lang_choices",
    "resolve_lang",
    "weekday_label",
]


def _lang(lang: object) -> str:
    """対応言語のコードへ。対応外・解釈不能は DEFAULT_LANG。"""
    return normalize_lang(lang) or DEFAULT_LANG


def _table(prefix: str, lang: str) -> dict[str, str]:
    """``prefix`` で始まる文字列の文言を、接頭辞を外したキーで表示言語 1 つ分返す。

    値は書式を埋めない生の文字列（テンプレは ``.format``、JS は位置引数で埋める）。
    その言語に無いキーは DEFAULT_LANG の文言で出す（``t()`` と同じ寄せ方）。
    """
    out: dict[str, str] = {}
    for key, entry in _CATALOG.items():
        if not key.startswith(prefix):
            continue
        value = entry.get(lang, entry.get(DEFAULT_LANG))
        if isinstance(value, str):
            out[key[len(prefix):]] = value
    return out


def weekday_label(iso_date: str, lang: str) -> str:
    """ISO 日付の曜日ラベル（UX W4 / P66）。解釈できなければ空文字。"""
    try:
        d = date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return ""
    names = texts("ui.weekdays", lang=_lang(lang))
    index = d.weekday()
    return names[index] if index < len(names) else ""


def absolute_title(iso_date: str, lang: str, *, kind: str = "due") -> str:
    """ツールチップ用の絶対表記（UX W4 / P66）。相対表示だけだと週をまたぐと混乱する。"""
    if not iso_date:
        return ""
    wd = weekday_label(iso_date, lang)
    if not wd:
        # 解釈できない日付で「期日 bogus（）」のような壊れたツールチップを出さない。
        return ""
    key = "ui.abs_date_title" if kind == "due" else "ui.abs_mtime_title"
    return t(key, lang=_lang(lang), date=iso_date, wd=wd)


def age_label(days: int | None, lang: str) -> str:
    """経過日数の表記を言語で統一する（UX W4 / P66）。"""
    if days is None:
        return ""
    if days <= 0:
        return t("ui.age_today", lang=_lang(lang))
    return t("ui.age_days", lang=_lang(lang), n=days)


def get_messages(lang: str) -> dict[str, str]:
    """lang 解決済みの文言 dict を返す（テンプレの ``T``）。未知 lang は DEFAULT_LANG で出す。"""
    code = _lang(lang)
    out = _table(_UI, code)
    # 解決済みの言語を dict 自身に載せる。card view のように Request を持たない層が
    # 絶対日付ラベルの言語を決めるのに使う（UX W4 / P66）。
    out["__lang__"] = code
    return out


def js_messages(lang: str) -> dict[str, str]:
    """JS（static/i18n.js の ``DS_T``）へ渡す表示言語 1 つ分の文言表。"""
    return _table(_JS, _lang(lang))


def lang_choices() -> list[dict[str, str]]:
    """設定モーダルの言語ボタン。対応言語ごとに、その言語自身での名前を添える。"""
    return [{"code": code, "name": t("ui.lang_name", lang=code)} for code in SUPPORTED_LANGS]


def resolve_lang(request: Request, lang: str | None = None) -> str:
    """表示言語を解決する: ``?lang=`` クエリ > cookie > CLI と同じ決め方。

    CLI と同じ決め方は ``DOCSWEEP_LANG`` > 設定の ``lang``（明示したときだけ）> OS の
    表示言語 > en（``docsweep.i18n.resolve_lang``）。``lang`` を省くと ``?lang=`` を
    リクエストから読む（引数で受けていないサブページも同じ決め方になる）。
    """
    from ..i18n import resolve_lang as resolve_display_lang

    if lang is None:
        lang = request.query_params.get("lang")
    if lang in SUPPORTED_LANGS:
        return lang
    cookie = request.cookies.get(LANG_COOKIE)
    if cookie in SUPPORTED_LANGS:
        return cookie
    return resolve_display_lang(config=request.app.state.docsweep.config)
