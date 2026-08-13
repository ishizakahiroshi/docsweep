"""`docsweep new <type> <topic>` のテンプレ即生成。

規約（templates/CLAUDE.md）の必須セクション・H1 ラベルに沿った雛形を出す。
配置先は Config.work_dir（既定 docs/local/）で一元解決する。プロジェクトに docs/ が
存在するかどうかで保存先を暗黙に変えない。

新規生成時に frontmatter `due:` を初日から入れる（親 plan kanban-board-write-ops の §C4 §C2）。
オフセット日数は ``Config.due_default_offset_days``（``.docsweep.yaml`` の ``due:`` ブロック）で可変。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import Config, resolve_work_dir
from .okf import bundled_okf_profile
from .work_queue import ensure_write_allowed


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


@dataclass
class NewDoc:
    path: Path
    created: bool
    due: str | None = None


def _placement_dir(
    project_dir: Path,
    *,
    config: Config | None = None,
    work_dir: str | None = None,
) -> Path:
    # 直接 API 呼び出し（旧利用者）には従来の docs/ fallback を残す。CLI / MCP は
    # 必ず Config を渡すため、設定済み work_dir では docs/ の有無で保存先を変えない。
    if config is None and work_dir is None:
        local = project_dir / "docs" / "local"
        if local.is_dir():
            return local
        if (project_dir / "docs").is_dir():
            return project_dir / "docs"
    raw = work_dir if work_dir is not None else (config.work_dir if config is not None else None)
    return resolve_work_dir(project_dir, raw)


def _okf_frontmatter(
    *, doc_type: str, state: str, due: str | None, today: str | None = None
) -> str:
    """OKF（plan_okf-adoption_2026-06-29.md）準拠の frontmatter ブロックを返す。

    最低限のフィールド: ``type`` / OKF ``status`` / ``docsweep_state`` / ``tags: []`` / ``owner`` /
    ``review_status: draft`` / ``related: []`` / ``last_reviewed: <today>``。
    ``due:`` は呼び出し側が決める（plan/pending は付与、bugfix は新規時は付けない設計）。

    旧来の "due だけの最小 frontmatter" と異なり常に frontmatter ブロックを出力する。
    既存ファイル（frontmatter 無し）は触らないので後方互換は維持される（parser 側が
    H1 ラベル + ファイル名にフォールバックする）。
    """
    today = today or _today()
    lifecycle_status = bundled_okf_profile().lifecycle_default
    lines = [
        "---",
        f"type: {doc_type}",
        f"status: {lifecycle_status}",
        f"docsweep_state: {state}",
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
        _okf_frontmatter(doc_type="plan", state="planned", due=due)
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
        _okf_frontmatter(doc_type="bugfix", state="in-progress", due=None)
        # 2026-06-23 改修: [対応中] を [実行中] に統合（active 廃止）。
        + f"# [実行中] {title}\n\n"
        "## 症状\n\n<TODO>\n\n## 根本原因\n\n<TODO>\n\n## 修正内容\n\n<TODO>\n\n"
        "## 変更ファイル\n\n<TODO>\n\n## 検証\n\n<TODO>\n\n## 備忘\n\n<TODO>\n"
    )


def _pending_body(title: str, *, due: str | None = None) -> str:
    return (
        _okf_frontmatter(doc_type="pending", state="pending", due=due)
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
    config: Config | None = None,
    work_dir: str | None = None,
    allow_sensitive: bool = False,
) -> NewDoc:
    """テンプレ MD を新規生成して :class:`NewDoc` を返す。

    Args:
        doc_type: ``plan`` / ``bugfix`` / ``pending``。
        topic: ファイル名の ``<topic>`` 部（ケバブケース推奨）。
        project_dir: 配置先プロジェクトのルート。
        title: H1 タイトル。省略時は ``topic`` を流用。
        due: 初期 due を直接指定（``YYYY-MM-DD``）。明示指定が最優先。
        offset_days: ``Config.due_default_offset_days``。``due`` 未指定時の自動計算に使う。
    """
    if doc_type not in _BUILDERS:
        raise ValueError(f"未知の種別 '{doc_type}'（plan|bugfix|pending）")
    out_dir = _placement_dir(project_dir, config=config, work_dir=work_dir)
    resolved_due = _resolve_initial_due(doc_type, due=due, offset_days=offset_days)
    body = _BUILDERS[doc_type](title or topic, due=resolved_due)
    if config is not None:
        ensure_write_allowed(
            config=config,
            project_dir=project_dir,
            target_dir=out_dir,
            content=body,
            allow_sensitive=allow_sensitive,
        )
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

    path.write_text(body, encoding="utf-8")
    return NewDoc(path=path, created=True, due=resolved_due)


def _patch_related(path: Path, related_names: list[str]) -> None:
    """frontmatter related: を差し替え（親/子スキャフォールド用・最小実装）。"""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return
    end = text.find("\n---", 3)
    if end < 0:
        return
    fm = text[4:end]
    body = text[end + 4:]
    lines = fm.splitlines()
    out_lines: list[str] = []
    replaced = False
    items = ", ".join(related_names)
    for line in lines:
        if line.strip().startswith("related:"):
            out_lines.append(f"related: [{items}]")
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        out_lines.append(f"related: [{items}]")
    path.write_text("---\n" + "\n".join(out_lines) + "\n---" + body, encoding="utf-8")


def _patch_scalar(path: Path, field: str, value: str) -> None:
    """frontmatter の scalar extension を最小変更で追記する。"""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return
    end = text.find("\n---", 3)
    if end < 0:
        return
    fm = text[4:end]
    body = text[end + 4:]
    lines = fm.splitlines()
    prefix = f"{field}:"
    for index, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[index] = f"{field}: {value}"
            break
    else:
        lines.append(f"{field}: {value}")
    path.write_text("---\n" + "\n".join(lines) + "\n---" + body, encoding="utf-8")


def new_split_plans(
    topic: str,
    *,
    n: int,
    project_dir: Path,
    title: str | None = None,
    due: str | None = None,
    offset_days: dict[str, int] | None = None,
    config: Config | None = None,
    work_dir: str | None = None,
    allow_sensitive: bool = False,
) -> list[NewDoc]:
    """親 plan + 子 N 本を生成し related と方向付き親参照を付ける（UX W3 / P26）。"""
    if n < 1 or n > 20:
        raise ValueError("--split は 1〜20")
    parent_title = title or topic
    created: list[NewDoc] = []
    try:
        parent = new_doc(
            "plan", topic,
            project_dir=project_dir, title=parent_title,
            due=due, offset_days=offset_days,
            config=config, work_dir=work_dir, allow_sensitive=allow_sensitive,
        )
        created.append(parent)
        children: list[NewDoc] = []
        child_names: list[str] = []
        for i in range(1, n + 1):
            child_topic = f"{topic}-c{i}"
            child = new_doc(
                "plan", child_topic,
                project_dir=project_dir,
                title=f"{parent_title} C{i}",
                due=due, offset_days=offset_days,
                config=config, work_dir=work_dir, allow_sensitive=allow_sensitive,
            )
            created.append(child)
            children.append(child)
            child_names.append(child.path.name)
        # parent related → children
        _patch_related(parent.path, child_names)
        # each child related → parent
        # Keep the repo-relative reference lexical.  ``docs/local`` may be a
        # junction whose resolved target is outside the project root; resolving it
        # here would make a valid generated child relation fail at creation time.
        parent_abs = Path(os.path.abspath(os.path.normpath(os.fspath(parent.path))))
        project_abs = Path(os.path.abspath(os.path.normpath(os.fspath(project_dir))))
        parent_ref = parent_abs.relative_to(project_abs).as_posix()
        for ch in children:
            _patch_related(ch.path, [parent.path.name])
            _patch_scalar(ch.path, "docsweep_parent", parent_ref)
        return created
    except Exception:
        # Every path above is freshly allocated by ``new_doc`` (collisions get a
        # suffix), so removing only this call's created files cannot touch user data.
        for doc in reversed(created):
            try:
                if doc.created and doc.path.is_file():
                    doc.path.unlink()
            except OSError:
                # Preserve the original failure; the caller reports the partial
                # cleanup as a follow-up warning when provenance registration fails.
                pass
        raise
