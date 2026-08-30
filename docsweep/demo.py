"""``docsweep demo`` — 使い捨てのサンプル project を作る（UX W4 / P70）。

記事・SNS デモ・初回体験用。**既存のプロジェクトには一切触らない**。

- 既定は OS の一時ディレクトリ配下に新しいフォルダを作る（`--dir` で明示もできる）。
- 生成するのは `docs/local/` 配下の md と `.docsweep.yaml` だけ。git 管理はしない。
- グローバル config の `roots` へは登録しない（`~/.docsweep/injected.json` を汚さない）。
  デモを見るときは `--root <生成先>` を明示して各コマンドを叩く。
- 中身は状態が散らばった 8 本。overdue / 今日 / 未来 / 期日なし / 保留 / 完了 が
  1 画面で揃うので、`triage` も看板も「空っぽ」にならない。
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

DEMO_DIR_PREFIX = "docsweep-demo-"

# (ファイル名, H1 ラベル, タイトル, docsweep_state, due オフセット日 or None, 概要)
# type は必ずファイル名の接頭辞から取る（frontmatter と filename の食い違い warning を出さない）。
_SEEDS: list[tuple[str, str, str, str, int | None, str]] = [
    ("plan_search-relevance.md", "[実行中]", "検索の関連度を作り直す", "in-progress", -3,
     "全文検索の並びが古い順のままで、探しているものが下に沈む。"),
    ("plan_onboarding-flow.md", "[計画]", "初回オンボーディングの導線", "planned", 0,
     "初回起動から最初の価値到達までが 5 手かかっている。"),
    ("bugfix_login-timeout_2026-05-02.md", "[実行中]", "ログインが 30 秒で切れる", "in-progress", -9,
     "セッション延長が効かず、作業中にログイン画面へ戻される。"),
    ("bugfix_csv-encoding_2026-05-20.md", "[様子見]", "CSV 取り込みの文字化け", "watching", 5,
     "cp932 の CSV を取り込むと一部の列が化ける。直したが再発を見ている。"),
    ("plan_billing-rework.md", "[計画]", "課金まわりの作り直し", "planned", 21,
     "プラン変更の日割りが仕様化されていない。"),
    ("pending_mobile-app.md", "[保留]", "モバイルアプリを出すかの判断", "pending", None,
     "着手条件は Web の月間利用が安定してから。"),
    ("pending_i18n.md", "[保留]", "多言語対応の範囲を決める", "pending", None,
     "英語だけ先に出すか、最初から 3 言語そろえるか。"),
    ("plan_weekly-report.md", "[完了]", "週次レポートの自動生成", "done", None,
     "毎週月曜に前週分をまとめて配信するところまで完了。"),
]

_CONFIG_YAML = """# docsweep demo project（使い捨て）
work_dir: docs/local
work_policy: private
"""


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


def _body(title: str, summary: str) -> str:
    return (
        "\n## 概要\n\n"
        f"{summary}\n\n"
        "## context配分\n\n"
        "| C | 種別 | 内容 | 備考/注意点 |\n"
        "|---|---|---|---|\n"
        "| C1 | planned | 現状を実測する | デモ用のサンプルなので中身は空 |\n"
        "| C2 | planned | 直す | |\n\n"
        "## 完了条件\n\n"
        "- [ ] デモ用のサンプルなので完了条件は入っていない\n\n"
        "## 検証\n\n"
        "- [ ] 未実施\n"
    )


def build_demo(target: Path | None = None, *, today: date | None = None) -> DemoResult:
    """サンプル project を作って ``DemoResult`` を返す。

    Args:
        target: 生成先。``None`` なら一時ディレクトリに新規作成する。
        today: due 計算の基準日（テスト用）。
    """
    today = today or date.today()
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
    (target / ".docsweep.yaml").write_text(_CONFIG_YAML, encoding="utf-8")

    written: list[Path] = []
    for name, label, title, state, offset, summary in _SEEDS:
        due = (today + timedelta(days=offset)).isoformat() if offset is not None else None
        text = (
            _frontmatter(_doc_type(name), state, due, today)
            + f"\n\n# {label} {title}\n"
            + _body(title, summary)
        )
        p = work / name
        p.write_text(text, encoding="utf-8")
        written.append(p)

    return DemoResult(root=target, files=written, created_dir=created_dir)


def render_demo(result: DemoResult) -> str:
    lines = [
        f"demo project を作りました: {result.root}",
        f"  md {len(result.files)} 本 / .docsweep.yaml 1 本",
        "",
        "そのまま試すコマンド（--root を付ける限り、ほかのプロジェクトには触りません）:",
    ]
    for cmd in result.to_dict()["next"]:
        lines.append(f"  $ {cmd}")
    lines += [
        "",
        "使い終わったらフォルダごと消してかまいません（グローバル設定には登録していません）。",
    ]
    return "\n".join(lines)


def demo_json(result: DemoResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
