"""状態モデルのプリセット（inject で各プロジェクトへ流し込む正本ラインナップ）。

公式プリセット＝正本。利用者はこれを選んで注入し、必要なら .docsweep.yaml で部分上書きする。
v0.1.0 は claude-jp（日本語・H1 ラベル運用）と frontmatter（汎用・status フィールド併記）の 2 種。
"""

from __future__ import annotations

from dataclasses import dataclass

from .i18n import t
from .states import DEFAULT_STATES, StateModel


@dataclass(frozen=True)
class Preset:
    name: str
    # 説明の辞書キー（docsweep/i18n/locales/<言語>/messages.json の presets.*）。文言は ``description`` で引く。
    description_key: str
    lang: str
    states: StateModel
    use_frontmatter: bool = False
    # プリセット定義の改訂版。注入内容（ラベル節の生成・状態モデル）の意味が変わったら手で bump する。
    # 注入時にマニフェストへ記録し UI が「どの版が入っているか」を表示する。
    version: str = "1"

    @property
    def description(self) -> str:
        """説明。注入する .docsweep.yaml の見出しに書くので、プリセットの言語で返す。"""
        return t(self.description_key, lang=self.lang)


def _default_state_model() -> StateModel:
    return StateModel(list(DEFAULT_STATES))


PRESETS: dict[str, Preset] = {
    "claude-jp": Preset(
        name="claude-jp",
        description_key="presets.claude_jp.description",
        lang="ja",
        states=_default_state_model(),
        use_frontmatter=False,
        version="2",
    ),
    "frontmatter": Preset(
        name="frontmatter",
        description_key="presets.frontmatter.description",
        lang="en",
        states=_default_state_model(),
        use_frontmatter=True,
        version="2",
    ),
}

DEFAULT_PRESET = "claude-jp"


def get_preset(name: str | None) -> Preset:
    key = name or DEFAULT_PRESET
    if key not in PRESETS:
        raise ValueError(t("presets.unknown", name=key, available=", ".join(PRESETS)))
    return PRESETS[key]
