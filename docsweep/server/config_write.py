"""Web UI からの ``~/.docsweep/config.yaml`` 書き換え（roots のみ・surgical）。

設計の正本: docs/local/plan_web-roots-management.md §C1

不変条件:
- 書き換えるのは ``roots:`` トップレベルキーだけ。他キー・コメント行は一切触らない
  （yaml 全体を dump し直すとユーザーの手書きコメント・ひな型コメントが消えるため、
  テキストレベルで該当ブロックのみ置換する）。
- 置換結果は必ず ``yaml.safe_load`` で検証してから書き込む（壊れた yaml を残さない）。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..atomic import write_atomic
from ..config import GLOBAL_CONFIG_PATH

# トップレベル ``roots:`` キーのブロック。続き行はリスト項目だけでなく、その間に挟まった
# インデント付きコメント行も含める。空行は次キーとの境界として残す。
#
# リスト項目だけを続き行とすると、``roots:`` の途中にコメントが 1 行あるだけでブロックが
# そこで切れ、**その後ろのリスト項目が置換されずに残る**。利用者から見ると Web UI で外した
# はずの root が消えず、しかも yaml としては妥当なので検証も通る（F-05・2026-07-21 監査）。
_ROOTS_BLOCK_RE = re.compile(
    r"^roots:[^\n]*\n(?:[ \t]+(?:-|#)[^\n]*\n)*", re.MULTILINE
)
_ROOTS_COMMENT_RE = re.compile(r"^[ \t]+#[^\n]*$", re.MULTILINE)


def _render_roots_block(roots: list[Path]) -> str:
    lines = ["roots:"]
    for r in roots:
        lines.append(f"  - {r.as_posix()}")
    return "\n".join(lines) + "\n"


def update_global_roots(roots: list[Path], *, config_path: Path | None = None) -> Path:
    """グローバル config の ``roots:`` キーだけを差し替える（他キー・コメント温存）。

    ファイルが無ければ roots だけの新規ファイルを作る。置換後の全文は yaml として
    検証し、パース不能なら書き込まず ValueError を投げる（安全側で失敗）。
    """
    path = (config_path or GLOBAL_CONFIG_PATH).expanduser()
    block = _render_roots_block(roots)

    if path.is_file():
        text = path.read_text(encoding="utf-8")
        match = _ROOTS_BLOCK_RE.search(text)
        if match:
            # ブロック内に挟まっていたコメントは失わない。元の位置（項目の間）へは
            # 戻せないので、新しいリストの直後へまとめて置く。
            kept = _ROOTS_COMMENT_RE.findall(match.group(0))
            replacement = block + ("".join(f"{c}\n" for c in kept) if kept else "")
            new_text = text[: match.start()] + replacement + text[match.end() :]
        else:
            sep = "" if (not text or text.endswith("\n")) else "\n"
            new_text = text + sep + block
    else:
        new_text = block

    parsed = yaml.safe_load(new_text)
    if not isinstance(parsed, dict) or "roots" not in parsed:
        raise ValueError("roots 置換後の config.yaml が不正です（書き込みを中止しました）")

    write_atomic(path, new_text, encoding="utf-8")
    return path
