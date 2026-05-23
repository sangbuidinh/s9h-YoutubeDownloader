import sqlite3
import sys
from contextlib import closing
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.runtime_paths import db_file


TABLES = (
    "channels",
    "download_items",
    "download_files",
    "app_meta",
)

QUERY_PLANS = (
    (
        "get_channel_video_entries items by platform + channel_id",
        """
        SELECT *
        FROM download_items
        WHERE platform = ?
          AND channel_id = ?
        ORDER BY id
        """,
        ("youtube", "channel"),
    ),
    (
        "get_channel_video_entries items by platform + channel_id + save_base_folder_norm",
        """
        SELECT *
        FROM download_items
        WHERE platform = ?
          AND channel_id = ?
          AND save_base_folder_norm = ?
        ORDER BY id
        """,
        ("youtube", "channel", "d:/out"),
    ),
    (
        "get_channel_video_entries files by platform + channel_id + save_base_folder_norm",
        """
        SELECT df.item_id, df.part, df.status, df.filename_raw, df.path_raw
        FROM download_files df
        JOIN download_items di ON di.id = df.item_id
        WHERE di.platform = ?
          AND di.channel_id = ?
          AND di.save_base_folder_norm = ?
        ORDER BY di.id, df.part
        """,
        ("youtube", "channel", "d:/out"),
    ),
    (
        "find item by platform + channel_id + video_id + save_base_folder_norm",
        """
        SELECT *
        FROM download_items
        WHERE platform = ?
          AND channel_id = ?
          AND video_id = ?
          AND save_base_folder_norm = ?
        LIMIT 1
        """,
        ("youtube", "channel", "video", "d:/out"),
    ),
    (
        "get_video_entry by platform + channel_id + video_id",
        """
        SELECT *
        FROM download_items
        WHERE platform = ?
          AND channel_id = ?
          AND video_id = ?
        ORDER BY id
        LIMIT 1
        """,
        ("youtube", "channel", "video"),
    ),
    (
        "load files by item_id",
        """
        SELECT *
        FROM download_files
        WHERE item_id = ?
        ORDER BY part
        """,
        (1,),
    ),
    (
        "load file by item_id + part",
        """
        SELECT *
        FROM download_files
        WHERE item_id = ?
          AND part = ?
        LIMIT 1
        """,
        (1, "video"),
    ),
    (
        "find file by path_norm",
        """
        SELECT *
        FROM download_files
        WHERE path_norm = ?
        LIMIT 1
        """,
        ("d:/out/video.mp4",),
    ),
)


def main() -> int:
    _configure_stdio()
    path = db_file()
    print("SQLite index audit")
    print(f"db_path: {path}")
    if not path.exists():
        print("status: DB file is missing")
        return 2

    with closing(_connect_read_only(path)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        _print_indexes(conn)
        _print_query_plans(conn)
    return 0


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _print_indexes(conn: sqlite3.Connection) -> None:
    print()
    print("Indexes")
    for table_name in TABLES:
        print(f"table: {table_name}")
        if not _table_exists(conn, table_name):
            print("  missing table")
            continue

        indexes = conn.execute(f"PRAGMA index_list({quote_identifier(table_name)})").fetchall()
        if not indexes:
            print("  none")
            continue

        for index in indexes:
            index_name = index["name"]
            unique = "yes" if index["unique"] else "no"
            origin = index["origin"]
            columns = _indexed_columns(conn, index_name)
            sql = _index_sql(conn, index_name)
            print(f"  index: {index_name}")
            print(f"    unique: {unique}")
            print(f"    origin: {origin}")
            print(f"    columns: {', '.join(columns) if columns else '(none)'}")
            print(f"    sql: {sql or '(implicit index; SQL unavailable)'}")


def _print_query_plans(conn: sqlite3.Connection) -> None:
    print()
    print("EXPLAIN QUERY PLAN")
    for label, sql, params in QUERY_PLANS:
        print(f"query: {label}")
        rows = conn.execute(f"EXPLAIN QUERY PLAN {_single_line(sql)}", params).fetchall()
        for row in rows:
            print(f"  {row['detail']}")


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _indexed_columns(conn: sqlite3.Connection, index_name: str) -> list[str]:
    rows = conn.execute(f"PRAGMA index_xinfo({quote_identifier(index_name)})").fetchall()
    key_rows = [row for row in rows if row["key"]]
    key_rows.sort(key=lambda row: row["seqno"])
    return [_index_column_name(row) for row in key_rows]


def _index_column_name(row: sqlite3.Row) -> str:
    name = row["name"]
    if name is not None:
        return str(name)
    cid = row["cid"]
    if cid == -1:
        return "rowid"
    if cid == -2:
        return f"expression[{row['seqno']}]"
    return f"cid[{cid}]"


def _index_sql(conn: sqlite3.Connection, index_name: str) -> str | None:
    row = conn.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'index' AND name = ?
        """,
        (index_name,),
    ).fetchone()
    return row["sql"] if row else None


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _single_line(sql: str) -> str:
    return " ".join(sql.split())


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
