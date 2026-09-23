"""コマンド終了後の次の一手ヒント（UX W1 / P4）。"""

from __future__ import annotations

import os
from pathlib import Path

from .config import Config
from .doctor import run_doctor
from .i18n import t
from .index import db_path


def hints_enabled() -> bool:
    if os.environ.get("DOCSWEEP_HINTS", "").strip() in ("0", "false", "no"):
        return False
    return True


def _hint(text: str) -> str:
    """「ヒント:」の接頭辞も表示言語で付ける。"""
    return t("hints.line", text=text)


def suggest_after_command(command: str, config: Config | None = None) -> str | None:
    if not hints_enabled():
        return None
    try:
        if command in ("scan", "brief", "day"):
            db = db_path()
            if not db.is_file():
                return _hint(t("hints.index_missing"))
            age_h = (Path(db).stat().st_mtime)
            import time
            hours = (time.time() - age_h) / 3600
            if hours > 24:
                return _hint(t("hints.index_stale", hours=hours))
        if command == "init":
            return _hint(t("hints.after_init"))
        if command == "doctor" and config is not None:
            rep = run_doctor(config=config)
            for it in rep.items:
                if it.status in ("warn", "fail") and it.fix:
                    return _hint(it.fix)
        if command == "sweep":
            return _hint(t("hints.after_sweep"))
    except Exception:
        return None
    return None
