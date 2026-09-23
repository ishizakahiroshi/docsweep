#!/usr/bin/env python3
"""docsweep pre-commit hook — frontmatter 不整合検知。

採用者が ``install-hooks.sh`` / ``install-hooks.ps1`` で ``.git/hooks/pre-commit`` に
配置することを想定。**docsweep 本体がインストールされていなくても動く**ようフォールバックを
内蔵する（docsweep を入れていないリポでも、frontmatter の値域違反はコミット時に止まる）。

検知対象:

- ``type:`` が空、または文字列ではない
- ``status:`` が OKF lifecycle（draft / stable / deprecated）でも旧 docsweep 値でもない
- ``docsweep_state:`` が docsweep の状態語彙外
- ``review_status:`` が draft / review / published 以外
- ``related:`` で参照される .md が存在しない
- frontmatter の YAML パース失敗
- private work_dir の staged file
- staged file 本文の高信頼 secret（値はエラー出力に含めない）

非 OKF 採用ファイル（frontmatter なし）はスキップ（H1 ラベル運用は触らない）。
plan_* / bugfix_* / pending_* で始まる .md のみを対象にする。

出力する文言と、見出し・語の語彙（ja / en）は隣の ``docsweep-check.i18n.json`` に置く
（install-hooks が hook と一緒に ``.git/hooks/`` へコピーする）。見出しと語はどの言語の
表記でも受け付け、文言は表示言語で出す。
"""

from __future__ import annotations

import functools
import json
import re
import subprocess
import sys
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any


ALLOWED_LIFECYCLE_STATUSES = {"draft", "stable", "deprecated"}
LEGACY_STATUSES = {
    "planned", "in-progress", "watching", "done", "discarded", "pending",
}
ALLOWED_REVIEW_STATUSES = {"draft", "review", "published"}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# 文言と語彙の辞書（{"ja": {key: 値}, "en": {...}}）。hook 本体と同じディレクトリに置く
# （配置後は .git/hooks/ の中）。``doc.heading.*`` は docsweep/doc_vocab.py の HEADINGS と
# 同じ表記にそろえること（この hook は docsweep 無しで動く必要があるため自分で持つ。
# 片方だけ変えない）。
_I18N_FILE = "docsweep-check.i18n.json"
# 辞書が読めないときは言語を選ぶ材料も無いので、ここだけ 2 言語を 1 行に並べて直書きする。
_I18N_MISSING = (
    "docsweep-check: docsweep-check.i18n.json is missing or unreadable next to the hook"
    " / フックの隣の docsweep-check.i18n.json が見つからないか読めません."
    " Re-run install-hooks / install-hooks を再実行してください"
)

# 委譲 plan（docsweep_delegation: external）の C ごとに必須の H4 8 項目。
# 各項目はどの言語の表記で書いてもよく、同じ項目が言語をまたいで 2 回出たら重複とみなす。
_DELEGATION_H4_KEYS = (
    "doc.heading.goal",
    "doc.heading.current_impl",
    "doc.heading.work",
    "doc.heading.files_to_change",
    "doc.heading.keep",
    "doc.heading.out_of_scope",
    "doc.heading.how_to_verify",
    "doc.heading.completion",
)
_DELEGATION_REQUIRED_KEYS = frozenset({
    "doc.heading.goal",
    "doc.heading.work",
    "doc.heading.how_to_verify",
    "doc.heading.completion",
})
_MARKDOWN_HEADING_RE = re.compile(r"^(#{2,6})[ \t]+(.+?)[ \t]*$")
_CONTEXT_C_ROW_RE = re.compile(r"^\s*\|\s*(C[1-9][0-9]*)\s*\|")
_C_HEADING_RE = re.compile(r"^C([1-9][0-9]*)\b")
_TODO_PLACEHOLDER_RE = re.compile(r"^<\s*TODO(?:\s*:[^>]*)?>$|^TODO(?:\s*:[^>]*)?$", re.IGNORECASE)

_catalog_cache: dict[str, dict[str, Any]] | None = None


