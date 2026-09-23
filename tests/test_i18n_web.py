"""Web UI のエラー文言と画面・JS の文言が表示言語で切り替わること。

- API のエラー detail は画面の言語（``?lang=`` / cookie / 設定・OS）で返る
- ``?lang=`` で開いた言語は cookie に残り、JS の再描画・API 呼び出しにも引き継がれる
- 画面（テンプレ）と JS の文言は ``locales/<言語>/ui.json`` の ``ui.*`` / ``js.*`` にだけ置く。
  JS へは表示言語 1 つ分を ``ds-i18n`` のデータ島で渡し、static/i18n.js は文言を持たない
  （ja / en のキーとプレースホルダがそろうこと・en に日本語が無いことは test_i18n.py が見る）
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from docsweep import i18n  # noqa: E402
from docsweep.config import load_config  # noqa: E402
from docsweep.server.app import create_app  # noqa: E402

TOKEN = "test-token-i18n-web"
SERVER = Path(__file__).resolve().parents[1] / "docsweep" / "server"
STATIC = SERVER / "static"
TEMPLATES = SERVER / "templates"
JAPANESE = re.compile(r"[぀-ヿ㐀-鿿＀-￯]")


@pytest.fixture
def client(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj_a"
    proj.mkdir(parents=True)
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")
    (proj / "plan_a.md").write_text("# [計画] a\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("# [計画] outside\n", encoding="utf-8")
    cfg = load_config(explicit_roots=[str(root)], global_path=tmp_path / "no_global.yaml")
    return TestClient(create_app(cfg, token=TOKEN)), outside


def _status_outside(c: TestClient, outside: Path, **params: str):
    return c.post(
        "/api/cards/status",
        params=params,
        data={"token": TOKEN, "path": str(outside), "new_state": "watching"},
    )


def test_api_error_detail_follows_query_lang(client):
    c, outside = client
    r = _status_outside(c, outside, lang="en")
    assert r.status_code == 403
    assert r.json()["detail"] == "path outside scan roots"


def test_api_error_detail_defaults_to_display_lang(client):
    # conftest が OS の表示言語を ja に固定している
    c, outside = client
    r = _status_outside(c, outside)
    assert r.status_code == 403
    assert r.json()["detail"] == "スキャンルートの外のパスです"


def test_api_error_detail_follows_cookie(client):
    c, outside = client
    c.cookies.set("docsweep_lang", "en")
    r = _status_outside(c, outside)
    assert r.json()["detail"] == "path outside scan roots"


def test_token_error_follows_lang(client):
    c, _ = client
    r = c.get("/api/board/triage", params={"lang": "en"})
    assert r.status_code == 403
    assert not JAPANESE.search(r.json()["detail"])


def test_query_lang_is_kept_for_later_requests(client):
    """?lang=en で開いた後の再描画と API も英語のまま（途中で言語が混ざらない）。"""
    c, outside = client
    r = c.get("/board", params={"token": TOKEN, "lang": "en"})
    assert r.status_code == 200
    # URL の token を外すリダイレクトを挟むので、最終応答ではなくクライアント側の cookie を見る
    assert c.cookies.get("docsweep_lang") == "en"
    fragment = c.get("/board/fragment", params={"token": TOKEN})
    assert "Overdue" in fragment.text
    assert "やり忘れ" not in fragment.text
    assert _status_outside(c, outside).json()["detail"] == "path outside scan roots"


def test_request_without_query_lang_does_not_set_cookie(client):
    c, _ = client
    r = c.get("/board", params={"token": TOKEN})
    assert r.status_code == 200
    assert c.cookies.get("docsweep_lang") is None


def test_unknown_query_lang_is_ignored(client):
    c, _ = client
    r = c.get("/board", params={"token": TOKEN, "lang": "fr"})
    assert r.status_code == 200
    assert 'lang="ja"' in r.text
    assert c.cookies.get("docsweep_lang") is None


@pytest.mark.parametrize("page", ["/graph", "/brief", "/cross", "/resurrect", "/capture"])
def test_subpages_follow_query_lang(client, page):
    c, _ = client
    r = c.get(page, params={"token": TOKEN, "lang": "en"})
    assert r.status_code == 200
    assert 'lang="en"' in r.text


def _graph_labels(html: str) -> dict[str, str]:
    match = re.search(r'<script type="application/json" id="graph-labels">(.*?)</script>', html)
    assert match, "graph-labels が無い"
    return json.loads(match.group(1))


def test_graph_page_labels_follow_lang(client):
    c, _ = client
    en = c.get("/graph", params={"token": TOKEN, "lang": "en"}).text
    assert "1 nodes / 0 edges" in en
    assert "isolated" in en
    assert _graph_labels(en) == {"state": "state", "project": "project", "type": "type"}
    ja = c.get("/graph", params={"token": TOKEN, "lang": "ja"}).text
    assert "1 ノード / 0 エッジ" in ja
    assert "孤立" in ja
    assert _graph_labels(ja) == {"state": "状態", "project": "プロジェクト", "type": "種別"}


# ---- JS の文言（ds-i18n のデータ島） ------------------------------------------------


def _catalog(prefix: str, lang: str) -> dict[str, str]:
    """ui.json の ``prefix`` キーを接頭辞なしで（その言語の値で）返す。"""
    return {
        key[len(prefix):]: value
        for key, value in i18n.CATALOGS[lang].items()
        if key.startswith(prefix) and isinstance(value, str)
    }


def _ds_i18n(html: str) -> dict[str, str]:
    blocks = re.findall(r'<script type="application/json" id="ds-i18n">(.*?)</script>', html, re.DOTALL)
    assert len(blocks) == 1, "ds-i18n のデータ島がちょうど 1 つ無い"
    return json.loads(blocks[0])


@pytest.mark.parametrize("lang", i18n.SUPPORTED_LANGS)
def test_board_embeds_js_messages_in_the_page_language(client, lang):
    c, _ = client
    html = c.get("/board", params={"token": TOKEN, "lang": lang}).text
    embedded = _ds_i18n(html)
    assert embedded.keys() == _catalog("js.", i18n.DEFAULT_LANG).keys()
    assert embedded == _catalog("js.", lang)
    # i18n.js が読む時点でデータ島がもう DOM にあること（i18n.js より前に置く）
    assert html.index('id="ds-i18n"') < html.index('<script src="/static/i18n.js">')


def test_board_js_messages_follow_the_cookie(client):
    c, _ = client
    c.cookies.set("docsweep_lang", "en")
    embedded = _ds_i18n(c.get("/board", params={"token": TOKEN}).text)
    assert embedded["loading"] == i18n.CATALOGS["en"]["js.loading"]
    assert not [key for key, value in embedded.items() if JAPANESE.search(value)]


def test_every_page_that_loads_i18n_js_embeds_the_table() -> None:
    for tpl in sorted(TEMPLATES.glob("*.html")):
        text = tpl.read_text(encoding="utf-8")
        if "/static/i18n.js" in text:
            assert 'id="ds-i18n"' in text, tpl.name


def test_i18n_js_holds_no_message_table() -> None:
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert not JAPANESE.search(_strip_js_comments(js))
    assert "TABLES" not in js
    # `key: "文言"` の形の表が残っていない
    assert not re.search(r'^\s*\w+:\s*"', js, re.MULTILINE)
    assert 'getElementById("ds-i18n")' in js


def _strip_js_comments(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.DOTALL)
    return re.sub(r"(^|\s)//[^\n]*", r"\1", js)


def _strip_template_comments(html: str) -> str:
    html = re.sub(r"\{#.*?#\}", "", html, flags=re.DOTALL)
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)


def test_static_js_and_templates_have_no_japanese_literals() -> None:
    """日本語の文言はコメント以外に書かない（ui.json へ置く）。"""
    leaked: list[str] = []
    for path in sorted(STATIC.glob("*.js")):
        if path.name.endswith(".min.js"):
            continue
        for number, line in enumerate(_strip_js_comments(path.read_text(encoding="utf-8")).splitlines(), 1):
            if JAPANESE.search(line):
                leaked.append(f"{path.name}:{number}: {line.strip()}")
    for path in sorted(TEMPLATES.glob("*.html")):
        text = _strip_template_comments(path.read_text(encoding="utf-8"))
        for number, line in enumerate(text.splitlines(), 1):
            if JAPANESE.search(line):
                leaked.append(f"{path.name}:{number}: {line.strip()}")
    assert leaked == []


def _first_argument(src: str, start: int) -> str:
    """``start`` から関数呼び出しの第 1 引数の文字列を返す（引数の区切りと括弧の対応だけ見る）。"""
    depth = 0
    quote = ""
    i = start
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return src[start:i]
            depth -= 1
        elif ch == "," and depth == 0:
            return src[start:i]
        i += 1
    return src[start:]


def _used_js_keys() -> set[str]:
    used: set[str] = set()
    for path in sorted(STATIC.glob("*.js")):
        if path.name.endswith(".min.js"):
            continue
        src = _strip_js_comments(path.read_text(encoding="utf-8"))
        # DS_T はどのファイルでも、T / fmt は w4.js の DS_T の薄いラッパ
        names = r"DS_T|T|fmt" if path.name == "w4.js" else r"DS_T"
        for match in re.finditer(rf"(?<![\w.])(?:window\.)?(?:{names})\(", src):
            arg = _first_argument(src, match.end())
            literal = re.fullmatch(r'\s*"([a-z0-9_]+)"\s*', arg)
            if literal:
                used.add(literal.group(1))
            else:
                # 三項演算子で選ぶ形（cond ? "a" : "b"）は両方のキーを数える
                used.update(re.findall(r'[?:]\s*"([a-z0-9_]+)"', arg))
    # 組み立てて引くキー（keymap.js の "state_" + state / w4.js の tour_N + "_title" 等）
    used |= {f"state_{s}" for s in ("planned", "in_progress", "watching", "pending", "done", "discarded")}
    used |= {f"tour_{i}_{part}" for i in range(1, 5) for part in ("title", "body")}
    return used


def test_static_js_only_uses_keys_that_exist_in_ui_json() -> None:
    declared = set(_catalog("js.", i18n.DEFAULT_LANG))
    used = _used_js_keys()
    assert used, "DS_T の呼び出しが 1 つも拾えていない（抽出が壊れている）"
    assert sorted(used - declared) == []


# ---- テンプレの文言（T） ----------------------------------------------------------


def test_get_messages_is_built_from_ui_json() -> None:
    from docsweep.server.i18n import get_messages

    for lang in i18n.SUPPORTED_LANGS:
        messages = get_messages(lang)
        assert messages.pop("__lang__") == lang
        assert messages == _catalog("ui.", lang)
    # 対応外の言語は DEFAULT_LANG で出す
    assert get_messages("zz")["__lang__"] == i18n.DEFAULT_LANG


def test_weekdays_have_seven_names_in_every_language() -> None:
    from docsweep.server.i18n import weekday_label

    for lang in i18n.SUPPORTED_LANGS:
        assert len(i18n.texts("ui.weekdays", lang=lang)) == 7, lang
    assert weekday_label("2026-08-30", "ja") == "日"
    assert weekday_label("2026-08-30", "en") == "Sun"


def _lang_buttons(html: str) -> list[tuple[str, str, bool]]:
    buttons = re.findall(
        r'<button[^>]*data-action="settings-set-lang"[^>]*data-lang="([^"]+)"([^>]*)>([^<]*)</button>',
        html,
    )
    return [(code, name.strip(), "disabled" in rest) for code, rest, name in buttons]


@pytest.mark.parametrize("lang", i18n.SUPPORTED_LANGS)
def test_settings_shows_one_language_button_per_supported_language(client, lang):
    c, _ = client
    c.cookies.set("docsweep_lang", lang)
    html = c.get("/board/_partial/settings", params={"token": TOKEN}).text
    assert _lang_buttons(html) == [
        (code, i18n.CATALOGS[code]["ui.lang_name"], code == lang) for code in i18n.SUPPORTED_LANGS
    ]
