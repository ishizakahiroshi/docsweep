"""`docsweep new <type> <topic>` のテンプレ即生成。

規約（templates/CLAUDE.md）の必須セクション・H1 ラベルに沿った雛形を出す。
配置先は docs/local/ があればそこ、無ければ docs/ 直下。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


@dataclass
class NewDoc:
    path: Path
    created: bool


def _placement_dir(project_dir: Path) -> Path:
    local = project_dir / "docs" / "local"
    if local.is_dir():
        return local
    docs = project_dir / "docs"
    if docs.is_dir():
        return docs
    return project_dir


def _plan_body(title: str) -> str:
    return (
        f"# [計画] {title}\n\n"
        "## context配分\n\n"
        "| C | 内容 | 種別 |\n|---|---|---|\n| C1 | <TODO> | plan |\n\n"
        "## 概要\n\n<TODO: 何をしようとしているか>\n"
    )


def _bugfix_body(title: str) -> str:
    return (
        f"# [対応中] {title}\n\n"
        "## 症状\n\n<TODO>\n\n## 根本原因\n\n<TODO>\n\n## 修正内容\n\n<TODO>\n\n"
        "## 変更ファイル\n\n<TODO>\n\n## 検証\n\n<TODO>\n\n## 備忘\n\n<TODO>\n"
    )


def _pending_body(title: str) -> str:
    return (
        f"# [保留] {title}\n\n"
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


def new_doc(doc_type: str, topic: str, *, project_dir: Path, title: str | None = None) -> NewDoc:
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

    body = _BUILDERS[doc_type](title or topic)
    path.write_text(body, encoding="utf-8")
    return NewDoc(path=path, created=True)
