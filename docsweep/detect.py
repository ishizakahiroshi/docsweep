"""ステータス検出: frontmatter > H1 > filename（明示が強い）。

3 方式は同時併用可。食い違ったら CONFLICT フラグで可視化する（自動では直さない）。
概要は規約で必須セクションが固定なので、AI 要約なしで先頭 1〜2 行を機械抽出する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from .config import TypeDef
from .services.frontmatter import read_frontmatter_text
from .states import StateModel

_H1_RE = re.compile(r"^#\s+(.*)$", re.MULTILINE)
_H1_LABEL_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")
# fenced code block の開始/終了マーカー（``` か ~~~ が 3 個以上・先頭インデント許容）。
_FENCE_TOKEN_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
# HTML コメント形式のメタ: <!--docsweep-meta ... -->（内側 YAML 本体を group(1) で取る）。
# md の frontmatter を持てない生成物（design-html / review-sheet / mockup 等）用。
_HTML_META_RE = re.compile(r"<!--\s*docsweep-meta\s*\n(.*?)\n\s*-->", re.DOTALL)


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
    due: str | None = None  # frontmatter due: YYYY-MM-DD（None = 未記入）
    due_parse_error: bool = False  # due フィールドがあるがパース不能
    # OKF（Open Knowledge Format）併用フィールド。frontmatter にあれば取り込む。
    # 旧来の H1 ラベル運用のみのファイルでは全て空値（後方互換）。
    tags: list[str] = field(default_factory=list)
    owner: str | None = None
    review_status: str | None = None  # draft / review / published / archived 等の自由値
    related: list[str] = field(default_factory=list)
    last_reviewed: str | None = None  # YYYY-MM-DD（パース失敗時は素の文字列で保持）
    frontmatter_type: str | None = None  # frontmatter の type 値（plan/bugfix/pending 等）
    type_conflict: bool = False  # frontmatter type と filename 由来 type が食い違う
    frontmatter_warnings: list[str] = field(default_factory=list)  # warn 文字列の生コピー
    # sweep 挙動の指示。既定は「関連リリース or 親 plan が archive されたら道連れ」= None 相当。
    # ``never_archive`` を指定すると sweep/promote の archive 移送対象から外れる（可視化はする）。
    docsweep_policy: str | None = None


def _read_head(text: str, limit: int = 8000) -> str:
    return text[:limit]


def _parse_frontmatter_dict(text: str) -> dict | None:
    """先頭の YAML frontmatter または HTML の ``<!--docsweep-meta ... -->`` を dict で返す。

    - .md 等の frontmatter を持てるファイル: 先頭 ``---\\n...\\n---`` を YAML として読む
    - .html 等 frontmatter を持てないファイル: 先頭近くの ``<!--docsweep-meta\\n...\\n-->`` を
      YAML として読む（design-html / review-sheet / mockup / recap-html 等の生成物向け）

    どちらのブロックも無ければ ``None``。既存の ``_detect_frontmatter`` / ``_extract_due`` は
    各々 yaml.safe_load を独立に呼ぶが、OKF 拡張フィールド（type/tags/owner/review_status/
    related/last_reviewed/docsweep_policy）の取り込みは 1 回パースしたものを共有する方が素直
    なので、共有ヘルパとして導入する。
    """
    data, body = read_frontmatter_text(text)
    if body != text:
        return data

    yaml_body: str | None = None
    # HTML の docsweep-meta コメントは先頭 4KB 以内に置く運用（生成テンプレ側で保証）。
    head = text[:4000]
    hm = _HTML_META_RE.search(head)
    if hm:
        yaml_body = hm.group(1)
    if yaml_body is None:
        return None
    try:
        data = yaml.safe_load(yaml_body) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _coerce_str_list(raw: object) -> list[str]:
    """frontmatter の ``tags:`` / ``related:`` を文字列リスト化する。

    YAML では ``tags: [a, b]`` / ``tags:\\n  - a\\n  - b`` / ``tags: a`` の 3 形式が混じる。
    単一文字列はスカラ → 1 要素リストへ昇格、None は空リスト、それ以外は str() 化して拾う。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for v in raw:
            if v is None:
                continue
            s = str(v).strip()
            if s:
                out.append(s)
        return out
    s = str(raw).strip()
    return [s] if s else []


def _coerce_date_str(raw: object) -> str | None:
    """``last_reviewed`` を ``YYYY-MM-DD`` 文字列化する。パース不能なら素の文字列を返す。"""
    if raw is None:
        return None
    if hasattr(raw, "isoformat"):
        try:
            return raw.isoformat()
        except (TypeError, ValueError):
            return str(raw)
    return str(raw).strip() or None


