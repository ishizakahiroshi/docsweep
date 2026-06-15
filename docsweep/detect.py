"""ステータス検出: frontmatter > H1 > filename（明示が強い）。

3 方式は同時併用可。食い違ったら CONFLICT フラグで可視化する（自動では直さない）。
概要は規約で必須セクションが固定なので、AI 要約なしで先頭 1〜2 行を機械抽出する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from .config import TypeDef
from .states import StateModel

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.*)$", re.MULTILINE)
_H1_LABEL_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
# fenced code block の開始/終了マーカー（``` か ~~~ が 3 個以上・先頭インデント許容）。
_FENCE_TOKEN_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def mask_code_fences(text: str) -> str:
    """fenced code block の中身を同じ長さの空白へ置換する（改行・全体長は保存）。

    コードブロック内の ``# ...`` 行を H1 や見出しと誤検出しないための前処理。長さを保つので
    マスク後に得たマッチ位置（start/end）を**原文 text にそのまま流用**できる。
    """
    lines = text.split("\n")
    in_fence = False
    marker = ""
    for i, line in enumerate(lines):
        m = _FENCE_TOKEN_RE.match(line)
        if not in_fence:
            if m:
                in_fence = True
                marker = m.group(1)[0]
                lines[i] = " " * len(line)
        else:
            closing = bool(m and m.group(1)[0] == marker)
            lines[i] = " " * len(line)
            if closing:
                in_fence = False
    return "\n".join(lines)


@dataclass
class Detection:
    state_key: str | None
    state_label: str | None  # 表示用ブラケット付き（例 "[完了]"）
    source: str  # frontmatter | h1 | filename | none
    title: str | None
    conflict: bool  # 複数方式で検出した状態が食い違ったか
    parse_error: bool


def _read_head(text: str, limit: int = 8000) -> str:
    return text[:limit]


def _detect_frontmatter(text: str, sm: StateModel) -> str | None:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    if status is None:
        return None
    s = sm.match(str(status))
    return s.key if s else None


def _detect_h1(text: str, sm: StateModel) -> tuple[str | None, str | None, str | None]:
    """H1 から (state_key, label_token, title) を返す。"""
    # コードブロック内の '# ...' を H1 と誤認しないようマスクしてから探す。
    m = _H1_RE.search(mask_code_fences(text))
    if not m:
        return None, None, None
    h1 = m.group(1).strip()
    lm = _H1_LABEL_RE.match(h1)
    if not lm:
        # ラベルなし H1。タイトルだけ取れる。
        return None, None, h1
    token, title = lm.group(1).strip(), lm.group(2).strip()
    s = sm.match(token)
    return (s.key if s else None), token, (title or None)


def _detect_filename(filename: str, sm: StateModel) -> str | None:
    """ファイル名プレフィックス方式（例 done_plan_xxx.md）。"""
    head = filename.split("_", 1)[0]
    s = sm.match(head)
    return s.key if s else None


def detect_status(
    *, text: str, filename: str, sm: StateModel, _type: TypeDef | None = None
) -> Detection:
    text = _read_head(text)
    parse_error = False

    fm = _detect_frontmatter(text, sm)
    h1_key, h1_token, title = _detect_h1(text, sm)
    fn = _detect_filename(filename, sm)

    # 検出された候補（None 以外）が複数あり食い違うか。
    candidates = [c for c in (fm, h1_key, fn) if c is not None]
    conflict = len(set(candidates)) > 1

    # 優先順位 frontmatter > H1 > filename。
    if fm is not None:
        key, source = fm, "frontmatter"
    elif h1_key is not None:
        key, source = h1_key, "h1"
    elif fn is not None:
        key, source = fn, "filename"
    else:
        key, source = None, "none"

    # ラベルらしき H1 はあるが state に解決できない → パース要修正扱い。
    if key is None and h1_token is not None:
        parse_error = True

    label = f"[{h1_token}]" if (source == "h1" and h1_token) else None
    if label is None and key is not None:
        st = sm.by_key(key)
        if st:
            label = f"[{st.label()}]"

    return Detection(
        state_key=key,
        state_label=label,
        source=source,
        title=title,
        conflict=conflict,
        parse_error=parse_error,
    )


def extract_summary(text: str, section: str) -> str | None:
    """``## <section>`` セクション直下の先頭 1〜2 行（非空）を返す。"""
    # コードフェンスの ``` 行やフェンス内見出しを概要本文に拾わないようマスクする。
    text = mask_code_fences(text)
    pat = re.compile(rf"^#{{2,3}}\s+{re.escape(section)}\s*$", re.MULTILINE)
    m = pat.search(text)
    if not m:
        return None
    rest = text[m.end():]
    lines: list[str] = []
    for raw in rest.splitlines():
        line = raw.strip()
        if line.startswith("#"):  # 次の見出しに到達
            break
        if not line:
            if lines:
                break
            continue
        # 引用メタ行（> 最終更新: ...）はスキップ。
        if line.startswith(">"):
            continue
        lines.append(line)
        if len(lines) >= 2:
            break
    if not lines:
        return None
    return " ".join(lines)
