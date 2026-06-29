"""C1 (wings): SQLite 索引 + search_paths / exclude + sync_index の単体テスト。"""

from __future__ import annotations

import json as _json
import time
from pathlib import Path

import pytest

from docsweep import index as db
from docsweep.config import load_config
from docsweep.engine import scan_records
from docsweep.index import load_records_from_index
from docsweep.scan import _expand_search_paths, sync_index


def _write(p: Path, text: str, age_days: int = 0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if age_days:
        import os
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
    return p


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """2 つのプロジェクト風ディレクトリを持つワークスペース。"""
    root = tmp_path / "dev"
    # プロジェクト A — pyproject.toml をマーカーに（git なし環境でも project_root 判定が安定するように）
    _write(root / "alpha" / "pyproject.toml", "[project]\nname='alpha'\n")
    _write(root / "alpha" / "docs" / "local" / "plan_one.md", "# [計画] one\n\n## 概要\n\nfoo\n")
    _write(root / "alpha" / "docs" / "local" / "bugfix_a.md",
           "# [対応中] a\n\n## 症状\n\nx\n## 根本原因\n\n## 修正内容\n\n## 変更ファイル\n\n## 検証\n\n## 備忘\n")
    # プロジェクト B
    _write(root / "beta" / "pyproject.toml", "[project]\nname='beta'\n")
    _write(root / "beta" / "docs" / "local" / "pending_x.md",
           "# [保留] x\n\n## 概要\n\n## 保留理由\n\n## 着手条件\n")
    return root


def _config_with_search(root: Path, tmp_path: Path):
    """search_paths を直接設定した Config を返す。"""
    cfg = load_config(explicit_roots=[str(root)], global_path=tmp_path / "noexists.yaml")
    # 直接書換: search_paths はグローバル yaml 経由がメインだがテストでは inject
    cfg.search_paths = [str(root / "*")]
    return cfg


# ===================================================================
# スキーマ / 接続
# ===================================================================


def test_schema_init_idempotent(tmp_path: Path) -> None:
    db_file = tmp_path / "idx.db"
    with db.connect(db_file) as conn:
        assert db.get_schema_version(conn) == db.SCHEMA_VERSION
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert {"projects", "files", "tags", "related", "meta"} <= tables

    # 二度開いても壊れない
    with db.connect(db_file) as conn:
        assert db.get_schema_version(conn) == db.SCHEMA_VERSION


def test_wal_mode_enabled(tmp_path: Path) -> None:
    db_file = tmp_path / "idx.db"
    with db.connect(db_file) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


# ===================================================================
# config.search_paths 展開
# ===================================================================


def test_expand_search_paths_glob(workspace: Path, tmp_path: Path) -> None:
    cfg = _config_with_search(workspace, tmp_path)
    expanded = _expand_search_paths(cfg)
    names = {p.name for p in expanded}
    assert "alpha" in names and "beta" in names


def test_expand_search_paths_exclude(workspace: Path, tmp_path: Path) -> None:
    cfg = _config_with_search(workspace, tmp_path)
    cfg.search_exclude = list(cfg.search_exclude) + [f"{workspace.as_posix()}/beta"]
    expanded = _expand_search_paths(cfg)
    names = {p.name for p in expanded}
    assert "alpha" in names and "beta" not in names


def test_expand_search_paths_fallback_to_roots(tmp_path: Path) -> None:
    """search_paths 未設定なら roots をフォールバックとして使う（後方互換）。"""
    root = tmp_path / "fallback"
    root.mkdir()
    cfg = load_config(explicit_roots=[str(root)], global_path=tmp_path / "no.yaml")
    assert not cfg.search_paths
    expanded = _expand_search_paths(cfg)
    assert expanded == [root.resolve()]


# ===================================================================
# sync_index 差分同期
# ===================================================================


def test_sync_index_initial(workspace: Path, tmp_path: Path) -> None:
    cfg = _config_with_search(workspace, tmp_path)
    db_file = tmp_path / "idx.db"
    stats = sync_index(cfg, db_path_override=db_file)

    assert stats.projects == 2
    assert stats.files_added == 3
    assert stats.files_updated == 0
    assert stats.files_deleted == 0

    with db.connect(db_file) as conn:
        rows = conn.execute("SELECT project_id, rel_path, type, status FROM files ORDER BY rel_path").fetchall()
        names = {r["rel_path"] for r in rows}
        assert names == {
            "docs/local/plan_one.md",
            "docs/local/bugfix_a.md",
            "docs/local/pending_x.md",
        }
        # type / status が埋まっている
        plan_row = next(r for r in rows if r["rel_path"].endswith("plan_one.md"))
        assert plan_row["type"] == "plan"
        # status は state model から付くキー（"計画" 相当）
        assert plan_row["status"] is not None


def test_sync_index_unchanged_skips(workspace: Path, tmp_path: Path) -> None:
    cfg = _config_with_search(workspace, tmp_path)
    db_file = tmp_path / "idx.db"
    sync_index(cfg, db_path_override=db_file)
    stats2 = sync_index(cfg, db_path_override=db_file)
    assert stats2.files_added == 0
    assert stats2.files_updated == 0
    assert stats2.files_unchanged == 3
    assert stats2.files_deleted == 0


def test_sync_index_detects_update(workspace: Path, tmp_path: Path) -> None:
    cfg = _config_with_search(workspace, tmp_path)
    db_file = tmp_path / "idx.db"
    sync_index(cfg, db_path_override=db_file)

    # ファイルを改変 (mtime と内容両方変える)
    target = workspace / "alpha" / "docs" / "local" / "plan_one.md"
    new_mtime = time.time() + 2  # 確実に差分
    target.write_text("# [実行中] one updated\n\n## 概要\n\nbar\n", encoding="utf-8")
    import os
    os.utime(target, (new_mtime, new_mtime))

    stats = sync_index(cfg, db_path_override=db_file)
    assert stats.files_updated == 1
    assert stats.files_added == 0


def test_sync_index_detects_deletion(workspace: Path, tmp_path: Path) -> None:
    cfg = _config_with_search(workspace, tmp_path)
    db_file = tmp_path / "idx.db"
    sync_index(cfg, db_path_override=db_file)

    (workspace / "beta" / "docs" / "local" / "pending_x.md").unlink()
    stats = sync_index(cfg, db_path_override=db_file)
    assert stats.files_deleted == 1

    with db.connect(db_file) as conn:
        rows = conn.execute("SELECT rel_path FROM files").fetchall()
        names = {r["rel_path"] for r in rows}
        assert "docs/local/pending_x.md" not in names


def test_sync_index_full_rebuild(workspace: Path, tmp_path: Path) -> None:
    cfg = _config_with_search(workspace, tmp_path)
    db_file = tmp_path / "idx.db"
    sync_index(cfg, db_path_override=db_file)
    stats = sync_index(cfg, full=True, db_path_override=db_file)
    # 全件再構築なので "全件" が added or updated 扱い
    assert stats.files_total == 3
    # full=True 時は files が空からスタートするので全部 added
    assert stats.files_added == 3


# ===================================================================
# config.yaml の projects ブロック読み込み
# ===================================================================


def test_load_config_reads_projects_block(tmp_path: Path) -> None:
    """グローバル config.yaml の ``projects.search_paths`` / ``exclude`` を読む。"""
    g = tmp_path / "global.yaml"
    g.write_text(
        f"""
projects:
  search_paths:
    - "{(tmp_path / 'foo').as_posix()}"
    - "{(tmp_path / 'bar').as_posix()}"
  exclude:
    - "**/skip-me/**"
""".strip(),
        encoding="utf-8",
    )
    cfg = load_config(global_path=g)
    assert len(cfg.search_paths) == 2
    assert any("foo" in p for p in cfg.search_paths)
    assert "**/skip-me/**" in cfg.search_exclude


# ===================================================================
# tags / related の同期
# ===================================================================


# ===================================================================
# 索引から FileRecord 復元 + scan_records フォールバック
# ===================================================================


def test_load_records_from_index_returns_none_when_db_empty(tmp_path: Path) -> None:
    """DB が無ければ None（フォールバックシグナル）。"""
    cfg = load_config(explicit_roots=[str(tmp_path)], global_path=tmp_path / "no.yaml")
    # 環境の DOCSWEEP_INDEX_DB をテスト用パスに置く
    import os
    db_file = tmp_path / "absent.db"
    old = os.environ.get("DOCSWEEP_INDEX_DB")
    os.environ["DOCSWEEP_INDEX_DB"] = str(db_file)
    try:
        assert load_records_from_index(cfg) is None
    finally:
        if old is None:
            os.environ.pop("DOCSWEEP_INDEX_DB", None)
        else:
            os.environ["DOCSWEEP_INDEX_DB"] = old


def test_load_records_from_index_round_trip(workspace: Path, tmp_path: Path) -> None:
    cfg = _config_with_search(workspace, tmp_path)
    db_file = tmp_path / "idx.db"
    sync_index(cfg, db_path_override=db_file)

    import os
    old = os.environ.get("DOCSWEEP_INDEX_DB")
    os.environ["DOCSWEEP_INDEX_DB"] = str(db_file)
    try:
        records = load_records_from_index(cfg)
    finally:
        if old is None:
            os.environ.pop("DOCSWEEP_INDEX_DB", None)
        else:
            os.environ["DOCSWEEP_INDEX_DB"] = old

    assert records is not None
    assert len(records) == 3
    names = {Path(r.path).name for r in records}
    assert names == {"plan_one.md", "bugfix_a.md", "pending_x.md"}
    # 復元された FileRecord は classify 後の flags / allowed_actions を持つ
    for r in records:
        assert isinstance(r.flags, list)
        assert isinstance(r.allowed_actions, list)


def test_scan_records_falls_back_when_no_index(workspace: Path, tmp_path: Path) -> None:
    """DOCSWEEP_INDEX_DB を未存在のパスに向ければ scan_records は run_scan へフォールバックする。"""
    cfg = load_config(explicit_roots=[str(workspace)], global_path=tmp_path / "no.yaml")

    import os
    old = os.environ.get("DOCSWEEP_INDEX_DB")
    os.environ["DOCSWEEP_INDEX_DB"] = str(tmp_path / "does-not-exist.db")
    try:
        records = scan_records(cfg)
    finally:
        if old is None:
            os.environ.pop("DOCSWEEP_INDEX_DB", None)
        else:
            os.environ["DOCSWEEP_INDEX_DB"] = old

    # run_scan 由来で全件取れている
    names = {Path(r.path).name for r in records}
    assert {"plan_one.md", "bugfix_a.md", "pending_x.md"} <= names


def test_scan_records_uses_index_when_available(workspace: Path, tmp_path: Path) -> None:
    """sync_index 後は索引から復元される（DB を空にした場合との差で確認）。"""
    cfg = _config_with_search(workspace, tmp_path)
    db_file = tmp_path / "idx.db"
    sync_index(cfg, db_path_override=db_file)

    import os
    old = os.environ.get("DOCSWEEP_INDEX_DB")
    os.environ["DOCSWEEP_INDEX_DB"] = str(db_file)
    try:
        records = scan_records(cfg)
    finally:
        if old is None:
            os.environ.pop("DOCSWEEP_INDEX_DB", None)
        else:
            os.environ["DOCSWEEP_INDEX_DB"] = old

    assert len(records) == 3
    # 索引由来でも flags / allowed_actions は埋まっている
    plan_one = next(r for r in records if Path(r.path).name == "plan_one.md")
    assert plan_one.type == "plan"


def test_schema_v1_to_v2_migration(tmp_path: Path) -> None:
    """v1 (基本メタのみ) で作られた DB を開いた時、ALTER TABLE で v2 カラムが追加される。"""
    import sqlite3

    db_file = tmp_path / "v1.db"
    # v1 スキーマを手動で構築
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE projects (
            project_id TEXT PRIMARY KEY, root_path TEXT, remote_url TEXT, last_scanned TEXT
        );
        CREATE TABLE files (
            file_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            rel_path TEXT NOT NULL,
            type TEXT, status TEXT, review_status TEXT, owner TEXT,
            last_reviewed TEXT, claimed_at TEXT, mtime REAL, body_sha TEXT,
            embedding BLOB,
            UNIQUE(project_id, rel_path)
        );
        CREATE TABLE tags (file_id INTEGER, tag TEXT, PRIMARY KEY(file_id, tag));
        CREATE TABLE related (
            src_file_id INTEGER, dst_path TEXT, dst_file_id INTEGER,
            PRIMARY KEY(src_file_id, dst_path)
        );
        INSERT INTO meta VALUES('schema_version', '1');
    """)
    conn.commit()
    conn.close()

    # connect() の中で init_schema → _migrate_to_v2 が走る
    with db.connect(db_file) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(files)").fetchall()}
        # v2 で追加されたカラムが揃う
        for required in ("title", "summary", "state_label", "flags",
                         "allowed_actions", "abs_path", "project_root"):
            assert required in cols, f"v2 カラム {required} が追加されていない"
        assert db.get_schema_version(c) == 2


def test_sync_index_writes_tags_and_related(tmp_path: Path) -> None:
    root = tmp_path / "dev"
    _write(root / "alpha" / "pyproject.toml", "[project]\nname='alpha'\n")
    _write(
        root / "alpha" / "docs" / "local" / "plan_tagged.md",
        """---
tags:
  - foo
  - bar
related:
  - docs/local/plan_other.md
---
# [計画] tagged

## 概要

x
""",
    )

    cfg = load_config(explicit_roots=[str(root)], global_path=tmp_path / "no.yaml")
    cfg.search_paths = [str(root / "*")]
    db_file = tmp_path / "idx.db"
    sync_index(cfg, db_path_override=db_file)

    with db.connect(db_file) as conn:
        rows = conn.execute("SELECT file_id, rel_path FROM files").fetchall()
        assert len(rows) == 1
        file_id = rows[0]["file_id"]

        tags = {r["tag"] for r in conn.execute(
            "SELECT tag FROM tags WHERE file_id=?", (file_id,)
        ).fetchall()}
        assert {"foo", "bar"} <= tags

        rel_paths = {r["dst_path"] for r in conn.execute(
            "SELECT dst_path FROM related WHERE src_file_id=?", (file_id,)
        ).fetchall()}
        assert "docs/local/plan_other.md" in rel_paths
