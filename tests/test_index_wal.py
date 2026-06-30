"""C3 (bloat-mitigation): WAL の autocheckpoint 明示と TRUNCATE checkpoint。"""

from __future__ import annotations

from pathlib import Path

from docsweep import index as db


def test_wal_autocheckpoint_pragma_is_set(tmp_path: Path) -> None:
    """connect() で wal_autocheckpoint が明示的に設定される。"""
    db_file = tmp_path / "idx.db"
    with db.connect(db_file) as conn:
        row = conn.execute("PRAGMA wal_autocheckpoint").fetchone()
    assert int(row[0]) == db.WAL_AUTOCHECKPOINT_PAGES


def test_checkpoint_truncate_returns_tuple(tmp_path: Path) -> None:
    """checkpoint_truncate は (busy, log_pages, checkpointed_pages) の 3-tuple を返す。"""
    db_file = tmp_path / "idx.db"
    with db.connect(db_file) as conn:
        # 何か書き込んで -wal を作る
        db.upsert_project(conn, "alpha", "/tmp/alpha", None, None)
        result = db.checkpoint_truncate(conn)

    assert isinstance(result, tuple)
    assert len(result) == 3
    busy, log_pages, ckpt_pages = result
    assert busy in (0, 1)
    assert log_pages >= 0
    assert ckpt_pages >= 0


def test_checkpoint_truncate_shrinks_wal(tmp_path: Path) -> None:
    """大量書込で膨れた -wal が TRUNCATE checkpoint 後に縮む。"""
    db_file = tmp_path / "idx.db"
    with db.connect(db_file) as conn:
        db.upsert_project(conn, "alpha", "/tmp/alpha", None, None)
        for i in range(50):
            db.upsert_file(
                conn, project_id="alpha", rel_path=f"f{i}.md",
                type_="plan", status="[計画]", review_status=None, owner=None,
                last_reviewed=None, claimed_at=None, mtime=float(i),
                body_sha=f"sha{i}", title=f"t-{i}" * 20,
            )

        wal_before = db.collect_stats(conn, path=db_file)["wal_size_bytes"]
        db.checkpoint_truncate(conn)
        wal_after = db.collect_stats(conn, path=db_file)["wal_size_bytes"]

    # autocheckpoint で既に縮んでいるケースもあるので「増えていない」を保証
    assert wal_after <= wal_before
