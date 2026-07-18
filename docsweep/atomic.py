"""アトミック書き込み + 楽観ロック（mtime check）。

全ての書き込み API はこのヘルパ経由で MD を更新する。Web UI と MCP どちらから書いても安全。

- アトミック書き込み: 同ディレクトリに一時ファイル作成 → ``os.replace`` で差し替え。
  Windows でも ``os.replace`` は atomic（NTFS の ``MoveFileEx`` 経由）。
- 楽観ロック: ``expected_mtime`` を任意引数で受け取り、不一致なら ``ConflictError``。
  Web UI からは必ず送る（読み込み時の mtime を保持）。CLI / MCP は省略可。
- 行単位編集 vs 全置換:
  - ``update_line`` は frontmatter `due:` 1 行や H1 ラベル先頭だけを置換（本文を触らない）
  - ``write_atomic`` は全置換（Web UI の本文編集ペインから呼ぶ）

以前は書き込み前に ``.docsweep/backup/`` へ md 丸ごとコピーを 30 日保持する backup 機構を
持っていたが、v0.4 で撤去した。理由: 実質 99% の書き込みは ``update_line`` 経由の 1 行差分
（H1 ラベル `[完了]` 化・`due:` 差替）で、その世代を全ファイル丸コピーで残すのは過剰。
また ``.docsweep/backup/`` を公開リポで gitignore し忘れて private md が意図せず push される
事故が実際に発生した（`docs/local/*.md` 系）。復元用途は git 側で本体を追う運用に一本化する。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


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


def write_atomic(
    path: Path,
    content: str,
    *,
    expected_mtime: float | None = None,
    encoding: str = "utf-8",
) -> float:
    """``path`` の内容を ``content`` で全置換する。書き込み後の新 mtime を返す。

    ``expected_mtime`` が指定され、実 mtime と一致しなければ ``ConflictError``。
    """
    path = Path(path)
    if expected_mtime is not None and path.is_file():
        actual = _mtime(path)
        if not _mtime_close(actual, expected_mtime):
            raise ConflictError(path, expected_mtime, actual)
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
) -> float:
    """``transform(text) -> new_text`` を経由して 1 ファイルを書き換える。

    呼び出し側の transform 関数が「frontmatter `due:` を 1 行だけ差し替える」「H1 行先頭の
    ラベルだけ差し替える」のような行単位の正規表現置換を行うため、本ヘルパは
    アトミック書き込み・楽観ロックだけを担う。
    """
    path = Path(path)
    if expected_mtime is not None and path.is_file():
        actual = _mtime(path)
        if not _mtime_close(actual, expected_mtime):
            raise ConflictError(path, expected_mtime, actual)
    text = path.open("r", encoding="utf-8", newline="").read()
    new_text = transform(text)
    if new_text == text:
        # 変更なし。書き込みも省略する。
        return _mtime(path)
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
