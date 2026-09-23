"""利用者に見える文言・用語の読み込みと、表示言語の決め方（CLI・エンジン・MCP・Web UI 共通）。

文言と用語はソースに書かず、言語ごとの JSON に置く::

    docsweep/i18n/locales/<言語>/<分類>.json   （例: ja/messages.json・en/messages.json）

- 対応言語は ``locales/`` の下のフォルダで決まる。言語を足すときはフォルダを 1 つ足す
  （コードは変えない）。足りないキーは ``en`` の文言で出す。
- 分類（JSON ファイル）は読み込み時にまとめる。同じ言語の中でキーが重なったら起動時に止める。
- 値は文字列か文字列のリスト。文字列は :func:`t` で引き、``str.format`` のプレースホルダ
  （``{path}`` 等）を埋める。リスト（判定用のキーワード等）は :func:`texts` と
  :func:`variants` で引く。

表示言語は次の順で決める（:func:`resolve_lang`）。

1. 明示の指定（CLI の ``--lang``、Web UI の ``?lang=`` / cookie）
2. 環境変数 ``DOCSWEEP_LANG``
3. 設定の ``lang``（global / project で明示したときだけ）
4. OS の表示言語（Windows は UI 言語を見る。Git Bash は ``LANG=en_US.UTF-8`` を入れる
   ことがあり、日本語の利用者が AI のシェル経由で使うと英語になってしまうため ``LANG`` は見ない）
5. ``en``

現在の表示言語は contextvar で持つ。CLI は起動時、MCP はサーバー起動時、Web UI は
リクエストごとに決める。例外のメッセージは ``raise`` した時点の言語で作るので、
``str(exc)`` をそのまま返している経路も表示言語で出る。
"""

from __future__ import annotations

import contextvars
import json
import locale
import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

LOCALES_DIR = Path(__file__).with_name("locales")
DEFAULT_LANG = "en"
ENV_VAR = "DOCSWEEP_LANG"

# 値は文字列か文字列のリスト。
Value = str | list[str]

_current: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "docsweep_lang", default=None
)


