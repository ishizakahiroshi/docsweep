"""``docsweep review``（チェックリスト）で、移せない文書を選んでも残りを処理すること。

以前は 1 件の失敗（``docsweep_policy: never_archive`` の文書の廃止など）で例外のまま止まり、
その前に選んだ文書だけが移送され、移送件数も出なかった。``triage --review`` と同じく、
失敗した文書は ``  ! <パス>: <理由>`` の行で知らせて残りを続け、最後に件数を出す。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from docsweep.config import load_config
from docsweep.engine import doc_for_path
from docsweep.i18n import t, use_lang
from docsweep.review import run_review
from docsweep.services.archive import undo_last_batch


def _pending(path: Path, *, age_days: int, never_archive: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: pending", "status: draft", "docsweep_state: pending"]
    if never_archive:
        lines.append("docsweep_policy: never_archive")
    lines += ["---", f"# [保留] {path.stem}", "", "## 概要", "", "本文", ""]
    path.write_text("\n".join(lines), encoding="utf-8", newline="")
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def _pick_all(monkeypatch: pytest.MonkeyPatch) -> None:
    questionary = SimpleNamespace(
        Choice=lambda title, value: SimpleNamespace(title=title, value=value),
        checkbox=lambda prompt, choices, **_kwargs: SimpleNamespace(
            ask=lambda: [choice.value for choice in choices]  # 全部選ぶ
        ),
    )
    monkeypatch.setitem(sys.modules, "questionary", questionary)


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_review_continues_past_a_document_that_cannot_be_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], lang: str
) -> None:
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    queue = project / "docs" / "local"
    # 古い順に並ぶ: first → kept（移送禁止）→ last。kept で止まると last が残る
    first = _pending(queue / "pending_first.md", age_days=30)
    kept = _pending(queue / "pending_kept.md", age_days=20, never_archive=True)
    last = _pending(queue / "pending_last.md", age_days=10)
    befores = first.read_bytes(), kept.read_bytes(), last.read_bytes()

    global_path = tmp_path / "global.yaml"
    global_path.write_text("roots: []\n", encoding="utf-8")
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=global_path)
    kept_path = doc_for_path(kept, cfg).record.path
    _pick_all(monkeypatch)

    with use_lang(lang):
        assert run_review(cfg) == 0
        expected_head = t("review.moved_with_errors", count=2, errors=1)
        expected_error = f"  ! {kept_path}: " + t("engine.never_archive_policy", path=kept_path)

    out = capsys.readouterr().out.splitlines()
    assert expected_head in out
    assert expected_error in out
    # 移送禁止の文書は元のまま、前後の文書は両方 archive へ移る
    assert kept.read_bytes() == befores[1]
    assert not first.exists() and not last.exists()
    assert (queue / "archive" / "pending_first.md").exists()
    assert (queue / "archive" / "pending_last.md").exists()

    # 移した 2 本は 1 回の undo でまとめて戻る
    undone = undo_last_batch(config=cfg)
    assert undone.failed == []
    assert (first.read_bytes(), kept.read_bytes(), last.read_bytes()) == befores


def test_review_without_errors_keeps_the_plain_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    _pending(project / "docs" / "local" / "pending_only.md", age_days=10)
    global_path = tmp_path / "global.yaml"
    global_path.write_text("roots: []\n", encoding="utf-8")
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=global_path)
    _pick_all(monkeypatch)

    assert run_review(cfg) == 0
    out = capsys.readouterr().out.splitlines()
    assert t("review.moved", count=1) in out
    assert not any(line.startswith("  ! ") for line in out)
