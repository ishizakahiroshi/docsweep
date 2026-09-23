"""逆戻り防止: 言語ごとの文言・用語がソースコードに書き込まれていないこと。

文言と用語は ``docsweep/i18n/locales/<言語>/*.json`` に置き、コードはキーで引くだけにする。
ここではソース（Python・Web UI の JS と HTML テンプレート）を数える:

- Python: 日本語を含む文字列リテラル（docstring と属性の説明文字列は除く）、日本語を含まず
  英単語だけの ``raise`` のメッセージ（日本語の利用者に英語が出る。例外クラスが
  ``super().__init__`` で組み立てるものを含む）、言語コードとの比較
  （``lang == "en"`` のように文言をコードで選び分ける書き方）
- Web UI の JS: 日本語を含む文字列リテラル
- Web UI の CSS: コメントを除いた、日本語を含む行（``content:`` に書いた文言）
- Web UI の HTML テンプレート: コメントを除いた、日本語を含む行

どれも 0 であること。言語の文言ではないのに当たるもの（日本語の文字範囲を表す正規表現等）
だけを、理由付きで ``ALLOWED`` に置く。``python tests/test_i18n_coverage.py`` が今の値を出す。
"""

from __future__ import annotations

import ast
import re
import sys
import warnings
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "docsweep"
JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
ENGLISH_WORD = re.compile(r"[A-Za-z]{3,}")
LANG_CODES = {"ja", "en"}


def _text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
    return None


def _docstring_ids(tree: ast.AST) -> set[int]:
    """docstring と、文として置いただけの文字列（属性の説明など）の id。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            ids.add(id(node.value))
    return ids


def _is_lang_compare(node: ast.AST) -> bool:
    """``x == "en"`` / ``"ja" != x`` のような、言語コードとの比較か。"""
    if not isinstance(node, ast.Compare) or not all(
        isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops
    ):
        return False
    operands = [node.left, *node.comparators]
    return any(
        isinstance(item, ast.Constant) and item.value in LANG_CODES for item in operands
    )


def _is_super_init(node: ast.AST) -> bool:
    """``super().__init__(...)`` の呼び出しか。"""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "__init__"
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Name)
        and node.func.value.func.id == "super"
    )


def count_python(path: Path) -> tuple[int, int, int]:
    """(日本語の文字列リテラル, 英語だけの raise, 言語コードとの比較)。"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = _docstring_ids(tree)
    inside_fstring = {
        id(value) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for value in node.values
    }
    japanese = english_raise = lang_compare = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Constant, ast.JoinedStr)) and id(node) not in inside_fstring:
            text = _text(node)
            if text and id(node) not in docstrings and JAPANESE.search(text):
                japanese += 1
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            text = _text(node.exc.args[0])
            if text and not JAPANESE.search(text) and ENGLISH_WORD.search(text):
                english_raise += 1
        # 例外クラスが自分の __init__ で組み立てるメッセージ（raise の引数には現れない）
        if _is_super_init(node) and node.args:
            text = _text(node.args[0])
            if text and not JAPANESE.search(text) and ENGLISH_WORD.search(text):
                english_raise += 1
        if _is_lang_compare(node):
            lang_compare += 1
    return japanese, english_raise, lang_compare


_JS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_JS_LINE_COMMENT = re.compile(r"(?m)(^|\s)//.*$")
_JS_STRING = re.compile(r"""(['"`])(?:\\.|(?!\1).)*?\1""", re.DOTALL)


def count_js(path: Path) -> int:
    source = _JS_BLOCK_COMMENT.sub("", path.read_text(encoding="utf-8"))
    source = _JS_LINE_COMMENT.sub(r"\1", source)
    return sum(1 for match in _JS_STRING.finditer(source) if JAPANESE.search(match.group(0)))


def count_css(path: Path) -> int:
    """コメントを除いた CSS の日本語（``content: "..."`` に文言を書くと画面に出る）。"""
    source = _JS_BLOCK_COMMENT.sub("", path.read_text(encoding="utf-8"))
    return sum(1 for line in source.splitlines() if JAPANESE.search(line))


