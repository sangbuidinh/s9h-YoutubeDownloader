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
            _check_required_tables(conn, result)
            _check_integrity(conn, result)

            if _has_tables(result, "download_items"):
                _check_download_items(conn, result)
            if _has_tables(result, "download_files", "download_items"):
                _check_download_files(conn, result)
            _collect_summary(conn, result)
    except sqlite3.Error as exc:
        result.blocking_issues.append(f"DB open/query failed: {type(exc).__name__}: {exc}")

    result.healthy = not result.blocking_issues
    return result


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _check_required_tables(conn: sqlite3.Connection, result: HealthCheckResult) -> None:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    existing = {row["name"] for row in rows}
    missing = sorted(REQUIRED_TABLES - existing)
    result.summary["required_tables_present"] = not missing
    result.summary["missing_tables"] = missing
    if missing:
        result.blocking_issues.append(f"Missing required tables: {', '.join(missing)}")


def _check_integrity(conn: sqlite3.Connection, result: HealthCheckResult) -> None:
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    messages = [row[0] for row in rows]
    result.summary["integrity_check"] = messages
    if messages != ["ok"]:
        result.blocking_issues.append("PRAGMA integrity_check did not return ok.")


def _check_download_items(conn: sqlite3.Connection, result: HealthCheckResult) -> None:
    item_count = conn.execute("SELECT COUNT(*) FROM download_items").fetchone()[0]
    result.summary["download_items"] = item_count

    duplicates = conn.execute(
        """
        SELECT platform, channel_id, video_id, save_base_folder_norm, COUNT(*) AS count
        FROM download_items
        GROUP BY platform, channel_id, video_id, save_base_folder_norm
        HAVING COUNT(*) > 1
        ORDER BY count DESC, platform, channel_id, video_id, save_base_folder_norm
        LIMIT 10
        """
    ).fetchall()
    result.summary["duplicate_identity_examples"] = [_identity_text(row) for row in duplicates]
    if duplicates:
        result.blocking_issues.append("Duplicate download item identity rows found.")


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


def _collect_summary(conn: sqlite3.Connection, result: HealthCheckResult) -> None:
    for table_name in ("channels", "download_items", "download_files"):
        if not _has_tables(result, table_name):
            continue
        result.summary[table_name] = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

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
    missing = set(result.summary.get("missing_tables", []))
    return all(table_name not in missing for table_name in table_names)


def print_health_report(result: HealthCheckResult) -> None:
    print("SQLite state health check")
    print(f"db_path: {result.db_path}")
    print(f"status: {'HEALTHY' if result.healthy else 'UNHEALTHY'}")

    print("summary:")
    for key in ("channels", "download_items", "download_files"):
        if key in result.summary:
            print(f"  {key}: {result.summary[key]}")
    print(f"  required_tables_present: {result.summary.get('required_tables_present', False)}")
    print(f"  integrity_check: {result.summary.get('integrity_check', [])}")
    print(f"  duplicate_identity_examples: {result.summary.get('duplicate_identity_examples', [])}")
    print(f"  invalid_file_parts: {result.summary.get('invalid_file_parts', {})}")
    print(f"  orphan_download_files: {result.summary.get('orphan_download_files', 0)}")

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
        f"video_id={row['video_id']}; save_base_folder_norm={row['save_base_folder_norm']}; "
        f"count={row['count']}"
    )


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
