"""Read-only parent/child closeout inspection fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from docsweep import cli as cli_mod
from docsweep.closeout import check_closeout
from docsweep.config import load_config
from docsweep.templates_gen import new_split_plans


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _fm(*, state: str = "in-progress", related: str = "[]", parent: str | None = None) -> str:
    parent_line = f"docsweep_parent: {parent}\n" if parent else ""
    return (
        "---\n"
        "type: plan\n"
        "status: draft\n"
        f"docsweep_state: {state}\n"
        f"{parent_line}"
        "review_status: draft\n"
        f"related: {related}\n"
        "---\n"
    )


def _body(title: str, *, state: str = "in-progress", related: str = "[]", parent: str | None = None,
          unchecked: bool = False, plain: bool = False, conflict: bool = False,
          acceptance: bool = True) -> str:
    mark = "[ ]" if unchecked else "[x]"
    completion = f"- {mark} 実装を完了"
    if plain:
        completion = "- 実装を完了した"
    h1_state = {
        "planned": "計画",
        "in-progress": "実行中",
        "watching": "様子見",
        "done": "完了",
        "pending": "保留",
        "discarded": "廃止",
    }.get(state, "計画")
    fm_state = "watching" if conflict else state
    text = (
        _fm(state=fm_state, related=related, parent=parent)
        + f"# [{h1_state}] {title}\n\n"
        + "## 完了条件\n\n"
        + completion
        + "\n\n## 検証\n\n"
        + f"- {mark} 自動確認: pytest -q 成功\n"
    )
    if acceptance:
        text += "\n## 受入条件\n\n- [x] 手動確認: 完了\n"
    return text


def _cfg(project: Path):
    return load_config(explicit_roots=[project.as_posix()], global_path=project / "missing.yaml")


def test_closeout_legacy_related_infers_only_filename_matching_child(tmp_path: Path):
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    local = project / "docs" / "local"
    parent = _write(local / "plan_alpha.md", _body("alpha"))
    child = _write(
        local / "plan_alpha_c1_backend.md",
        _body("child", related="[plan_alpha.md]"),
    )
    non_child = _write(
        local / "plan_other.md",
        _body("other", related="[plan_alpha.md]"),
    )

    result = check_closeout(parent, project_dir=project, config=_cfg(project))

    assert result.verdict == "ready"
    assert [Path(item["path"]).name for item in result.children] == [child.name]
    assert any(w["code"] == "generic_related_not_child" for w in result.warnings)
    assert result.parent["docsweep_state"] == "in-progress"
    assert result.parent["mtime"] == parent.stat().st_mtime
    assert result.parent["details"]["linkcheck"]["progress_hint"] == "no_section"
    assert non_child.exists()


def test_closeout_explicit_parent_and_new_split_write_parent_extension(tmp_path: Path):
    project = tmp_path / "repo"
    created = new_split_plans("alpha", n=2, project_dir=project, offset_days={})
    parent = created[0].path
    for doc in created:
        doc.path.write_text(
            doc.path.read_text(encoding="utf-8")
            + "\n## 完了条件\n\n- 実装を完了した\n"
            + "\n## 検証\n\n- [x] 自動確認: pytest -q 成功\n"
            + "\n## 受入条件\n\n- [x] 手動確認: 完了\n",
            encoding="utf-8",
            newline="",
        )
    result = check_closeout(parent, project_dir=project, config=_cfg(project))

    assert result.verdict == "manual_review_required"
    assert len(result.children) == 2
    assert {item["relation"] for item in result.children} == {"explicit"}
    for item in result.children:
        assert item["docsweep_parent"] == "docs/local/plan_alpha.md"
    assert all(check["reason"] == "prose_not_machine_proof" for check in result.manual_checks)


def test_closeout_reports_conflict_and_unchecked_as_blockers(tmp_path: Path):
    project = tmp_path / "repo"
    parent = _write(
        project / "docs" / "local" / "plan_parent.md",
        _body("parent", unchecked=True),
    )
    _write(
        project / "docs" / "local" / "plan_parent_c1_x.md",
        _body("child", related="[plan_parent.md]", conflict=True),
    )

    result = check_closeout(parent, project_dir=project, config=_cfg(project))
    codes = {item["code"] for item in result.blockers}
    assert result.verdict == "not_ready"
    assert "unchecked_checkbox" in codes
    assert "state_conflict" in codes


def test_closeout_done_requires_acceptance_section(tmp_path: Path):
    project = tmp_path / "repo"
    parent = _write(
        project / "docs" / "local" / "plan_parent.md",
        _body("parent", acceptance=False),
    )
    result = check_closeout(parent, project_dir=project, config=_cfg(project), target_state="done")
    assert result.verdict == "not_ready"
    assert any(item["code"] == "missing_section" and item["section"] == "acceptance"
               for item in result.blockers)


def test_closeout_watching_does_not_require_acceptance_section(tmp_path: Path):
    project = tmp_path / "repo"
    parent = _write(
        project / "docs" / "local" / "plan_parent.md",
        _body("parent", acceptance=False),
    )
    result = check_closeout(parent, project_dir=project, config=_cfg(project), target_state="watching")
    assert result.verdict == "ready"
    assert not any(item.get("section") == "acceptance" for item in result.blockers)


def test_closeout_detects_explicit_cycle_and_state_order(tmp_path: Path):
    project = tmp_path / "repo"
    parent = _write(
        project / "docs" / "local" / "plan_parent.md",
        _body("parent", state="watching", parent="docs/local/plan_parent_c1_x.md"),
    )
    child = _write(
        project / "docs" / "local" / "plan_parent_c1_x.md",
        _body("child", state="in-progress", parent="docs/local/plan_parent.md"),
    )
    result = check_closeout(parent, project_dir=project, config=_cfg(project))
    codes = {item["code"] for item in result.blockers}
    assert {"cycle", "state_order_conflict"}.issubset(codes)
    assert child.as_posix() in {item["path"] for item in result.children}


def test_closeout_ignores_configured_obsidian_artifacts_but_keeps_ignored_local(tmp_path: Path):
    project = tmp_path / "repo"
    parent = _write(project / "docs" / "local" / "plan_parent.md", _body("parent"))
    _write(
        project / "docs" / "local" / "plan_parent_c1_x.md",
        _body("child", related="[plan_parent.md]"),
    )
    _write(
        project / "docs" / "obsidian" / "plan_parent_c2_report.md",
        _body("artifact", related="[plan_parent.md]"),
    )
    cfg = _cfg(project)
    cfg.ignore = ["docs/obsidian"]
    result = check_closeout(parent, project_dir=project, config=cfg)
    assert [item["name"] for item in result.children] == ["plan_parent_c1_x.md"]


def test_closeout_preserves_crlf_japanese_and_reports_dirty_overlap(tmp_path: Path):
    project = tmp_path / "repo"
    parent = project / "docs" / "local" / "plan_parent.md"
    body = _body("日本語", acceptance=True) + "\n## 変更予定ファイル\n\n- docs/changed.py\n"
    _write(parent, body.replace("\n", "\r\n"))
    changed = _write(project / "docs" / "changed.py", "changed\n")
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    result = check_closeout(parent, project_dir=project, config=_cfg(project))
    assert result.parent["h1"] == "[実行中] 日本語"
    assert any(check["reason"] == "planned_file_is_dirty" for check in result.manual_checks)
    assert changed.as_posix().endswith("docs/changed.py")


def test_closeout_follows_docs_local_junction_when_supported(tmp_path: Path):
    project = tmp_path / "repo"
    target = tmp_path / "local-notes"
    target.mkdir(parents=True)
    local = project / "docs" / "local"
    local.parent.mkdir(parents=True)
    try:
        os.symlink(target, local, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory junction/symlink is unavailable: {exc}")
    parent = _write(local / "plan_parent.md", _body("junction parent"))
    _write(
        local / "plan_parent_c1_x.md",
        _body("junction child", parent="docs/local/plan_parent.md"),
    )
    result = check_closeout(parent, project_dir=project, config=_cfg(project))
    assert len(result.children) == 1
    assert result.children[0]["relation"] == "explicit"


def test_closeout_is_read_only_and_cli_json_has_exit_contract(tmp_path: Path, capsys):
    project = tmp_path / "repo"
    parent = _write(project / "docs" / "local" / "plan_parent.md", _body("parent"))
    before = hashlib.sha256(parent.read_bytes()).hexdigest()
    before_mtime = parent.stat().st_mtime_ns

    rc = cli_mod.main([
        "closeout-check", "--path", str(parent), "--project-dir", str(project), "--json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["verdict"] == "ready"
    assert hashlib.sha256(parent.read_bytes()).hexdigest() == before
    assert parent.stat().st_mtime_ns == before_mtime


def test_closeout_keeps_delegated_h4_evidence_classification(tmp_path: Path):
    project = tmp_path / "repo"
    _write(project / "docsweep" / "linkcheck.py", "# fixture\n")
    parent = _write(
        project / "docs" / "local" / "plan_delegated.md",
        "---\n"
        "type: plan\n"
        "status: draft\n"
        "docsweep_state: planned\n"
        "docsweep_delegation: external\n"
        "related: []\n"
        "---\n"
        "# [計画] delegated\n\n"
        "## context配分\n\n"
        "| C | 種別 | 内容 | 備考/注意点 |\n"
        "|---|---|---|---|\n"
        "| C1 | planned | 実装 | — |\n\n"
        "## 概要\n\n概要。\n\n"
        "## C 詳細\n\n"
        "### C1 実装\n\n"
        "#### 目的\n\n目的を一つに定める。\n\n"
        "#### 調査で確認した現在の実装\n\n現在の実装を確認した。\n\n"
        "#### 作業内容\n\n対象を変更する。\n\n"
        "#### 変更予定ファイル\n\n- `docsweep/linkcheck.py`\n\n"
        "#### 維持する仕様\n\n既存の仕様を維持する。\n\n"
        "#### スコープ外\n\n関連しない変更は扱わない。\n\n"
        "#### 検証方法\n\n- pytest tests/test_linkcheck.py -q 成功\n\n"
        "#### 完了条件\n\n- C1 の完了結果を確認する。\n"
    )

    result = check_closeout(parent, project_dir=project, config=_cfg(project))

    assert result.parent["details"]["sections"]["changed_files"] == ["変更予定ファイル"]
    assert result.parent["details"]["linkcheck"]["declared_files"] == [
        {
            "path": "docsweep/linkcheck.py",
            "resolved_path": (project / "docsweep" / "linkcheck.py").as_posix(),
            "exists": True,
            "inside_project": True,
        }
    ]


def test_closeout_cli_manual_and_blocker_exit_codes(tmp_path: Path, capsys):
    project = tmp_path / "repo"
    manual = _write(
        project / "docs" / "local" / "plan_manual.md",
        _body("manual", plain=True),
    )
    rc_manual = cli_mod.main([
        "closeout-check", "--path", str(manual), "--project-dir", str(project), "--json",
    ])
    manual_payload = json.loads(capsys.readouterr().out)
    assert rc_manual == 1
    assert manual_payload["verdict"] == "manual_review_required"

    blocker = _write(
        project / "docs" / "local" / "plan_blocker.md",
        _body("blocker", unchecked=True),
    )
    rc_blocker = cli_mod.main([
        "closeout-check", "--path", str(blocker), "--project-dir", str(project), "--json",
    ])
    blocker_payload = json.loads(capsys.readouterr().out)
    assert rc_blocker == 2
    assert blocker_payload["verdict"] == "not_ready"


def test_closeout_unresolved_parent_is_not_empty_success(tmp_path: Path):
    project = tmp_path / "repo"
    parent = _write(project / "docs" / "local" / "plan_parent.md", _body("parent"))
    _write(
        project / "docs" / "local" / "plan_parent_c1_x.md",
        _body("child", parent="docs/local/missing-parent.md"),
    )
    result = check_closeout(parent, project_dir=project, config=_cfg(project))
    assert result.verdict == "not_ready"
    assert any(item["code"] == "unresolved_parent" for item in result.blockers)


def test_closeout_parent_reference_must_be_repo_relative(tmp_path: Path):
    project = tmp_path / "repo"
    parent = _write(project / "docs" / "local" / "plan_parent.md", _body("parent"))
    _write(
        project / "docs" / "local" / "plan_parent_c1_x.md",
        _body("child", parent="../plan_parent.md"),
    )
    result = check_closeout(parent, project_dir=project, config=_cfg(project))
    assert result.verdict == "not_ready"
    assert any(item["code"] == "outside_parent" for item in result.blockers)


def test_apply_relabel_explicit_ignored_plan_updates_h1_and_frontmatter(tmp_path: Path, capsys):
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    _write(project / ".gitignore", "docs/local/\n")
    plan = _write(project / "docs" / "local" / "plan_parent.md", _body("parent"))

    rc = cli_mod.main([
        "apply", "--root", str(project), "--path", str(plan),
        "--action", "relabel", "--to", "watching",
    ])
    capsys.readouterr()

    assert rc == 0
    text = plan.read_text(encoding="utf-8")
    assert "docsweep_state: watching" in text
    assert "# [様子見] parent" in text
    assert not (project / "archive" / plan.name).exists()


def test_apply_relabel_done_does_not_implicitly_archive(tmp_path: Path, capsys):
    project = tmp_path / "repo"
    _write(project / ".gitignore", "docs/local/\n")
    plan = _write(project / "docs" / "local" / "plan_parent.md", _body("parent"))

    rc = cli_mod.main([
        "apply", "--root", str(project), "--path", str(plan),
        "--action", "relabel", "--to", "done",
    ])
    capsys.readouterr()

    assert rc == 0
    assert plan.exists()
    assert "docsweep_state: done" in plan.read_text(encoding="utf-8")
    assert "# [完了] parent" in plan.read_text(encoding="utf-8")
    assert not (project / "archive" / plan.name).exists()
