"""インタラクティブ チェックリスト（--review 専用）。

人間対話のみ。--auto / --json は非対話を厳守するためここを通さない。
questionary（review extra）の checkbox を使い、選択分を archive へ一括移送する。
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .engine import ScanResult, apply_action, archive_doc, run_scan
from .models import Flag


def _candidates(result: ScanResult) -> list:
    """要判断＋廃止候補＋保留（今すぐ判断が要るもの）。"""
    out = []
    for d in result.docs:
        r = d.record
        if Flag.NEEDS_DECISION.value in r.flags or r.state == "pending" or (r.archivable and r.auto_movable):
            out.append(d)
    out.sort(key=lambda d: d.record.age_days, reverse=True)
    return out


def run_review(config: Config) -> int:
    try:
        import questionary
    except ImportError:
        print("--review には review extra が必要です: pip install 'docsweep[review]'")
        return 3

    result = run_scan(config)
    docs = _candidates(result)
    if not docs:
        print("判断が要るファイルはありません。")
        return 0

    choices = []
    for d in docs:
        r = d.record
        label = r.state_label or "[?]"
        flags = f" !{','.join(r.flags)}" if r.flags else ""
        title = f"{label} {r.project}/{Path(r.path).name}  {r.age_days}d{flags}"
        choices.append(questionary.Choice(title=title, value=d))

    picked = questionary.checkbox(
        "archive へ移送するファイルを選択（space で選択・enter で確定）", choices=choices
    ).ask()
    if not picked:
        print("選択なし。中止しました。")
        return 0

    moved = 0
    for d in picked:
        r = d.record
        if r.state in {"done", "discarded"}:
            # 既に終端ラベル → そのまま archive へ。
            archive_doc(d, config)
        elif r.state == "watching":
            apply_action(d, "promote", config)
        elif "discard" in r.allowed_actions:
            apply_action(d, "discard", config)
        else:
            continue
        moved += 1
    print(f"{moved} 件を archive へ移送しました。")
    return 0
