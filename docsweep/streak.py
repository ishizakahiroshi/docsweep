"""朝の習慣と片付け量の軽量メトリクス（UX W4 / P20）。

「連続で朝の 1 個を見た日数」と「今週 archive した件数」だけを出す。

方針（カタログの注意書きに従う）:
- **罪悪感 UI にしない**。0 でも責める文言を出さない。呼び出し側は数値だけ受け取る。
- ``metrics: false``（config）または ``DOCSWEEP_METRICS=0`` で完全に無効化できる。
- 記録するのは**日付だけ**。何を見たか・何を書いたかは残さない。
- 記録先は ``~/.docsweep/streak.json``。壊れていても既定値で動く（実害ゼロ）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .atomic import write_atomic
from .config import GLOBAL_CONFIG_PATH, Config
from .services.archive import move_log_path

STREAK_FILE = GLOBAL_CONFIG_PATH.parent / "streak.json"
STREAK_SCHEMA_VERSION = 1
# 保持する日数。streak の計算に必要な範囲だけ残し、無限に伸ばさない。
_KEEP_DAYS = 400


def metrics_enabled(config: Config | None = None) -> bool:
    if os.environ.get("DOCSWEEP_METRICS", "").strip() in ("0", "false", "no"):
        return False
    if config is not None and getattr(config, "metrics_enabled", True) is False:
        return False
    return True


def _load(path: Path | None = None) -> dict:
    p = path or STREAK_FILE
    if not p.is_file():
        return {"version": STREAK_SCHEMA_VERSION, "days": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"version": STREAK_SCHEMA_VERSION, "days": []}
    if not isinstance(data, dict):
        return {"version": STREAK_SCHEMA_VERSION, "days": []}
    days = data.get("days")
    data["days"] = [d for d in days if isinstance(d, str)] if isinstance(days, list) else []
    return data


def _save(data: dict, path: Path | None = None) -> None:
    p = path or STREAK_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(p, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def record_open(*, today: date | None = None, path: Path | None = None) -> None:
    """「朝の 1 個を見た」を 1 日 1 回だけ記録する（同じ日の重複は増えない）。"""
    today = today or date.today()
    data = _load(path)
    iso = today.isoformat()
    if iso in data["days"]:
        return
    cutoff = (today - timedelta(days=_KEEP_DAYS)).isoformat()
    data["days"] = sorted({d for d in data["days"] if d >= cutoff} | {iso})
    _save(data, path)


def current_streak(*, today: date | None = None, path: Path | None = None) -> int:
    """今日または昨日を起点に、連続して記録がある日数を返す。

    今日まだ見ていなくても昨日までの連続は消えない（起点を昨日に落とす）。
    """
    today = today or date.today()
    days = set(_load(path)["days"])
    if not days:
        return 0
    start = today if today.isoformat() in days else today - timedelta(days=1)
    if start.isoformat() not in days:
        return 0
    n = 0
    cur = start
    while cur.isoformat() in days:
        n += 1
        cur -= timedelta(days=1)
    return n


def archived_this_week(config: Config, *, today: date | None = None) -> int:
    """今週（月曜起点）に archive へ移送した件数を moves.jsonl から数える。

    新しい記録は増やさない。既に書かれている移送ログを読むだけ。
    """
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    count = 0
    for root in config.roots:
        p = move_log_path(root)
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("status") not in (None, "ok", "moved"):
                continue
            ts = entry.get("ts")
            if not isinstance(ts, str):
                continue
            try:
                when = datetime.fromisoformat(ts).date()
            except ValueError:
                continue
            if when >= monday:
                count += 1
    return count


@dataclass
class Metrics:
    streak_days: int
    archived_this_week: int
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "streak_days": self.streak_days,
            "archived_this_week": self.archived_this_week,
            "enabled": self.enabled,
        }


def collect(config: Config, *, today: date | None = None) -> Metrics:
    """表示用のメトリクスをまとめて取る。無効化されていればゼロ値を返す。"""
    if not metrics_enabled(config):
        return Metrics(streak_days=0, archived_this_week=0, enabled=False)
    try:
        streak = current_streak(today=today)
    except Exception:  # noqa: BLE001 - 計測で UI を止めない
        streak = 0
    try:
        archived = archived_this_week(config, today=today)
    except Exception:  # noqa: BLE001
        archived = 0
    return Metrics(streak_days=streak, archived_this_week=archived, enabled=True)
