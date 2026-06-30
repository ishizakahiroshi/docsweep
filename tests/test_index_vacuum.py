"""C2 (bloat-mitigation): VACUUM — 物理サイズ回収と手動メンテ口。"""

from __future__ import annotations

from pathlib import Path

from docsweep import index as db


def _bulk_insert(conn, n: int) -> None:
    db.upsert_project(conn, "alpha", "/tmp/alpha", None, None)
    for i in range(n):
        db.upsert_file(
            conn, project_id="alpha", rel_path=f"docs/local/f{i}.md",
            type_="plan", status="[計画]", review_status=None, owner=None,
            last_reviewed=None, claimed_at=None, mtime=float(i),
            body_sha=f"sha{i}",
            title=f"title-{i}" * 30,  # ある程度の本文ボリューム
            summary=f"summary-{i}" * 30,
        )


def test_vacuum_shrinks_file_after_delete(tmp_path: Path) -> None:
    """大量挿入 → DELETE → VACUUM で freelist と物理サイズが両方縮む。"""
    db_file = tmp_path / "idx.db"
    with db.connect(db_file) as conn:
        _bulk_insert(conn, 300)

    # 別接続でサイズ測定 → DELETE → サイズ測定 → VACUUM → サイズ測定
    with db.connect(db_file) as conn:
        full = db.collect_stats(conn, path=db_file)
        assert full["files"] == 300

        conn.execute("DELETE FROM files")
        conn.commit()
        deleted = db.collect_stats(conn, path=db_file)
        assert deleted["files"] == 0
        assert deleted["freelist_count"] > 0
        # DELETE 直後は物理サイズは変わらない（freelist にページが残る）

        db.vacuum(conn)
        vacuumed = db.collect_stats(conn, path=db_file)

    assert vacuumed["freelist_count"] == 0
    assert vacuumed["db_size_bytes"] < deleted["db_size_bytes"]


def test_vacuum_noop_on_clean_db(tmp_path: Path) -> None:
    """freelist が無い DB で VACUUM しても破壊しない（行は残る）。"""
    db_file = tmp_path / "idx.db"
    with db.connect(db_file) as conn:
        _bulk_insert(conn, 10)
        db.vacuum(conn)
        stats = db.collect_stats(conn, path=db_file)

    assert stats["files"] == 10
    assert stats["freelist_count"] == 0
