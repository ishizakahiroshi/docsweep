"""SQLite 索引基盤（C1）— `~/.docsweep/index.db` を 1 DB に統合し project_id で分割。

役割: 全プロジェクトの md メタ情報・タグ・related を 1 つの SQLite に持ち、
triage / brief / cross / graph / resurrect の高速応答を支える。

設計の要点:
- 1 DB 統合（プロジェクト分割しない）: 横断クエリ性能・新規プロジェクト自動取込・運用簡素化のため
- WAL モード: CLI と Web UI を同時起動してもロックしない
- 索引なしフォールバック: 既存コマンドは「索引があれば索引から、なければ scan へ」で 100% 後方互換
- 旧 ``docsweep/index.py`` (INDEX.md/.json 横断集約) は ``aggregate_index.py`` にリネーム済
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 2


class IndexSchemaError(RuntimeError):
    """索引 DB の schema が壊れている、または未対応であることを示す。"""


# DB 配置はホームディレクトリ直下の ~/.docsweep/index.db（OS 非依存）。
# テスト等で差し替えたい場合は ``DOCSWEEP_INDEX_DB`` 環境変数で上書き可能。
DEFAULT_DB_PATH = Path.home() / ".docsweep" / "index.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS projects (
  project_id   TEXT PRIMARY KEY,
  root_path    TEXT NOT NULL,
  remote_url   TEXT,
  last_scanned TEXT
);

CREATE TABLE IF NOT EXISTS files (
  file_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id     TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  rel_path       TEXT NOT NULL,
  type           TEXT,
  status         TEXT,
  review_status  TEXT,
  owner          TEXT,
  last_reviewed  TEXT,
  claimed_at     TEXT,
  mtime          REAL,
  body_sha       TEXT,
  embedding      BLOB,
  -- v2 (C1 後半): コマンド出力に必要なカラムを索引だけで完結できるよう追加
  title          TEXT,
  summary        TEXT,
  state_label    TEXT,
  state_source   TEXT,
  flags          TEXT,            -- JSON 配列文字列
  allowed_actions TEXT,           -- JSON 配列文字列
  due            TEXT,
  due_parse_error INTEGER,        -- 0/1
  archivable     INTEGER,         -- 0/1
  auto_movable   INTEGER,         -- 0/1
  project_root   TEXT,            -- 絶対パス
  abs_path       TEXT,            -- 絶対パス（移送等で参照）
  UNIQUE(project_id, rel_path)
);

CREATE TABLE IF NOT EXISTS tags (
  file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
  tag     TEXT NOT NULL,
  PRIMARY KEY(file_id, tag)
);

CREATE TABLE IF NOT EXISTS related (
  src_file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
  dst_path    TEXT NOT NULL,
  dst_file_id INTEGER REFERENCES files(file_id) ON DELETE SET NULL,
  PRIMARY KEY(src_file_id, dst_path)
);

CREATE INDEX IF NOT EXISTS idx_files_project_type ON files(project_id, type);
CREATE INDEX IF NOT EXISTS idx_files_status       ON files(status);
CREATE INDEX IF NOT EXISTS idx_files_owner        ON files(owner);
CREATE INDEX IF NOT EXISTS idx_tags_tag           ON tags(tag);
CREATE INDEX IF NOT EXISTS idx_related_dst        ON related(dst_file_id);
"""

# ``executescript`` は保留中の transaction を暗黙に commit するため、schema
# 生成を ``BEGIN IMMEDIATE`` で囲めない。各 statement を個別に発行して、初期化と
# migration の lock 境界を明示する。
_SCHEMA_STATEMENTS = tuple(
    statement.strip()
    for statement in SCHEMA_SQL.split(";")
    if statement.strip()
)
_REQUIRED_TABLES = frozenset({"meta", "projects", "files", "tags", "related"})
_V1_SCHEMA_VERSION = 1


def db_path(override: Path | None = None) -> Path:
    """DB ファイルパスを返す（``override`` > 環境変数 > 既定の順）。"""
    if override is not None:
        return Path(override)
    import os

    env = os.environ.get("DOCSWEEP_INDEX_DB")
    if env:
        return Path(env)
    return DEFAULT_DB_PATH


