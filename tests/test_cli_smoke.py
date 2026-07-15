"""T-06: 全 CLI サブコマンドの ``--help`` smoke（M-14 対応）。

53+ あるサブコマンドが少なくとも「パーサ組み立てに失敗しない」ことを保証する。
実挙動は各機能テスト（test_engine / test_activity 等）に譲る。

サブコマンド一覧は ``build_parser()`` から動的に抽出するので、追加/削除に自動追従する。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from docsweep.cli import build_parser


def _subcommands() -> list[str]:
    """argparse の subparsers から実際に登録されているサブコマンド名を取り出す。"""
    parser = build_parser()
    sub_action = parser._subparsers._group_actions[0]  # type: ignore[attr-defined]
    return sorted(sub_action.choices.keys())


SUBCOMMANDS = _subcommands()


def test_subcommand_extraction_is_nonempty():
    """extraction 自体が動くこと（0 件回帰の早期検知）。"""
    assert len(SUBCOMMANDS) >= 40, f"サブコマンドが極端に減っている: {SUBCOMMANDS}"


@pytest.mark.parametrize("cmd", SUBCOMMANDS)
def test_cli_subcommand_help_exits_zero(cmd: str):
    """``python -m docsweep <cmd> --help`` が exit 0 で返る。

    パーサ組み立て時の import エラー・decorator ミス等はここで落ちる。
    """
    proc = subprocess.run(
        [sys.executable, "-m", "docsweep", cmd, "--help"],
        capture_output=True,
        timeout=10,
        text=True,
    )
    assert proc.returncode == 0, (
        f"docsweep {cmd} --help が exit {proc.returncode} で失敗。\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    # --help は少なくとも usage 行を stdout に出す
    assert "usage" in proc.stdout.lower() or proc.stdout.strip(), (
        f"docsweep {cmd} --help が空 stdout: stderr={proc.stderr}"
    )


def test_cli_root_help_exits_zero():
    """トップレベル ``python -m docsweep --help`` も exit 0。"""
    proc = subprocess.run(
        [sys.executable, "-m", "docsweep", "--help"],
        capture_output=True,
        timeout=10,
        text=True,
    )
    assert proc.returncode == 0
    assert "usage" in proc.stdout.lower()