_ALLOWED_POLICIES: frozenset[str] = frozenset({"archive_with_release", "never_archive"})


def _extract_okf_fields(data: dict | None) -> dict:
    """frontmatter dict から OKF 拡張フィールドを取り出す（無いキーは既定値）。"""
    if not data:
        return {
            "tags": [], "owner": None, "review_status": None,
            "related": [], "last_reviewed": None, "frontmatter_type": None,
            "docsweep_policy": None,
        }
    owner_raw = data.get("owner")
    review_raw = data.get("review_status")
    type_raw = data.get("type")
    policy_raw = data.get("docsweep_policy")
    policy: str | None = None
    if policy_raw is not None:
        s = str(policy_raw).strip()
        if s in _ALLOWED_POLICIES:
            policy = s
    return {
        "tags": _coerce_str_list(data.get("tags")),
        "owner": (str(owner_raw).strip() or None) if owner_raw is not None else None,
        "review_status": (str(review_raw).strip() or None) if review_raw is not None else None,
        "related": _coerce_str_list(data.get("related")),
        "last_reviewed": _coerce_date_str(data.get("last_reviewed")),
        "frontmatter_type": (str(type_raw).strip() or None) if type_raw is not None else None,
        "docsweep_policy": policy,
    }


def _detect_frontmatter(text: str, sm: StateModel) -> str | None:
    data = _parse_frontmatter_dict(text)
    if not data:
        return None
    status = data.get("status")
    if status is None:
        return None
    s = sm.match(str(status))
    return s.key if s else None


def _extract_due(text: str) -> tuple[str | None, bool]:
    """frontmatter から due: YYYY-MM-DD を抽出する。

    Returns:
        (due_str, parse_error)
        due_str  — "YYYY-MM-DD" 文字列（due フィールドが無い場合は None）
        parse_error — due フィールドは存在するが YYYY-MM-DD に変換できない場合 True
    """
    data = _parse_frontmatter_dict(text)
    if not data or "due" not in data:
        return None, False
    raw = data["due"]
    if raw is None:
        return None, False
    # YAML は YYYY-MM-DD を datetime.date に自動変換する。
    if hasattr(raw, "isoformat"):
        return raw.isoformat(), False
    # 文字列で来た場合は YYYY-MM-DD 形式かチェックする。
    s = str(raw).strip()
    import re as _re
    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s, False
    return None, True


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


def _detect_filename(filename: str, sm: StateModel, type_name: str | None = None) -> str | None:
    """ファイル名プレフィックス方式（例 done_plan_xxx.md）。

    先頭セグメントが「種別名そのもの」（pending_*.md の "pending" 等）の場合は type 接頭辞で
    あって state 接頭辞ではないので state とみなさない。これがないと pending 種別の語が state
    "pending" と衝突し、H1 が [保留] 以外の pending_*.md に誤って CONFLICT が立つ。
    """
    head = filename.split("_", 1)[0]
    if type_name and head == type_name:
        return None
    s = sm.match(head)
    return s.key if s else None


def detect_status(
    *, text: str, filename: str, sm: StateModel, _type: TypeDef | None = None
) -> Detection:
    text = _read_head(text)
    parse_error = False

    fm = _detect_frontmatter(text, sm)
    h1_key, h1_token, title = _detect_h1(text, sm)
    fn = _detect_filename(filename, sm, _type.name if _type else None)
    due, due_parse_error = _extract_due(text)

    fm_dict = _parse_frontmatter_dict(text)
    okf = _extract_okf_fields(fm_dict)
    warnings: list[str] = []

    # frontmatter type と filename 由来 type の食い違いを warn 扱いで surface する。
    # 自動上書きしない（plan_okf-adoption_2026-06-29.md C1 の方針）。
    fm_type = okf["frontmatter_type"]
    type_conflict = False
    if fm_type and _type and fm_type != _type.name:
        type_conflict = True
        warnings.append(
            f"frontmatter type='{fm_type}' と filename 由来 type='{_type.name}' が食い違います"
        )
    # status の frontmatter vs H1 ラベル食い違いも warn として明示する
    # （既存の `conflict` フラグだけだと「どこが」分からないため）。
    if fm is not None and h1_key is not None and fm != h1_key:
        warnings.append(
            f"frontmatter status='{fm}' と H1 ラベル由来 status='{h1_key}' が食い違います"
        )

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
        due=due,
        due_parse_error=due_parse_error,
        tags=okf["tags"],
        owner=okf["owner"],
        review_status=okf["review_status"],
        related=okf["related"],
        last_reviewed=okf["last_reviewed"],
        frontmatter_type=okf["frontmatter_type"],
        type_conflict=type_conflict,
        frontmatter_warnings=warnings,
        docsweep_policy=okf["docsweep_policy"],
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
