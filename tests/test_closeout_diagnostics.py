"""Normal diagnostics, expected negative controls, and real failures stay distinct."""

from pathlib import Path

import pytest

from docsweep.closeout import _classify_verification, check_closeout
from docsweep.config import load_config
from docsweep.i18n import use_lang


@pytest.mark.parametrize("line", [
    "`php -l fixture.php` → No syntax errors",
    "php -l fixture.php: No syntax errors detected in fixture.php",
])
def test_php_normal_diagnostic_is_passed(line: str) -> None:
    assert _classify_verification("検証", line) == "passed"


@pytest.mark.parametrize("line", [
    "No syntax errors",
    "tree-sitter: `root.has_error` is True; `MISSING ;` and `ERROR` were observed",
    "期待した負例: 最小fixtureの構文エラーを確認した。",
])
def test_diagnostic_without_machine_success_remains_claimed(line: str) -> None:
    assert _classify_verification("検証", line) == "claimed"


@pytest.mark.parametrize("line, expected", [
    ("php -l fixture.php: No syntax errors; pytest: 1 failed", "failed"),
    ("php -l fixture.php: No syntax errors; CI が落ちた", "failed"),
    ("期待した負例を確認したが、pytest: 2 failed", "failed"),
    ("php -l fixture.php: No syntax errors; not tested", "not_run"),
    ("ruff: failure", "failed"),
    ("テストが失敗した", "failed"),
    ("失敗が 2 件残っている", "failed"),
    ("エラーは 1 件残っている", "failed"),
    ("php -l fixture.php: No syntax errors; 失敗が 2 件残っている", "failed"),
    ("pytest 0 failed; mypy failure", "failed"),
    ("pytest 0 failed; exit status 1", "failed"),
    ("pytest 0 failed; ruff error", "failed"),
])
def test_success_diagnostic_never_hides_real_failure(line: str, expected: str) -> None:
    assert _classify_verification("検証", line) == expected


def test_normal_diagnostic_outside_verification_is_not_machine_proof() -> None:
    assert _classify_verification("備忘", "php -l fixture.php: No syntax errors") == "claimed"


def test_checked_negative_control_still_requires_human_review(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    queue = project / "docs" / "local"
    queue.mkdir(parents=True)
    plan = queue / "plan_diagnostics.md"
    plan.write_text(
        "---\ntype: plan\nstatus: draft\ndocsweep_state: in-progress\n---\n"
        "# [実行中] 合成診断\n\n## 完了条件\n\n- [x] 実装した\n\n"
        "## 検証\n\n- [x] php -l fixture.php: No syntax errors\n"
        "- [x] 期待した負例: fixtureの構文エラーを確認した。\n",
        encoding="utf-8",
    )
    config = load_config(explicit_roots=[project.as_posix()], global_path=project / "missing.yaml")
    result = check_closeout(plan, project_dir=project, config=config)
    evidence = result.parent["details"]["verification"]
    assert [item["status"] for item in evidence] == ["passed", "claimed"]
    assert evidence[0]["kind"] == "automatic"
    assert any(item["reason"] == "failure_word_without_assertion" for item in result.manual_checks)
    assert not any(item["code"] == "failed" for item in result.blockers)
    assert result.verdict == "manual_review_required"


@pytest.mark.parametrize("lang", ["ja", "en"])
@pytest.mark.parametrize("line", [
    "No syntax errors",
    "php -l fixture.php: No syntax errors; exit status 1",
    "php -l fixture.php: No syntax errors; Could not open input file: absent.php",
    "php -l の期待出力は `No syntax errors`。今回は保留。",
])
def test_diagnostic_or_failed_command_cannot_auto_pass(lang: str, line: str) -> None:
    with use_lang(lang):
        assert _classify_verification("Verification", line) != "passed"


@pytest.mark.parametrize("line", [
    "No syntax errors",
    "php -l fixture.php: No syntax errors; exit status 1",
    "php -l fixture.php: No syntax errors; Could not open input file: absent.php",
    "php -l の期待出力は `No syntax errors`。今回は保留。",
])
def test_checked_diagnostic_keeps_manual_or_blocker(tmp_path: Path, line: str) -> None:
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    plan = project / "docs" / "local" / "plan_probe.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(
        "---\ntype: plan\nstatus: draft\ndocsweep_state: in-progress\n---\n"
        "# [実行中] Probe\n\n## 完了条件\n- [x] 実装した\n\n"
        "## 検証\n- [x] pytest 1 passed\n- [x] " + line
        + "\n\n## 受入\n- [x] 自動検査は合格\n",
        encoding="utf-8",
    )
    config = load_config(explicit_roots=[project.as_posix()], global_path=project / "missing.yaml")
    result = check_closeout(plan, project_dir=project, config=config)
    assert result.verdict != "ready"
    assert result.blockers or result.manual_checks
