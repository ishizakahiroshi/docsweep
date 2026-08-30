"""UX W4（C4）の画面側の配線をテストで固定する。

JS の挙動そのものは目視で確かめる。ここで守るのは「壊れると画面が静かに死ぬ」箇所:
CSP 不変条件・script 登録・要素と i18n キーの存在。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from docsweep.config import load_config  # noqa: E402
from docsweep.server.app import create_app  # noqa: E402
from docsweep.server.i18n import MESSAGES, absolute_title, age_label, weekday_label  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "docsweep" / "server" / "static"
TEMPLATES = Path(__file__).resolve().parents[1] / "docsweep" / "server" / "templates"
TOKEN = "test-token-w4-ui"


@pytest.fixture
def client(tmp_path: Path):
    root = tmp_path / "dev"
    proj = root / "proj"
    proj.mkdir(parents=True)
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")
    (proj / "plan_a.md").write_text(
        "---\ndue: 2026-08-30\n---\n# [計画] a\n\n## 概要\n\nA\n", encoding="utf-8"
    )
    cfg = load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")
    app = create_app(cfg, token=TOKEN)
    return TestClient(app), proj


# ===== CSP 不変条件 ===========================================================


def test_w4_js_is_a_separate_file_not_inline() -> None:
    """inline script は CSP script-src 'self' で黙って実行されない。"""
    assert (STATIC / "w4.js").is_file()
    board = (TEMPLATES / "board.html").read_text(encoding="utf-8")
    assert '<script src="/static/w4.js"></script>' in board


def test_board_template_has_no_inline_event_handlers() -> None:
    board = (TEMPLATES / "board.html").read_text(encoding="utf-8")
    card = (TEMPLATES / "_card.html").read_text(encoding="utf-8")
    for name, text in (("board.html", board), ("_card.html", card)):
        found = re.findall(r"\son(?:click|change|input|submit|load|keyup)\s*=", text)
        assert not found, name + " に inline イベント属性がある: " + str(found)


def test_w4_js_does_not_use_inline_style_injection_for_theme() -> None:
    """テーマ切替は data 属性 + CSS 変数で行う（style 属性の直書きに戻さない）。"""
    js = (STATIC / "w4.js").read_text(encoding="utf-8")
    assert 'document.body.dataset.contrast' in js
    assert 'document.body.dataset.density' in js


# ===== 画面要素の存在 =========================================================


def test_board_renders_the_w4_controls(client) -> None:
    c, _ = client
    html = c.get("/board", params={"token": TOKEN}).text
    for needle in (
        'id="density-select"',      # P15
        'id="contrast-toggle"',     # P65
        'class="skip-link"',        # P64
        'id="focus-bar"',           # P22
        'id="tour"',                # P5
        'id="confirm-phrase"',      # P59
        'data-action="toggle-pin"',     # P21
        'data-action="toggle-snooze"',  # P21
        'data-action="focus-card"',     # P22
    ):
        assert needle in html, needle + " が board に出ていない"


def test_due_badge_carries_an_absolute_date_tooltip(client) -> None:
    """UX W4 / P66: 相対表示だけだと週をまたぐと分からない。"""
    c, _ = client
    html = c.get("/board", params={"token": TOKEN}).text
    assert "期日 2026-08-30" in html


def test_today_pick_age_is_localised(client) -> None:
    """「19d」の素の英語表記が残っていないこと（P66）。"""
    body = (TEMPLATES / "_board_body.html").read_text(encoding="utf-8")
    assert "}}d{% endif %}" not in body
    assert "today_pick.age_label" in body


# ===== i18n の網羅 ============================================================


def test_every_new_message_has_both_languages() -> None:
    for key, entry in MESSAGES.items():
        assert entry.get("ja"), key + " に ja が無い"
        assert entry.get("en"), key + " に en が無い"


def test_js_dictionary_has_the_same_keys_in_both_languages() -> None:
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")
    tables = re.findall(r"^\s{4}(ja|en):\s*\{$", js, flags=re.MULTILINE)
    assert tables == ["ja", "en"], tables
    ja_block, en_block = _split_tables(js)
    ja_keys = set(re.findall(r"^\s{6}([a-z0-9_]+):", ja_block, flags=re.MULTILINE))
    en_keys = set(re.findall(r"^\s{6}([a-z0-9_]+):", en_block, flags=re.MULTILINE))
    assert ja_keys - en_keys == set(), "en に無いキー: " + str(sorted(ja_keys - en_keys))
    assert en_keys - ja_keys == set(), "ja に無いキー: " + str(sorted(en_keys - ja_keys))


def _split_tables(js: str) -> tuple[str, str]:
    start_ja = js.index("    ja: {")
    start_en = js.index("    en: {")
    end_en = js.index("\n  };", start_en)
    return js[start_ja:start_en], js[start_en:end_en]


def test_w4_js_only_uses_keys_that_exist_in_the_dictionary() -> None:
    js = (STATIC / "w4.js").read_text(encoding="utf-8")
    dictionary = (STATIC / "i18n.js").read_text(encoding="utf-8")
    used = set(re.findall(r'\b(?:T|fmt)\("([a-z0-9_]+)"', js))
    # ツアーは key + "_title" / "_body" で組み立てるので静的には拾えない分を足す。
    used |= {f"tour_{i}_{part}" for i in range(1, 5) for part in ("title", "body")}
    declared = set(re.findall(r"^\s{6}([a-z0-9_]+):", dictionary, flags=re.MULTILINE))
    missing = sorted(used - declared)
    assert not missing, "i18n.js に無いキーを使っている: " + str(missing)


# ===== 日付フォーマッタ =======================================================


def test_weekday_label_matches_the_language() -> None:
    assert weekday_label("2026-08-30", "ja") == "日"
    assert weekday_label("2026-08-30", "en") == "Sun"


def test_absolute_title_is_empty_for_an_unparsable_date() -> None:
    """壊れた日付で「期日 bogus（）」のようなツールチップを出さない。"""
    assert absolute_title("bogus", "ja") == ""
    assert absolute_title("", "ja") == ""


def test_age_label_uses_words_not_a_bare_d_suffix() -> None:
    assert age_label(19, "ja") == "19 日前"
    assert age_label(19, "en") == "19d ago"
    assert age_label(0, "ja") == "今日"
    assert age_label(None, "ja") == ""


def test_get_messages_reports_the_resolved_language() -> None:
    from docsweep.server.i18n import get_messages

    assert get_messages("en")["__lang__"] == "en"
    assert get_messages("ja")["__lang__"] == "ja"
    assert get_messages("zz")["__lang__"] == "ja"
