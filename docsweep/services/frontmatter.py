"""``update_frontmatter_field`` — OKF frontmatter フィールドの個別書き換え。

C4 で Web UI からの tags/owner/related/review_status 編集を受けるための薄い service。
本文・H1 ラベル・他フィールドには触らず、指定された 1 フィールドだけを置換または挿入する。

- 行単位の YAML 操作（``atomic.update_line`` 経由・本文を触らない）
- frontmatter が無いファイルは新規に挿入する（``update_due`` の作法を踏襲）
- list 型（``tags`` / ``related``）はフロー記法 ``[a, b]`` でシリアライズする
- ``review_status`` / ``owner`` はスカラ（空文字を渡したら値を空に・行は残す）
- ``current_owner`` で git config / OS ログイン名から既定の owner 名を解決する
  （C2 の ``docsweep config user.name`` が来るまでの暫定動線）
"""

from __future__ import annotations

import getpass
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..atomic import update_line

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# 単純なスカラ / list / フィールド名のみ。任意キーは受けない（API 側で許可リスト管理）。
ALLOWED_FIELDS: frozenset[str] = frozenset(
    {"tags", "owner", "related", "review_status", "last_reviewed", "due"}
)
LIST_FIELDS: frozenset[str] = frozenset({"tags", "related"})

# YAML キー名は単語＋アンダースコアに限定（インジェクション防止）。
_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class FrontmatterValidationError(ValueError):
    """field 名や値が許容外のときに発生。"""


class FrontmatterBlockStyleError(ValueError):
    """手書き block-style list（``tags:\\n  - a``）の書き換え要求を拒否したとき発生。

    現在の実装はフロー記法（``tags: [a, b]``）前提で行単位置換するため、block 記法の
    継続行（``  - item``）を残したままキー行だけ書き換えると YAML パースが壊れる。
    そのような入力を受けたら破壊せず ValueError を投げてユーザーに気付かせる。
    """


def read_frontmatter_text(text: str) -> tuple[dict, str]:
    """テキスト先頭の YAML frontmatter と残りの本文を返す。

    frontmatter が無い場合は ``({}, text)``、YAML が不正または mapping 以外の場合は
    ``({}, body)`` を返す。後者では frontmatter の囲み自体は認識できているため、本文から
    ブロックを分離した状態を維持する。
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text
    body = text[match.end():]
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}, body
    if data is None:
        return {}, body
    if not isinstance(data, dict):
        return {}, body
    return data, body


def read_frontmatter(path: Path) -> dict | None:
    """ファイル先頭の YAML frontmatter を mapping として読む。

    frontmatter が無い、ファイルを読めない、YAML が不正、または YAML のルートが mapping
    でない場合は ``None`` を返す。
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return None
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    if data is None:
        return {}
    if not isinstance(data, dict):
        return None
    return data


@dataclass
class UpdateFrontmatterResult:
    path: str
    field: str
    old_value: str | list[str] | None
    new_value: str | list[str] | None
    new_mtime: float

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "field": self.field,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "new_mtime": self.new_mtime,
        }


def _validate_scalar(value: str) -> str:
    """改行・YAML 制御文字を含むスカラを拒否する。"""
    if "\n" in value or "\r" in value:
        raise FrontmatterValidationError("スカラ値に改行は含められません")
    # `:` や `#` を含む値は引用する判断が必要なので、ここでは簡易対応として弾く。
    # owner 名・YYYY-MM-DD・review_status の値域では `:` / `#` は使わない想定。
    if ":" in value or "#" in value:
        raise FrontmatterValidationError(
            "スカラ値に ':' / '#' は含められません（引用が必要なため拒否します）"
        )
    return value.strip()


def _validate_list_item(item: str) -> str:
    item = item.strip()
    if not item:
        raise FrontmatterValidationError("list 要素に空文字は入れられません")
    if "\n" in item or "\r" in item:
        raise FrontmatterValidationError("list 要素に改行は含められません")
    # フロー記法 ``[a, b]`` で安全に書ける文字に限定する（`,` `[` `]` `"` `'` を含めない）。
    if any(c in item for c in (",", "[", "]", '"', "'", "#")):
        raise FrontmatterValidationError(
            f"list 要素に使えない文字が含まれます: {item!r}"
        )
    return item


def _format_value(field: str, value) -> str:
    """frontmatter に書き込む YAML フラグメントを作る。

    - list フィールドは ``tags: [a, b]`` 形式
    - スカラは ``owner: hiroshi`` 形式（空値は ``owner: `` ＝行を残し値を空に）
    """
    if field in LIST_FIELDS:
        items = [_validate_list_item(str(v)) for v in (value or [])]
        return f"{field}: [{', '.join(items)}]"
    s = "" if value is None else _validate_scalar(str(value))
    if s:
        return f"{field}: {s}"
    return f"{field}: "


_FIELD_LINE_TEMPLATE = r"^(?P<indent>[ \t]*){name}[ \t]*:[ \t]*(?P<value>.*)$"

# block-style list の継続行検出（``  - item`` / ``  -\n`` / ``  -`` EOF）。
# 行頭スペース/タブ 1 個以上 + ``-`` + (空白 or 行末)。
_BLOCK_LIST_CONTINUATION_RE = re.compile(r"^[ \t]+-(?:[ \t]|$)")


def _field_line_re(field: str) -> re.Pattern[str]:
    if not _FIELD_NAME_RE.match(field):
        raise FrontmatterValidationError(f"不正なフィールド名: {field!r}")
    return re.compile(_FIELD_LINE_TEMPLATE.format(name=re.escape(field)), re.MULTILINE)


