"""エラーメッセージからドキュメントへの深リンク（UX W4 / P71）。

止まりやすい場所（YAML パース失敗・未知のサブコマンド・コンソール encoding・
規約違反）で「何を読めばいいか」を 1 行足す。docs/ は wheel に同梱されないので
リンク先は GitHub の URL にする（pip install した利用者でも辿れる）。

``DOCSWEEP_HINTS=0`` で抑止できる（``hints.hints_enabled`` と同じスイッチ）。
"""

from __future__ import annotations

from dataclasses import dataclass

DOC_BASE = "https://github.com/ishizakahiroshi/docsweep/blob/main/"


@dataclass(frozen=True)
class DocLink:
    """1 つの help id に対応する読み先。"""

    doc: str
    """リポジトリ相対のドキュメントパス（アンカー付き可）。"""

    hint: str
    """1 行で「何をすればいいか」。"""

    @property
    def url(self) -> str:
        return DOC_BASE + self.doc


LINKS: dict[str, DocLink] = {
    "cli.unknown_command": DocLink(
        "README.md#使い方",
        "サブコマンド名を確認してください（`docsweep cookbook` に状況別の例があります）",
    ),
    "config.yaml_parse": DocLink(
        "templates/.docsweep.yaml",
        ".docsweep.yaml の書式を確認してください（テンプレートが正本です）",
    ),
    "console.encoding": DocLink(
        "README.md#windows",
        "PYTHONIOENCODING=utf-8 を指定するか、--json 以外の出力を使ってください",
    ),
    "naming.work_md": DocLink(
        "docs/conventions.md",
        "作業 md の命名は plan_ / bugfix_ / pending_ の接頭辞で決まります",
    ),
    "closeout.parent_only": DocLink(
        "docs/conventions.md",
        "closeout-check は親子構造を持つ plan_*.md だけを対象にします",
    ),
    "states.label": DocLink(
        "docs/conventions.md",
        "H1 のステータスラベルは docsweep/states.py の DEFAULT_STATES が正本です",
    ),
}


def doc_hint(help_id: str, *, enabled: bool = True) -> str | None:
    """``help_id`` に対応する 2 行のヒント文字列を返す（無ければ ``None``）。"""
    if not enabled:
        return None
    link = LINKS.get(help_id)
    if link is None:
        return None
    return f"hint: {link.hint}\n  → {link.url}  (help id: {help_id})"


def known_ids() -> list[str]:
    return sorted(LINKS)
