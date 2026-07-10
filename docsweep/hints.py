"""コマンド終了後の次の一手ヒント（UX W1 / P4）。"""

from __future__ import annotations

import os
from pathlib import Path

from .config import Config
from .doctor import run_doctor
from .index import db_path


def hints_enabled() -> bool:
    if os.environ.get("DOCSWEEP_HINTS", "").strip() in ("0", "false", "no"):
        return False
    return True


def suggest_after_command(command: str, config: Config | None = None) -> str | None:
    if not hints_enabled():
        return None
    try:
        if command in ("scan", "brief", "day"):
            db = db_path()
            if not db.is_file():
                return "hint: index がありません → python -m docsweep index-sync"
            age_h = (Path(db).stat().st_mtime)
            import time
            hours = (time.time() - age_h) / 3600
            if hours > 24:
                return f"hint: index が {hours:.0f}h 古い → python -m docsweep index-sync"
        if command == "init":
            return "hint: 次は python -m docsweep doctor && python -m docsweep brief"
        if command == "doctor" and config is not None:
            rep = run_doctor(config=config)
            for it in rep.items:
                if it.status in ("warn", "fail") and it.fix:
                    return f"hint: {it.fix}"
        if command == "sweep":
            return "hint: 誤移送したら python -m docsweep undo"
    except Exception:
        return None
    return None