_V2_COLUMNS: tuple[tuple[str, str], ...] = (
    ("title", "TEXT"),
    ("summary", "TEXT"),
    ("state_label", "TEXT"),
    ("state_source", "TEXT"),
    ("flags", "TEXT"),
    ("allowed_actions", "TEXT"),
    ("due", "TEXT"),
    ("due_parse_error", "INTEGER"),
    ("archivable", "INTEGER"),
    ("auto_movable", "INTEGER"),
    ("project_root", "TEXT"),
    ("abs_path", "TEXT"),
)


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """v1 (基本メタのみ) で作られた既存 DB に新カラムを追加する。値は NULL のまま。

    既存 files 行は次回 sync_index で値が埋まる（mtime 差分で再走査されないと埋まらない
    ため、必要なら手で ``docsweep index-rebuild`` を案内する想定）。
    """
    existing = {r[1] for r in conn.execute("PRAGMA table_info(files)").fetchall()}
    for name, sqltype in _V2_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE files ADD COLUMN {name} {sqltype}")


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _schema_version_or_error(conn: sqlite3.Connection) -> int | None:
    """schema_version を読む。壊れた meta を現行版へ上書きしない。"""
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    if row is None:
        return None
    try:
        return int(str(row[0]).strip())
    except (TypeError, ValueError) as exc:
        raise IndexSchemaError(
            "index schema_version is not numeric; restore the database or remove it "
            "after taking a backup"
        ) from exc


def _schema_state(conn: sqlite3.Connection) -> int | None:
    """DB の状態を返す。``None`` は user table のない未初期化 DB。"""
    tables = _user_tables(conn)
    if not tables:
        return None
    if "meta" not in tables:
        raise IndexSchemaError(
            "index database is partially initialized (meta table is missing)"
        )
    version = _schema_version_or_error(conn)
    if version is None:
        raise IndexSchemaError(
            "index database has no schema_version; restore the database or remove it "
            "after taking a backup"
        )
    return version


def _missing_schema_parts(conn: sqlite3.Connection) -> list[str]:
    missing = sorted(_REQUIRED_TABLES - _user_tables(conn))
    if "files" in _user_tables(conn):
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(files)").fetchall()
        }
        required_columns = {
            "file_id", "project_id", "rel_path", "type", "status", "review_status",
            "owner", "last_reviewed", "claimed_at", "mtime", "body_sha", "embedding",
            *(name for name, _sqltype in _V2_COLUMNS),
        }
        missing.extend(f"files.{name}" for name in sorted(required_columns - columns))
    return missing


def _validate_current_schema(conn: sqlite3.Connection) -> None:
    missing = _missing_schema_parts(conn)
    if missing:
        raise IndexSchemaError(
            "index schema_version is current but the schema is incomplete: "
            + ", ".join(missing)
        )


def _create_schema(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


def _validate_migration_base(conn: sqlite3.Connection) -> None:
    missing = sorted(_REQUIRED_TABLES - _user_tables(conn))
    if "files" in _user_tables(conn):
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(files)").fetchall()
        }
        required_v1 = {
            "file_id", "project_id", "rel_path", "type", "status", "review_status",
            "owner", "last_reviewed", "claimed_at", "mtime", "body_sha", "embedding",
        }
        missing.extend(f"files.{name}" for name in sorted(required_v1 - columns))
    if missing:
        raise IndexSchemaError(
            "index schema_version=1 is incomplete and cannot be migrated: "
            + ", ".join(missing)
        )


def init_schema(conn: sqlite3.Connection) -> None:
    """必要な時だけ schema を生成・移行する。

    現行 schema の接続は SELECT/PRAGMA だけの fast path とし、未初期化 DB と v1
    migration だけを ``BEGIN IMMEDIATE`` で直列化する。lock を取った後に必ず状態を
    読み直すため、並行接続が同じ ALTER を二度実行することはない。
    """
    state = _schema_state(conn)
    if state == SCHEMA_VERSION:
        _validate_current_schema(conn)
        return
    if state not in (None, _V1_SCHEMA_VERSION):
        raise IndexSchemaError(
            f"unsupported index schema_version={state}; current version is {SCHEMA_VERSION}"
        )

    conn.execute("BEGIN IMMEDIATE")
    try:
        # 先に見た状態は lock 待ちの間に別接続が更新している可能性がある。
        state = _schema_state(conn)
        if state == SCHEMA_VERSION:
            _validate_current_schema(conn)
            conn.rollback()
            return
        if state not in (None, _V1_SCHEMA_VERSION):
            raise IndexSchemaError(
                f"unsupported index schema_version={state}; current version is {SCHEMA_VERSION}"
            )

        if state is None:
            _create_schema(conn)
        else:
            _validate_migration_base(conn)
            _migrate_to_v2(conn)
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def get_schema_version(conn: sqlite3.Connection) -> int | None:
    return _schema_version_or_error(conn)