def _load_catalog() -> dict[str, dict[str, Any]] | None:
    """隣の辞書を読む。無い・壊れているときは None（呼び出し側が 1 行で止める）。"""
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache
    try:
        data = json.loads(Path(__file__).with_name(_I18N_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not all(
        isinstance(entries, dict) for entries in data.values()
    ):
        return None
    _catalog_cache = data
    return data


def _catalog() -> dict[str, dict[str, Any]]:
    data = _load_catalog()
    if data is None:
        raise RuntimeError(_I18N_MISSING)
    return data


@functools.lru_cache(maxsize=1)
def _docsweep_resolver() -> Callable[[], str] | None:
    try:
        from docsweep.i18n import resolve_lang
    except ImportError:  # docsweep を入れていないリポでも動かす
        return None
    return resolve_lang


def _normalize_lang(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if text.startswith("ja"):
        return "ja"
    if text.startswith("en"):
        return "en"
    return None


def _display_lang() -> str:
    """表示言語（ja / en）。docsweep があればその決め方に従い、無ければ同じ順で自前で決める。

    DOCSWEEP_LANG → OS の表示言語 → en。Windows は UI 言語だけを見る（Git Bash は
    ``LANG=en_US.UTF-8`` を入れることがあり、日本語の利用者が英語になってしまうため）。
    """
    resolver = _docsweep_resolver()
    if resolver is not None:
        return _normalize_lang(resolver()) or "en"
    lang = _normalize_lang(os.environ.get("DOCSWEEP_LANG"))
    if lang:
        return lang
    if sys.platform == "win32":
        try:
            import ctypes

            langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        except Exception:
            return "en"
        # 下位 10 bit が primary language。0x11 = Japanese。
        return "ja" if (langid & 0x3FF) == 0x11 else "en"
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return "ja" if value.lower().startswith("ja") else "en"
    return "en"


def _text(key: str, lang: str) -> str | None:
    """``lang`` の文字列。その言語に無ければ en。"""
    catalog = _catalog()
    for code in (lang, "en"):
        value = catalog.get(code, {}).get(key)
        if isinstance(value, str):
            return value
    return None


def _heading_name(key: str, lang: str) -> str:
    """エラーに出す見出し名（``lang`` の表記）。"""
    return _text(key, lang) or key


def _msg(key: str, **params: object) -> str:
    """表示言語の文言。辞書にキーが無いとき（hook と辞書の版ずれ）はキーと値だけ出す。"""
    template = _text(key, _display_lang())
    if template is None:
        return f"{key} {params}"
    return template.format(**params)


def _variants(key: str) -> tuple[str, ...]:
    """``key`` の全言語の表記（一覧の値は展開する）。解析はどの言語で書かれた文書も読む。"""
    found: list[str] = []
    for entries in _catalog().values():
        value = entries.get(key)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and item:
                found.append(item)
    return tuple(dict.fromkeys(found))


def _heading_lang(key: str, title: str) -> str | None:
    """見出し ``title`` が ``key`` のどの言語の表記か。どれでもなければ None。"""
    for code, entries in _catalog().items():
        if entries.get(key) == title:
            return code
    return None


def _plan_lang(headings: list[tuple[int, int, str]]) -> str:
    """plan の言語。context配分 → C 詳細 の見出しがどちらの表記かで決め、無ければ ja。"""
    for key in ("doc.heading.context", "doc.heading.c_details"):
        for _line, level, title in headings:
            if level == 2 and (code := _heading_lang(key, title)):
                return code
    return "ja"


@functools.cache
def _word_re(word: str) -> re.Pattern[str]:
    """曖昧表現 1 語の検索式。英字で始まる・終わる側だけ語の境界を見る。

    英語は ``incorrectly`` の中の ``correctly`` のような語の一部に当てず、大文字小文字も
    区別しない。日本語は従来どおり部分一致になる。
    """
    body = r"\s+".join(re.escape(part) for part in word.split())
    if re.match(r"[A-Za-z0-9_]", word):
        body = r"(?<![A-Za-z0-9_])" + body
    if re.search(r"[A-Za-z0-9_]$", word):
        body += r"(?![A-Za-z0-9_])"
    return re.compile(body, re.IGNORECASE)


def _markdown_headings(text: str) -> list[tuple[int, int, str]]:
    """Return ``(line index, level, title)`` outside fenced code blocks."""
    headings: list[tuple[int, int, str]] = []
    in_fence = False
    fence_char = ""
    for index, raw_line in enumerate(text.splitlines()):
        line = raw_line.rstrip("\r")
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_char = marker
            elif marker == fence_char:
                in_fence = False
                fence_char = ""
            continue
        if in_fence:
            continue
        match = _MARKDOWN_HEADING_RE.match(line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))
    return headings


def _section_body(lines: list[str], headings: list[tuple[int, int, str]], index: int) -> str:
    """Return the text after one heading and before the next Markdown heading."""
    start = headings[index][0] + 1
    end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
    return "\n".join(lines[start:end])


def _heading_body(
    lines: list[str], headings: list[tuple[int, int, str]], target: tuple[int, int, str]
) -> str:
    for index, heading in enumerate(headings):
        if heading == target:
            return _section_body(lines, headings, index)
    return ""


def _context_c_ids(text: str) -> list[str]:
    lines = text.splitlines()
    headings = _markdown_headings(text)
    context_titles = _variants("doc.heading.context")
    for index, (_line, level, title) in enumerate(headings):
        if level == 2 and title in context_titles:
            body = _section_body(lines, headings, index)
            return [match.group(1) for line in body.splitlines() if (match := _CONTEXT_C_ROW_RE.match(line))]
    return []


def _is_unfilled_body(body: str) -> bool:
    meaningful: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        line = re.sub(r"^(?:[-*+・]\s*)", "", line)
        line = re.sub(r"^\d+[.)]\s*", "", line)
        if not line or line in {"—", "–"} or _TODO_PLACEHOLDER_RE.fullmatch(line):
            continue
        meaningful.append(line)
    return not meaningful


def _find_by_basename(start: Path, name: str) -> bool:
    """作業 queue とその archive の中に、その **ファイル名** の md があるかを返す。

    探索範囲は ``start``（参照元 md のあるディレクトリ）とその配下、および
    ``start`` の親を 2 階層まで遡った先の ``archive/`` 配下に限る。
    リポジトリ全体は走査しない（大きなリポで pre-commit が重くなるため）。
    """
    if not name:
        return False
    roots = [start]
    cur = start
    for _ in range(2):
        cur = cur.parent
        roots.append(cur)
    seen: set[str] = set()
    for root in roots:
        try:
            key = str(root.resolve()).lower()
        except OSError:
            continue
        if key in seen or not root.is_dir():
            continue
        seen.add(key)
        try:
            for found in root.rglob(name):
                if found.is_file():
                    return True
        except OSError:
            continue
    return False


def _check_delegated_plan(path: Path, text: str, data: dict, warnings: list[str] | None) -> list[str]:
    """Validate the opt-in C detail format without importing docsweep itself."""
    if data.get("docsweep_delegation") != "external" or data.get("type") != "plan":
        return []

    errors: list[str] = []
    lines = text.splitlines()
    headings = _markdown_headings(text)
    # エラーに出す見出し名は plan 自身の言語で書く（英語の plan に「## C 詳細」と言わない）。
    lang = _plan_lang(headings)
    context_heading = _heading_name("doc.heading.context", lang)
    detail_heading = _heading_name("doc.heading.c_details", lang)
    detail_titles = _variants("doc.heading.c_details")

    detail_index = next(
        (index for index, (_line, level, title) in enumerate(headings)
         if level == 2 and title in detail_titles),
        None,
    )
    if detail_index is None:
        errors.append(_msg("hook.missing_c_details", path=path, heading=detail_heading))

    detail_end = len(lines)
    if detail_index is not None:
        for index in range(detail_index + 1, len(headings)):
            if headings[index][1] <= 2:
                detail_end = headings[index][0]
                break

    c_entries = [
        heading for heading in headings
        if heading[1] == 3
        and detail_index is not None
        and headings[detail_index][0] < heading[0] < detail_end
        and _C_HEADING_RE.match(heading[2])
    ]
    context_ids = _context_c_ids(text)
    detail_ids = [
        f"C{_C_HEADING_RE.match(title).group(1)}"
        for _line, _level, title in c_entries
    ]
    context_set = set(context_ids)
    detail_set = set(detail_ids)

    if not context_ids:
        errors.append(_msg("hook.context_needs_c_rows", path=path, heading=context_heading))
    if len(context_ids) != len(context_set):
        errors.append(_msg("hook.context_duplicate_rows", path=path, heading=context_heading))
    if len(detail_ids) != len(detail_set):
        errors.append(_msg("hook.c_details_duplicate", path=path, heading=detail_heading))
    for context_id in sorted(context_set - detail_set, key=lambda value: int(value[1:])):
        errors.append(_msg(
            "hook.context_row_without_detail",
            path=path, heading=context_heading, c_id=context_id, number=context_id[1:],
        ))
    for detail_id in sorted(detail_set - context_set, key=lambda value: int(value[1:])):
        errors.append(_msg(
            "hook.detail_without_context_row",
            path=path, heading=context_heading, c_id=detail_id, number=detail_id[1:],
        ))

    # H4 の表記（どの言語でも）→ 項目のキー
    h4_keys = {title: key for key in _DELEGATION_H4_KEYS for title in _variants(key)}
    ambiguous_words = _variants("hook.ambiguous_words")
    for c_index, c_entry in enumerate(c_entries):
        c_id = _C_HEADING_RE.match(c_entry[2]).group(1)
        c_start = c_entry[0]
        c_end = c_entries[c_index + 1][0] if c_index + 1 < len(c_entries) else detail_end
        c_headings = [
            heading for heading in headings
            if c_start < heading[0] < c_end and heading[1] == 4
        ]
        found: dict[str, list[str]] = {key: [] for key in _DELEGATION_H4_KEYS}
        for _line, _level, title in c_headings:
            if title in h4_keys:
                found[h4_keys[title]].append(title)
        for key in _DELEGATION_H4_KEYS:
            titles = found[key]
            if not titles:
                errors.append(_msg(
                    "hook.missing_h4", path=path, number=c_id, title=_heading_name(key, lang),
                ))
            elif len(titles) > 1:
                shown = " / ".join(f"#### {title}" for title in dict.fromkeys(titles))
                errors.append(_msg("hook.duplicate_h4", path=path, number=c_id, titles=shown))

        for heading in c_headings:
            title = heading[2]
            key = h4_keys.get(title)
            if key not in _DELEGATION_REQUIRED_KEYS:
                continue
            body = _heading_body(lines, headings, heading)
            if _is_unfilled_body(body):
                errors.append(_msg("hook.empty_h4", path=path, number=c_id, title=title))
            if key == "doc.heading.completion" and warnings is not None:
                for word in ambiguous_words:
                    if _word_re(word).search(body):
                        warnings.append(_msg(
                            "hook.ambiguous_completion", path=path, number=c_id, word=word,
                        ))

    # 分類語（``hook.classification_words``）を含む H2 / H3 見出しは警告する。
    # closeout-check に分類させたい正規の節（``hook.exempt_titles``）は例外。分類語を含むこと自体が
    # 目的なので警告しない。C 単位の H4 8 項目は level 4 なのでそもそも走査外。
    # 正規セクション（plan の 完了条件 / 検証 / 受入条件、bugfix の 変更ファイル、
    # 委譲 plan の 変更予定ファイル）を入れていないと、**必須の節そのものが警告される**。
    if warnings is not None:
        exempt_titles = set(_variants("hook.exempt_titles"))
        classification_words = [word.casefold() for word in _variants("hook.classification_words")]
        for _line, level, title in headings:
            if level not in (2, 3):
                continue
            if title in exempt_titles:
                continue
            lowered = title.casefold()
            for word in classification_words:
                if word in lowered:
                    warnings.append(_msg(
                        "hook.classification_word", path=path, level=level, title=title,
                    ))
                    break
    return errors


def _staged_md_files() -> list[Path]:
    """``git diff --cached --name-only --diff-filter=AM`` で対象 md を取得。"""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
            text=True, encoding="utf-8", errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    files: list[Path] = []
    for line in out.splitlines():
        line = line.strip()
        if not line.endswith(".md"):
            continue
        name = Path(line).name
        if not any(
            name.startswith(prefix) for prefix in ("plan_", "bugfix_", "pending_")
        ):
            continue
        p = Path(line)
        if p.is_file():
            files.append(p)
    return files


def _parse_yaml_minimal(text: str) -> dict | None:
    """yaml.safe_load を試し、無ければ最小 parser でフォールバック。

    フォールバックは ``key: value`` / ``key: [a, b]`` の 2 形式のみ扱う。
    """
    try:
        import yaml  # type: ignore[import-not-found]
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
        return None
    except ImportError:
        pass
    out: dict = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            items = [s.strip().strip("'\"") for s in inner.split(",") if s.strip()]
            out[key] = items
        elif raw == "":
            out[key] = None
        else:
            out[key] = raw.strip("'\"")
    return out


def _check_one(path: Path, warnings: list[str] | None = None) -> list[str]:
    """1 ファイルを検査してエラー文字列のリストを返す（空なら OK）。"""
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [_msg("hook.read_failed", path=path, error=e)]

    m = _FRONTMATTER_RE.match(text)
    if not m:
        # frontmatter 無し（旧来の H1 ラベル運用）は対象外。
        return []
    body = m.group(1)
    data = _parse_yaml_minimal(body)
    if data is None:
        return [_msg("hook.yaml_parse_failed", path=path)]

    doc_type = data.get("type")
    if not isinstance(doc_type, str) or not doc_type.strip():
        errors.append(_msg("hook.type_empty", path=path))

    status = data.get("status")
    if status is not None and status not in (ALLOWED_LIFECYCLE_STATUSES | LEGACY_STATUSES):
        errors.append(_msg("hook.status_invalid", path=path, status=repr(status)))

    state = data.get("docsweep_state")
    if state is not None and state not in LEGACY_STATUSES:
        errors.append(_msg("hook.state_invalid", path=path, state=repr(state)))

    review = data.get("review_status")
    if review is not None and review not in ALLOWED_REVIEW_STATUSES:
        errors.append(_msg(
            "hook.review_status_invalid",
            path=path, review_status=repr(review), allowed=sorted(ALLOWED_REVIEW_STATUSES),
        ))

    related = data.get("related") or []
    if not isinstance(related, list):
        errors.append(_msg("hook.related_not_list", path=path))
    else:
        base = path.parent
        for ref in related:
            if not ref:
                continue
            ref_s = str(ref).strip()
            # 絶対パス or 相対パス（path 隣接 or リポルート相対）両対応の探索。
            candidates = [
                base / ref_s,
                Path(ref_s),
                Path.cwd() / ref_s,
            ]
            if any(c.is_file() for c in candidates):
                continue
            # パスで見つからない場合、同じ作業 queue の中を **ファイル名で** 探す。
            #
            # `related` の正本はファイル名であり、パスではない（templates/CLAUDE.md）。
            # docsweep は完了した md を archive/ へ移送するのが仕事なので、
            # **パスで書いた参照は移送のたびに切れる**。ファイル名なら移送に耐える。
            # ここに basename 探索が無かったため、規約どおりファイル名で書くと
            # 移送後に hook が「存在しない」と言う、という食い違いが起きていた。
            if _find_by_basename(base, Path(ref_s).name):
                continue
            errors.append(_msg("hook.related_missing", path=path, ref=repr(ref_s)))
    errors.extend(_check_delegated_plan(path, text, data, warnings))
    return errors


def _repo_root() -> Path | None:
    try:
        raw = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
            encoding="utf-8",
            errors="replace",
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return Path(raw).resolve() if raw else None


def _staged_paths() -> list[Path]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [Path(line.strip()) for line in out.splitlines() if line.strip()]


def _work_settings(root: Path) -> tuple[str, str, str]:
    path = root / ".docsweep.yaml"
    if not path.is_file():
        return "docs/local", "private", "block"
    try:
        data = _parse_yaml_minimal(path.read_text(encoding="utf-8", errors="replace")) or {}
    except OSError:
        return "docs/local", "private", "block"
    work_dir = str(data.get("work_dir") or "docs/local").strip()
    policy = str(data.get("work_policy") or "private").strip().lower()
    secret_policy = str(data.get("secret_policy") or "block").strip().lower()
    return work_dir, policy, secret_policy


_HIGH_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_pat", re.compile(r"(?:ghp_|github_pat_)[A-Za-z0-9_\-]{20,}")),
    ("anthropic_sk", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_sk", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "generic_bearer",
        re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{24,}"),
    ),
)


def _check_staged_privacy() -> tuple[list[str], list[str]]:
    """private queue / secret を staged diff で検査する。秘密値は出力しない。"""
    root = _repo_root()
    if root is None:
        return [], []
    staged = _staged_paths()
    if not staged:
        return [], []
    work_raw, work_policy, secret_policy = _work_settings(root)
    try:
        work = (root / work_raw).resolve() if not Path(work_raw).is_absolute() else Path(work_raw).resolve()
        work.relative_to(root)
    except ValueError:
        return [_msg("hook.work_dir_not_relative")], []
    except OSError:
        return [_msg("hook.work_dir_unresolvable")], []

    errors: list[str] = []
    warnings: list[str] = []
    for rel in staged:
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(work)
            in_work = True
        except ValueError:
            in_work = False
        if in_work and work_policy == "private":
            errors.append(_msg("hook.private_work_dir_staged", path=rel.as_posix()))
        if secret_policy == "off" or os.environ.get("DOCSWEEP_ALLOW_SENSITIVE") == "1":
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        kinds = [kind for kind, pattern in _HIGH_SECRET_PATTERNS if pattern.search(text)]
        for kind in kinds:
            message = _msg("hook.secret_detected", path=rel.as_posix(), kind=kind)
            if secret_policy == "warn":
                warnings.append(message)
            else:
                errors.append(message)
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    # 辞書が無いと何も言えないので、対象の有無に関係なく最初に止める
    # （hook だけを手でコピーした等。install-hooks の再実行で直る）。
    if _load_catalog() is None:
        sys.stderr.write(f"{_I18N_MISSING}\n")
        return 1
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        targets = [Path(a) for a in args]
    else:
        targets = _staged_md_files()
    all_errors: list[str] = []
    # 警告は出所ごとに分ける。混ぜると「secret_policy=warn」の見出しの下に
    # 書式警告が並び、利用者が原因を取り違える（設定を疑って調べ始める）。
    secret_warnings: list[str] = []
    format_warnings: list[str] = []
    if not args:
        privacy_errors, privacy_warnings = _check_staged_privacy()
        all_errors.extend(privacy_errors)
        secret_warnings.extend(privacy_warnings)
    if not targets and not all_errors and not secret_warnings:
        return 0
    for p in targets:
        all_errors.extend(_check_one(p, format_warnings))
    for header_key, warnings in (
        ("hook.header_secret_warnings", secret_warnings),
        ("hook.header_format_warnings", format_warnings),
    ):
        if not warnings:
            continue
        sys.stderr.write(f"{_msg(header_key)}\n")
        for warning in warnings:
            sys.stderr.write(f"  - {warning}\n")
    if not all_errors:
        return 0
    sys.stderr.write(f"{_msg('hook.header_errors')}\n")
    for e in all_errors:
        sys.stderr.write(f"  - {e}\n")
    sys.stderr.write(f"\n{_msg('hook.footer')}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
