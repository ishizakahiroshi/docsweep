"""``update_content`` — MD 本文の全置換（楽観ロック必須・Web UI の本文編集ペインから呼ぶ）。

- ``atomic.write_atomic`` 経由でアトミック書き込み + 楽観ロック
- バリデーション: 完全空（0 バイト）は拒否、H1 が消えていたら警告（拒否はしない）
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..atomic import write_atomic
from ..config import Config, config_for_project
from ..detect import _H1_RE, mask_code_fences
from ..work_queue import ensure_write_allowed, find_project_dir


class ContentValidationError(ValueError):
    """new_content がバリデーションに引っかかったときに発生。"""


@dataclass
class UpdateContentResult:
    path: str
    new_mtime: float
    new_sha256: str
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "new_mtime": self.new_mtime,
            "new_sha256": self.new_sha256,
            "warnings": list(self.warnings),
        }


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def update_content(
    abs_path: Path,
    new_content: str,
    *,
    expected_mtime: float | None = None,
    config: Config | None = None,
    allow_sensitive: bool = False,
) -> UpdateContentResult:
    """MD 本文を全置換する。0 バイトは拒否・H1 欠落は警告のみ。

    Args:
        abs_path: 書き込み対象 MD の絶対パス（呼び出し側でスコープ境界検証済み前提）
        new_content: 新しい本文（UTF-8 文字列）
        expected_mtime: 楽観ロック用（Web UI からは必須・MCP は省略可）
    """
    if new_content == "":
        raise ContentValidationError("new_content が空です（0 バイト書き込みは拒否されます）")

    warnings: list[str] = []
    masked = mask_code_fences(new_content)
    if not _H1_RE.search(masked):
        warnings.append("H1 行が見つかりません（ステータスラベル抽出ができなくなる可能性）")
    from ..secrets_guard import enforce_secret_policy, format_warnings
    effective_config = config
    if config is not None:
        project_root = config.project_dir or find_project_dir(
            config=config, cwd=Path(abs_path).parent
        )
        effective_config = config_for_project(config, project_root)

    hits = enforce_secret_policy(
        new_content,
        policy=effective_config.secret_policy if effective_config is not None else "warn",
        allow_sensitive=allow_sensitive,
    )
    warnings.extend(format_warnings(hits))

    if config is not None:
        ensure_write_allowed(
            config=effective_config,
            project_dir=project_root,
            target_dir=Path(abs_path).parent,
            content=new_content,
            allow_sensitive=allow_sensitive,
        )

    new_mtime = write_atomic(Path(abs_path), new_content, expected_mtime=expected_mtime)
    return UpdateContentResult(
        path=Path(abs_path).resolve().as_posix(),
        new_mtime=new_mtime,
        new_sha256=_sha256_hex(new_content),
        warnings=warnings,
    )
