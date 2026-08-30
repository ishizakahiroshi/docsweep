"""配布物 pre-commit hook (``templates/.githooks/docsweep-check.py``) のテスト。

hook 単体を ``python <path> <targets>`` の形で起動し、frontmatter 不整合で exit 1、
正常 md で exit 0 になることを確認する。docsweep 本体への import 依存は持たない実装なので、
ここでは subprocess 経由で起動して exit code を見る。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


HOOK = (
    Path(__file__).resolve().parents[1]
    / "templates"
    / ".githooks"
    / "docsweep-check.py"
)


def _run(args: list[Path]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8",
    )


def test_hook_passes_valid_frontmatter(tmp_path: Path):
    p = tmp_path / "plan_ok.md"
    p.write_text(
        "---\n"
        "type: plan\n"
        "status: planned\n"
        "review_status: draft\n"
        "related: []\n"
        "---\n"
        "# [計画] OK\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 0, r.stderr


def test_hook_passes_when_no_frontmatter(tmp_path: Path):
    """frontmatter が無いファイル（旧来の H1 ラベルのみ）はスキップで OK。"""
    p = tmp_path / "plan_h1only.md"
    p.write_text("# [計画] H1 only\n", encoding="utf-8")
    r = _run([p])
    assert r.returncode == 0


def test_hook_allows_unknown_type(tmp_path: Path):
    p = tmp_path / "plan_bad.md"
    p.write_text(
        "---\ntype: weirdtype\nstatus: planned\n---\n# [計画] bad\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 0, r.stderr


def test_hook_fails_on_invalid_status(tmp_path: Path):
    p = tmp_path / "plan_badstatus.md"
    p.write_text(
        "---\ntype: plan\nstatus: notavalue\n---\n# [計画] x\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 1
    assert "status=" in r.stderr


def test_hook_fails_on_invalid_review_status(tmp_path: Path):
    p = tmp_path / "plan_badreview.md"
    p.write_text(
        "---\ntype: plan\nstatus: planned\nreview_status: weird\n---\n# [計画] x\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 1
    assert "review_status" in r.stderr


def test_hook_fails_on_missing_related(tmp_path: Path):
    p = tmp_path / "plan_relbad.md"
    p.write_text(
        "---\ntype: plan\nstatus: planned\nrelated: [does_not_exist.md]\n---\n"
        "# [計画] x\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 1
    assert "related" in r.stderr


def test_hook_passes_with_existing_related(tmp_path: Path):
    other = tmp_path / "plan_other.md"
    other.write_text("# [計画] other\n", encoding="utf-8")
    p = tmp_path / "plan_main.md"
    p.write_text(
        "---\ntype: plan\nstatus: planned\nrelated: [plan_other.md]\n---\n"
        "# [計画] main\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 0, r.stderr


def _delegated_plan(*, c_heading: str = "### C1 実装", completion: str = "観測可能な完了結果。") -> str:
    return (
        "---\n"
        "type: plan\n"
        "status: draft\n"
        "docsweep_state: planned\n"
        "review_status: draft\n"
        "related: []\n"
        "docsweep_delegation: external\n"
        "---\n"
        "# [計画] delegated\n\n"
        "## context配分\n\n"
        "| C | 種別 | 内容 | 備考/注意点 |\n"
        "|---|---|---|---|\n"
        "| C1 | planned | 実装 | — |\n\n"
        "## 概要\n\n概要。\n\n"
        "## C 詳細\n\n"
        f"{c_heading}\n\n"
        "#### 目的\n\n目的を一つに定める。\n\n"
        "#### 調査で確認した現在の実装\n\n現在の実装を確認した。\n\n"
        "#### 作業内容\n\n対象を変更する。\n\n"
        "#### 変更予定ファイル\n\n- `docsweep/example.py`\n\n"
        "#### 維持する仕様\n\n既存の仕様を維持する。\n\n"
        "#### スコープ外\n\n関連しない変更は扱わない。\n\n"
        "#### 検証方法\n\npytest を実行する。\n\n"
        f"#### 完了条件\n\n{completion}\n"
    )


def test_hook_rejects_delegated_plan_without_c_detail(tmp_path: Path):
    p = tmp_path / "plan_missing_detail.md"
    p.write_text(
        "---\ntype: plan\nstatus: draft\ndocsweep_delegation: external\n---\n"
        "# [計画] missing\n",
        encoding="utf-8",
    )

    r = _run([p])

    assert r.returncode == 1
    assert "C 詳細" in r.stderr


def test_hook_rejects_context_detail_mismatch_and_todo(tmp_path: Path):
    p = tmp_path / "plan_bad_delegate.md"
    body = _delegated_plan().replace("| C1 | planned", "| C2 | planned")
    p.write_text(body.replace("#### 検証方法", "#### 維持する仕様"), encoding="utf-8")

    r = _run([p])

    assert r.returncode == 1
    assert "context配分" in r.stderr
    assert "がありません" in r.stderr or "未記入" in r.stderr


def test_hook_accepts_filled_delegated_plan(tmp_path: Path):
    p = tmp_path / "plan_delegate_ok.md"
    p.write_text(_delegated_plan(), encoding="utf-8")

    r = _run([p])

    assert r.returncode == 0, r.stderr


def test_hook_warns_on_ambiguous_completion_without_failing(tmp_path: Path):
    p = tmp_path / "plan_delegate_ambiguous.md"
    p.write_text(_delegated_plan(completion="正しく動く"), encoding="utf-8")

    r = _run([p])

    assert r.returncode == 0
    assert "正しく動く" in r.stderr


def test_hook_warns_on_classification_word_in_h3_without_failing(tmp_path: Path):
    p = tmp_path / "plan_delegate_heading_warning.md"
    p.write_text(_delegated_plan(c_heading="### C1 検証"), encoding="utf-8")

    r = _run([p])

    assert r.returncode == 0
    assert "分類語" in r.stderr
