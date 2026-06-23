"""MCP 書き込みツール（update_status / update_due / update_content / archive_done）の検証。

build_server から FastMCP 経由でツールを取り出し、関数本体を直接呼ぶ
（HTTP/stdio を介さずロジックだけテストする）。``mcp`` extra が無い環境では skip。

カバー範囲（子 C3 plan §C2〜§C6）:
- 正常系（日本語ラベル解釈・相対 due / 楽観ロック / archive 連携）
- スコープ外パス・``..`` を含むパスの拒否（path_scope error dict）
- mtime 競合（conflict error dict）
- バリデーション違反（validation error dict）
- archive_done の破壊安全（空指定 + auto=False）
- ツール登録数の増加（既存 11 → 15 件）
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("mcp.server.fastmcp")

from docsweep.config import load_config  # noqa: E402
from docsweep.mcp_server import build_server  # noqa: E402


def _tools(server) -> dict:
    """FastMCP 内部の {name: callable} 辞書を返す（fn を取り出す）。"""
    return {name: t.fn for name, t in server._tool_manager._tools.items()}


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    """root（スキャンルート）と proj（プロジェクト境界）を作る。"""
    root = tmp_path / "dev"
    proj = root / "proj"
    proj.mkdir(parents=True)
    (proj / ".docsweep.yaml").write_text("", encoding="utf-8")
    return root, proj


def _cfg(root: Path):
    return load_config(explicit_roots=[str(root)], global_path=root / "no_global.yaml")


# ------------------------------------------------------------------
# ツール登録
# ------------------------------------------------------------------

def test_build_server_registers_new_write_tools(tmp_path: Path):
    """既存 11 ツール + C3 で 4 ツール追加 = 15 件登録されている。"""
    root, _ = _setup(tmp_path)
    cfg = _cfg(root)
    server = build_server(cfg)
    tools = _tools(server)
    # 既存
    for t in ("scan", "triage", "apply", "sweep", "promote", "index",
              "summary", "inject", "eject", "inject_global", "eject_global"):
        assert t in tools, f"既存ツール {t} が消えている"
    # 新規
    for t in ("update_status", "update_due", "update_content", "archive_done"):
        assert t in tools, f"新規ツール {t} が登録されていない"


# ------------------------------------------------------------------
# update_status
# ------------------------------------------------------------------

def test_update_status_accepts_japanese_label(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_a.md", "# [計画] テスト\n\n## 概要\n\nx\n")
    server = build_server(_cfg(root))
    res = _tools(server)["update_status"](str(f), "実行中")
    assert res["new_label"] == "[実行中]"
    assert res["old_label"] == "[計画]"
    assert res["archive_triggered"] is False
    assert res["new_mtime_iso"]  # ISO 文字列


def test_update_status_accepts_internal_key(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_a.md", "# [計画] テスト\n\n## 概要\n\nx\n")
    server = build_server(_cfg(root))
    res = _tools(server)["update_status"](str(f), "in-progress")
    assert res["new_label"] == "[実行中]"


def test_update_status_done_triggers_archive(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_a.md", "# [様子見] テスト\n\n## 概要\n\nx\n")
    server = build_server(_cfg(root))
    res = _tools(server)["update_status"](str(f), "完了")
    assert res["archive_triggered"] is True
    # archive 結果が同梱されている
    assert "archive" in res
    moved = res["archive"]["moved"]
    assert any(Path(m["from"]).name == "plan_a.md" for m in moved)
    # 元ファイルは archive へ移送済（同名ファイルが元位置に残らない）
    assert not f.exists()


def test_update_status_rejects_path_outside_root(tmp_path: Path):
    root, _ = _setup(tmp_path)
    # スキャンルート外
    outside = tmp_path / "other" / "plan_a.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("# [計画] x\n", encoding="utf-8")
    server = build_server(_cfg(root))
    res = _tools(server)["update_status"](str(outside), "実行中")
    assert res.get("kind") == "path_scope"
    assert "error" in res


def test_update_status_rejects_dotdot_path(tmp_path: Path):
    root, proj = _setup(tmp_path)
    _write(proj / "docs" / "plan_a.md", "# [計画] x\n")
    server = build_server(_cfg(root))
    res = _tools(server)["update_status"]("docs/../docs/plan_a.md", "実行中")
    assert res.get("kind") == "path_scope"


def test_update_status_validation_violation_returns_error_dict(tmp_path: Path):
    """bugfix_*.md に [計画] は拒否される（plan 専用ラベル）。

    2026-06-23 改修: 旧 [対応中] は [実行中] のエイリアスとなり plan でも通るようになった
    ため、本テストは「bugfix で [計画] を拒否」というバリデーション違反パターンに変更。
    """
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "bugfix_x_2026-01-01.md", "# [実行中] x\n\n## 症状\n\na\n")
    server = build_server(_cfg(root))
    res = _tools(server)["update_status"](str(f), "計画")
    assert res.get("kind") == "validation"


def test_update_status_mtime_conflict(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_a.md", "# [計画] x\n\n## 概要\n\na\n")
    server = build_server(_cfg(root))
    stale = f.stat().st_mtime - 1000
    res = _tools(server)["update_status"](str(f), "実行中", expected_mtime=stale)
    assert res.get("kind") == "conflict"
    assert res["expected_mtime"] == stale


# ------------------------------------------------------------------
# update_due
# ------------------------------------------------------------------

def test_update_due_replaces_existing(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_a.md",
               "---\ndue: 2026-06-15\n---\n# [計画] x\n\n## 概要\n\na\n")
    server = build_server(_cfg(root))
    res = _tools(server)["update_due"](str(f), "2026-06-29")
    assert res["new_due"] == "2026-06-29"
    assert res["old_due"] == "2026-06-15"
    assert res["postpone_count"] == 1
    assert res["new_mtime_iso"]


def test_update_due_accepts_relative_spec(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_a.md",
               "---\ndue: 2026-06-15\n---\n# [計画] x\n\n## 概要\n\na\n")
    server = build_server(_cfg(root))
    res = _tools(server)["update_due"](str(f), "+1w")
    expected = (date.today() + timedelta(days=7)).isoformat()
    assert res["new_due"] == expected


def test_update_due_invalid_spec_returns_error(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_a.md",
               "---\ndue: 2026-06-15\n---\n# [計画] x\n\n## 概要\n\na\n")
    server = build_server(_cfg(root))
    res = _tools(server)["update_due"](str(f), "yesterday")
    assert res.get("kind") == "validation"


def test_update_due_rejects_path_outside_root(tmp_path: Path):
    root, _ = _setup(tmp_path)
    outside = tmp_path / "other" / "plan_a.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("# [計画] x\n", encoding="utf-8")
    server = build_server(_cfg(root))
    res = _tools(server)["update_due"](str(outside), "+1w")
    assert res.get("kind") == "path_scope"


def test_update_due_mtime_conflict(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_a.md",
               "---\ndue: 2026-06-15\n---\n# [計画] x\n\n## 概要\n\na\n")
    server = build_server(_cfg(root))
    stale = f.stat().st_mtime - 1000
    res = _tools(server)["update_due"](str(f), "+1w", expected_mtime=stale)
    assert res.get("kind") == "conflict"


# ------------------------------------------------------------------
# update_content
# ------------------------------------------------------------------

def test_update_content_full_replace(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_a.md", "# [計画] 旧\n\n## 概要\n\n旧\n")
    server = build_server(_cfg(root))
    new = "# [実行中] 新\n\n## 概要\n\n新\n"
    res = _tools(server)["update_content"](str(f), new)
    assert f.read_text(encoding="utf-8") == new
    assert res["new_sha256"]
    assert res["warnings"] == []


def test_update_content_rejects_empty(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_a.md", "# [計画] x\n\n## 概要\n\na\n")
    server = build_server(_cfg(root))
    res = _tools(server)["update_content"](str(f), "")
    assert res.get("kind") == "validation"
    # 元ファイル温存
    assert "# [計画] x" in f.read_text(encoding="utf-8")


def test_update_content_warns_when_h1_missing(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_a.md", "# [計画] x\n\n## 概要\n\na\n")
    server = build_server(_cfg(root))
    res = _tools(server)["update_content"](str(f), "## 概要\n\n本文だけ\n")
    assert any("H1" in w for w in res.get("warnings", []))


def test_update_content_rejects_path_outside_root(tmp_path: Path):
    root, _ = _setup(tmp_path)
    outside = tmp_path / "other" / "plan_a.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("# [計画] x\n", encoding="utf-8")
    server = build_server(_cfg(root))
    res = _tools(server)["update_content"](str(outside), "# [計画] new\n")
    assert res.get("kind") == "path_scope"


def test_update_content_mtime_conflict(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_a.md", "# [計画] x\n\n## 概要\n\na\n")
    server = build_server(_cfg(root))
    stale = f.stat().st_mtime - 1000
    res = _tools(server)["update_content"](str(f), "# [計画] new\n", expected_mtime=stale)
    assert res.get("kind") == "conflict"


def test_update_content_rejects_non_md_path(tmp_path: Path):
    """`.md` 以外のファイルへの書き込みは path_scope で拒否される。"""
    root, proj = _setup(tmp_path)
    txt = proj / "docs" / "note.txt"
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text("plain", encoding="utf-8")
    server = build_server(_cfg(root))
    res = _tools(server)["update_content"](str(txt), "new")
    assert res.get("kind") == "path_scope"


# ------------------------------------------------------------------
# archive_done
# ------------------------------------------------------------------

def test_archive_done_auto_moves_done_and_discarded(tmp_path: Path):
    root, proj = _setup(tmp_path)
    _write(proj / "docs" / "plan_done.md", "# [完了] x\n\n## 概要\n\na\n")
    _write(proj / "docs" / "plan_disc.md", "# [廃止] y\n\n## 概要\n\nb\n")
    _write(proj / "docs" / "plan_watch.md", "# [様子見] z\n\n## 概要\n\nc\n")
    server = build_server(_cfg(root))
    res = _tools(server)["archive_done"](auto=True)
    moved_names = {Path(m["from"]).name for m in res["moved"]}
    assert moved_names == {"plan_done.md", "plan_disc.md"}


def test_archive_done_explicit_paths(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_done.md", "# [完了] x\n\n## 概要\n\na\n")
    server = build_server(_cfg(root))
    res = _tools(server)["archive_done"](paths=[str(f)])
    assert any(Path(m["from"]).name == "plan_done.md" for m in res["moved"])


def test_archive_done_rejects_watching_when_specified(tmp_path: Path):
    root, proj = _setup(tmp_path)
    f = _write(proj / "docs" / "plan_watch.md", "# [様子見] z\n\n## 概要\n\nc\n")
    server = build_server(_cfg(root))
    res = _tools(server)["archive_done"](paths=[str(f)])
    assert res["moved"] == []
    assert any("not archivable" in s["reason"] for s in res["skipped"])


def test_archive_done_empty_when_no_paths_and_no_auto(tmp_path: Path):
    """破壊安全側: 何も指定がなければ空結果。"""
    root, proj = _setup(tmp_path)
    _write(proj / "docs" / "plan_done.md", "# [完了] x\n\n## 概要\n\na\n")
    server = build_server(_cfg(root))
    res = _tools(server)["archive_done"]()
    assert res["moved"] == []
    assert res["skipped"] == []


def test_archive_done_rejects_path_outside_root(tmp_path: Path):
    root, _ = _setup(tmp_path)
    outside = tmp_path / "other" / "plan_done.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("# [完了] x\n", encoding="utf-8")
    server = build_server(_cfg(root))
    res = _tools(server)["archive_done"](paths=[str(outside)])
    assert "errors" in res
    assert any(e.get("kind") == "path_scope" for e in res["errors"])
