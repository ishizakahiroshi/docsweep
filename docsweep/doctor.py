"""``docsweep doctor`` — 環境ヘルスチェック（UX W1 / P3）。

config / roots / index 鮮度 / inject / extras / MCP ヒントを checklist で返す。
人間向け表と ``--json`` の両対応。修復コマンド文字列を各項目に載せる。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from .config import (
    GLOBAL_CONFIG_PATH,
    Config,
    archive_dir_for_project,
    load_config,
    project_work_settings,
    resolve_work_dir,
)
from .i18n import t
from .index import db_path
from .inject import GUIDANCE_PATH, MANIFEST_PATH, list_injected
from .work_queue import check_work_queue

Status = Literal["ok", "warn", "fail", "hint"]


@dataclass
class CheckItem:
    id: str
    status: Status
    label: str
    detail: str = ""
    fix: str | None = None  # 修復に使えるコマンド例


@dataclass
class DoctorReport:
    generated_at: str
    ok: bool
    items: list[CheckItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "ok": self.ok,
            "items": [asdict(i) for i in self.items],
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _index_age_hours(db_path: Path) -> float | None:
    """index.db の最終更新からの経過時間（時間）。無ければ None。"""
    if not db_path.is_file():
        return None
    try:
        mtime = db_path.stat().st_mtime
    except OSError:
        return None
    age_sec = max(0.0, datetime.now(timezone.utc).timestamp() - mtime)
    return age_sec / 3600.0


def _max_project_last_scanned(db_path: Path) -> str | None:
    if not db_path.is_file():
        return None
    try:
        # URI query/fragment characters are data in a Windows filename, not
        # SQLite URI syntax. Keep drive/separators while quoting #, ?, and %.
        uri_path = quote(Path(db_path).as_posix(), safe="/:")
        conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT MAX(last_scanned) FROM projects WHERE last_scanned IS NOT NULL"
            ).fetchone()
            return row[0] if row and row[0] else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def run_doctor(
    *,
    config: Config | None = None,
    global_path: Path | None = None,
    index_db: Path | None = None,
    warn_index_hours: float = 24.0,
    fail_index_hours: float = 168.0,
    project_dir: Path | None = None,
) -> DoctorReport:
    """ヘルスチェックを実行して DoctorReport を返す。"""
    gpath = global_path or GLOBAL_CONFIG_PATH
    items: list[CheckItem] = []

    # 1. config
    if gpath.is_file():
        items.append(CheckItem(
            id="config",
            status="ok",
            label="config.yaml",
            detail=str(gpath),
        ))
    else:
        items.append(CheckItem(
            id="config",
            status="warn",
            label="config.yaml",
            detail=t("doctor.config_not_found", path=gpath),
            fix="python -m docsweep init",
        ))

    # 2. roots
    cfg = config
    if cfg is None:
        try:
            cfg = load_config(global_path=gpath if gpath.is_file() else gpath)
        except Exception as e:  # noqa: BLE001
            items.append(CheckItem(
                id="roots",
                status="fail",
                label=t("doctor.label_roots"),
                detail=t("doctor.config_load_failed", error=e),
                fix="python -m docsweep init",
            ))
            cfg = None

    if cfg is not None:
        if not cfg.roots:
            items.append(CheckItem(
                id="roots",
                status="warn",
                label=t("doctor.label_roots"),
                detail=t("doctor.roots_empty"),
                fix=t("doctor.roots_empty_fix"),
            ))
        else:
            missing = [str(r) for r in cfg.roots if not Path(r).exists()]
            if missing:
                items.append(CheckItem(
                    id="roots",
                    status="fail",
                    label=t("doctor.label_roots"),
                    detail=t("doctor.roots_missing", paths=", ".join(missing)),
                    fix=t("doctor.roots_missing_fix"),
                ))
            else:
                items.append(CheckItem(
                    id="roots",
                    status="ok",
                    label=t("doctor.label_roots"),
                    detail=t(
                        "doctor.roots_ok",
                        count=len(cfg.roots),
                        paths=", ".join(str(r) for r in cfg.roots[:5]),
                    ),
                ))

        # 2b. work queue privacy.  This is read-only: it never edits .gitignore,
        # untracks files, moves queue entries, or rewrites history.
        candidates: list[Path] = []
        if project_dir is not None:
            candidates = [Path(project_dir).resolve()]
        elif cfg.project_dir is not None:
            candidates = [cfg.project_dir.resolve()]
        else:
            for raw_root in cfg.roots:
                root = Path(raw_root).resolve()
                if any((root / marker).exists() for marker in cfg.project_markers):
                    candidates.append(root)
                if root.is_dir():
                    try:
                        candidates.extend(
                            child
                            for child in root.iterdir()
                            if child.is_dir()
                            and any((child / marker).exists() for marker in cfg.project_markers)
                        )
                    except OSError:
                        pass
        if not candidates:
            items.append(CheckItem(
                id="work_queue",
                status="hint",
                label=t("doctor.label_work_queue"),
                detail=t("doctor.work_queue_no_project"),
                fix="python -m docsweep doctor --project-dir <project>",
            ))
        else:
            queue_errors: list[str] = []
            queue_warnings: list[str] = []
            seen_projects: set[str] = set()
            for candidate in candidates:
                key = candidate.as_posix().casefold()
                if key in seen_projects:
                    continue
                seen_projects.add(key)
                try:
                    effective = load_config(project_dir=candidate, global_path=gpath)
                    queue = resolve_work_dir(candidate, effective.work_dir)
                    result = check_work_queue(
                        config=effective,
                        project_dir=candidate,
                        target_dir=queue,
                    )
                    queue_errors.extend(f"{candidate.name}: {err}" for err in result.errors)
                    queue_warnings.extend(f"{candidate.name}: {warning}" for warning in result.warnings)
                    _work_dir, work_policy, _secret_policy = project_work_settings(candidate, effective)
                    if work_policy == "private":
                        archive_raw = archive_dir_for_project(candidate, effective)
                        archive_candidate = Path(archive_raw)
                        archive = (
                            archive_candidate.resolve()
                            if archive_candidate.is_absolute()
                            else resolve_work_dir(candidate, archive_raw)
                        )
                        try:
                            archive.relative_to(queue)
                        except ValueError:
                            queue_warnings.append(
                                t("doctor.archive_outside_queue", project=candidate.name)
                            )
                except (OSError, ValueError, PermissionError) as exc:
                    queue_errors.append(t("doctor.work_queue_unresolved", project=candidate.name))
            if queue_errors:
                items.append(CheckItem(
                    id="work_queue",
                    status="fail",
                    label=t("doctor.label_work_queue"),
                    detail="; ".join(queue_errors[:5]),
                    fix=t("doctor.work_queue_fail_fix"),
                ))
            elif queue_warnings:
                items.append(CheckItem(
                    id="work_queue",
                    status="warn",
                    label=t("doctor.label_work_queue"),
                    detail="; ".join(queue_warnings[:5]),
                    fix=t("doctor.work_queue_warn_fix"),
                ))
            else:
                items.append(CheckItem(
                    id="work_queue",
                    status="ok",
                    label=t("doctor.label_work_queue"),
                    detail=t("doctor.work_queue_ok", count=len(seen_projects)),
                ))

    # 3. index
    db = index_db or db_path()
    age_h = _index_age_hours(db)
    last = _max_project_last_scanned(db)
    if age_h is None:
        items.append(CheckItem(
            id="index",
            status="warn",
            label="index.db",
            detail=t("doctor.index_missing", path=db),
            fix="python -m docsweep index-sync",
        ))
    elif age_h >= fail_index_hours:
        items.append(CheckItem(
            id="index",
            status="fail",
            label="index.db",
            detail=t("doctor.index_very_old", hours=age_h, last=last or "-", path=db),
            fix="python -m docsweep index-sync",
        ))
    elif age_h >= warn_index_hours:
        items.append(CheckItem(
            id="index",
            status="warn",
            label="index.db",
            detail=t("doctor.index_old", hours=age_h, last=last or "-", path=db),
            fix="python -m docsweep index-sync",
        ))
    else:
        items.append(CheckItem(
            id="index",
            status="ok",
            label="index.db",
            detail=t("doctor.index_fresh", hours=age_h, last=last or "-", path=db),
        ))

    # 4. inject
    injected = list_injected()
    guidance_ok = GUIDANCE_PATH.is_file()
    if injected or guidance_ok:
        detail_parts = []
        if guidance_ok:
            detail_parts.append(t("doctor.inject_guidance", path=GUIDANCE_PATH))
        if injected:
            detail_parts.append(t("doctor.inject_manifest_entries", count=len(injected)))
        if MANIFEST_PATH.is_file():
            detail_parts.append(t("doctor.inject_manifest", path=MANIFEST_PATH))
        items.append(CheckItem(
            id="inject",
            status="ok",
            label=t("doctor.label_inject"),
            detail=" | ".join(detail_parts),
        ))
    else:
        items.append(CheckItem(
            id="inject",
            status="hint",
            label=t("doctor.label_inject"),
            detail=t("doctor.inject_missing"),
            fix="python -m docsweep inject --global",
        ))

    # 5. extras
    extras = {
        "fastapi": _has_module("fastapi"),
        "jinja2": _has_module("jinja2"),
        "questionary": _has_module("questionary"),
        "mcp": _has_module("mcp"),
    }
    missing_web = [k for k in ("fastapi", "jinja2") if not extras[k]]
    if missing_web:
        items.append(CheckItem(
            id="extras_web",
            status="hint",
            label=t("doctor.label_extras_web"),
            detail=t("doctor.extras_missing", names=", ".join(missing_web)),
            fix="pip install 'docsweep[web]'",
        ))
    else:
        items.append(CheckItem(
            id="extras_web",
            status="ok",
            label=t("doctor.label_extras_web"),
            detail=t("doctor.extras_web_ok"),
        ))
    if not extras["mcp"]:
        items.append(CheckItem(
            id="extras_mcp",
            status="hint",
            label=t("doctor.label_extras_mcp"),
            detail=t("doctor.extras_mcp_missing"),
            fix="pip install 'docsweep[mcp]'",
        ))
    else:
        items.append(CheckItem(
            id="extras_mcp",
            status="ok",
            label=t("doctor.label_extras_mcp"),
            detail=t("doctor.extras_mcp_ok"),
        ))

    # 6. MCP 登録ヒント（検査は軽く・存在だけ）
    claude_cfg = Path.home() / ".claude" / "claude_desktop_config.json"
    # Claude Code は settings 系が別。ヒントとして既知パスを列挙。
    items.append(CheckItem(
        id="mcp_hint",
        status="hint",
        label=t("doctor.mcp_hint_label"),
        detail=t("doctor.mcp_hint_detail", path=claude_cfg),
        fix="python -m docsweep mcp --help",
    ))

    ok = not any(i.status == "fail" for i in items)
    return DoctorReport(generated_at=_now_iso(), ok=ok, items=items)


def _pad(text: str, width: int) -> str:
    """表示幅（全角は 2 桁）で ``width`` まで右を空白で埋める。

    ``str.ljust`` は文字数で数えるので、日本語の見出しや状態名だと列がずれる。
    """
    used = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)
    return text + " " * max(0, width - used)


def format_human(report: DoctorReport) -> str:
    """人間向けの表テキスト。見出し・状態名・項目名は表示言語で出す（id / status は JSON 側）。"""
    lines = [
        f"docsweep doctor | {_status_emoji(report.ok)} "
        f"{'OK' if report.ok else t('doctor.needs_attention')} | {report.generated_at}",
        "",
        f"{_pad(t('doctor.header_status'), 6)}  {_pad(t('doctor.header_check'), 14)}  "
        f"{t('doctor.header_detail')}",
        "-" * 72,
    ]
    for it in report.items:
        status = _pad(t(f"doctor.status_{it.status}"), 6)
        lines.append(f"{status}  {_pad(it.label, 14)}  {it.detail}")
        if it.fix:
            lines.append(f"{'':6}  {_pad(t('doctor.fix_label'), 14)}  {it.fix}")
    return "\n".join(lines) + "\n"


def _status_emoji(ok: bool) -> str:
    return "OK" if ok else "!!"
