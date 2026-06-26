import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, state_store
from scripts.sqlite_state_health_check import check_sqlite_state_health


PLATFORM = "youtube"
CHANNEL = "channel"
NOW = "2026-01-01T00:00:00+00:00"


def main() -> int:
    _configure_stdio()
    _test_valid_schema_v3_without_archive()
    _test_schema_v3_requires_app_ledger()
    _test_valid_schema_v4_reports_strict_index_metadata()
    _test_v4_partial_index_is_unhealthy()
    _test_v4_wrong_table_index_is_unhealthy()
    _test_true_duplicate_total_is_not_capped()
    _test_health_check_is_read_only()
    print("SQLite health check smoke tests passed")
    return 0


def _test_valid_schema_v3_without_archive() -> None:
    with TemporaryDirectory(prefix="health_v3_valid_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v3_database(db_path)

        result = check_sqlite_state_health(db_path)

        _assert(result.healthy, f"valid v3 database was unhealthy: {result.blocking_issues}")
        _assert(result.summary["archive_rows"] == 0, "v3 archive rows default is not zero")
        _assert(result.summary["archive_item_rows"] == 0, "v3 archive item rows default is not zero")
        _assert(result.summary["archive_file_rows"] == 0, "v3 archive file rows default is not zero")
        _assert(db_store.DOWNLOAD_STATE_ARCHIVE_TABLE not in result.summary["existing_tables"], "v3 fixture unexpectedly has archive table")
        _assert("video_identity_index" not in result.summary, "v3 required v4 identity index")


def _test_schema_v3_requires_app_ledger() -> None:
    with TemporaryDirectory(prefix="health_v3_no_ledger_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v3_database(db_path)
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("DROP TABLE app_schema_migrations")
            conn.commit()

        result = check_sqlite_state_health(db_path)

        _assert(not result.healthy, "schema v3 without app ledger was healthy")
        _assert("app_schema_migrations" in result.summary["missing_tables"], "missing app ledger was not reported")
        _assert(not any("no such table" in issue for issue in result.blocking_issues), "missing ledger caused unrelated query exception")


def _test_valid_schema_v4_reports_strict_index_metadata() -> None:
    with TemporaryDirectory(prefix="health_v4_valid_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v4_database(db_path, archive_rows=True)

        result = check_sqlite_state_health(db_path)

        _assert(result.healthy, f"valid v4 database was unhealthy: {result.blocking_issues}")
        metadata = result.summary["video_identity_index"]
        _assert(metadata["present"] is True, "v4 identity index not reported present")
        _assert(metadata["table"] == "download_items", "v4 identity index table not reported")
        _assert(metadata["columns"] == ("platform", "channel_id", "video_id"), "v4 identity index columns not reported")
        _assert(metadata["unique"] is True, "v4 identity index uniqueness not reported")
        _assert(metadata["partial"] is False, "v4 identity index partial flag not reported")
        _assert(result.summary["archive_rows"] == 2, "archive summary was not collected")
        _assert(result.summary["archive_item_rows"] == 1, "archive item rows summary wrong")
        _assert(result.summary["archive_file_rows"] == 1, "archive file rows summary wrong")


def _test_v4_partial_index_is_unhealthy() -> None:
    with TemporaryDirectory(prefix="health_v4_partial_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v4_database(db_path)
        _replace_identity_index(
            db_path,
            """
            CREATE UNIQUE INDEX uq_download_items_video_identity
            ON download_items(platform, channel_id, video_id)
            WHERE video_id LIKE 'x%'
            """,
        )

        result = check_sqlite_state_health(db_path)

        _assert(not result.healthy, "v4 partial identity index was healthy")
        _assert(result.summary["video_identity_index"]["partial"] is True, "partial flag not reported")
        _assert(any("identity index" in issue for issue in result.blocking_issues), "partial index issue not reported")
        _assert(_identity_index_sql(db_path).upper().find("WHERE") >= 0, "health check repaired partial index")


def _test_v4_wrong_table_index_is_unhealthy() -> None:
    with TemporaryDirectory(prefix="health_v4_wrong_table_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v4_database(db_path)
        _replace_identity_index(
            db_path,
            """
            CREATE UNIQUE INDEX uq_download_items_video_identity
            ON download_state_archive(platform, channel_id, video_id)
            """,
        )

        result = check_sqlite_state_health(db_path)

        _assert(not result.healthy, "v4 wrong-table identity index was healthy")
        _assert(result.summary["video_identity_index"]["table"] == "download_state_archive", "wrong table not reported")


def _test_true_duplicate_total_is_not_capped() -> None:
    with TemporaryDirectory(prefix="health_duplicates_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v3_database(db_path)
        for index in range(17):
            _insert_duplicate_group(db_path, f"dup-{index:02d}")

        result = check_sqlite_state_health(db_path)

        _assert(result.summary["video_identity_duplicates"] == 17, "duplicate total was capped or miscounted")
        _assert(len(result.summary["duplicate_identity_examples"]) == 10, "duplicate examples were not limited to 10")


def _test_health_check_is_read_only() -> None:
    with TemporaryDirectory(prefix="health_read_only_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v4_database(db_path)
        _replace_identity_index(
            db_path,
            """
            CREATE UNIQUE INDEX uq_download_items_video_identity
            ON download_items(platform, channel_id, video_id)
            WHERE video_id LIKE 'x%'
            """,
        )
        _insert_duplicate_group(db_path, "readonly-dup")
        before = _snapshot_database(db_path)

        result = check_sqlite_state_health(db_path)

        _assert(not result.healthy, "read-only invalid fixture unexpectedly healthy")
        _assert(_snapshot_database(db_path) == before, "health check modified database content or schema")


def _create_v3_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        _create_core_schema(conn)
        conn.execute("CREATE INDEX idx_download_items_channel ON download_items(channel_db_id)")
        conn.execute(
            "CREATE INDEX idx_download_items_channel_folder ON download_items(platform, channel_id, save_base_folder_norm)"
        )
        conn.execute("CREATE INDEX idx_download_files_path_norm ON download_files(path_norm)")
        conn.execute(
            "INSERT INTO app_meta(key, value, updated_at) VALUES (?, '3', ?)",
            (db_store.SCHEMA_VERSION_KEY, NOW),
        )
        conn.execute(
            """
            CREATE TABLE app_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        for version in (1, 2, 3):
            conn.execute(
                "INSERT INTO app_schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (version, db_store.APPLICATION_MIGRATION_NAMES[version], NOW),
            )
        conn.commit()


def _create_v4_database(db_path: Path, *, archive_rows: bool = False) -> None:
    db_store.initialize_database(db_path)
    _forget_initialized(db_path)
    if archive_rows:
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                f"""
                INSERT INTO {db_store.DOWNLOAD_STATE_ARCHIVE_TABLE}(
                    entity_type,
                    source_table,
                    source_row_id,
                    platform,
                    channel_id,
                    video_id,
                    reason,
                    payload_json,
                    archived_at
                )
                VALUES ('download_item', 'download_items', 1, ?, ?, 'archived', 'test', '{{}}', ?)
                """,
                (PLATFORM, CHANNEL, NOW),
            )
            conn.execute(
                f"""
                INSERT INTO {db_store.DOWNLOAD_STATE_ARCHIVE_TABLE}(
                    entity_type,
                    source_table,
                    source_row_id,
                    platform,
                    channel_id,
                    video_id,
                    reason,
                    payload_json,
                    archived_at
                )
                VALUES ('download_file', 'download_files', 1, ?, ?, 'archived', 'test', '{{}}', ?)
                """,
                (PLATFORM, CHANNEL, NOW),
            )
            conn.commit()


def _create_core_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE channels (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            channel_name TEXT NULL,
            save_base_folder_raw TEXT NULL,
            save_base_folder_norm TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(platform, channel_id, save_base_folder_norm)
        );
        CREATE TABLE download_items (
            id INTEGER PRIMARY KEY,
            channel_db_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            platform TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            video_id TEXT NOT NULL,
            save_base_folder_raw TEXT NULL,
            save_base_folder_norm TEXT NOT NULL,
            original_title TEXT NULL,
            sanitized_filename_base TEXT NOT NULL,
            display_order_at_download INTEGER NULL,
            status TEXT NULL,
            manual_status TEXT NULL,
            manual_override INTEGER NULL CHECK(manual_override IN (0, 1) OR manual_override IS NULL),
            downloaded_at TEXT NULL,
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(platform, channel_id, video_id, save_base_folder_norm),
            FOREIGN KEY(channel_db_id) REFERENCES channels(id) ON DELETE CASCADE
        );
        CREATE TABLE download_files (
            id INTEGER PRIMARY KEY,
            item_id INTEGER NOT NULL REFERENCES download_items(id) ON DELETE CASCADE,
            part TEXT NOT NULL CHECK(part IN ('video', 'thumb', 'audio')),
            status TEXT NULL,
            filename_raw TEXT NULL,
            filename_norm TEXT NULL,
            path_raw TEXT NULL,
            path_norm TEXT NULL,
            is_valid INTEGER NOT NULL DEFAULT 1 CHECK(is_valid IN (0, 1)),
            validation_reason TEXT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(item_id, part),
            FOREIGN KEY(item_id) REFERENCES download_items(id) ON DELETE CASCADE
        );
        """
    )


def _replace_identity_index(db_path: Path, create_sql: str) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(f"DROP INDEX {db_store.VIDEO_IDENTITY_INDEX}")
        conn.execute(create_sql)
        conn.commit()


def _insert_duplicate_group(db_path: Path, video_id: str) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        for suffix in ("a", "b"):
            folder = f"D:/{video_id}-{suffix}"
            channel_db_id = conn.execute(
                """
                INSERT INTO channels(platform, channel_id, channel_name, save_base_folder_raw, save_base_folder_norm, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (PLATFORM, CHANNEL, "Channel", folder, _norm(folder), NOW, NOW),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO download_items(
                    channel_db_id,
                    platform,
                    channel_id,
                    video_id,
                    save_base_folder_raw,
                    save_base_folder_norm,
                    sanitized_filename_base,
                    status,
                    updated_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_db_id,
                    PLATFORM,
                    CHANNEL,
                    video_id,
                    folder,
                    _norm(folder),
                    video_id,
                    state_store.STATUS_NOT_DOWNLOADED,
                    NOW,
                    NOW,
                ),
            )
        conn.commit()


def _identity_index_sql(db_path: Path) -> str:
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (db_store.VIDEO_IDENTITY_INDEX,),
        ).fetchone()
    return str(row[0]) if row else ""


def _snapshot_database(db_path: Path) -> dict:
    with closing(sqlite3.connect(db_path)) as conn:
        return {
            "schema": list(
                conn.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type IN ('table', 'index') ORDER BY type, name"
                )
            ),
            "row_counts": {
                table_name: conn.execute(f"SELECT COUNT(*) FROM {db_store.quote_identifier(table_name)}").fetchone()[0]
                for table_name in _table_names(conn)
            },
            "ledger": list(
                conn.execute(
                    "SELECT version, name FROM app_schema_migrations ORDER BY version"
                )
            )
            if "app_schema_migrations" in _table_names(conn)
            else [],
        }


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _forget_initialized(db_path: Path) -> None:
    db_store._INITIALIZED_DATABASES.pop(db_path.resolve(strict=False), None)


def _norm(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.endswith("/") and len(text) > 1 and not (len(text) == 3 and text[1] == ":"):
        text = text[:-1]
    return text.casefold()


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
