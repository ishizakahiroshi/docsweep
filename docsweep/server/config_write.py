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

from ..config import GLOBAL_CONFIG_PATH

# トップレベル ``roots:`` キーのブロック（ブロック形式の続き行 = インデント行 or リスト行、
# または同一行 flow 形式）にマッチする。続き行には空行を含めない（次キーとの境界を保つ）。
_ROOTS_BLOCK_RE = re.compile(
    r"^roots:[^\n]*\n(?:[ \t]+[^\n]*\n|[ \t]*-[^\n]*\n)*",
    re.MULTILINE,
)


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
        if _ROOTS_BLOCK_RE.search(text):
            new_text = _ROOTS_BLOCK_RE.sub(block, text, count=1)
        else:
            sep = "" if (not text or text.endswith("\n")) else "\n"
            new_text = text + sep + block
    else:
        new_text = block

    parsed = yaml.safe_load(new_text)
    if not isinstance(parsed, dict) or "roots" not in parsed:
        raise ValueError("roots 置換後の config.yaml が不正です（書き込みを中止しました）")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return path
