"""状態モデル（states）— 単一正本。

設計の正本はこのファイル自身（``DEFAULT_STATES``）と docs/conventions.md。
設計を決めた経緯は docs/local/archive/v0.5.x/plan_state-tag-orthogonalization.md と
docs/local/archive/v0.1.0/plan_v0.1.0-product-requirements.md（どちらも非公開の作業ログ）。

config の ``states:`` を唯一の正本とし、ここから
「検出ロジック・自動 archive 可否・Web 表示・注入テンプレ文面」を全部導出する。
内蔵デフォルトを下に持ち、利用者は上書き・追加・言語追加だけで差分運用できる。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .i18n import MESSAGES, SUPPORTED_LANGS, current_lang, t, variants

# サポートする検出言語（ラベル辞書のキー）。言語別 JSON のフォルダで決まる。
LANGS = SUPPORTED_LANGS


@dataclass(frozen=True)
class State:
    """1 つの内部状態の定義。

    labels: 言語コード -> ブラケット内のラベル文字列（例 {"ja": "計画", "en": "Planned"}）。
    archive: archive 対象になりうるか（``[完了]``/``[廃止]`` のみ True）。
    auto_move: ``--auto`` で自動移送してよいか。watching は必ず False（寝かせ中＝守る）。
    extra_aliases: 廃止された旧ラベルや別名を「読み取り側のエイリアス」として吸収するための
        追加文字列タプル（書き出し時には使われない）。2026-06-23 改修で廃止した
        ``[対応中]`` を ``in-progress`` のエイリアスとして登録するために導入。
    """

    key: str
    labels: dict[str, str]
    archive: bool = False
    auto_move: bool = False
    color: str | None = None
    icon: str | None = None
    extra_aliases: tuple[str, ...] = ()

    def label(self, lang: str | None = None) -> str:
        """``lang`` の表記のラベル。省くと表示言語。その言語の表記が無ければ最初の表記。"""
        return self.labels.get(lang or current_lang()) or next(iter(self.labels.values()))

    def aliases(self) -> set[str]:
        """全言語のラベル文字列 + extra_aliases（すべて小文字化）。検出時のエイリアス照合に使う。"""
        result = {v.strip().lower() for v in self.labels.values() if v}
        result.update(a.strip().lower() for a in self.extra_aliases if a)
        return result


# 内蔵デフォルト（何も書かなければこれで動く）。
# plan_v0.1.0-product-requirements.md「★ 状態モデル」の表に対応。
#
# 2026-06-23 改修: かつて bugfix 専用に分離していた ``active`` ([対応中]) を ``in-progress``
# ([実行中]) に統合した。理由: 同じ「着手中」概念を 2 ラベルに分けることがユーザー UX を
# 悪化させ、ピッカーの番号重複や種別出し分けロジックを生んでいたため。
# 既存の `bugfix_*.md` に書かれた ``[対応中]`` は ``in-progress`` のエイリアスとして
# 読み取り時にマッチさせる（既存ファイルは書き換えない）。
# 経緯: docs/local/kanban-card-ux-options/index.html、設計プラン
# docs/local/archive/v0.5.x/plan_state-tag-orthogonalization.md（改訂版）
#
# ラベルの語は言語別の JSON（docsweep/i18n/locales/<言語>/terms.json の ``state.label.<key>``、
# 読み取りだけ受け付ける旧ラベル・別名は ``state.alias.<key>``）に置く。言語を足すと
# その言語のラベルも自動で読み書きできる。
def _default_state(key: str, *, archive: bool = False, auto_move: bool = False) -> State:
    alias_key = f"state.alias.{key}"
    return State(
        key,
        {lang: t(f"state.label.{key}", lang=lang) for lang in SUPPORTED_LANGS},
        archive=archive,
        auto_move=auto_move,
        # 旧 bugfix 用ラベル [対応中] 等を読み取り側のエイリアスとして吸収する。
        extra_aliases=variants(alias_key) if alias_key in MESSAGES else (),
    )


DEFAULT_STATES: tuple[State, ...] = (
    _default_state("planned"),
    _default_state("in-progress"),
    _default_state("watching"),
    _default_state("done", archive=True, auto_move=True),
    _default_state("discarded", archive=True, auto_move=True),
    _default_state("pending"),
)


@dataclass
class StateModel:
    """states 定義の集合。エイリアス→state の逆引きを提供する。"""

    states: list[State] = field(default_factory=lambda: list(DEFAULT_STATES))

    def __post_init__(self) -> None:
        self._by_key: dict[str, State] = {s.key: s for s in self.states}
        self._by_alias: dict[str, State] = {}
        for s in self.states:
            for a in s.aliases():
                self._by_alias[a] = s
            # 内部キー自体も frontmatter の status 値として受け付ける。
            self._by_alias.setdefault(s.key.lower(), s)

    def by_key(self, key: str) -> State | None:
        return self._by_key.get(key)

    def label_lang(self, token: str | None) -> str | None:
        """ラベル文字列がどの言語の表記か（``ja`` / ``en`` 等）。内部キー・別名・不明は None。

        状態を書き換えるとき、文書にすでにある表記の言語へそろえるために使う（``[計画]`` の
        文書を ``--lang en`` で動かしても ``[Watching]`` を混ぜない）。末尾一致は ``match`` と同じ。
        """
        if not token:
            return None
        text = token.strip().lower()
        pairs = sorted(
            ((label.strip().lower(), code) for s in self.states for code, label in s.labels.items()),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        for label, code in pairs:
            if not label:
                continue
            if text == label:
                return code
            if text.endswith(label) and len(text) > len(label) and not text[-len(label) - 1].isalnum():
                return code
        return None

    def match(self, token: str | None) -> State | None:
        """ラベル文字列または内部キーから state を引く（言語非依存）。

        1. まず完全一致（既存挙動）
        2. ダメなら末尾一致を試す。`[v0.1.0 完了]` のようなバージョン情報付きラベルや
           `[draft 計画]` のような注釈付きラベルを救うため。誤検出を抑えるため、
           「末尾に alias があり、その直前が非英数字（空白等）」のときだけ採用する。
        """
        if not token:
            return None
        text = token.strip().lower()
        # 1. 完全一致
        if text in self._by_alias:
            return self._by_alias[text]
        # 2. 末尾一致（長い alias から順に試して最初に当たったものを採用）
        for alias in sorted(self._by_alias.keys(), key=len, reverse=True):
            if not alias:
                continue
            if text.endswith(alias) and len(text) > len(alias):
                boundary = text[-len(alias) - 1]
                if not boundary.isalnum():
                    return self._by_alias[alias]
        return None

    @property
    def archivable_keys(self) -> set[str]:
        return {s.key for s in self.states if s.archive}

    @property
    def auto_move_keys(self) -> set[str]:
        return {s.key for s in self.states if s.auto_move}


def build_state_model(states_cfg: list[dict] | None) -> StateModel:
    """config の ``states:`` リストから StateModel を構築する。None なら内蔵デフォルト。

    config 形式（各要素）::

        - key: done
          labels: {ja: 完了, en: Done}
          archive: true
          auto_move: true
    """
    if not states_cfg:
        return StateModel()
    states: list[State] = []
    seen_keys: set[str] = set()
    seen_aliases: dict[str, str] = {}  # alias(小文字ラベル) -> 最初に定義した key
    for raw in states_cfg:
        key = raw["key"]
        labels = dict(raw.get("labels") or {})
        if not labels:
            raise ValueError(t("states.labels_missing", state=key))
        # 重複は後勝ちで dict 上書きされ、ラベルが別 state（archive 可否が違う）に解決されて
        # 静かに誤判定するため、設定構築時に fail-fast で弾く。
        if key in seen_keys:
            raise ValueError(t("states.duplicate_key", state=key))
        seen_keys.add(key)
        st = State(
            key=key,
            labels=labels,
            archive=bool(raw.get("archive", False)),
            auto_move=bool(raw.get("auto_move", False)),
            color=raw.get("color"),
            icon=raw.get("icon"),
        )
        for a in st.aliases() | {key.lower()}:
            if a in seen_aliases and seen_aliases[a] != key:
                raise ValueError(
                    t("states.duplicate_label", label=a, first=seen_aliases[a], second=key)
                )
            seen_aliases.setdefault(a, key)
        states.append(st)
    return StateModel(states)
