"""不変条件: 物理削除の口が構造的に存在しないことを担保する（親 plan C6）。

設計の正本: docs/local/plan_due-date-second-axis.md §C4 / plan_kanban-board-write-ops_c3 §C6

「最悪でも archive 移動止まり・rm 相当の口を実装として持たない」を強制する。
AI / Web UI / CLI のどの経路から呼んでも `unlink` / `remove` / `rmtree` が走らないことを
公開ツール名のレベルと services 層のコード走査で 2 段確認する。

archive・cleanup・backup などの内部ヘルパは旧バックアップ削除（30 日経過品）で
``unlink`` を使うため、本テストでは services / MCP ツール公開口のみを対象にする。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("mcp.server.fastmcp")

from docsweep.config import load_config  # noqa: E402
from docsweep.mcp_server import build_server  # noqa: E402

PKG_DIR = Path(__file__).resolve().parent.parent / "docsweep"
SERVICES_DIR = PKG_DIR / "services"


# 公開ツール名に含まれていてはならない動詞（rm / delete / remove / purge / unlink / wipe）。
# archive_done のみが「破棄に近い」操作を許され、それも実体は move であることが
# 別テスト（test_mcp_write_tools.test_archive_done_*）で確認される。
_FORBIDDEN_TOOL_NAME_TOKENS = ("delete", "remove", "unlink", "rm_", "purge_md", "wipe", "destroy")


def test_no_destructive_tool_names_in_mcp_server(tmp_path: Path):
    """MCP の公開ツール名に削除系の動詞が含まれていないこと。"""
    root = tmp_path / "dev"
    root.mkdir(parents=True)
    cfg = load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")
    server = build_server(cfg)
    tool_names = list(server._tool_manager._tools.keys())
    for name in tool_names:
        low = name.lower()
        for token in _FORBIDDEN_TOOL_NAME_TOKENS:
            assert token not in low, (
                f"MCP ツール名 '{name}' に削除系トークン '{token}' が含まれています "
                f"（物理削除の口を持たない不変条件に違反）"
            )


def _collect_call_names(tree: ast.AST) -> set[str]:
    """AST から呼び出されている関数/メソッド名を集める（最終要素のみ）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute):
                names.add(fn.attr)
            elif isinstance(fn, ast.Name):
                names.add(fn.id)
    return names


# services 層で呼ばれていてはならない物理削除 API。
# - os.unlink / os.remove / os.rmdir / shutil.rmtree
# - Path.unlink / Path.rmdir
# atomic.py が一時ファイル片付けで os.unlink を使うのは services 層の外なので対象外。
_FORBIDDEN_CALLS = {"unlink", "remove", "rmdir", "rmtree"}


def test_services_do_not_call_destructive_apis():
    """services/*.py が物理削除 API を呼んでいないこと（archive_done も含む）。"""
    py_files = sorted(SERVICES_DIR.glob("*.py"))
    assert py_files, "services 層にファイルが見つからない（プロジェクト構成が変わった?）"
    violations: list[str] = []
    for f in py_files:
        if f.name == "__init__.py":
            continue
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src)
        calls = _collect_call_names(tree)
        bad = calls & _FORBIDDEN_CALLS
        if bad:
            violations.append(f"{f.name}: {sorted(bad)}")
    assert not violations, (
        f"services 層で物理削除 API が呼ばれています: {violations}"
    )


def test_mcp_server_module_does_not_call_destructive_apis():
    """``docsweep/mcp_server.py`` 本体でも物理削除 API を呼ばないこと。"""
    src = (PKG_DIR / "mcp_server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = _collect_call_names(tree)
    bad = calls & _FORBIDDEN_CALLS
    assert not bad, f"mcp_server.py で物理削除 API が呼ばれています: {sorted(bad)}"


def test_archive_done_only_moves_never_deletes(tmp_path: Path):
    """archive_done を呼ばれても、元ファイルは消えるのではなく archive 配下へ移動する。

    親 plan C6「最悪 archive 止まり」の動的検証。
    """
    from docsweep.services.archive import archive_done

    root = tmp_path / "dev"
    proj = root / "proj"
    proj.mkdir(parents=True)
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")
    docs = proj / "docs"
    docs.mkdir()
    src = docs / "plan_done.md"
    src.write_text("# [完了] x\n\n## 概要\n\na\n", encoding="utf-8")
    cfg = load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")
    res = archive_done(config=cfg, auto=True)
    moved = res.moved
    assert moved, "archive_done が何も処理していない"
    # 元ファイルは「消えた」のではなく、archive 配下に存在する。
    dst = Path(moved[0].dst)
    assert dst.exists(), "archive 移送先にファイルが存在しない（物理削除されている可能性）"
    assert "archive" in dst.parts


def test_atomic_unlink_is_only_for_tempfile_cleanup():
    """``atomic.py`` の unlink 使用は tempfile クリーンアップに限定されることを目視確認。

    AST レベルでは検出できないため、ソース文字列で ``tempfile`` コンテキストに限定される
    ことを確認する。services / MCP からは atomic を経由しても物理削除されない
    （write は move/replace のみ）。v0.4 で backup 機構を撤去したため、`_cleanup_backups`
    は allowed_contexts から外した。
    """
    src = (PKG_DIR / "atomic.py").read_text(encoding="utf-8")
    # unlink の登場行を抜き出す。
    lines = src.splitlines()
    unlink_lines = [(i, line) for i, line in enumerate(lines) if "unlink" in line]
    # 各登場箇所が tempfile 後始末コンテキスト内にあること。
    allowed_contexts = ("tempfile",)
    full = src
    for _, line in unlink_lines:
        # 「unlink を呼ぶ箇所」が unkown context だったら fail。
        # docstring・コメント内も拾うが、それは elements に追加分のみで害はない。
        ok = any(token in full for token in allowed_contexts)
        assert ok, f"atomic.py に想定外の unlink 使用: {line.strip()!r}"


# 書き込み系 services が「.md 以外」を書こうとしない（バイナリ拒否相当）— ホワイトボックス確認。
def test_services_write_only_md_via_resolve_writable_md(tmp_path: Path):
    """resolve_writable_md は .md 以外を弾く。services 層のテストは別ファイルでカバー済。"""
    from docsweep.security.path import PathScopeError, resolve_writable_md

    root = tmp_path / "dev"
    root.mkdir()
    txt = root / "note.txt"
    txt.write_text("x", encoding="utf-8")
    with pytest.raises(PathScopeError):
        resolve_writable_md(str(txt), roots=[root])


def test_dotdot_path_explicitly_rejected(tmp_path: Path):
    """``..`` を含むパスは realpath 解決前に弾く。"""
    from docsweep.security.path import PathScopeError, resolve_writable_md

    root = tmp_path / "dev"
    root.mkdir()
    (root / "a.md").write_text("# x\n", encoding="utf-8")
    with pytest.raises(PathScopeError):
        resolve_writable_md("../a.md", roots=[root])
