"""inject/eject 履歴マニフェストの読み書き。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

MANIFEST_PATH = Path.home() / ".docsweep" / "injected.json"


def load_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        return {"projects": {}}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"projects": {}}


def save_manifest(data: dict) -> None:
    """``injected.json`` を tmp → os.replace でアトミックに書く。"""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{MANIFEST_PATH.name}.", suffix=".tmp", dir=str(MANIFEST_PATH.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(payload)
        os.replace(tmp_name, str(MANIFEST_PATH))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
