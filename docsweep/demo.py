"""``docsweep demo`` — 使い捨てのサンプル project を作る（UX W4 / P70）。

記事・SNS デモ・初回体験用。**既存のプロジェクトには一切触らない**。

- 既定は OS の一時ディレクトリ配下に新しいフォルダを作る（`--dir` で明示もできる）。
- 生成するのは `docs/local/` 配下の md と `.docsweep.yaml` だけ。git 管理はしない。
- グローバル config の `roots` へは登録しない（`~/.docsweep/injected.json` を汚さない）。
  デモを見るときは `--root <生成先>` を明示して各コマンドを叩く。
- 中身は状態が散らばった 8 本。overdue / 今日 / 未来 / 期日なし / 保留 / 完了 が
  1 画面で揃うので、`triage` も看板も「空っぽ」にならない。
- 文書は表示言語（``--lang`` / ``DOCSWEEP_LANG`` / OS）で作り、``.docsweep.yaml`` に
  ``lang`` を書いて、そのデモ project の文書の言語を固定する。
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .doc_vocab import column, heading
from .i18n import current_lang, normalize_lang, t, texts
from .states import StateModel

DEMO_DIR_PREFIX = "docsweep-demo-"


@dataclass(frozen=True)
class _Seed:
    name: str
    state: str
    due_offset: int | None

    def text(self, part: str, lang: str) -> str:
        """タイトル・概要（``part`` = ``title`` / ``summary``）。サンプルの中身なので
        言語ごとに ``docsweep/i18n/locales/<言語>/demo.json`` の ``demo.seed.<ファイル名の stem>.*`` に置く。"""
        return t(f"demo.seed.{Path(self.name).stem}.{part}", lang=lang)


# type は必ずファイル名の接頭辞から取る（frontmatter と filename の食い違い warning を出さない）。
_SEEDS: tuple[_Seed, ...] = (
    _Seed("plan_search-relevance.md", "in-progress", -3),
    _Seed("plan_onboarding-flow.md", "planned", 0),
    _Seed("bugfix_login-timeout_2026-05-02.md", "in-progress", -9),
    _Seed("bugfix_csv-encoding_2026-05-20.md", "watching", 5),
    _Seed("plan_billing-rework.md", "planned", 21),
    _Seed("pending_mobile-app.md", "pending", None),
    _Seed("pending_i18n.md", "pending", None),
    _Seed("plan_weekly-report.md", "done", None),
)

# 表と完了条件・検証の中身（サンプルなので空に近い）は demo.json の ``demo.table_rows`` /
# ``demo.completion_item`` / ``demo.verification_item``、設定のコメントは ``demo.config_comment``。


@dataclass
class DemoResult:
    root: Path
    files: list[Path]
    created_dir: bool

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "files": [str(p) for p in self.files],
            "created_dir": self.created_dir,
            "next": [
                f"docsweep scan --root {self.root}",
                f"docsweep triage --root {self.root}",
                f"docsweep serve --root {self.root}",
            ],
        }


def _doc_type(name: str) -> str:
    """ファイル名の接頭辞から type を決める（frontmatter と食い違わせない）。"""
    return name.split("_", 1)[0]


def _frontmatter(doc_type: str, state: str, due: str | None, today: date) -> str:
    lines = [
        "---",
        f"type: {doc_type}",
        "status: draft",
        f"docsweep_state: {state}",
        "tags: [demo]",
        "owner: ",
        "review_status: draft",
        "related: []",
        f"last_reviewed: {today.isoformat()}",
    ]
    if due:
        lines.append(f"due: {due}")
    lines.append("---")
    return "\n".join(lines)


def _body(doc_type: str, summary: str, lang: str) -> str:
    # 概要の見出しは種別の規約に合わせる（bugfix は「症状」。scan の概要抽出がこれを読む）
    summary_key = "symptoms" if doc_type == "bugfix" else "summary"
    header = " | ".join(["C"] + [column(key, lang) for key in ("status", "description", "notes")])
    rows = "\n".join(texts("demo.table_rows", lang=lang))
    return (
        f"\n## {heading(summary_key, lang)}\n\n"
        f"{summary}\n\n"
        f"## {heading('context', lang)}\n\n"
        f"| {header} |\n"
        "|---|---|---|---|\n"
        f"{rows}\n\n"
        f"## {heading('completion', lang)}\n\n"
        f"{t('demo.completion_item', lang=lang)}\n\n"
        f"## {heading('verification', lang)}\n\n"
        f"{t('demo.verification_item', lang=lang)}\n"
    )


def build_demo(
    target: Path | None = None, *, today: date | None = None, lang: str | None = None
) -> DemoResult:
    """サンプル project を作って ``DemoResult`` を返す。

    Args:
        target: 生成先。``None`` なら一時ディレクトリに新規作成する。
        today: due 計算の基準日（テスト用）。
        lang: 文書の言語（対応言語のコード）。省く・対応外なら表示言語。
    """
    today = today or date.today()
    lang = normalize_lang(lang) or current_lang()
    created_dir = False
    if target is None:
        target = Path(tempfile.mkdtemp(prefix=DEMO_DIR_PREFIX))
        created_dir = True
    else:
        target = Path(target)
        created_dir = not target.exists()
        target.mkdir(parents=True, exist_ok=True)

    work = target / "docs" / "local"
    work.mkdir(parents=True, exist_ok=True)
    (target / ".docsweep.yaml").write_text(
        f"{t('demo.config_comment', lang=lang)}\nwork_dir: docs/local\nwork_policy: private\nlang: {lang}\n",
        encoding="utf-8",
    )

    states = StateModel()
    written: list[Path] = []
    for seed in _SEEDS:
        due = (
            (today + timedelta(days=seed.due_offset)).isoformat()
            if seed.due_offset is not None
            else None
        )
        state = states.by_key(seed.state)
        label = state.label(lang) if state else seed.state
        doc_type = _doc_type(seed.name)
        text = (
            _frontmatter(doc_type, seed.state, due, today)
            + f"\n\n# [{label}] {seed.text('title', lang)}\n"
            + _body(doc_type, seed.text("summary", lang), lang)
        )
        p = work / seed.name
        p.write_text(text, encoding="utf-8")
        written.append(p)

    return DemoResult(root=target, files=written, created_dir=created_dir)


def render_demo(result: DemoResult) -> str:
    lines = [
        t("demo.created", root=result.root),
        t("demo.file_counts", count=len(result.files)),
        "",
        t("demo.try_commands"),
    ]
    for cmd in result.to_dict()["next"]:
        lines.append(f"  $ {cmd}")
    lines += [
        "",
        t("demo.cleanup"),
    ]
    return "\n".join(lines)


def demo_json(result: DemoResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