# C3 (bloat-mitigation): WAL の自動 checkpoint しきい値（ページ数）。SQLite の既定 1000 と
# 同値だが、設計意図として明示する。長時間プロセスでこれを超えると自動 truncate される。
WAL_AUTOCHECKPOINT_PAGES = 1000


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """WAL モードで接続を開く context manager。

    親ディレクトリが無ければ作る。`foreign_keys` は ON で開く（related の ON DELETE が効くように）。
    """
    target = db_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"PRAGMA wal_autocheckpoint={WAL_AUTOCHECKPOINT_PAGES}")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        yield conn
    finally:
        conn.close()


# ===================================================================
# C2 / C3 (bloat-mitigation): VACUUM と WAL checkpoint
# ===================================================================


def vacuum(conn: sqlite3.Connection) -> None:
    """``VACUUM`` を実行して freelist を解放しファイルを縮める。

    VACUUM はトランザクション内では実行できない。本コードベースの ``connect()`` は
    ``isolation_level=None``（autocommit）で開くので、明示的な ``BEGIN`` が無ければ
    ここからそのまま発行できる。他プロセスが書込中の場合は ``OperationalError`` で
    失敗するので呼び出し側で案内すること。

    WAL モードでは VACUUM の結果が -wal に蓄積され、main DB ファイルは checkpoint
    まで縮まないため、直後に TRUNCATE checkpoint を打って物理サイズも回収する。
    """
    conn.execute("VACUUM")
    checkpoint_truncate(conn)