def _read_current(text: str, field: str) -> str | None:
    """frontmatter から指定フィールドの「行右辺」を生で返す（無ければ None）。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    inner = m.group(1)
    line_re = _field_line_re(field)
    lm = line_re.search(inner)
    if not lm:
        return None
    return (lm.group("value") or "").strip() or None


def _replace_or_insert(text: str, field: str, new_yaml_line: str) -> str:
    """frontmatter 内の field 行を置換、無ければ frontmatter を新設・追記する。

    ``update_due._replace_or_insert_due`` のロジックをそのまま field 別に汎用化した。
    リプレース時はインデント幅を保つ（ネストフィールド対応・通常は 0 幅）。
    """
    fm = _FRONTMATTER_RE.match(text)
    line_re = _field_line_re(field)
    if fm:
        inner = fm.group(1)
        m_line = line_re.search(inner)
        if m_line is not None:
            # 手書き block 記法（``field:\n  - a\n  - b``）検出: フロー記法前提の
            # 行単位置換では継続行が孤立して YAML が壊れるので、書き換えを拒否する。
            after = inner[m_line.end():]
            if after.startswith("\n"):
                after = after[1:]
            nl = after.find("\n")
            next_line = after if nl == -1 else after[:nl]
            if next_line and _BLOCK_LIST_CONTINUATION_RE.match(next_line):
                raise FrontmatterBlockStyleError(
                    f"frontmatter フィールド {field!r} が block-style list で書かれています "
                    f"（次行: {next_line!r}）。docsweep は現在フロー記法 "
                    f"（{field}: [a, b]）のみ書き換え可能です。"
                    " 手動で flow 記法へ変換してから再実行してください。"
                )
            indent = m_line.group("indent") or ""
            replacement = f"{indent}{new_yaml_line}"
            new_inner = inner[: m_line.start()] + replacement + inner[m_line.end():]
        else:
            sep = "" if inner.endswith("\n") or not inner else "\n"
            new_inner = f"{inner}{sep}{new_yaml_line}\n"
        head = "---\n"
        tail = "\n---\n" if not new_inner.endswith("\n") else "---\n"
        return head + new_inner + tail + text[fm.end():]
    return f"---\n{new_yaml_line}\n---\n{text}"


def update_frontmatter_field(
    abs_path: Path,
    field: str,
    new_value,
    *,
    expected_mtime: float | None = None,
) -> UpdateFrontmatterResult:
    """frontmatter の 1 フィールドだけを書き換える（本文・H1・他フィールドは温存）。

    Args:
        abs_path: 書き込み対象 MD の絶対パス（呼び出し側でスコープ境界検証済み前提）
        field: ``tags`` / ``owner`` / ``related`` / ``review_status`` / ``last_reviewed``
        new_value: list フィールドは ``list[str]``、スカラは ``str`` または ``None``
        expected_mtime: 楽観ロック用（None で強制上書き）
    """
    if field not in ALLOWED_FIELDS:
        raise FrontmatterValidationError(
            f"許可されていないフィールド名: {field!r}（許可: {sorted(ALLOWED_FIELDS)}）"
        )
    new_line = _format_value(field, new_value)
    text_before = Path(abs_path).open("r", encoding="utf-8", newline="").read()
    old_raw = _read_current(text_before, field)

    def _xform(text: str) -> str:
        return _replace_or_insert(text, field, new_line)

    new_mtime = update_line(Path(abs_path), transform=_xform, expected_mtime=expected_mtime)

    # 返却用 old/new の正規化（list/scalar/None）。
    def _normalize(v) -> str | list[str] | None:
        if field in LIST_FIELDS:
            if v is None:
                return []
            if isinstance(v, list):
                return [str(x) for x in v]
            return [v]
        if v is None or v == "":
            return None
        return str(v)

    return UpdateFrontmatterResult(
        path=Path(abs_path).resolve().as_posix(),
        field=field,
        old_value=_normalize(old_raw),
        new_value=_normalize(new_value),
        new_mtime=new_mtime,
    )


# ------------------------------------------------------------------
# 現在ユーザー名の解決（claim/unclaim 用・C2 の `docsweep config user.name` が来るまでの暫定）
# ------------------------------------------------------------------


def _git_user_name(cwd: Path | None = None) -> str | None:
    """`git config user.name` を読む。失敗（git 未導入・未設定）したら None。"""
    try:
        result = subprocess.run(
            ["git", "config", "--get", "user.name"],
            capture_output=True, text=True, timeout=2, check=False,
            cwd=str(cwd) if cwd else None,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    name = (result.stdout or "").strip()
    return name or None


def _os_login() -> str | None:
    try:
        return getpass.getuser() or None
    except Exception:  # noqa: BLE001 - 一部 CI 環境で例外が出る
        try:
            return os.environ.get("USERNAME") or os.environ.get("USER") or None
        except Exception:  # noqa: BLE001
            return None


def current_owner(cwd: Path | None = None) -> str:
    """claim/unclaim で使う「現在ユーザー名」を返す。

    優先順位 (C2 で完成):
    1. ``docsweep config user.name``（``~/.docsweep/config.yaml`` の ``user.name``）
    2. ``git config user.name``
    3. OS ログイン名
    4. ``"unknown"``
    """
    # 1 の解決は config.get_user_setting に集約（循環 import を避けるため遅延 import）。
    try:
        from ..config import get_user_setting
        configured = get_user_setting("user.name")
    except Exception:  # noqa: BLE001 - 設定ファイル破損時も claim を止めない
        configured = None
    return configured or _git_user_name(cwd) or _os_login() or "unknown"
