"""stdin が人の操作する端末かを判定する（確認を出してよいか）。"""

from __future__ import annotations

import io
import os
import sys


def _windows_fd_is_console(fd: int) -> bool:
    # sys.platform で分けると、mypy が Windows 以外ではこの先を読み飛ばす（msvcrt と
    # ctypes.windll は Windows にしか無いので、分けないと Linux の型検査が落ちる）
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(fd)
        mode = ctypes.c_uint32()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except (OSError, ValueError, AttributeError):
        return False


def stdin_is_interactive() -> bool:
    """stdin が対話端末なら True。

    Windows の NUL はキャラクタデバイスなので ``isatty()`` が True を返す。AI エージェントの
    シェル実行は stdin を NUL につなぐため、``isatty()`` だけで判定すると非対話の実行で確認を
    出して止まる（2026-09-15〜09-23 に 4 回）。本物のファイル記述子を持つ stdin は、Windows では
    コンソールかどうか（GetConsoleMode）まで確かめる。fileno を持たない差し替え（テスト等）は
    ``isatty()`` の答えをそのまま使う。
    """
    stdin = sys.stdin
    if not bool(getattr(stdin, "isatty", lambda: False)()):
        return False
    if os.name != "nt":
        return True
    try:
        fd = stdin.fileno()
    except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
        return True
    return _windows_fd_is_console(fd)
