import argparse
import argparse
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.runtime_paths import db_file


EXIT_HEALTHY = 0
EXIT_UNHEALTHY = 1
EXIT_MISSING_DB = 2

REQUIRED_TABLES = {
    "app_meta",
    "channels",
    "download_items",
    "download_files",
}
SCHEMA_VERSION_KEY = "schema_version"
DOWNLOAD_STATE_ARCHIVE_TABLE = "download_state_archive"
VIDEO_IDENTITY_INDEX = "uq_download_items_video_identity"
VALID_FILE_PARTS = ("video", "thumb", "audio")
NULL_KEY = "<NULL>"


@dataclass
class HealthCheckResult:
    db_path: Path
    healthy: bool = False
    missing: bool = False
    blocking_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    status_counts: Counter = field(default_factory=Counter)
    manual_override_counts: Counter = field(default_factory=Counter)
    manual_status_counts: Counter = field(default_factory=Counter)


@dataclass(frozen=True)
class SQLiteIndexMetadata:
    name: str
    table_name: str
    columns: tuple[str | None, ...]
    unique: bool
    partial: bool
    origin: str | None


def main() -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Check health of the SQLite download state database.")
    parser.add_argument(
        "--db",
        metavar="PATH",
        type=Path,
        default=None,
        help="SQLite DB path. Defaults to data/download_state.sqlite3.",
    )
    args = parser.parse_args()

    result = check_sqlite_state_health(args.db or db_file())
    print_health_report(result)
    if result.missing:
        return EXIT_MISSING_DB
    return EXIT_HEALTHY if result.healthy else EXIT_UNHEALTHY


