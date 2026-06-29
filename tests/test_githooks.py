"""配布物 pre-commit hook (``templates/.githooks/docsweep-check.py``) のテスト。

hook 単体を ``python <path> <targets>`` の形で起動し、frontmatter 不整合で exit 1、
正常 md で exit 0 になることを確認する。docsweep 本体への import 依存は持たない実装なので、
ここでは subprocess 経由で起動して exit code を見る。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

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


def test_hook_fails_on_invalid_type(tmp_path: Path):
    p = tmp_path / "plan_bad.md"
    p.write_text(
        "---\ntype: weirdtype\nstatus: planned\n---\n# [計画] bad\n",
        encoding="utf-8",
    )
    r = _run([p])
    assert r.returncode == 1
    assert "type=" in r.stderr


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
