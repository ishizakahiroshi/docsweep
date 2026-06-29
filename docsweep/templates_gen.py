"""`docsweep new <type> <topic>` のテンプレ即生成。

規約（templates/CLAUDE.md）の必須セクション・H1 ラベルに沿った雛形を出す。
配置先は docs/local/ があればそこ、無ければ docs/ 直下。

新規生成時に frontmatter `due:` を初日から入れる（親 plan kanban-board-write-ops の §C4 §C2）。
オフセット日数は ``Config.due_default_offset_days``（``.docsweep.yaml`` の ``due:`` ブロック）で可変。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


@dataclass
class NewDoc:
    path: Path
    created: bool
    due: str | None = None


def _placement_dir(project_dir: Path) -> Path:
    local = project_dir / "docs" / "local"
    if local.is_dir():
        return local
    docs = project_dir / "docs"
    if docs.is_dir():
        return docs
    return project_dir


def _okf_frontmatter(
    *, doc_type: str, status: str, due: str | None, today: str | None = None
) -> str:
    """OKF（plan_okf-adoption_2026-06-29.md）準拠の frontmatter ブロックを返す。

    最低限のフィールド: ``type`` / ``status`` / ``tags: []`` / ``owner`` /
    ``review_status: draft`` / ``related: []`` / ``last_reviewed: <today>``。
    ``due:`` は呼び出し側が決める（plan/pending は付与、bugfix は新規時は付けない設計）。

    旧来の "due だけの最小 frontmatter" と異なり常に frontmatter ブロックを出力する。
    既存ファイル（frontmatter 無し）は触らないので後方互換は維持される（parser 側が
    H1 ラベル + ファイル名にフォールバックする）。
    """
    today = today or _today()
    lines = [
        "---",
        f"type: {doc_type}",
        f"status: {status}",
        "tags: []",
        "owner: ",
        "review_status: draft",
        "related: []",
        f"last_reviewed: {today}",
    ]
    if due:
        lines.append(f"due: {due}")
    lines.append("---")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def _plan_body(title: str, *, due: str | None = None) -> str:
    return (
        _okf_frontmatter(doc_type="plan", status="planned", due=due)
        + f"# [計画] {title}\n\n"
        "## context配分\n\n"
        "| C | 内容 | 種別 |\n|---|---|---|\n| C1 | <TODO> | plan |\n\n"
        "## 概要\n\n<TODO: 何をしようとしているか>\n"
    )


def _bugfix_body(title: str, *, due: str | None = None) -> str:
    # bugfix は新規時に `due:` を入れない（[様子見] 遷移時に AI / 人が後付け追記する想定）。
    # 引数 due は受け取るが、本ビルダーでは無視する（呼び出し側の一貫性のため）。
    _ = due
    return (
        _okf_frontmatter(doc_type="bugfix", status="in-progress", due=None)
        # 2026-06-23 改修: [対応中] を [実行中] に統合（active 廃止）。
        + f"# [実行中] {title}\n\n"
        "## 症状\n\n<TODO>\n\n## 根本原因\n\n<TODO>\n\n## 修正内容\n\n<TODO>\n\n"
        "## 変更ファイル\n\n<TODO>\n\n## 検証\n\n<TODO>\n\n## 備忘\n\n<TODO>\n"
    )


def _pending_body(title: str, *, due: str | None = None) -> str:
    return (
        _okf_frontmatter(doc_type="pending", status="pending", due=due)
        + f"# [保留] {title}\n\n"
        "## 概要\n\n<TODO: 何を止めたか>\n\n## 保留理由\n\n<TODO>\n\n## 着手条件\n\n- <TODO>\n"
    )


_BUILDERS = {"plan": _plan_body, "bugfix": _bugfix_body, "pending": _pending_body}


def _filename(doc_type: str, topic: str) -> str:
    topic = topic.strip().lower().replace(" ", "-")
    # パス区切り（/ \）と親参照（..）を除去し、生成先ディレクトリ外への書き込みを防ぐ。
    topic = re.split(r"[\\/]", topic)[-1].strip(". ") or "untitled"
    if doc_type == "bugfix":
        return f"bugfix_{topic}_{_today()}.md"
    return f"{doc_type}_{topic}.md"


def _resolve_initial_due(
    doc_type: str,
    *,
    due: str | None,
    offset_days: dict[str, int] | None,
    today: date | None = None,
) -> str | None:
    """初期 ``due`` を決める。

    優先順位:
    1. ``due`` が明示指定されていればそれをそのまま使う（"YYYY-MM-DD" 想定・検証は呼び出し側）。
    2. ``offset_days[doc_type]`` が設定されていれば ``today + N`` を返す。
    3. bugfix は新規時 due を付けない（呼び出し側で None 渡し or _bugfix_body 内で無視）。
    """
    if due is not None:
        return due
    if doc_type == "bugfix":
        # 新規 bugfix には初期 due を付けない（[様子見] 遷移時に追記する設計）。
        return None
    offsets = offset_days or {}
    n = offsets.get(doc_type)
    if n is None:
        return None
    base = today or date.today()
    return (base + timedelta(days=int(n))).isoformat()


def new_doc(
    doc_type: str,
    topic: str,
    *,
    project_dir: Path,
    title: str | None = None,
    due: str | None = None,
    offset_days: dict[str, int] | None = None,
) -> NewDoc:
    """テンプレ MD を新規生成して :class:`NewDoc` を返す。

    Args:
        doc_type: ``plan`` / ``bugfix`` / ``pending``。
        topic: ファイル名の ``<topic>`` 部（ケバブケース推奨）。
        project_dir: 配置先ベース（``docs/local/`` → ``docs/`` の順で解決）。
        title: H1 タイトル。省略時は ``topic`` を流用。
        due: 初期 due を直接指定（``YYYY-MM-DD``）。明示指定が最優先。
        offset_days: ``Config.due_default_offset_days``。``due`` 未指定時の自動計算に使う。
    """
    if doc_type not in _BUILDERS:
        raise ValueError(f"未知の種別 '{doc_type}'（plan|bugfix|pending）")
    out_dir = _placement_dir(project_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    base = _filename(doc_type, topic)
    path = out_dir / base
    # 衝突したら枝番。
    if path.exists():
        stem, suffix = path.stem, path.suffix
        n = 2
        while path.exists():
            path = out_dir / f"{stem}_{n}{suffix}"
            n += 1

    resolved_due = _resolve_initial_due(doc_type, due=due, offset_days=offset_days)
    body = _BUILDERS[doc_type](title or topic, due=resolved_due)
    path.write_text(body, encoding="utf-8")
    return NewDoc(path=path, created=True, due=resolved_due)
