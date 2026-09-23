"""エラーメッセージからドキュメントへの深リンク（UX W4 / P71）。

止まりやすい場所（YAML パース失敗・未知のサブコマンド・コンソール encoding・
規約違反）で「何を読めばいいか」を 1 行足す。docs/ は wheel に同梱されないので
リンク先は GitHub の URL にする（pip install した利用者でも辿れる）。

``DOCSWEEP_HINTS=0`` で抑止できる（``hints.hints_enabled`` と同じスイッチ）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .i18n import t

DOC_BASE = "https://github.com/ishizakahiroshi/docsweep/blob/main/"


@dataclass(frozen=True)
class DocLink:
    """1 つの help id に対応する読み先。"""

    help_id: str

    @property
    def doc(self) -> str:
        """リポジトリ相対のドキュメントパス（アンカー付き可）を表示言語で返す。

        読み先は言語ごとに terms.json の ``doc_links.doc.<help id>`` に置く（英語版がある
        文書は英語の表示言語で英語版の節へ飛ばす。無い文書は全言語で同じパス）。
        """
        return t(f"doc_links.doc.{self.help_id}")

    @property
    def hint(self) -> str:
        """1 行で「何をすればいいか」（messages.json の ``doc_links.<help id>``）。"""
        return t(f"doc_links.{self.help_id}")

    @property
    def url(self) -> str:
        return DOC_BASE + self.doc


LINKS: dict[str, DocLink] = {
    help_id: DocLink(help_id)
    for help_id in (
        "cli.unknown_command",
        "config.yaml_parse",
        "console.encoding",
        "naming.work_md",
        "closeout.parent_only",
        "states.label",
    )
}


def doc_hint(help_id: str, *, enabled: bool = True) -> str | None:
    """``help_id`` に対応する 2 行のヒント文字列を返す（無ければ ``None``）。"""
    if not enabled:
        return None
    link = LINKS.get(help_id)
    if link is None:
        return None
    # 「ヒント:」と「ヘルプ ID」の見出しも表示言語で出す（help_id 自体は機械向けの識別子）
    return (
        t("hints.line", text=link.hint)
        + "\n"
        + t("hints.doc_link_line", url=link.url, help_id=help_id)
    )


def known_ids() -> list[str]:
    return sorted(LINKS)
