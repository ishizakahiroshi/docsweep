"""archive へ移すどの経路も ``docsweep undo`` で戻せること（場所・参照・状態ラベル）。

sweep / promote / apply の discard・promote / ``triage --review`` / ``review`` のチェックリスト /
``release close`` は、以前は batch_id を記録しておらず、undo が「取り消すものがない」と返していた。
batch_id は利用者の 1 回の操作ごとに 1 つ作る（1 回の undo で、その操作で動いた分がまとめて戻る）。

状態を書き換えてから移す操作（promote / discard / Web の [完了] など）は、場所だけ戻すと
文書が [完了] のまま queue へ戻り、次の sweep でまた移ってしまう。移す直前の H1 のラベルと
frontmatter の値も記録して、undo で元どおり（バイト単位で同じ）に書き戻す。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from docsweep.config import load_config
from docsweep.engine import apply_action, auto_sweep, doc_for_path, promote_state
from docsweep.interactive import KEY_DISCARD, KEY_DONE, apply_decision
from docsweep.release import close_release
from docsweep.services.archive import archive_done, undo_last_batch
from docsweep.services.status import update_status

_H1 = {"done": "完了", "watching": "様子見", "planned": "計画", "pending": "保留"}


def _queue(tmp_path: Path) -> Path:
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    queue = project / "docs" / "local"
    queue.mkdir(parents=True)
    return queue


def _cfg(tmp_path: Path):
    global_path = tmp_path / "global.yaml"
    if not global_path.exists():
        global_path.write_text("roots: []\n", encoding="utf-8")
    return load_config(explicit_roots=[str(tmp_path)], global_path=global_path)


def _doc(path: Path, *, state: str, doc_type: str = "plan", parent: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"type: {doc_type}", "status: draft", f"docsweep_state: {state}"]
    if parent is not None:
        lines.append(f"docsweep_parent: {parent}")
    lines += ["---", f"# [{_H1[state]}] {path.stem}", "", "## 概要", "", "本文", ""]
    path.write_text("\n".join(lines), encoding="utf-8", newline="")
    return path


def _doc_for(path: Path, cfg):
    doc = doc_for_path(path, cfg)
    assert doc is not None
    return doc


# ---- 経路ごと ---------------------------------------------------------------------


def test_promote_is_undone_with_the_parent_ref_and_the_label(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    watched = _doc(queue / "plan_watched.md", state="watching")
    child = _doc(queue / "plan_child.md", state="planned", parent="docs/local/plan_watched.md")
    watched_before, child_before = watched.read_bytes(), child.read_bytes()
    cfg = _cfg(tmp_path)

    moved = promote_state(cfg)

    assert not watched.exists()
    assert len(moved) == 1 and moved[0].batch_id
    undone = undo_last_batch(config=cfg)
    assert undone.batch_id == moved[0].batch_id
    assert undone.failed == []
    assert watched.read_bytes() == watched_before  # [様子見] と docsweep_state: watching に戻る
    assert child.read_bytes() == child_before
    assert {change["field"] for change in undone.states_restored} == {"h1", "docsweep_state"}


@pytest.mark.parametrize(("action", "state"), [("discard", "planned"), ("promote", "watching")])
def test_apply_archive_actions_are_undone(tmp_path: Path, action: str, state: str) -> None:
    queue = _queue(tmp_path)
    path = _doc(queue / "plan_target.md", state=state)
    before = path.read_bytes()
    cfg = _cfg(tmp_path)

    entry = apply_action(_doc_for(path, cfg), action, cfg)

    assert not path.exists()
    assert entry.batch_id
    undone = undo_last_batch(config=cfg)
    assert undone.batch_id == entry.batch_id
    assert undone.failed == []
    assert path.read_bytes() == before


def test_apply_callers_can_share_one_batch(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    first = _doc(queue / "plan_first.md", state="planned")
    second = _doc(queue / "plan_second.md", state="watching")
    befores = first.read_bytes(), second.read_bytes()
    cfg = _cfg(tmp_path)

    apply_action(_doc_for(first, cfg), "discard", cfg, batch_id="shared")
    apply_action(_doc_for(second, cfg), "promote", cfg, batch_id="shared")

    undone = undo_last_batch(config=cfg)
    assert undone.batch_id == "shared"
    assert (first.read_bytes(), second.read_bytes()) == befores


def test_approved_suggestions_are_undone_in_one_step(tmp_path: Path) -> None:
    from docsweep.auto_triage import apply_suggestions

    queue = _queue(tmp_path)
    first = _doc(queue / "plan_first.md", state="planned")
    second = _doc(queue / "plan_second.md", state="watching")
    befores = first.read_bytes(), second.read_bytes()
    cfg = _cfg(tmp_path)
    decisions = [
        {"path": _doc_for(first, cfg).record.path, "action": "discard"},
        {"path": _doc_for(second, cfg).record.path, "action": "promote"},
    ]

    result = apply_suggestions(cfg, decisions)

    assert result.failed == [] and len(result.applied) == 2
    assert not first.exists() and not second.exists()
    undone = undo_last_batch(config=cfg)
    assert undone.failed == []
    assert (first.read_bytes(), second.read_bytes()) == befores  # 承認した分が 1 回でまとめて戻る


@pytest.mark.parametrize("decision", [KEY_DONE, KEY_DISCARD])
def test_interactive_triage_decisions_are_undone(tmp_path: Path, decision: str) -> None:
    queue = _queue(tmp_path)
    path = _doc(queue / "pending_idea.md", state="pending", doc_type="pending")
    before = path.read_bytes()
    cfg = _cfg(tmp_path)

    result = apply_decision(_doc_for(path, cfg), decision, cfg)

    assert result.archived is True and result.error is None
    assert not path.exists()
    undone = undo_last_batch(config=cfg)
    assert undone.batch_id is not None
    assert undone.failed == []
    assert path.read_bytes() == before  # [保留] に戻る（次の sweep で移らない）


def test_review_checklist_is_undone_in_one_step(tmp_path: Path, monkeypatch) -> None:
    from docsweep.review import run_review

    queue = _queue(tmp_path)
    finished = _doc(queue / "plan_finished.md", state="done")
    idea = _doc(queue / "pending_idea.md", state="pending", doc_type="pending")
    befores = finished.read_bytes(), idea.read_bytes()

    class _Checkbox:
        def __init__(self, choices: list) -> None:
            self.choices = choices

        def ask(self) -> list:
            return [choice.value for choice in self.choices]  # 全部選ぶ

    questionary = SimpleNamespace(
        Choice=lambda title, value: SimpleNamespace(title=title, value=value),
        checkbox=lambda prompt, choices, **_kwargs: _Checkbox(choices),
    )
    monkeypatch.setitem(sys.modules, "questionary", questionary)
    cfg = _cfg(tmp_path)

    assert run_review(cfg) == 0
    assert not finished.exists() and not idea.exists()

    undone = undo_last_batch(config=cfg)
    assert undone.failed == []
    assert (finished.read_bytes(), idea.read_bytes()) == befores  # 1 回の undo で両方戻る


def test_web_done_then_undo_restores_the_label(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from docsweep.server.app import create_app

    queue = _queue(tmp_path)
    path = _doc(queue / "plan_active.md", state="planned")
    before = path.read_bytes()
    client = TestClient(create_app(_cfg(tmp_path), token="undo-token"))

    done = client.post(
        "/api/cards/status",
        data={"token": "undo-token", "path": path.resolve().as_posix(), "new_state": "done"},
    )
    assert done.status_code == 200, done.text
    assert not path.exists()

    undone = client.post("/api/cards/undo", data={"token": "undo-token"})
    assert undone.status_code == 200, undone.text
    assert undone.json()["failed"] == []
    assert path.read_bytes() == before


def test_mcp_style_status_then_archive_restores_the_label(tmp_path: Path) -> None:
    """MCP の update_status と同じ組み立て（update_status → archive_done に結果を渡す）。"""
    queue = _queue(tmp_path)
    path = _doc(queue / "plan_active.md", state="planned")
    before = path.read_bytes()
    cfg = _cfg(tmp_path)

    res = update_status(path, "discarded", project_root=queue.parents[1], config=cfg)
    archive_done(config=cfg, paths=[res.path], state_changes={res.path: res})

    assert not path.exists()
    assert undo_last_batch(config=cfg).failed == []
    assert path.read_bytes() == before


def _git_repo(path: Path, *, tag: str) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "docsweep-test"], check=True)
    (path / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)
    subprocess.run(["git", "-C", str(path), "tag", tag], check=True)


def test_release_close_is_undone(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repo(project, tag="v2.3.4")
    (project / ".docsweep.yaml").write_text(
        "archive_dir: docs/local/archive\n"
        "archive_partition: release\n"
        "release_tracking:\n  mode: enabled\n  archive_group_by: minor\n",
        encoding="utf-8",
    )
    path = project / "docs" / "local" / "plan_shipped.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\ntype: plan\nstatus: done\ntarget_release: v2.3.x\n---\n# [完了] shipped\n\n## 概要\n\nbody\n",
        encoding="utf-8",
    )
    global_path = project / "global.yaml"
    global_path.write_text("", encoding="utf-8")
    cfg = load_config(project_dir=project, explicit_roots=[str(project)], global_path=global_path)

    applied = close_release(cfg, "v2.3.4")

    assert len(applied.moved) == 1
    assert not path.exists()
    undone = undo_last_batch(config=cfg)
    assert undone.batch_id is not None
    assert undone.failed == []
    assert path.is_file()


# ---- 状態の書き戻しの細部 -------------------------------------------------------------


def test_sweep_of_an_already_done_doc_has_no_state_to_restore(tmp_path: Path) -> None:
    """sweep はラベルを書き換えない。undo は場所だけ戻し、ラベルは [完了] のまま。"""
    queue = _queue(tmp_path)
    path = _doc(queue / "plan_finished.md", state="done")
    before = path.read_bytes()
    cfg = _cfg(tmp_path)

    auto_sweep(cfg)
    undone = undo_last_batch(config=cfg)

    assert undone.states_restored == []
    assert path.read_bytes() == before


def test_english_label_is_restored_in_english(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    path = queue / "plan_watched.md"
    path.write_text(
        "---\ntype: plan\ndocsweep_state: watching\n---\n# [Watching] watched\n\n## Summary\n\nx\n",
        encoding="utf-8", newline="",
    )
    before = path.read_bytes()
    cfg = _cfg(tmp_path)

    promote_state(cfg)
    undo_last_batch(config=cfg)

    assert path.read_bytes() == before


def test_legacy_status_field_is_restored(tmp_path: Path) -> None:
    """docsweep_state が無く status に状態を書く旧形式も、書き換えた status を戻す。"""
    queue = _queue(tmp_path)
    path = queue / "plan_legacy.md"
    path.write_text("---\ntype: plan\nstatus: planned\n---\n# [計画] legacy\n", encoding="utf-8", newline="")
    before = path.read_bytes()
    cfg = _cfg(tmp_path)

    apply_action(_doc_for(path, cfg), "discard", cfg)
    undone = undo_last_batch(config=cfg)

    assert {change["field"] for change in undone.states_restored} == {"h1", "status"}
    assert path.read_bytes() == before


def test_state_field_added_by_the_archive_is_removed_again(tmp_path: Path) -> None:
    """OKF の status だけで docsweep_state が無い文書は、書き足した行を undo で消す。"""
    queue = _queue(tmp_path)
    path = queue / "plan_okf.md"
    path.write_text("---\ntype: plan\nstatus: draft\n---\n# [計画] okf\n", encoding="utf-8", newline="")
    before = path.read_bytes()
    cfg = _cfg(tmp_path)

    apply_action(_doc_for(path, cfg), "discard", cfg)
    undo_last_batch(config=cfg)

    assert path.read_bytes() == before


def test_label_changed_after_the_archive_is_not_overwritten(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    path = _doc(queue / "plan_watched.md", state="watching")
    cfg = _cfg(tmp_path)
    promote_state(cfg)
    archived = queue / "archive" / "plan_watched.md"
    archived.write_text(
        archived.read_text(encoding="utf-8").replace("# [完了]", "# [実行中]"),
        encoding="utf-8", newline="",
    )

    undone = undo_last_batch(config=cfg)

    assert path.is_file()  # 場所は戻る
    assert "# [実行中] plan_watched" in path.read_text(encoding="utf-8")  # 人が変えたラベルは残す
    assert [failure.get("field") for failure in undone.failed] == ["h1"]
    # frontmatter は書き換えたときの値のままだったので戻る
    assert "docsweep_state: watching" in path.read_text(encoding="utf-8")
    # 同じ書き戻しを二度しない
    assert undo_last_batch(config=cfg).batch_id is None
