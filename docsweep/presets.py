"""状態モデルのプリセット（inject で各プロジェクトへ流し込む正本ラインナップ）。

公式プリセット＝正本。利用者はこれを選んで注入し、必要なら .docSweep.yaml で部分上書きする。
v0.1.0 は claude-jp（日本語・H1 ラベル運用）と frontmatter（汎用・status フィールド併記）の 2 種。
"""

from __future__ import annotations

from dataclasses import dataclass

from .states import DEFAULT_STATES, StateModel


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    lang: str
    states: StateModel
    use_frontmatter: bool = False


def _default_state_model() -> StateModel:
    return StateModel(list(DEFAULT_STATES))


PRESETS: dict[str, Preset] = {
    "claude-jp": Preset(
        name="claude-jp",
        description="Claude Code 向け日本語ルール（H1 ステータスラベル運用）。docSweep 標準。",
        lang="ja",
        states=_default_state_model(),
        use_frontmatter=False,
    ),
    "frontmatter": Preset(
        name="frontmatter",
        description="汎用。H1 ラベルに加え front matter の status: を併記する運用。",
        lang="en",
        states=_default_state_model(),
        use_frontmatter=True,
    ),
}

DEFAULT_PRESET = "claude-jp"


def get_preset(name: str | None) -> Preset:
    key = name or DEFAULT_PRESET
    if key not in PRESETS:
        raise ValueError(f"未知のプリセット '{key}'（利用可能: {', '.join(PRESETS)}）")
    return PRESETS[key]
