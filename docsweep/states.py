"""状態モデル（states）— 単一正本。

設計の正本: docs/local/plan_state-tag-orthogonalization.md / plan_v0.1.0-product-requirements.md

config の ``states:`` を唯一の正本とし、ここから
「検出ロジック・自動 archive 可否・Web 表示・注入テンプレ文面」を全部導出する。
内蔵デフォルトを下に持ち、利用者は上書き・追加・言語追加だけで差分運用できる。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# サポートする検出言語（ラベル辞書のキー）。
LANGS = ("ja", "en")


@dataclass(frozen=True)
class State:
    """1 つの内部状態の定義。

    labels: 言語コード -> ブラケット内のラベル文字列（例 {"ja": "計画", "en": "Planned"}）。
    archive: archive 対象になりうるか（``[完了]``/``[廃止]`` のみ True）。
    auto_move: ``--auto`` で自動移送してよいか。watching は必ず False（寝かせ中＝守る）。
    """

    key: str
    labels: dict[str, str]
    archive: bool = False
    auto_move: bool = False
    color: str | None = None
    icon: str | None = None

    def label(self, lang: str = "ja") -> str:
        return self.labels.get(lang) or next(iter(self.labels.values()))

    def aliases(self) -> set[str]:
        """全言語のラベル文字列（小文字化）。検出時のエイリアス照合に使う。"""
        return {v.strip().lower() for v in self.labels.values() if v}


# 内蔵デフォルト（何も書かなければこれで動く）。
# plan_v0.1.0-product-requirements.md「★ 状態モデル」の表に対応。
DEFAULT_STATES: tuple[State, ...] = (
    State("planned", {"ja": "計画", "en": "Planned"}, archive=False, auto_move=False),
    State("in-progress", {"ja": "実行中", "en": "In Progress"}, archive=False, auto_move=False),
    State("watching", {"ja": "様子見", "en": "Watching"}, archive=False, auto_move=False),
    State("done", {"ja": "完了", "en": "Done"}, archive=True, auto_move=True),
    State("discarded", {"ja": "廃止", "en": "Discarded"}, archive=True, auto_move=True),
    State("pending", {"ja": "保留", "en": "Pending"}, archive=False, auto_move=False),
    # bugfix の調査・修正中ラベル。plan の in-progress と同じ扱い（自動移送しない）。
    State("active", {"ja": "対応中", "en": "Active"}, archive=False, auto_move=False),
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

    def match(self, token: str | None) -> State | None:
        """ラベル文字列または内部キーから state を引く（言語非依存）。

        1. まず完全一致（既存挙動）
        2. ダメなら末尾一致を試す。`[v0.1.0 完了]` のようなバージョン情報付きラベルや
           `[draft 計画]` のような注釈付きラベルを救うため。誤検出を抑えるため、
           「末尾に alias があり、その直前が非英数字（空白等）」のときだけ採用する。
        """
        if not token:
            return None
        t = token.strip().lower()
        # 1. 完全一致
        if t in self._by_alias:
            return self._by_alias[t]
        # 2. 末尾一致（長い alias から順に試して最初に当たったものを採用）
        for alias in sorted(self._by_alias.keys(), key=len, reverse=True):
            if not alias:
                continue
            if t.endswith(alias) and len(t) > len(alias):
                boundary = t[-len(alias) - 1]
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
            raise ValueError(f"state '{key}' に labels がありません")
        # 重複は後勝ちで dict 上書きされ、ラベルが別 state（archive 可否が違う）に解決されて
        # 静かに誤判定するため、設定構築時に fail-fast で弾く。
        if key in seen_keys:
            raise ValueError(f"state key '{key}' が重複しています")
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
                    f"ラベル '{a}' が state '{seen_aliases[a]}' と '{key}' で重複しています"
                )
            seen_aliases.setdefault(a, key)
        states.append(st)
    return StateModel(states)