_HTML_COMMENT = re.compile(r"<!--.*?-->|\{#.*?#\}", re.DOTALL)


def count_html(path: Path) -> int:
    source = _HTML_COMMENT.sub("", path.read_text(encoding="utf-8"))
    return sum(1 for line in source.splitlines() if JAPANESE.search(line))


def current_counts() -> dict[str, tuple[int, int, int]]:
    counts: dict[str, tuple[int, int, int]] = {}
    for path in sorted(PACKAGE.rglob("*")):
        rel = path.relative_to(PACKAGE).as_posix()
        if not path.is_file() or "__pycache__" in rel:
            continue
        if path.suffix == ".py":
            value = count_python(path)
        elif path.suffix == ".js" and rel.startswith("server/static/"):
            value = (count_js(path), 0, 0)
        elif path.suffix == ".css" and rel.startswith("server/static/"):
            value = (count_css(path), 0, 0)
        elif path.suffix == ".html" and rel.startswith("server/templates/"):
            value = (count_html(path), 0, 0)
        else:
            continue
        if value != (0, 0, 0):
            counts[rel] = value
    return counts


# 言語の文言ではないのに数に入るもの: (日本語, 英語だけの raise, 言語コードとの比較) と理由。
ALLOWED: dict[str, tuple[tuple[int, int, int], str]] = {
    "i18n/__init__.py": (
        (0, 7, 0),
        "JSON の読み込み・キー引きに失敗したときの開発者向けの例外（文言の JSON 自体が読めない段階）",
    ),
    "capture/llm.py": ((1, 0, 0), "ファイル名に使える文字の範囲（ひらがな・カタカナ・漢字）の正規表現"),
    "templates_gen.py": ((1, 0, 0), "ファイル名に使える文字の範囲（ひらがな・カタカナ・漢字）の正規表現"),
    "resurrect/similarity.py": ((1, 0, 0), "類似度の単語に数える文字の範囲（漢字・かな）の正規表現"),
    "index.py": (
        (1, 0, 0),
        "SQLite のスキーマ文字列の中の開発者向けコメント（利用者に出ない。変えると既存 DB のスキーマ文字列と差が出る）",
    ),
}


def test_no_language_text_in_the_source() -> None:
    current = current_counts()
    expected = {rel: counts for rel, (counts, _reason) in ALLOWED.items()}
    unexpected = {rel: value for rel, value in current.items() if expected.get(rel) != value}
    assert not unexpected, (
        "ソースに言語の文言・言語での分岐があります。docsweep/i18n/locales/<言語>/*.json へ移して"
        f" t() / texts() / variants() で引いてください（数は 日本語, 英語だけの raise, 言語比較）: {unexpected}"
    )
    stale = {rel: counts for rel, counts in expected.items() if current.get(rel) != counts}
    assert not stale, f"ALLOWED の数が今のソースと合いません。更新してください: {stale}"


def test_counter_sees_messages_but_not_docstrings(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        '"""モジュールの説明。"""\n'
        "class A:\n"
        "    x: int = 0\n"
        '    """属性の説明。"""\n'
        "def f(path):\n"
        '    """関数の説明。"""\n'
        '    raise ValueError(f"見つかりません: {path}")\n'
        "def g():\n"
        '    raise ValueError("file not found")\n'
        "def h(lang):\n"
        '    print("完了")\n'
        '    return "x" if lang == "en" else "y"\n',
        encoding="utf-8",
    )

    assert count_python(source) == (2, 1, 1)


def test_counter_sees_js_strings_but_not_comments(tmp_path: Path) -> None:
    source = tmp_path / "sample.js"
    source.write_text(
        "// 説明のコメント\n"
        "/* 複数行の\n   コメント */\n"
        'toast("保存しました"); // 行末のコメント\n'
        "const url = 'https://example.invalid/x';\n",
        encoding="utf-8",
    )

    assert count_js(source) == 1


if __name__ == "__main__":
    sys.stdout.write("# rel: (japanese, english_raise, lang_compare)\n")
    for rel, value in current_counts().items():
        sys.stdout.write(f'    "{rel}": {value},\n')
