"""stdin の対話判定（確認を出してよいか）の回帰防止。

Windows の NUL は isatty() が True を返すため、AI エージェントの非対話実行で
docsweep new が release tracking の確認を出して止まっていた。
"""

from __future__ import annotations

import os
import sys

from docsweep.cli.interactive import stdin_is_interactive


class _FakeTTY:
    def isatty(self) -> bool:
        return True


class _FakePipe:
    def isatty(self) -> bool:
        return False


def test_null_device_stdin_is_not_interactive(monkeypatch) -> None:
    # Windows では NUL の isatty() は True。Linux/macOS では /dev/null は False。どちらでも非対話。
    with open(os.devnull, encoding="utf-8") as devnull:
        monkeypatch.setattr(sys, "stdin", devnull)
        assert stdin_is_interactive() is False


def test_pipe_stdin_is_not_interactive(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", _FakePipe())
    assert stdin_is_interactive() is False


def test_substituted_tty_without_fileno_is_trusted(monkeypatch) -> None:
    # テストで sys.stdin を isatty だけの偽物に差し替える既存の書き方を壊さない。
    monkeypatch.setattr(sys, "stdin", _FakeTTY())
    assert stdin_is_interactive() is True
