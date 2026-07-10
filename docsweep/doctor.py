"""``docsweep doctor`` — 環境ヘルスチェック（UX W1 / P3）。

config / roots / index 鮮度 / inject / extras / MCP ヒントを checklist で返す。
人間向け表と ``--json`` の両対応。修復コマンド文字列を各項目に載せる。
"""

from __future__ import annotations

import importlib.util
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .config import GLOBAL_CONFIG_PATH, Config, load_config
from .index import db_path
from .inject import GUIDANCE_PATH, MANIFEST_PATH, list_injected

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
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
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
            detail=f"見つかりません: {gpath}",
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
                label="roots",
                detail=f"config 読み込み失敗: {e}",
                fix="python -m docsweep init",
            ))
            cfg = None

    if cfg is not None:
        if not cfg.roots:
            items.append(CheckItem(
                id="roots",
                status="warn",
                label="roots",
                detail="スキャン root が空です",
                fix="python -m docsweep init  # または config.yaml の roots を編集",
            ))
        else:
            missing = [str(r) for r in cfg.roots if not Path(r).exists()]
            if missing:
                items.append(CheckItem(
                    id="roots",
                    status="fail",
                    label="roots",
                    detail=f"存在しない path: {', '.join(missing)}",
                    fix="config.yaml の roots を実在ディレクトリに修正",
                ))
            else:
                items.append(CheckItem(
                    id="roots",
                    status="ok",
                    label="roots",
                    detail=f"{len(cfg.roots)} path(s): " + ", ".join(str(r) for r in cfg.roots[:5]),
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
            detail=f"未作成: {db}",
            fix="python -m docsweep index-sync",
        ))
    elif age_h >= fail_index_hours:
        items.append(CheckItem(
            id="index",
            status="fail",
            label="index.db",
            detail=f"非常に古い ({age_h:.1f}h) · last_scanned={last or '—'} · {db}",
            fix="python -m docsweep index-sync",
        ))
    elif age_h >= warn_index_hours:
        items.append(CheckItem(
            id="index",
            status="warn",
            label="index.db",
            detail=f"古い ({age_h:.1f}h) · last_scanned={last or '—'} · {db}",
            fix="python -m docsweep index-sync",
        ))
    else:
        items.append(CheckItem(
            id="index",
            status="ok",
            label="index.db",
            detail=f"鮮度 OK ({age_h:.1f}h) · last_scanned={last or '—'} · {db}",
        ))

    # 4. inject
    injected = list_injected()
    guidance_ok = GUIDANCE_PATH.is_file()
    if injected or guidance_ok:
        detail_parts = []
        if guidance_ok:
            detail_parts.append(f"guidance: {GUIDANCE_PATH}")
        if injected:
            detail_parts.append(f"manifest entries: {len(injected)}")
        if MANIFEST_PATH.is_file():
            detail_parts.append(f"manifest: {MANIFEST_PATH}")
        items.append(CheckItem(
            id="inject",
            status="ok",
            label="inject",
            detail=" · ".join(detail_parts),
        ))
    else:
        items.append(CheckItem(
            id="inject",
            status="hint",
            label="inject",
            detail="未注入（AI セッション開始 brief 導線が効かない可能性）",
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
            label="extras (web)",
            detail=f"未インストール: {', '.join(missing_web)}",
            fix="pip install 'docsweep[web]'",
        ))
    else:
        items.append(CheckItem(
            id="extras_web",
            status="ok",
            label="extras (web)",
            detail="fastapi + jinja2 利用可",
        ))
    if not extras["mcp"]:
        items.append(CheckItem(
            id="extras_mcp",
            status="hint",
            label="extras (mcp)",
            detail="mcp パッケージ未インストール",
            fix="pip install 'docsweep[mcp]'",
        ))
    else:
        items.append(CheckItem(
            id="extras_mcp",
            status="ok",
            label="extras (mcp)",
            detail="mcp 利用可",
        ))

    # 6. MCP 登録ヒント（検査は軽く・存在だけ）
    claude_cfg = Path.home() / ".claude" / "claude_desktop_config.json"
    # Claude Code は settings 系が別。ヒントとして既知パスを列挙。
    items.append(CheckItem(
        id="mcp_hint",
        status="hint",
        label="MCP 登録",
        detail=(
            "AI ツール側に docsweep MCP を登録しているか確認してください。"
            f" 参考: python -m docsweep mcp · claude config 付近={claude_cfg}"
        ),
        fix="python -m docsweep mcp --help",
    ))

    ok = not any(i.status == "fail" for i in items)
    return DoctorReport(generated_at=_now_iso(), ok=ok, items=items)


def format_human(report: DoctorReport) -> str:
    """人間向けの表テキスト。"""
    lines = [
        f"docsweep doctor · {_status_emoji(report.ok)} "
        f"{'OK' if report.ok else 'NEEDS ATTENTION'} · {report.generated_at}",
        "",
        f"{'STATUS':<6}  {'CHECK':<14}  DETAIL",
        "-" * 72,
    ]
    for it in report.items:
        lines.append(f"{it.status.upper():<6}  {it.label:<14}  {it.detail}")
        if it.fix:
            lines.append(f"{'':6}  {'fix:':<14}  {it.fix}")
    return "\n".join(lines) + "\n"


def _status_emoji(ok: bool) -> str:
    return "OK" if ok else "!!"
