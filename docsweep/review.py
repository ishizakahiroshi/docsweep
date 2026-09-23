"""インタラクティブ チェックリスト（--review 専用）。

人間対話のみ。--auto / --json は非対話を厳守するためここを通さない。
questionary（review extra）の checkbox を使い、選択分を archive へ一括移送する。
"""

from __future__ import annotations

from pathlib import Path

from .archive import new_batch_id
from .atomic import ConflictError
from .config import Config
from .engine import ScanResult, apply_action, archive_doc, run_scan
from .i18n import t
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
        print(t("review.extra_required"))
        return 3

    result = run_scan(config)
    docs = _candidates(result)
    if not docs:
        print(t("review.nothing_to_decide"))
        return 0

    choices = []
    for d in docs:
        r = d.record
        label = r.state_label or "[?]"
        flags = f" !{','.join(r.flags)}" if r.flags else ""
        title = f"{label} {r.project}/{Path(r.path).name}  {r.age_days}d{flags}"
        choices.append(questionary.Choice(title=title, value=d))

    # instruction を渡さないと questionary が英語の操作案内を出す。回答後の
    # "done (N selections)" は questionary に直書きで差し替えられない
    picked = questionary.checkbox(
        t("review.pick_prompt"), choices=choices, instruction=t("review.pick_instruction")
    ).ask()
    if not picked:
        print(t("review.nothing_selected"))
        return 0

    moved = 0
    failed: list[tuple[str, str]] = []
    # チェックリストで選んだ分を 1 バッチにし、``docsweep undo`` 1 回でまとめて戻せるようにする。
    batch_id = new_batch_id()
    for d in picked:
        r = d.record
        # 1 件の失敗（移送禁止の文書・途中で書き換わったファイル等）で止めず、残りを続ける。
        # 止めると前に選んだ分だけが移り、件数も出ない（triage --review と同じ扱いにする）
        try:
            if r.state in {"done", "discarded"}:
                # 既に終端ラベル → そのまま archive へ。
                archive_doc(d, config, batch_id=batch_id)
            elif r.state == "watching":
                apply_action(d, "promote", config, batch_id=batch_id)
            elif "discard" in r.allowed_actions:
                apply_action(d, "discard", config, batch_id=batch_id)
            else:
                continue
        except (ConflictError, OSError, UnicodeError, ValueError) as exc:
            failed.append((r.path, str(exc)))
            continue
        moved += 1
    if failed:
        print(t("review.moved_with_errors", count=moved, errors=len(failed)))
        for path, error in failed:
            print(f"  ! {path}: {error}")
    else:
        print(t("review.moved", count=moved))
    return 0
