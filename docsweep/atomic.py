"""アトミック書き込み + 楽観ロック（mtime check） + バックアップ。

全ての書き込み API はこのヘルパ経由で MD を更新する。Web UI と MCP どちらから書いても安全。

- アトミック書き込み: 同ディレクトリに一時ファイル作成 → ``os.replace`` で差し替え。
  Windows でも ``os.replace`` は atomic（NTFS の ``MoveFileEx`` 経由）。
- 楽観ロック: ``expected_mtime`` を任意引数で受け取り、不一致なら ``ConflictError``。
  Web UI からは必ず送る（読み込み時の mtime を保持）。CLI / MCP は省略可。
- バックアップ: 全書き込み前に ``.docsweep/backup/`` へコピー。既定 30 日保持・自動掃除。
- 行単位編集 vs 全置換:
  - ``update_line`` は frontmatter `due:` 1 行や H1 ラベル先頭だけを置換（本文を触らない）
  - ``write_atomic`` は全置換（Web UI の本文編集ペインから呼ぶ）
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

BACKUP_DIR_NAME = "backup"
BACKUP_RETENTION_SECONDS = 30 * 24 * 60 * 60  # 30 日


class ConflictError(Exception):
    """expected_mtime と実 mtime が一致しないときに発生（楽観ロックの不一致）。"""

    def __init__(self, path: Path, expected: float, actual: float) -> None:
        super().__init__(
            f"mtime conflict at {path}: expected={expected!r} actual={actual!r}"
        )
        self.path = path
        self.expected = expected
        self.actual = actual


def _mtime(path: Path) -> float:
    return path.stat().st_mtime


def _project_root_for(path: Path) -> Path | None:
    """書き込み対象 path から ``.docsweep/`` を置くプロジェクト境界を辿る。

    ``backup`` の置き場決めにだけ使うので、見つからなければ path の親で代用する
    （バックアップを取らないより取った方が安全という選択）。
    """
    cur = path.parent.resolve()
    while True:
        for marker in (".docsweep.yaml", ".git", "pyproject.toml", "package.json"):
            if (cur / marker).exists():
                return cur
        parent = cur.parent
        if parent == cur:
            return None
        cur = parent


def backup_dir_for(path: Path) -> Path:
    root = _project_root_for(path) or path.parent
    return root / ".docsweep" / BACKUP_DIR_NAME


def backup(path: Path) -> Path | None:
    """書き込み直前に呼ぶ。``.docsweep/backup/<filename>.<unix_ns>`` へコピー。

    対象が存在しない（新規作成）場合は何もしないで None を返す。古いバックアップは
    呼び出しのついでに掃除する（30 日超で自動削除）。

    サフィックスは秒精度だと sweep が同一ファイルを短時間に 2 回書くケース（relabel →
    archive 移送）で前世代を上書きしてしまうため、``time.time_ns()`` のナノ秒精度に上げる。
    """
    if not path.is_file():
        return None
    dst_dir = backup_dir_for(path)
    dst_dir.mkdir(parents=True, exist_ok=True)
    ts = time.time_ns()
    dst = dst_dir / f"{path.name}.{ts}"
    shutil.copy2(str(path), str(dst))
    _cleanup_backups(dst_dir)
    return dst


def _cleanup_backups(dst_dir: Path) -> None:
    """30 日経過したバックアップを削除する（best-effort・失敗無視）。"""
    cutoff = time.time() - BACKUP_RETENTION_SECONDS
    try:
        for entry in dst_dir.iterdir():
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                pass
    except OSError:
        pass


def write_atomic(
    path: Path,
    content: str,
    *,
    expected_mtime: float | None = None,
    encoding: str = "utf-8",
    take_backup: bool = True,
) -> float:
    """``path`` の内容を ``content`` で全置換する。書き込み後の新 mtime を返す。

    ``expected_mtime`` が指定され、実 mtime と一致しなければ ``ConflictError``。
    ``take_backup=True`` のとき、既存ファイルを `.docsweep/backup/` へ退避する。
    """
    path = Path(path)
    if expected_mtime is not None and path.is_file():
        actual = _mtime(path)
        if not _mtime_close(actual, expected_mtime):
            raise ConflictError(path, expected_mtime, actual)
    if take_backup:
        backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            fh.write(content)
        os.replace(tmp_name, str(path))
    except Exception:
        # 一時ファイルが残らないよう片付ける。
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return _mtime(path)


def update_line(
    path: Path,
    *,
    transform,
    expected_mtime: float | None = None,
    take_backup: bool = True,
) -> float:
    """``transform(text) -> new_text`` を経由して 1 ファイルを書き換える。

    呼び出し側の transform 関数が「frontmatter `due:` を 1 行だけ差し替える」「H1 行先頭の
    ラベルだけ差し替える」のような行単位の正規表現置換を行うため、本ヘルパは
    アトミック書き込み・楽観ロック・バックアップだけを担う。
    """
    path = Path(path)
    if expected_mtime is not None and path.is_file():
        actual = _mtime(path)
        if not _mtime_close(actual, expected_mtime):
            raise ConflictError(path, expected_mtime, actual)
    text = path.open("r", encoding="utf-8", newline="").read()
    new_text = transform(text)
    if new_text == text:
        # 変更なし。書き込みも backup も省略する。
        return _mtime(path)
    if take_backup:
        backup(path)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return _mtime(path)


def _mtime_close(a: float, b: float, *, tol: float = 0.001) -> bool:
    """ファイルシステムの mtime 解像度（Windows FAT は 2 秒・NTFS は 100ns）を考慮した近似比較。

    そのまま `==` で比較すると、JSON 経由で round-trip した mtime と実 mtime が
    sub-millisecond オーダーで微妙にズレて誤検出する。1ms 以内なら同一とみなす。
    """
    return abs(a - b) < tol
