"""C5: index schema fast path, migration locking, and same-name project identity."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from docsweep import index as db
from docsweep.config import load_config
from docsweep.scan import scan, sync_index


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _make_v1_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
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
        """
    )
    conn.commit()
    conn.close()


def _init_concurrently(path: Path, count: int = 2) -> list[BaseException]:
    barrier = threading.Barrier(count)
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        conn = sqlite3.connect(str(path), isolation_level=None, timeout=10)
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            barrier.wait(timeout=10)
            db.init_schema(conn)
        except BaseException as exc:  # assert the aggregate after all workers finish
            with lock:
                errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    return errors


def test_current_schema_connection_has_no_schema_write_sql(tmp_path: Path) -> None:
    db_file = tmp_path / "index.db"
    with db.connect(db_file) as conn:
        traces: list[str] = []
        conn.set_trace_callback(traces.append)
        db.init_schema(conn)

    writes = (
        "CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ", "REPLACE ",
    )
    assert not any(
        trace.lstrip().upper().startswith(writes) for trace in traces
    ), traces


def test_parallel_initialization_finishes_with_one_consistent_schema(tmp_path: Path) -> None:
    db_file = tmp_path / "index.db"

    assert _init_concurrently(db_file) == []

    with db.connect(db_file) as conn:
        assert db.get_schema_version(conn) == db.SCHEMA_VERSION
        columns = {row[1] for row in conn.execute("PRAGMA table_info(files)")}
        assert {name for name, _sqltype in db._V2_COLUMNS} <= columns


def test_parallel_v1_migration_finishes_without_duplicate_columns(tmp_path: Path) -> None:
    db_file = tmp_path / "index.db"
    _make_v1_db(db_file)

    assert _init_concurrently(db_file) == []

    with db.connect(db_file) as conn:
        assert db.get_schema_version(conn) == db.SCHEMA_VERSION
        columns = [row[1] for row in conn.execute("PRAGMA table_info(files)")]
        assert len(columns) == len(set(columns))
        assert {name for name, _sqltype in db._V2_COLUMNS} <= set(columns)


@pytest.mark.parametrize("value", ["not-a-number", "3"])
def test_invalid_schema_version_is_an_explicit_recoverable_error(
    tmp_path: Path, value: str
) -> None:
    db_file = tmp_path / "index.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES('schema_version', ?)", (value,))
    conn.commit()
    conn.close()

    with pytest.raises(db.IndexSchemaError):
        with db.connect(db_file):
            pass


def test_same_basename_roots_have_stable_separate_index_records(tmp_path: Path) -> None:
    root_a = tmp_path / "left" / "repo"
    root_b = tmp_path / "right" / "repo"
    plan_template = (
        "---\n"
        "tags: [{tag}]\n"
        "related: [docs/local/other.md]\n"
        "---\n"
        "# [計画] {tag}\n\n"
        "## 概要\n\n{tag}\n"
    )
    for root, tag in ((root_a, "left"), (root_b, "right")):
        _write(root / "pyproject.toml", "[project]\nname='repo'\n")
        _write(root / "docs" / "local" / f"plan_{tag}.md", plan_template.format(tag=tag))

    cfg = load_config(
        explicit_roots=[str(root_a.parent), str(root_b.parent)],
        global_path=tmp_path / "missing.yaml",
    )
    cfg.search_paths = [str(root_a.parent), str(root_b.parent)]
    db_file = tmp_path / "index.db"

    sync_index(cfg, db_path_override=db_file)
    with db.connect(db_file) as conn:
        projects = conn.execute(
            "SELECT project_id, root_path FROM projects ORDER BY root_path"
        ).fetchall()
        files = conn.execute(
            "SELECT file_id, project_id, abs_path, rel_path FROM files ORDER BY abs_path"
        ).fetchall()
        tagged = conn.execute(
            "SELECT f.project_id, t.tag FROM files f JOIN tags t ON t.file_id=f.file_id"
        ).fetchall()
        related = conn.execute(
            "SELECT src_file_id, dst_path FROM related ORDER BY src_file_id"
        ).fetchall()

    assert len(projects) == 2
    assert len({row[0] for row in projects}) == 2
    assert {Path(row[1]).resolve() for row in projects} == {
        root_a.resolve(), root_b.resolve()
    }
    assert len(files) == 2
    by_root = {
        Path(row[2]).resolve().parents[2]: row[1]
        for row in files
    }
    assert by_root[root_a.resolve()] != by_root[root_b.resolve()]
    assert {row[1] for row in tagged} == {"left", "right"}
    assert {row[0] for row in tagged} == {row[1] for row in files}
    assert len(related) == 2
    assert {row[0] for row in related} == {row[0] for row in files}

    first_ids = {Path(row[1]).resolve(): row[0] for row in projects}
    sync_index(
        cfg,
        full=True,
        db_path_override=db_file,
    )
    cfg.search_paths = [str(root_b.parent), str(root_a.parent)]
    sync_index(cfg, full=True, db_path_override=db_file)
    with db.connect(db_file) as conn:
        second_ids = {
            Path(row["root_path"]).resolve(): row["project_id"]
            for row in conn.execute("SELECT project_id, root_path FROM projects")
            if Path(row["root_path"]).resolve() in first_ids
        }
    assert second_ids == first_ids

    fallback = scan(cfg)
    assert {
        Path(record.record.project_root).resolve(): record.record.project
        for record in fallback
    } == first_ids


def test_single_project_keeps_basename_project_id(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "pyproject.toml", "[project]\nname='repo'\n")
    _write(root / "docs" / "local" / "plan_one.md", "# [計画] one\n\n## 概要\n\nx\n")
    cfg = load_config(explicit_roots=[str(root)], global_path=tmp_path / "missing.yaml")
    cfg.search_paths = [str(root)]
    db_file = tmp_path / "index.db"

    sync_index(cfg, db_path_override=db_file)
    with db.connect(db_file) as conn:
        assert [row[0] for row in conn.execute("SELECT project_id FROM projects")] == ["repo"]
