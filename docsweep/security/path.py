"""MCP / Web の書き込み系で共有するパス境界チェック。

不変条件（親 plan C6 と plan_v0.1.0 §8 / state-tag C3 の合成）:
- スキャンルート配下のみ書き込み可（``realpath`` 解決後にスコープ境界を検証）
- ``..`` を含むパスは拒否（明示的に脱出を試みる入力を弾く）
- ``.md`` ファイルのみ書き込み可（バイナリ拒否・設定ファイルは別系統）

サーバー閲覧用の :func:`docsweep.server.security.resolve_under_roots` と
コア責務を共有しつつ、書き込み系専用に「``..`` 明示拒否」「相対パス解決の基点」を
追加で面倒見る薄いラッパ。読み取り系（Web プレビュー）はサーバー側の同名関数を
そのまま使ってよい。
"""

from __future__ import annotations

import os
from pathlib import Path

from ..server.security import resolve_under_roots


class PathScopeError(PermissionError):
    """書き込み対象がスキャンルート外・``..`` を含む・``.md`` 以外のときに発生。"""


def resolve_writable_md(
    raw_path: str,
    *,
    roots: list[Path],
    base_dir: Path | None = None,
) -> Path:
    """``raw_path`` をスキャンルート配下の ``.md`` 絶対パスに解決する。

    Args:
        raw_path: 相対 or 絶対の文字列パス。
        roots: 書き込み許可するスキャンルート群（``config.roots``）。
        base_dir: ``raw_path`` が相対のとき結合する基点（既定 ``Path.cwd()``）。

    Returns:
        ``realpath`` 解決後の絶対 ``Path``（ルート配下が保証される）。

    Raises:
        PathScopeError: ``..`` を含む / 解決後にルート外 / ``.md`` 以外。
    """
    if not raw_path or not isinstance(raw_path, str):
        raise PathScopeError(f"path が空または不正です: {raw_path!r}")

    # ``..`` を明示的に弾く。realpath は ``..`` を解決してしまうので
    # 「外に出ようとした意図そのもの」を入力段階で拒否する（多層防御）。
    parts = Path(raw_path).parts
    if any(p == ".." for p in parts):
        raise PathScopeError(f"path に '..' を含むことはできません: {raw_path!r}")

    # 相対パスは base_dir 基準で解決（指定なし時は CWD）。
    p = Path(raw_path)
    if not p.is_absolute():
        base = base_dir or Path.cwd()
        p = (base / p)

    resolved = resolve_under_roots(str(p), roots)
    if resolved is None:
        # 詳細メッセージは出さない（root 一覧の漏洩を避ける・呼び出し側が必要に応じて足す）。
        raise PathScopeError(f"path はスキャンルート配下の .md である必要があります: {raw_path!r}")
    # ``os.path.realpath`` は str を返すので Path 化して返却。
    return Path(os.path.realpath(str(resolved)))
