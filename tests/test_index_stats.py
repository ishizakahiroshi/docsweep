"""C1 (bloat-mitigation): index-stats — DB サイズ・行数・freelist の観測。"""

from __future__ import annotations

from pathlib import Path

import pytest

from docsweep import index as db


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    """空の DB を作って path を返す。"""
    db_file = tmp_path / "idx.db"
    with db.connect(db_file):
        pass
    return db_file


def test_collect_stats_empty(empty_db: Path) -> None:
    """空 DB でも全キーが揃い、行数は 0、サイズは正の値を返す。"""
    with db.connect(empty_db) as conn:
        stats = db.collect_stats(conn, path=empty_db)

    expected_keys = {
        "db_path", "db_size_bytes", "wal_size_bytes", "shm_size_bytes",
        "page_size", "page_count", "used_bytes",
        "freelist_count", "freelist_bytes",
        "projects", "files", "tags", "related",
        "embedding_rows", "embedding_bytes",
        "mtime_min", "mtime_max", "schema_version",
    }
    assert expected_keys <= set(stats.keys())

    assert stats["db_path"] == str(empty_db)
    assert stats["db_size_bytes"] > 0
    assert stats["page_size"] > 0
    assert stats["page_count"] > 0
    assert stats["projects"] == 0
    assert stats["files"] == 0
    assert stats["tags"] == 0
    assert stats["related"] == 0
    assert stats["embedding_rows"] == 0
    assert stats["embedding_bytes"] == 0
    assert stats["mtime_min"] is None
    assert stats["mtime_max"] is None
    assert stats["schema_version"] == db.SCHEMA_VERSION


def test_collect_stats_with_rows(tmp_path: Path) -> None:
    """projects/files/tags/related を投入後、行数と mtime 範囲が反映される。"""
    db_file = tmp_path / "idx.db"
    with db.connect(db_file) as conn:
        db.upsert_project(conn, "alpha", str(tmp_path / "alpha"), None, None)
        fid1 = db.upsert_file(
            conn, project_id="alpha", rel_path="docs/local/plan_a.md",
            type_="plan", status="[計画]", review_status=None, owner=None,
            last_reviewed=None, claimed_at=None, mtime=1000.0, body_sha="aaa",
        )
        fid2 = db.upsert_file(
            conn, project_id="alpha", rel_path="docs/local/plan_b.md",
            type_="plan", status="[計画]", review_status=None, owner=None,
            last_reviewed=None, claimed_at=None, mtime=2000.0, body_sha="bbb",
        )
        db.replace_tags(conn, fid1, ["t1", "t2"])
        db.replace_related(conn, fid2, ["docs/local/plan_a.md"])

        stats = db.collect_stats(conn, path=db_file)

    assert stats["projects"] == 1
    assert stats["files"] == 2
    assert stats["tags"] == 2
    assert stats["related"] == 1
    assert stats["mtime_min"] == 1000.0
    assert stats["mtime_max"] == 2000.0


def test_collect_stats_embedding_size(tmp_path: Path) -> None:
    """embedding BLOB が入ると embedding_rows / embedding_bytes に反映される。"""
    db_file = tmp_path / "idx.db"
    with db.connect(db_file) as conn:
        db.upsert_project(conn, "alpha", str(tmp_path), None, None)
        db.upsert_file(
            conn, project_id="alpha", rel_path="x.md",
            type_=None, status=None, review_status=None, owner=None,
            last_reviewed=None, claimed_at=None, mtime=None, body_sha=None,
        )
        # 1.5KB の擬似 embedding
        payload = b"\x00" * 1536
        conn.execute(
            "UPDATE files SET embedding=? WHERE project_id=? AND rel_path=?",
            (payload, "alpha", "x.md"),
        )

        stats = db.collect_stats(conn, path=db_file)

    assert stats["embedding_rows"] == 1
    assert stats["embedding_bytes"] == 1536


def test_collect_stats_freelist_after_delete(tmp_path: Path) -> None:
    """大量挿入 → DELETE で freelist_count が増える（VACUUM 効果計測の根拠）。"""
    db_file = tmp_path / "idx.db"
    with db.connect(db_file) as conn:
        db.upsert_project(conn, "alpha", str(tmp_path), None, None)
        # 多めに挿入してページ消費を確実にする
        for i in range(200):
            db.upsert_file(
                conn, project_id="alpha", rel_path=f"f{i}.md",
                type_="plan", status="[計画]", review_status=None, owner=None,
                last_reviewed=None, claimed_at=None, mtime=float(i),
                body_sha=f"sha{i}",
                title=f"title-{i}" * 20,  # ある程度の本文サイズ
            )

        before = db.collect_stats(conn, path=db_file)
        assert before["files"] == 200

        conn.execute("DELETE FROM files")
        conn.commit()

        after = db.collect_stats(conn, path=db_file)

    assert after["files"] == 0
    # DELETE 後は freelist にページが移っている
    assert after["freelist_count"] > before["freelist_count"]
    # ファイルサイズ自体は VACUUM 前なので縮まない
    assert after["db_size_bytes"] >= before["db_size_bytes"]