def _load_locale(lang_dir: Path) -> dict[str, Value]:
    merged: dict[str, Value] = {}
    for path in sorted(lang_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: the root must be an object")
        for key, value in data.items():
            if key in merged:
                raise ValueError(f"duplicate message key: {key} ({path})")
            if not (
                isinstance(value, str)
                or (isinstance(value, list) and all(isinstance(item, str) for item in value))
            ):
                raise ValueError(f"{path}: {key} must be a string or a list of strings")
            merged[key] = value
    return merged


def load_locales(root: Path = LOCALES_DIR) -> dict[str, dict[str, Value]]:
    """``locales/<言語>/*.json`` を読み、言語コード -> (キー -> 値) を返す。"""
    catalogs = {
        lang_dir.name: _load_locale(lang_dir)
        for lang_dir in sorted(root.iterdir())
        if lang_dir.is_dir() and not lang_dir.name.startswith((".", "_"))
    }
    if DEFAULT_LANG not in catalogs:
        raise ValueError(f"{root}: the default language '{DEFAULT_LANG}' is missing")
    return catalogs


CATALOGS: dict[str, dict[str, Value]] = load_locales()
SUPPORTED_LANGS: tuple[str, ...] = tuple(CATALOGS)

def _by_key(catalogs: Mapping[str, Mapping[str, Value]]) -> dict[str, dict[str, Value]]:
    """言語 -> (キー -> 値) を キー -> (言語 -> 値) へ並べ替える。"""
    by_key: dict[str, dict[str, Value]] = {}
    for lang, catalog in catalogs.items():
        for key, value in catalog.items():
            by_key.setdefault(key, {})[lang] = value
    return by_key


# キー -> 言語コード -> 値（キーから全言語を引く側の都合の形）。
MESSAGES: dict[str, dict[str, Value]] = _by_key(CATALOGS)


def normalize_lang(value: object) -> str | None:
    """``ja`` / ``ja_JP.UTF-8`` / ``en-US`` 等を対応言語のコードへ。対応外・解釈不能は None。"""
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_")
    code = text.split("_", 1)[0].split(".", 1)[0]
    return code if code in SUPPORTED_LANGS else None


def _windows_ui_lang() -> str | None:
    # 分岐を sys.platform で書くと、mypy は Windows 以外で下を検査しない
    # （ctypes.windll は Windows の型定義にしか無い）。
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except Exception:
        return None
    # LANGID -> "ja_JP" 等。副言語が表に無いときは主言語（下位 10 bit）の既定で引く。
    name = locale.windows_locale.get(langid) or locale.windows_locale.get(
        0x400 | (langid & 0x3FF)
    )
    return normalize_lang(name) or DEFAULT_LANG


def detect_os_lang(
    *, environ: Mapping[str, str] | None = None, platform: str | None = None
) -> str | None:
    """OS の表示言語を対応言語のコードで返す。分からなければ None。

    対応していない言語（fr 等）は ``en`` へ寄せる。
    """
    if (platform or sys.platform) == "win32":
        return _windows_ui_lang()
    env = os.environ if environ is None else environ
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = (env.get(name) or "").strip()
        if value:
            return normalize_lang(value) or DEFAULT_LANG
    return None


def resolve_lang(
    flag: object = None,
    *,
    config: object = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """表示言語を決める（順序はモジュールの docstring のとおり）。"""
    env = os.environ if environ is None else environ
    for candidate in (flag, env.get(ENV_VAR)):
        lang = normalize_lang(candidate)
        if lang:
            return lang
    if config is not None and getattr(config, "lang_explicit", False):
        lang = normalize_lang(getattr(config, "lang", None))
        if lang:
            return lang
    return detect_os_lang(environ=environ) or DEFAULT_LANG


def current_lang() -> str:
    """いまの表示言語。誰も決めていなければ環境変数と OS から決める。"""
    return _current.get() or resolve_lang()


def set_lang(lang: object) -> contextvars.Token[str | None]:
    """表示言語を決める（呼び出し側は必要なら :func:`reset_lang` で戻す）。"""
    return _current.set(normalize_lang(lang) or DEFAULT_LANG)


def reset_lang(token: contextvars.Token[str | None]) -> None:
    _current.reset(token)


@contextmanager
def use_lang(lang: object) -> Iterator[str]:
    token = set_lang(lang)
    try:
        yield _current.get() or DEFAULT_LANG
    finally:
        reset_lang(token)


def _value(key: str, lang: str | None) -> Value:
    entry = MESSAGES.get(key)
    if entry is None:
        raise KeyError(f"unknown message key: {key}")
    code = normalize_lang(lang) or current_lang()
    if code in entry:
        return entry[code]
    if DEFAULT_LANG in entry:
        return entry[DEFAULT_LANG]
    return next(iter(entry.values()))


def t(key: str, /, *, lang: str | None = None, **params: Any) -> str:
    """文言を表示言語で返す。未知のキーは KeyError（キー名をそのまま出さない）。

    文言は常に ``str.format`` を通す。文字としての波括弧は JSON 側で ``{{`` ``}}`` と書く。
    ``key`` は位置専用なので、プレースホルダに ``{key}`` を使える。``lang`` は言語の
    指定に使うため、プレースホルダ名には使えない（辞書のテストが検査する）。
    """
    template = _value(key, lang)
    if not isinstance(template, str):
        raise TypeError(f"message key {key} holds a list; use texts()")
    return template.format(**params)


def texts(key: str, /, *, lang: str | None = None) -> list[str]:
    """リストの値（判定用のキーワード・複数行の本文等）を表示言語で返す。"""
    value = _value(key, lang)
    return [value] if isinstance(value, str) else list(value)


def variants(key: str, /) -> tuple[str, ...]:
    """全言語の値を重複なしで返す（どの言語で書かれた文書も読むための判定用）。"""
    entry = MESSAGES.get(key)
    if entry is None:
        raise KeyError(f"unknown message key: {key}")
    seen: dict[str, None] = {}
    for value in entry.values():
        for item in [value] if isinstance(value, str) else value:
            seen.setdefault(item, None)
    return tuple(seen)


__all__ = [
    "CATALOGS",
    "DEFAULT_LANG",
    "ENV_VAR",
    "LOCALES_DIR",
    "MESSAGES",
    "SUPPORTED_LANGS",
    "current_lang",
    "detect_os_lang",
    "load_locales",
    "normalize_lang",
    "reset_lang",
    "resolve_lang",
    "set_lang",
    "t",
    "texts",
    "use_lang",
    "variants",
]