def check_sqlite_state_health(path: Path) -> HealthCheckResult:
    result = HealthCheckResult(db_path=path)
    if not path.exists():
        result.missing = True
        result.blocking_issues.append("DB file is missing.")
        return result

    try:
        with closing(_connect_read_only(path)) as conn:
            conn.row_factory = sqlite3.Row
            schema_version = _read_schema_version(conn)
            result.summary["schema_version"] = schema_version
            _check_required_tables(conn, result, schema_version)
            _check_integrity(conn, result)

            if _has_tables(result, "download_items"):
                _check_download_items(conn, result, schema_version)
            if _has_tables(result, "download_files", "download_items"):
                _check_download_files(conn, result)
            _collect_summary(conn, result)
    except sqlite3.Error as exc:
        result.blocking_issues.append(f"DB open/query failed: {type(exc).__name__}: {exc}")

    result.healthy = not result.blocking_issues
    return result


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _read_schema_version(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute(
            "SELECT value FROM app_meta WHERE key = ?",
            (SCHEMA_VERSION_KEY,),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        return int(str(row["value"]))
    except (TypeError, ValueError):
        return None


def _check_required_tables(
    conn: sqlite3.Connection,
    result: HealthCheckResult,
    schema_version: int | None,
) -> None:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    existing = {row["name"] for row in rows}
    result.summary["existing_tables"] = sorted(existing)
    required_tables = _required_tables_for_schema(schema_version)
    missing = sorted(required_tables - existing)
    result.summary["required_tables_present"] = not missing
    result.summary["missing_tables"] = missing
    if missing:
        result.blocking_issues.append(f"Missing required tables: {', '.join(missing)}")


def _required_tables_for_schema(schema_version: int | None) -> set[str]:
    required_tables = set(REQUIRED_TABLES)
    if schema_version is not None and schema_version >= 3:
        required_tables.add("app_schema_migrations")
    if schema_version is not None and schema_version >= 4:
        required_tables.add(DOWNLOAD_STATE_ARCHIVE_TABLE)
    return required_tables


def _check_integrity(conn: sqlite3.Connection, result: HealthCheckResult) -> None:
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    messages = [row[0] for row in rows]
    result.summary["integrity_check"] = messages
    if messages != ["ok"]:
        result.blocking_issues.append("PRAGMA integrity_check did not return ok.")


def _check_download_items(
    conn: sqlite3.Connection,
    result: HealthCheckResult,
    schema_version: int | None,
) -> None:
    item_count = conn.execute("SELECT COUNT(*) FROM download_items").fetchone()[0]
    result.summary["download_items"] = item_count

    duplicate_total = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM (
            SELECT 1
            FROM download_items
            GROUP BY platform, channel_id, video_id
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()["count"]
    duplicate_examples = conn.execute(
        """
        SELECT platform, channel_id, video_id, COUNT(*) AS count
        FROM download_items
        GROUP BY platform, channel_id, video_id
        HAVING COUNT(*) > 1
        ORDER BY count DESC, platform, channel_id, video_id
        LIMIT 10
        """
    ).fetchall()
    result.summary["video_identity_duplicates"] = duplicate_total
    result.summary["duplicate_identity_examples"] = [_identity_text(row) for row in duplicate_examples]
    if duplicate_total and schema_version is not None and schema_version >= 4:
        result.blocking_issues.append("Duplicate video identity rows found.")

    if schema_version is not None and schema_version >= 4:
        metadata = _get_index_metadata(conn, VIDEO_IDENTITY_INDEX)
        result.summary["video_identity_index"] = _index_metadata_summary(metadata)
        if not _matches_required_video_identity_index(metadata):
            result.blocking_issues.append("Missing or invalid full unique video identity index.")


def _check_download_files(conn: sqlite3.Connection, result: HealthCheckResult) -> None:
    placeholders = ",".join("?" for _ in VALID_FILE_PARTS)
    invalid_parts = conn.execute(
        f"""
        SELECT part, COUNT(*) AS count
        FROM download_files
        WHERE part NOT IN ({placeholders})
        GROUP BY part
        ORDER BY part
        """,
        VALID_FILE_PARTS,
    ).fetchall()
    result.summary["invalid_file_parts"] = {row["part"]: row["count"] for row in invalid_parts}
    if invalid_parts:
        result.blocking_issues.append("download_files contains invalid part values.")

    orphan_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM download_files df
        LEFT JOIN download_items di ON di.id = df.item_id
        WHERE di.id IS NULL
        """
    ).fetchone()["count"]
    result.summary["orphan_download_files"] = orphan_count
    if orphan_count:
        result.blocking_issues.append("download_files contains orphan rows.")

    duplicate_part_count = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM (
            SELECT 1
            FROM download_files
            GROUP BY item_id, part
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()["count"]
    result.summary["duplicate_file_part_rows"] = duplicate_part_count
    if duplicate_part_count:
        result.blocking_issues.append("download_files contains duplicate item/part rows.")


def _collect_summary(conn: sqlite3.Connection, result: HealthCheckResult) -> None:
    result.summary.setdefault("archive_rows", 0)
    result.summary.setdefault("archive_item_rows", 0)
    result.summary.setdefault("archive_file_rows", 0)

    for table_name in ("channels", "download_items", "download_files"):
        if not _has_tables(result, table_name):
            continue
        result.summary[table_name] = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

    if _has_tables(result, DOWNLOAD_STATE_ARCHIVE_TABLE):
        archive_counts = {
            row["entity_type"]: row["count"]
            for row in conn.execute(
                f"""
                SELECT entity_type, COUNT(*) AS count
                FROM {DOWNLOAD_STATE_ARCHIVE_TABLE}
                GROUP BY entity_type
                """
            )
        }
        archive_item_rows = archive_counts.get("download_item", 0)
        archive_file_rows = archive_counts.get("download_file", 0)
        result.summary["archive_item_rows"] = archive_item_rows
        result.summary["archive_file_rows"] = archive_file_rows
        result.summary["archive_rows"] = archive_item_rows + archive_file_rows

    if _has_tables(result, "download_items"):
        result.status_counts = Counter(
            {
                row["key"]: row["count"]
                for row in conn.execute(
                    """
                    SELECT COALESCE(status, ?) AS key, COUNT(*) AS count
                    FROM download_items
                    GROUP BY key
                    ORDER BY key
                    """,
                    (NULL_KEY,),
                )
            }
        )
        result.manual_override_counts = Counter(
            {
                row["key"]: row["count"]
                for row in conn.execute(
                    """
                    SELECT
                        CASE
                            WHEN manual_override IS NULL THEN 'missing/NULL'
                            WHEN manual_override = 0 THEN 'false/0'
                            WHEN manual_override = 1 THEN 'true/1'
                            ELSE 'invalid'
                        END AS key,
                        COUNT(*) AS count
                    FROM download_items
                    GROUP BY key
                    ORDER BY key
                    """
                )
            }
        )
        result.manual_status_counts = Counter(
            {
                row["key"]: row["count"]
                for row in conn.execute(
                    """
                    SELECT COALESCE(manual_status, ?) AS key, COUNT(*) AS count
                    FROM download_items
                    GROUP BY key
                    ORDER BY key
                    """,
                    (NULL_KEY,),
                )
            }
        )


def _has_tables(result: HealthCheckResult, *table_names: str) -> bool:
    existing = set(result.summary.get("existing_tables", []))
    return all(table_name in existing for table_name in table_names)


def print_health_report(result: HealthCheckResult) -> None:
    print("SQLite state health check")
    print(f"db_path: {result.db_path}")
    print(f"status: {'HEALTHY' if result.healthy else 'UNHEALTHY'}")

    print("summary:")
    for key in ("channels", "download_items", "download_files"):
        if key in result.summary:
            print(f"  {key}: {result.summary[key]}")
    print(f"  schema_version: {result.summary.get('schema_version')}")
    print(f"  required_tables_present: {result.summary.get('required_tables_present', False)}")
    print(f"  integrity_check: {result.summary.get('integrity_check', [])}")
    print(f"  video_identity_duplicates: {result.summary.get('video_identity_duplicates', 0)}")
    print(f"  duplicate_identity_examples: {result.summary.get('duplicate_identity_examples', [])}")
    print(f"  video_identity_index: {result.summary.get('video_identity_index', {})}")
    print(f"  invalid_file_parts: {result.summary.get('invalid_file_parts', {})}")
    print(f"  orphan_download_files: {result.summary.get('orphan_download_files', 0)}")
    print(f"  duplicate_file_part_rows: {result.summary.get('duplicate_file_part_rows', 0)}")
    print(f"  archive_rows: {result.summary.get('archive_rows', 0)}")
    print(f"  archive_item_rows: {result.summary.get('archive_item_rows', 0)}")
    print(f"  archive_file_rows: {result.summary.get('archive_file_rows', 0)}")

    print("status_counts:")
    _print_counter(result.status_counts)
    print("manual_override_counts:")
    _print_counter(result.manual_override_counts)
    print("manual_status_counts:")
    _print_counter(result.manual_status_counts)

    if result.blocking_issues:
        print("blocking_issues:")
        for issue in result.blocking_issues:
            print(f"  - {issue}")
    if result.warnings:
        print("warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")


def _print_counter(counter: Counter) -> None:
    if not counter:
        print("  <none>")
        return
    for key, count in sorted(counter.items()):
        print(f"  {key}: {count}")


def _identity_text(row: sqlite3.Row) -> str:
    return (
        f"platform={row['platform']}; channel_id={row['channel_id']}; "
        f"video_id={row['video_id']}; count={row['count']}"
    )


def _matches_required_video_identity_index(metadata: SQLiteIndexMetadata | None) -> bool:
    return (
        metadata is not None
        and metadata.table_name == "download_items"
        and metadata.columns == ("platform", "channel_id", "video_id")
        and metadata.unique is True
        and metadata.partial is False
    )


def _index_metadata_summary(metadata: SQLiteIndexMetadata | None) -> dict:
    if metadata is None:
        return {
            "present": False,
            "table": None,
            "columns": (),
            "unique": False,
            "partial": False,
            "origin": None,
        }
    return {
        "present": True,
        "table": metadata.table_name,
        "columns": metadata.columns,
        "unique": metadata.unique,
        "partial": metadata.partial,
        "origin": metadata.origin,
    }


def _get_index_metadata(conn: sqlite3.Connection, index_name: str) -> SQLiteIndexMetadata | None:
    row = conn.execute(
        """
        SELECT name, tbl_name, sql
        FROM sqlite_master
        WHERE type = 'index' AND name = ?
        """,
        (index_name,),
    ).fetchone()
    if row is None:
        return None

    table_name = str(_row_value(row, "tbl_name", 1))
    index_list_row = None
    for pragma_row in conn.execute(f"PRAGMA index_list({_quote_identifier(table_name)})").fetchall():
        if _row_value(pragma_row, "name", 1) == index_name:
            index_list_row = pragma_row
            break
    if index_list_row is None:
        return None

    xinfo_rows = conn.execute(f"PRAGMA index_xinfo({_quote_identifier(index_name)})").fetchall()
    key_rows = [xinfo_row for xinfo_row in xinfo_rows if int(_row_value(xinfo_row, "key", 5)) == 1]
    key_rows.sort(key=lambda xinfo_row: int(_row_value(xinfo_row, "seqno", 0)))
    columns = tuple(
        None if _row_value(xinfo_row, "name", 2) is None else str(_row_value(xinfo_row, "name", 2))
        for xinfo_row in key_rows
    )
    origin = _row_value(index_list_row, "origin", 3)
    return SQLiteIndexMetadata(
        name=str(_row_value(row, "name", 0)),
        table_name=table_name,
        columns=columns,
        unique=bool(int(_row_value(index_list_row, "unique", 2))),
        partial=bool(int(_row_value(index_list_row, "partial", 4))),
        origin=str(origin) if origin is not None else None,
    )


def _row_value(row, key: str, index: int):
    return row[key] if isinstance(row, sqlite3.Row) else row[index]


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