def checkpoint_truncate(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """``PRAGMA wal_checkpoint(TRUNCATE)`` を実行し ``-wal`` ファイルを切り詰める。

    Returns:
        ``(busy, log_pages, checkpointed_pages)`` — SQLite ドキュメント準拠。
        busy=1 は他プロセスが読込中で TRUNCATE まで進めなかったことを示す（PASSIVE 分は完了）。
    """
    row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if not row:
        return (0, 0, 0)
    return (int(row[0]), int(row[1]), int(row[2]))


def upsert_project(
    conn: sqlite3.Connection,
    project_id: str,
    root_path: str,
    remote_url: str | None,
    last_scanned: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO projects(project_id, root_path, remote_url, last_scanned)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
          root_path = excluded.root_path,
          remote_url = excluded.remote_url,
          last_scanned = excluded.last_scanned
        """,
        (project_id, root_path, remote_url, last_scanned),
    )


def upsert_file(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    rel_path: str,
    type_: str | None,
    status: str | None,
    review_status: str | None,
    owner: str | None,
    last_reviewed: str | None,
    claimed_at: str | None,
    mtime: float | None,
    body_sha: str | None,
    # v2 (C1 後半): コマンド出力に必要な追加カラム
    title: str | None = None,
    summary: str | None = None,
    state_label: str | None = None,
    state_source: str | None = None,
    flags: str | None = None,            # JSON 配列文字列
    allowed_actions: str | None = None,  # JSON 配列文字列
    due: str | None = None,
    due_parse_error: bool | None = None,
    archivable: bool | None = None,
    auto_movable: bool | None = None,
    project_root: str | None = None,
    abs_path: str | None = None,
) -> int:
    """files 行を UPSERT し file_id を返す。"""
    conn.execute(
        """
        INSERT INTO files(project_id, rel_path, type, status, review_status, owner,
                          last_reviewed, claimed_at, mtime, body_sha,
                          title, summary, state_label, state_source,
                          flags, allowed_actions, due, due_parse_error,
                          archivable, auto_movable, project_root, abs_path)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_id, rel_path) DO UPDATE SET
          type=excluded.type,
          status=excluded.status,
          review_status=excluded.review_status,
          owner=excluded.owner,
          last_reviewed=excluded.last_reviewed,
          claimed_at=excluded.claimed_at,
          mtime=excluded.mtime,
          body_sha=excluded.body_sha,
          title=excluded.title,
          summary=excluded.summary,
          state_label=excluded.state_label,
          state_source=excluded.state_source,
          flags=excluded.flags,
          allowed_actions=excluded.allowed_actions,
          due=excluded.due,
          due_parse_error=excluded.due_parse_error,
          archivable=excluded.archivable,
          auto_movable=excluded.auto_movable,
          project_root=excluded.project_root,
          abs_path=excluded.abs_path
        """,
        (project_id, rel_path, type_, status, review_status, owner,
         last_reviewed, claimed_at, mtime, body_sha,
         title, summary, state_label, state_source,
         flags, allowed_actions, due,
         None if due_parse_error is None else (1 if due_parse_error else 0),
         None if archivable is None else (1 if archivable else 0),
         None if auto_movable is None else (1 if auto_movable else 0),
         project_root, abs_path),
    )
    row = conn.execute(
        "SELECT file_id FROM files WHERE project_id=? AND rel_path=?",
        (project_id, rel_path),
    ).fetchone()
    return int(row[0])


def replace_tags(conn: sqlite3.Connection, file_id: int, tags: list[str]) -> None:
    conn.execute("DELETE FROM tags WHERE file_id=?", (file_id,))
    if tags:
        conn.executemany(
            "INSERT OR IGNORE INTO tags(file_id, tag) VALUES(?, ?)",
            [(file_id, t) for t in tags],
        )


def replace_related(conn: sqlite3.Connection, file_id: int, dst_paths: list[str]) -> None:
    conn.execute("DELETE FROM related WHERE src_file_id=?", (file_id,))
    if dst_paths:
        conn.executemany(
            "INSERT OR IGNORE INTO related(src_file_id, dst_path) VALUES(?, ?)",
            [(file_id, p) for p in dst_paths],
        )


def delete_file(conn: sqlite3.Connection, project_id: str, rel_path: str) -> None:
    conn.execute(
        "DELETE FROM files WHERE project_id=? AND rel_path=?",
        (project_id, rel_path),
    )


def known_files(
    conn: sqlite3.Connection, project_id: str,
) -> dict[str, tuple[float | None, str | None, str | None, str | None, str | None]]:
    """既知ファイルを返す（mtime/body_sha/flags/actions/abs_path）。差分判定に使う。"""
    rows = conn.execute(
        "SELECT rel_path, mtime, body_sha, flags, allowed_actions, abs_path FROM files WHERE project_id=?",
        (project_id,),
    ).fetchall()
    return {
        r["rel_path"]: (r["mtime"], r["body_sha"], r["flags"], r["allowed_actions"], r["abs_path"])
        for r in rows
    }


def list_projects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM projects ORDER BY project_id").fetchall()


# ===================================================================
# C1 (bloat-mitigation): 索引 DB の物理サイズ・行数・freelist を観測する
# ===================================================================


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def collect_stats(conn: sqlite3.Connection, *, path: Path | None = None) -> dict:
    """索引 DB の容量・行数・embedding サイズを収集する。

    Args:
        conn: 接続済 sqlite3.Connection
        path: DB ファイルパス（未指定なら ``db_path()`` の結果）。``-wal`` / ``-shm`` サイズの算出に使う

    Returns:
        観測値の dict。``index-stats`` コマンドと VACUUM 効果計測に共用する
    """
    target = db_path(path)
    wal_path = target.with_name(target.name + "-wal")
    shm_path = target.with_name(target.name + "-shm")

    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
    freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])

    def _count(sql: str) -> int:
        row = conn.execute(sql).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    embedding_rows = _count(
        "SELECT COUNT(*) FROM files WHERE embedding IS NOT NULL"
    )
    embedding_bytes = _count(
        "SELECT COALESCE(SUM(LENGTH(embedding)), 0) FROM files WHERE embedding IS NOT NULL"
    )

    mtime_min = conn.execute(
        "SELECT MIN(mtime) FROM files WHERE mtime IS NOT NULL"
    ).fetchone()[0]
    mtime_max = conn.execute(
        "SELECT MAX(mtime) FROM files WHERE mtime IS NOT NULL"
    ).fetchone()[0]

    return {
        "db_path": str(target),
        "db_size_bytes": _file_size(target),
        "wal_size_bytes": _file_size(wal_path),
        "shm_size_bytes": _file_size(shm_path),
        "page_size": page_size,
        "page_count": page_count,
        "used_bytes": page_size * (page_count - freelist_count),
        "freelist_count": freelist_count,
        "freelist_bytes": page_size * freelist_count,
        "projects": _count("SELECT COUNT(*) FROM projects"),
        "files": _count("SELECT COUNT(*) FROM files"),
        "tags": _count("SELECT COUNT(*) FROM tags"),
        "related": _count("SELECT COUNT(*) FROM related"),
        "embedding_rows": embedding_rows,
        "embedding_bytes": embedding_bytes,
        "mtime_min": mtime_min,
        "mtime_max": mtime_max,
        "schema_version": get_schema_version(conn),
    }


# ===================================================================
# C1 後半: 索引から FileRecord を再構成する（既存コマンドの索引利用切替用）
# ===================================================================


def _age_days_from_mtime(mtime: float | None) -> int:
    if mtime is None:
        return 0
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).timestamp()
    return max(0, int((now - mtime) // 86400))


def load_records_from_index(
    config,  # docsweep.config.Config — 循環 import 回避で型注釈は string
    *,
    db_path_override: Path | None = None,
    project_filter: str | None = None,
) -> list | None:
    """SQLite 索引から ``FileRecord`` 一覧を再構成する。

    Returns:
        ``list[FileRecord]`` — DB が空 / 無い / 不整合の場合は ``None`` を返す（呼び出し側
        が ``run_scan`` フォールバックに切替えるためのシグナル）。

    Note:
        本関数は ``classify`` を呼ばない。``flags`` / ``allowed_actions`` は索引に
        保存された値（``sync_index`` 時点の classify 結果）をそのまま復元する。
    """
    import json as _json

    from .models import FileRecord

    target = db_path(db_path_override)
    if not target.is_file():
        return None

    with connect(db_path_override) as conn:
        count_row = conn.execute("SELECT COUNT(*) FROM files").fetchone()
        if not count_row or count_row[0] == 0:
            return None

        sql = """
        SELECT
          project_id, rel_path, type, status, review_status, owner,
          last_reviewed, mtime, title, summary, state_label, state_source,
          flags, allowed_actions, due, due_parse_error, archivable, auto_movable,
          project_root, abs_path
        FROM files
        """
        params: tuple = ()
        if project_filter:
            sql += " WHERE project_id = ?"
            params = (project_filter,)

        records: list[FileRecord] = []
        for row in conn.execute(sql, params).fetchall():
            file_id_row = conn.execute(
                "SELECT file_id FROM files WHERE project_id=? AND rel_path=?",
                (row["project_id"], row["rel_path"]),
            ).fetchone()
            file_id = file_id_row[0] if file_id_row else None

            tags: list[str] = []
            related: list[str] = []
            if file_id is not None:
                tags = [r[0] for r in conn.execute(
                    "SELECT tag FROM tags WHERE file_id=? ORDER BY tag", (file_id,)
                ).fetchall()]
                related = [r[0] for r in conn.execute(
                    "SELECT dst_path FROM related WHERE src_file_id=? ORDER BY dst_path", (file_id,)
                ).fetchall()]

            try:
                flags = _json.loads(row["flags"]) if row["flags"] else []
            except (ValueError, TypeError):
                flags = []
            try:
                allowed_actions = _json.loads(row["allowed_actions"]) if row["allowed_actions"] else []
            except (ValueError, TypeError):
                allowed_actions = []

            mtime = float(row["mtime"]) if row["mtime"] is not None else 0.0

            records.append(FileRecord(
                path=row["abs_path"] or row["rel_path"],
                project=row["project_id"],
                project_root=row["project_root"] or "",
                type=row["type"],
                state=row["status"],
                state_label=row["state_label"],
                state_source=row["state_source"] or "none",
                title=row["title"],
                summary=row["summary"],
                mtime=mtime,
                age_days=_age_days_from_mtime(mtime),
                archivable=bool(row["archivable"]) if row["archivable"] is not None else False,
                auto_movable=bool(row["auto_movable"]) if row["auto_movable"] is not None else False,
                due=row["due"],
                due_parse_error=bool(row["due_parse_error"]) if row["due_parse_error"] is not None else False,
                flags=flags,
                allowed_actions=allowed_actions,
                tags=tags,
                owner=row["owner"],
                review_status=row["review_status"],
                related=related,
                last_reviewed=row["last_reviewed"],
            ))

    return records
