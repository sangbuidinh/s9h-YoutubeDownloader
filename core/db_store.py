import argparse
import json
import os
import sqlite3
import sys
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.filename_utils import normalize_output_stem
from core.runtime_paths import db_file


CURRENT_SCHEMA_VERSION = 4
SCHEMA_VERSION = CURRENT_SCHEMA_VERSION
LEGACY_SCHEMA_VERSION = 1
SCHEMA_VERSION_KEY = "schema_version"
APP_MIGRATION_LEDGER_TABLE = "app_schema_migrations"
SQLITE_BUSY_TIMEOUT_MS = 5000
MIGRATION_BACKUP_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S-%f"
PLATFORM_YOUTUBE = "youtube"
FILE_PARTS = ("video", "thumb", "audio")
DOWNLOAD_STATE_ARCHIVE_TABLE = "download_state_archive"
VIDEO_IDENTITY_INDEX = "uq_download_items_video_identity"
ARCHIVE_VIDEO_INDEX = "idx_download_state_archive_video"
VIDEO_IDENTITY_ARCHIVE_REASON_V4 = "video_identity_consolidation_v4"
REQUIRED_TABLES = (
    "app_meta",
    APP_MIGRATION_LEDGER_TABLE,
    "channels",
    "download_items",
    "download_files",
    DOWNLOAD_STATE_ARCHIVE_TABLE,
)
CORE_REQUIRED_TABLES = (
    "app_meta",
    "channels",
    "download_items",
    "download_files",
)
NULL_COUNTER_KEY = "<NULL>"

REQUIRED_COLUMNS = {
    "app_meta": ("key", "value", "updated_at"),
    APP_MIGRATION_LEDGER_TABLE: ("version", "name", "applied_at"),
    "channels": (
        "id",
        "platform",
        "channel_id",
        "channel_name",
        "save_base_folder_raw",
        "save_base_folder_norm",
        "created_at",
        "updated_at",
    ),
    "download_items": (
        "id",
        "channel_db_id",
        "platform",
        "channel_id",
        "video_id",
        "save_base_folder_raw",
        "save_base_folder_norm",
        "original_title",
        "sanitized_filename_base",
        "display_order_at_download",
        "status",
        "manual_status",
        "manual_override",
        "downloaded_at",
        "updated_at",
        "created_at",
    ),
    "download_files": (
        "id",
        "item_id",
        "part",
        "status",
        "filename_raw",
        "filename_norm",
        "path_raw",
        "path_norm",
        "is_valid",
        "validation_reason",
        "created_at",
        "updated_at",
    ),
    DOWNLOAD_STATE_ARCHIVE_TABLE: (
        "id",
        "entity_type",
        "source_table",
        "source_row_id",
        "platform",
        "channel_id",
        "video_id",
        "reason",
        "payload_json",
        "archived_at",
    ),
}

REQUIRED_INDEXES = {
    "idx_download_items_channel": ("download_items", ("channel_db_id",)),
    "idx_download_items_channel_folder": (
        "download_items",
        ("platform", "channel_id", "save_base_folder_norm"),
    ),
    "idx_download_files_path_norm": ("download_files", ("path_norm",)),
    "uq_download_items_video_identity": ("download_items", ("platform", "channel_id", "video_id")),
    ARCHIVE_VIDEO_INDEX: (
        DOWNLOAD_STATE_ARCHIVE_TABLE,
        ("platform", "channel_id", "video_id"),
    ),
}
REQUIRED_UNIQUE_INDEXES = {VIDEO_IDENTITY_INDEX}

CURRENT_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS app_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY,
        platform TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        channel_name TEXT NULL,
        save_base_folder_raw TEXT NULL,
        save_base_folder_norm TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(platform, channel_id, save_base_folder_norm)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS download_items (
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS download_files (
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
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {DOWNLOAD_STATE_ARCHIVE_TABLE} (
        id INTEGER PRIMARY KEY,
        entity_type TEXT NOT NULL
            CHECK(entity_type IN ('download_item', 'download_file')),
        source_table TEXT NOT NULL,
        source_row_id INTEGER NOT NULL,
        platform TEXT NULL,
        channel_id TEXT NULL,
        video_id TEXT NULL,
        reason TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        archived_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_download_items_channel
    ON download_items(channel_db_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_download_items_channel_folder
    ON download_items(platform, channel_id, save_base_folder_norm)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_download_files_path_norm
    ON download_files(path_norm)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_download_items_video_identity
    ON download_items(platform, channel_id, video_id)
    """,
    f"""
    CREATE INDEX IF NOT EXISTS {ARCHIVE_VIDEO_INDEX}
    ON {DOWNLOAD_STATE_ARCHIVE_TABLE}(platform, channel_id, video_id)
    """,
)

SCHEMA_SQL = ";\n".join(statement.strip() for statement in CURRENT_SCHEMA_STATEMENTS) + ";\n"

_INITIALIZED_DATABASES: dict[Path, "DatabaseFileIdentity"] = {}
_INITIALIZATION_LOCK = threading.RLock()


class DatabaseSchemaError(RuntimeError):
    pass


class DatabaseTooNewError(DatabaseSchemaError):
    pass


class DatabaseMigrationError(DatabaseSchemaError):
    pass


class DatabaseBackupError(DatabaseMigrationError):
    pass


class DatabaseValidationError(DatabaseSchemaError):
    pass


class DatabaseLockError(DatabaseSchemaError):
    pass


class DatabasePathError(DatabaseSchemaError):
    pass


class DatabaseFileChangedError(DatabaseSchemaError):
    pass


class ManagedSQLiteConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


@dataclass(frozen=True)
class DatabaseFileIdentity:
    device: int
    inode: int
    creation_marker_ns: int | None = None


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class SQLiteIndexMetadata:
    name: str
    table_name: str
    columns: tuple[str | None, ...]
    unique: bool
    partial: bool
    origin: str | None


@dataclass(frozen=True)
class DuplicateVideoMergePlan:
    survivor_item_id: int
    item_updates: dict
    part_updates: dict[str, dict]
    duplicate_item_ids: tuple[int, ...]


def connect_db(path: Path | None = None) -> sqlite3.Connection:
    return open_database_connection(path)


def open_database_connection(path: Path | None = None) -> sqlite3.Connection:
    db_path = _resolve_database_path(path)
    expected_identity = _ensure_initialized_database(db_path, allow_new=True)
    return _open_existing_database_connection(db_path, expected_identity, allow_retry=True)


def init_db(path: Path | None = None) -> Path:
    return initialize_database(path)


def initialize_database(path: Path | None = None) -> Path:
    db_path = _resolve_database_path(path)
    _ensure_initialized_database(db_path, allow_new=True)
    return db_path


def _ensure_initialized_database(db_path: Path, *, allow_new: bool) -> DatabaseFileIdentity:
    with _INITIALIZATION_LOCK:
        cached_identity = _INITIALIZED_DATABASES.get(db_path)
        if cached_identity is not None:
            current_identity = _capture_database_identity_for_cache_hit(db_path, cached_identity)
            if current_identity == cached_identity:
                return cached_identity
            _invalidate_cached_database_identity(db_path, cached_identity)
            allow_new = False

        _bootstrap_database(db_path, allow_new=allow_new)
        identity = _capture_database_identity(db_path)
        _INITIALIZED_DATABASES[db_path] = identity
        return identity


def validate_database_schema(conn: sqlite3.Connection) -> None:
    schema_version = _read_schema_version(conn)
    if schema_version != CURRENT_SCHEMA_VERSION:
        raise DatabaseValidationError(
            f"Schema version {schema_version!r} does not match current version {CURRENT_SCHEMA_VERSION}"
        )

    _validate_schema_for_version(conn, CURRENT_SCHEMA_VERSION)

    foreign_key_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_violations:
        raise DatabaseValidationError("Foreign-key check failed")


def _bootstrap_database(db_path: Path, *, allow_new: bool) -> None:
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DatabasePathError(f"Database directory is not writable: {db_path.parent}") from exc

    if db_path.exists() and not db_path.is_file():
        raise DatabasePathError(f"Database path is not a regular file: {db_path}")
    if not db_path.exists() and not allow_new:
        raise DatabaseFileChangedError(f"Database file disappeared: {db_path}")

    try:
        with closing(_connect_bootstrap_connection(db_path, allow_create=allow_new)) as conn:
            conn.row_factory = sqlite3.Row
            schema_version = _read_schema_version(conn)

            if schema_version is not None and schema_version > CURRENT_SCHEMA_VERSION:
                raise DatabaseTooNewError(
                    f"Database {db_path} has schema version {schema_version}; "
                    f"this app supports up to {CURRENT_SCHEMA_VERSION}"
                )

            existing_user_tables = _user_table_names(conn)
            if schema_version is None:
                if not existing_user_tables:
                    if not allow_new:
                        raise DatabaseFileChangedError(
                            f"Replacement database is empty or uninitialized: {db_path}"
                        )
                    _configure_bootstrap_journal(conn)
                    _create_new_database(conn)
                    return
                schema_version = _detect_legacy_schema_version(conn, existing_user_tables)

            if schema_version == CURRENT_SCHEMA_VERSION:
                _configure_bootstrap_journal(conn)
                validate_database_schema(conn)
                return

            if schema_version < LEGACY_SCHEMA_VERSION:
                raise DatabaseSchemaError(f"Unsupported old schema version: {schema_version}")
            _validate_schema_for_version(conn, schema_version)

            _create_migration_backup(conn, db_path, schema_version, CURRENT_SCHEMA_VERSION)
            _configure_bootstrap_journal(conn)
            _migrate_database(conn, schema_version, CURRENT_SCHEMA_VERSION)
    except sqlite3.OperationalError as exc:
        message = str(exc).casefold()
        if _is_sqlite_lock_error(exc):
            raise DatabaseLockError(f"Database is locked after busy timeout: {db_path}") from exc
        if "unable to open" in message or "readonly" in message or "read-only" in message:
            raise DatabasePathError(f"Database path is not writable: {db_path}") from exc
        raise


def _create_new_database(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("BEGIN IMMEDIATE")
        _create_current_schema(conn)
        now = _now_iso()
        _record_completed_application_migrations(conn, CURRENT_SCHEMA_VERSION, now)
        _write_schema_version(conn, CURRENT_SCHEMA_VERSION, now)
        validate_database_schema(conn)
        conn.commit()
    except Exception:
        _abort_transaction(conn)
        raise


def _migrate_database(conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
    try:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_application_ledger_for_existing_version(conn, from_version)
        for version in range(from_version + 1, to_version + 1):
            migration = MIGRATIONS_BY_VERSION.get(version)
            if migration is None:
                raise DatabaseMigrationError(f"No migration defined for schema version {version}")
            try:
                migration.apply(conn)
                now = _now_iso()
                _record_migration(conn, version, migration.name, now)
                _write_schema_version(conn, version, now)
            except DatabaseSchemaError:
                raise
            except Exception as exc:
                raise DatabaseMigrationError(
                    f"Migration {version} ({migration.name}) failed: {type(exc).__name__}: {exc}"
                ) from exc
        validate_database_schema(conn)
        conn.commit()
    except Exception:
        _abort_transaction(conn)
        raise


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    # The v1 runtime repeatedly dropped these obsolete indexes during init_db().
    # From v2 onward this cleanup is a one-time migration step only.
    conn.execute("DROP INDEX IF EXISTS idx_download_items_identity")
    conn.execute("DROP INDEX IF EXISTS idx_download_files_item_part")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_download_items_channel ON download_items(channel_db_id)")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_download_items_channel_folder
        ON download_items(platform, channel_id, save_base_folder_norm)
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_download_files_path_norm ON download_files(path_norm)")


def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    _create_application_ledger_table(conn)


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    _create_download_state_archive_table(conn)
    for group in _duplicate_video_identity_groups(conn):
        _consolidate_duplicate_video_identity(conn, group)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_download_items_video_identity
        ON download_items(platform, channel_id, video_id)
        """
    )


def _create_download_state_archive_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {DOWNLOAD_STATE_ARCHIVE_TABLE} (
            id INTEGER PRIMARY KEY,
            entity_type TEXT NOT NULL
                CHECK(entity_type IN ('download_item', 'download_file')),
            source_table TEXT NOT NULL,
            source_row_id INTEGER NOT NULL,
            platform TEXT NULL,
            channel_id TEXT NULL,
            video_id TEXT NULL,
            reason TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            archived_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {ARCHIVE_VIDEO_INDEX}
        ON {DOWNLOAD_STATE_ARCHIVE_TABLE}(platform, channel_id, video_id)
        """
    )


def _duplicate_video_identity_groups(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            platform,
            channel_id,
            video_id,
            COUNT(*) AS row_count
        FROM download_items
        GROUP BY platform, channel_id, video_id
        HAVING COUNT(*) > 1
        ORDER BY platform, channel_id, video_id
        """
    ).fetchall()


def _consolidate_duplicate_video_identity(conn: sqlite3.Connection, group: sqlite3.Row) -> None:
    platform = group["platform"]
    channel_id = group["channel_id"]
    video_id = group["video_id"]
    item_rows = _download_item_rows_for_identity(conn, platform, channel_id, video_id)
    if len(item_rows) <= 1:
        return

    item_ids = [int(row["id"]) for row in item_rows]
    file_rows = _download_file_rows_for_item_ids(conn, item_ids)
    archived_at = _now_iso()
    _archive_duplicate_video_group(conn, item_rows, file_rows, archived_at)
    plan = build_duplicate_video_merge_plan(item_rows, file_rows)
    _apply_duplicate_video_merge_plan(conn, plan)
    _delete_duplicate_item_rows(conn, plan.duplicate_item_ids)
    _verify_consolidated_video_identity(conn, platform, channel_id, video_id, plan.survivor_item_id)


def _download_item_rows_for_identity(
    conn: sqlite3.Connection,
    platform: str,
    channel_id: str,
    video_id: str,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM download_items
        WHERE platform = ? AND channel_id = ? AND video_id = ?
        ORDER BY id
        """,
        (platform, channel_id, video_id),
    ).fetchall()


def _download_file_rows_for_item_ids(conn: sqlite3.Connection, item_ids: list[int]) -> list[sqlite3.Row]:
    if not item_ids:
        return []
    placeholders = ",".join("?" for _ in item_ids)
    return conn.execute(
        f"""
        SELECT *
        FROM download_files
        WHERE item_id IN ({placeholders})
        ORDER BY item_id, part, id
        """,
        tuple(item_ids),
    ).fetchall()


def _archive_duplicate_video_group(
    conn: sqlite3.Connection,
    item_rows: list[sqlite3.Row],
    file_rows: list[sqlite3.Row],
    archived_at: str,
) -> None:
    item_identity_by_id = {
        int(row["id"]): (row["platform"], row["channel_id"], row["video_id"])
        for row in item_rows
    }
    for row in item_rows:
        _archive_state_row(
            conn,
            entity_type="download_item",
            source_table="download_items",
            row=row,
            platform=row["platform"],
            channel_id=row["channel_id"],
            video_id=row["video_id"],
            archived_at=archived_at,
        )
    for row in file_rows:
        platform, channel_id, video_id = item_identity_by_id[int(row["item_id"])]
        _archive_state_row(
            conn,
            entity_type="download_file",
            source_table="download_files",
            row=row,
            platform=platform,
            channel_id=channel_id,
            video_id=video_id,
            archived_at=archived_at,
        )


def _archive_state_row(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    source_table: str,
    row: sqlite3.Row,
    platform: str | None,
    channel_id: str | None,
    video_id: str | None,
    archived_at: str,
) -> None:
    conn.execute(
        f"""
        INSERT INTO {DOWNLOAD_STATE_ARCHIVE_TABLE}(
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            source_table,
            int(row["id"]),
            platform,
            channel_id,
            video_id,
            VIDEO_IDENTITY_ARCHIVE_REASON_V4,
            _row_payload_json(row),
            archived_at,
        ),
    )


def _row_payload_json(row: sqlite3.Row) -> str:
    payload = {key: row[key] for key in row.keys()}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_duplicate_video_merge_plan(
    item_rows: list[sqlite3.Row],
    file_rows: list[sqlite3.Row],
) -> DuplicateVideoMergePlan:
    if len(item_rows) < 2:
        raise DatabaseMigrationError("Duplicate video merge requires at least two item rows")

    sorted_items = sorted(item_rows, key=lambda row: int(row["id"]))
    survivor = sorted_items[0]
    platform = survivor["platform"]
    channel_id = survivor["channel_id"]
    video_id = survivor["video_id"]
    for row in sorted_items:
        if (row["platform"], row["channel_id"], row["video_id"]) != (platform, channel_id, video_id):
            raise DatabaseMigrationError("Duplicate video group contains mixed identities")

    folder_row = _select_item_folder_metadata_row(sorted_items, survivor)
    item_updates = {
        "channel_db_id": int(folder_row["channel_db_id"]),
        "platform": platform,
        "channel_id": channel_id,
        "video_id": video_id,
        "save_base_folder_raw": folder_row["save_base_folder_raw"],
        "save_base_folder_norm": folder_row["save_base_folder_norm"],
        "original_title": _select_text_from_most_recent_item(sorted_items, "original_title"),
        "sanitized_filename_base": _select_sanitized_filename_base(sorted_items, video_id),
        "display_order_at_download": _select_value_from_most_recent_item(
            sorted_items,
            "display_order_at_download",
            require_text=False,
        ),
        "manual_status": None,
        "manual_override": None,
        "downloaded_at": _select_earliest_non_empty_timestamp(sorted_items, "downloaded_at"),
        "updated_at": _select_valid_timestamp(sorted_items, "updated_at", latest=True) or survivor["updated_at"],
        "created_at": _select_valid_timestamp(sorted_items, "created_at", latest=False) or survivor["created_at"],
    }

    manual_row = _select_valid_manual_override_row(sorted_items)
    if manual_row is not None:
        item_updates["manual_status"] = manual_row["manual_status"]
        item_updates["manual_override"] = 1

    part_updates = {}
    for part in FILE_PARTS:
        part_rows = [row for row in file_rows if row["part"] == part]
        if part_rows:
            part_updates[part] = _merge_duplicate_file_part(part_rows)

    entry = {
        "channel_id": channel_id,
        "save_base_folder": item_updates["save_base_folder_raw"] or "",
        "video_id": video_id,
        "original_title": item_updates["original_title"],
        "sanitized_filename_base": item_updates["sanitized_filename_base"],
    }
    if item_updates["manual_override"] == 1:
        entry["manual_override"] = True
        entry["manual_status"] = item_updates["manual_status"]
    for part, updates in part_updates.items():
        _set_if_not_none(entry, f"{part}_filename", updates["filename_raw"])
        _set_if_not_none(entry, f"{part}_path", updates["path_raw"])
        _set_if_not_none(entry, f"{part}_status", updates["status"])
    item_updates["status"] = _get_effective_status(entry)

    return DuplicateVideoMergePlan(
        survivor_item_id=int(survivor["id"]),
        item_updates=item_updates,
        part_updates=part_updates,
        duplicate_item_ids=tuple(int(row["id"]) for row in sorted_items[1:]),
    )


def _select_item_folder_metadata_row(item_rows: list[sqlite3.Row], fallback: sqlite3.Row) -> sqlite3.Row:
    candidates = [
        row
        for row in item_rows
        if _has_text(row["save_base_folder_raw"]) or _has_text(row["save_base_folder_norm"])
    ]
    if not candidates:
        return fallback
    return max(candidates, key=_item_recency_key)


def _select_text_from_most_recent_item(item_rows: list[sqlite3.Row], column_name: str) -> str | None:
    row = _select_value_from_most_recent_item(item_rows, column_name)
    if row is None:
        return None
    return str(row)


def _select_value_from_most_recent_item(
    item_rows: list[sqlite3.Row],
    column_name: str,
    *,
    require_text: bool = True,
):
    if require_text:
        candidates = [row for row in item_rows if _has_text(row[column_name])]
    else:
        candidates = [row for row in item_rows if row[column_name] is not None]
    if not candidates:
        return None
    return max(candidates, key=_item_recency_key)[column_name]


def _select_sanitized_filename_base(item_rows: list[sqlite3.Row], video_id: str) -> str:
    raw_value = _select_text_from_most_recent_item(item_rows, "sanitized_filename_base")
    if not _has_text(raw_value):
        raw_value = f"yt_{video_id}"
    return normalize_output_stem(str(raw_value))


def _select_valid_manual_override_row(item_rows: list[sqlite3.Row]) -> sqlite3.Row | None:
    from core.state_store import SUPPORTED_STATUS_VALUES

    candidates = [
        row
        for row in item_rows
        if row["manual_override"] == 1 and row["manual_status"] in SUPPORTED_STATUS_VALUES
    ]
    if not candidates:
        return None
    return max(candidates, key=_item_recency_key)


def _merge_duplicate_file_part(file_rows: list[sqlite3.Row]) -> dict:
    selected = max(file_rows, key=_file_part_candidate_key)
    created_at = _select_valid_timestamp(file_rows, "created_at", latest=False) or selected["created_at"]
    updated_at = _select_valid_timestamp(file_rows, "updated_at", latest=True) or selected["updated_at"]
    filename_raw = selected["filename_raw"]
    path_raw = selected["path_raw"]
    status = selected["status"]
    filename_norm = _normalize_filename_text(filename_raw) if filename_raw is not None else None
    path_norm = _normalize_path_text(path_raw) if path_raw is not None else None
    is_valid, validation_reason = _file_validity(filename_raw, path_raw, status)
    return {
        "part": selected["part"],
        "status": status,
        "filename_raw": filename_raw,
        "filename_norm": filename_norm,
        "path_raw": path_raw,
        "path_norm": path_norm,
        "is_valid": is_valid,
        "validation_reason": validation_reason,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _file_part_candidate_key(row: sqlite3.Row) -> tuple:
    return (
        _file_status_rank(row["status"]),
        1 if _has_text(row["filename_raw"]) and _has_text(row["path_raw"]) and row["is_valid"] == 1 else 0,
        *_row_updated_key(row),
        int(row["id"]),
    )


def _file_status_rank(status: str | None) -> int:
    from core.state_store import STATUS_DOWNLOADED, STATUS_ERROR, STATUS_NOT_DOWNLOADED

    if status == STATUS_DOWNLOADED:
        return 3
    if status == STATUS_ERROR:
        return 2
    if status == STATUS_NOT_DOWNLOADED:
        return 1
    return 0


def _item_recency_key(row: sqlite3.Row) -> tuple:
    return (*_row_updated_key(row), int(row["id"]))


def _row_updated_key(row: sqlite3.Row) -> tuple[int, float]:
    parsed = _parse_timestamp(row["updated_at"])
    if parsed is None:
        return (0, 0.0)
    return (1, parsed)


def _select_valid_timestamp(rows: list[sqlite3.Row], column_name: str, *, latest: bool) -> str | None:
    candidates = []
    for row in rows:
        parsed = _parse_timestamp(row[column_name])
        if parsed is not None:
            candidates.append((parsed, int(row["id"]), row[column_name]))
    if not candidates:
        return None
    selected = max(candidates) if latest else min(candidates)
    return selected[2]


def _select_earliest_non_empty_timestamp(rows: list[sqlite3.Row], column_name: str) -> str | None:
    candidates = []
    for row in rows:
        value = row[column_name]
        if not _has_text(value):
            continue
        parsed = _parse_timestamp(value)
        candidates.append((1 if parsed is not None else 0, parsed or 0.0, str(value), int(row["id"]), value))
    if not candidates:
        return None
    valid_candidates = [candidate for candidate in candidates if candidate[0] == 1]
    selected_pool = valid_candidates or candidates
    return min(selected_pool, key=lambda item: (item[1], item[2], item[3]))[4]


def _parse_timestamp(value) -> float | None:
    if not _has_text(value):
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _apply_duplicate_video_merge_plan(conn: sqlite3.Connection, plan: DuplicateVideoMergePlan) -> None:
    updates = plan.item_updates
    _release_folder_unique_collisions(conn, plan)
    conn.execute(
        """
        UPDATE download_items
        SET channel_db_id = ?,
            platform = ?,
            channel_id = ?,
            video_id = ?,
            save_base_folder_raw = ?,
            save_base_folder_norm = ?,
            original_title = ?,
            sanitized_filename_base = ?,
            display_order_at_download = ?,
            status = ?,
            manual_status = ?,
            manual_override = ?,
            downloaded_at = ?,
            updated_at = ?,
            created_at = ?
        WHERE id = ?
        """,
        (
            updates["channel_db_id"],
            updates["platform"],
            updates["channel_id"],
            updates["video_id"],
            updates["save_base_folder_raw"],
            updates["save_base_folder_norm"],
            updates["original_title"],
            updates["sanitized_filename_base"],
            updates["display_order_at_download"],
            updates["status"],
            updates["manual_status"],
            updates["manual_override"],
            updates["downloaded_at"],
            updates["updated_at"],
            updates["created_at"],
            plan.survivor_item_id,
        ),
    )
    for part, part_updates in plan.part_updates.items():
        _upsert_survivor_file_part(conn, plan.survivor_item_id, part, part_updates)


def _release_folder_unique_collisions(conn: sqlite3.Connection, plan: DuplicateVideoMergePlan) -> None:
    if not plan.duplicate_item_ids:
        return
    updates = plan.item_updates
    target_norm = updates["save_base_folder_norm"]
    for item_id in plan.duplicate_item_ids:
        row = conn.execute(
            """
            SELECT id
            FROM download_items
            WHERE id = ?
              AND platform = ?
              AND channel_id = ?
              AND video_id = ?
              AND save_base_folder_norm = ?
            """,
            (
                item_id,
                updates["platform"],
                updates["channel_id"],
                updates["video_id"],
                target_norm,
            ),
        ).fetchone()
        if row is None:
            continue
        conn.execute(
            """
            UPDATE download_items
            SET save_base_folder_norm = ?
            WHERE id = ?
            """,
            (f"__phase2b_v4_released_{item_id}", item_id),
        )


def _upsert_survivor_file_part(
    conn: sqlite3.Connection,
    item_id: int,
    part: str,
    updates: dict,
) -> None:
    row = conn.execute(
        "SELECT id FROM download_files WHERE item_id = ? AND part = ?",
        (item_id, part),
    ).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO download_files(
                item_id,
                part,
                status,
                filename_raw,
                filename_norm,
                path_raw,
                path_norm,
                is_valid,
                validation_reason,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                part,
                updates["status"],
                updates["filename_raw"],
                updates["filename_norm"],
                updates["path_raw"],
                updates["path_norm"],
                updates["is_valid"],
                updates["validation_reason"],
                updates["created_at"],
                updates["updated_at"],
            ),
        )
        return

    conn.execute(
        """
        UPDATE download_files
        SET status = ?,
            filename_raw = ?,
            filename_norm = ?,
            path_raw = ?,
            path_norm = ?,
            is_valid = ?,
            validation_reason = ?,
            created_at = ?,
            updated_at = ?
        WHERE item_id = ? AND part = ?
        """,
        (
            updates["status"],
            updates["filename_raw"],
            updates["filename_norm"],
            updates["path_raw"],
            updates["path_norm"],
            updates["is_valid"],
            updates["validation_reason"],
            updates["created_at"],
            updates["updated_at"],
            item_id,
            part,
        ),
    )


def _delete_duplicate_item_rows(conn: sqlite3.Connection, item_ids: tuple[int, ...]) -> None:
    if not item_ids:
        return
    conn.execute(
        f"DELETE FROM download_items WHERE id IN ({','.join('?' for _ in item_ids)})",
        item_ids,
    )


def _verify_consolidated_video_identity(
    conn: sqlite3.Connection,
    platform: str,
    channel_id: str,
    video_id: str,
    survivor_item_id: int,
) -> None:
    rows = conn.execute(
        """
        SELECT id
        FROM download_items
        WHERE platform = ? AND channel_id = ? AND video_id = ?
        ORDER BY id
        """,
        (platform, channel_id, video_id),
    ).fetchall()
    if len(rows) != 1 or int(rows[0]["id"]) != survivor_item_id:
        raise DatabaseMigrationError(
            f"Video identity consolidation failed for {platform}/{channel_id}/{video_id}"
        )


MIGRATIONS = (
    Migration(2, "phase_2a_bootstrap_metadata", _migrate_to_v2),
    Migration(3, "create_app_schema_migrations_ledger", _migrate_to_v3),
    Migration(4, "canonicalize_video_scoped_download_items", _migrate_to_v4),
)
MIGRATIONS_BY_VERSION = {migration.version: migration for migration in MIGRATIONS}
APPLICATION_MIGRATION_NAMES = {LEGACY_SCHEMA_VERSION: "initial_schema"}
APPLICATION_MIGRATION_NAMES.update({migration.version: migration.name for migration in MIGRATIONS})
_EXPECTED_MIGRATION_VERSIONS = tuple(range(LEGACY_SCHEMA_VERSION + 1, CURRENT_SCHEMA_VERSION + 1))
if (
    len(MIGRATIONS_BY_VERSION) != len(MIGRATIONS)
    or tuple(sorted(MIGRATIONS_BY_VERSION)) != _EXPECTED_MIGRATION_VERSIONS
    or tuple(sorted(APPLICATION_MIGRATION_NAMES)) != tuple(range(LEGACY_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION + 1))
):
    raise RuntimeError("SQLite migrations must be unique and sequential")


def _resolve_database_path(path: Path | None = None) -> Path:
    return Path(path or db_file()).expanduser().resolve(strict=False)


def _connect_bootstrap_connection(path: Path, *, allow_create: bool) -> sqlite3.Connection:
    if allow_create and not path.exists():
        conn = sqlite3.connect(
            path,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
            factory=ManagedSQLiteConnection,
        )
    else:
        conn = _connect_existing_sqlite_file(path)
    conn.row_factory = sqlite3.Row
    _apply_connection_pragmas(conn)
    return conn


def _connect_existing_sqlite_file(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(
        f"{path.resolve(strict=False).as_uri()}?mode=rw",
        uri=True,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
        factory=ManagedSQLiteConnection,
    )


def _connect_configured(path: Path) -> sqlite3.Connection:
    conn = _connect_existing_sqlite_file(path)
    try:
        conn.row_factory = sqlite3.Row
        _apply_connection_pragmas(conn)
        return conn
    except Exception:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        raise


def _apply_connection_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")


def _configure_bootstrap_journal(conn: sqlite3.Connection) -> None:
    row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    mode = str(row[0] if row else "").casefold()
    if mode != "wal":
        raise DatabaseSchemaError(f"SQLite did not enable WAL journal mode; got {mode!r}")


def _create_current_schema(conn: sqlite3.Connection) -> None:
    for statement in CURRENT_SCHEMA_STATEMENTS:
        conn.execute(statement)


def _read_schema_version(conn: sqlite3.Connection) -> int | None:
    if "app_meta" not in _sqlite_table_names(conn):
        return None
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (SCHEMA_VERSION_KEY,)).fetchone()
    if row is None:
        return None
    value = str(row["value"] if isinstance(row, sqlite3.Row) else row[0])
    if not value.isdecimal():
        raise DatabaseSchemaError(f"Schema version is not a strict integer: {value!r}")
    return int(value)


def _write_schema_version(conn: sqlite3.Connection, version: int, now: str) -> None:
    conn.execute(
        """
        INSERT INTO app_meta(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (SCHEMA_VERSION_KEY, str(version), now),
    )


def _record_migration(
    conn: sqlite3.Connection,
    version: int,
    name: str,
    applied_at: str,
) -> None:
    expected_name = APPLICATION_MIGRATION_NAMES.get(version)
    if expected_name != name:
        raise DatabaseMigrationError(f"Migration name mismatch for version {version}: {name!r}")
    conn.execute(
        f"""
        INSERT INTO {APP_MIGRATION_LEDGER_TABLE}(version, name, applied_at)
        VALUES (?, ?, ?)
        """,
        (version, name, applied_at),
    )


def _record_completed_application_migrations(
    conn: sqlite3.Connection,
    through_version: int,
    applied_at: str,
) -> None:
    _create_application_ledger_table(conn)
    for version in range(LEGACY_SCHEMA_VERSION, through_version + 1):
        _record_migration(conn, version, APPLICATION_MIGRATION_NAMES[version], applied_at)


def _ensure_application_ledger_for_existing_version(conn: sqlite3.Connection, through_version: int) -> None:
    _create_application_ledger_table(conn)
    existing_rows = _read_application_migration_rows(conn)
    existing_by_version = {version: name for version, name in existing_rows}
    duplicate_versions = _duplicate_versions(existing_rows)
    if duplicate_versions:
        raise DatabaseValidationError(f"Application migration ledger has duplicate versions: {duplicate_versions}")

    for version, name in existing_rows:
        expected_name = APPLICATION_MIGRATION_NAMES.get(version)
        if expected_name is None:
            raise DatabaseValidationError(f"Application migration ledger has unknown version: {version}")
        if version > through_version:
            raise DatabaseValidationError(
                f"Application migration ledger version {version} is beyond metadata version {through_version}"
            )
        if name != expected_name:
            raise DatabaseValidationError(
                f"Application migration ledger version {version} has name {name!r}; expected {expected_name!r}"
            )

    now = _now_iso()
    for version in range(LEGACY_SCHEMA_VERSION, through_version + 1):
        if version not in existing_by_version:
            _record_migration(conn, version, APPLICATION_MIGRATION_NAMES[version], now)


def _validate_application_migration_ledger(conn: sqlite3.Connection, schema_version: int) -> None:
    _validate_required_columns_for_tables(conn, {APP_MIGRATION_LEDGER_TABLE})
    rows = _read_application_migration_rows(conn)
    duplicate_versions = _duplicate_versions(rows)
    if duplicate_versions:
        raise DatabaseValidationError(f"Application migration ledger has duplicate versions: {duplicate_versions}")

    versions = set()
    for version, name in rows:
        versions.add(version)
        expected_name = APPLICATION_MIGRATION_NAMES.get(version)
        if expected_name is None:
            if version > CURRENT_SCHEMA_VERSION:
                raise DatabaseTooNewError(
                    f"Application migration ledger contains unsupported version {version}; "
                    f"this app supports up to {CURRENT_SCHEMA_VERSION}"
                )
            raise DatabaseValidationError(f"Application migration ledger has unknown version: {version}")
        if name != expected_name:
            raise DatabaseValidationError(
                f"Application migration ledger version {version} has name {name!r}; expected {expected_name!r}"
            )
        if version > schema_version:
            raise DatabaseValidationError(
                f"Application migration ledger version {version} is beyond metadata version {schema_version}"
            )

    required_versions = set(range(LEGACY_SCHEMA_VERSION, schema_version + 1))
    missing_versions = sorted(required_versions - versions)
    if missing_versions:
        raise DatabaseValidationError(f"Application migration ledger is missing versions: {missing_versions}")


def _create_application_ledger_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {APP_MIGRATION_LEDGER_TABLE} (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _read_application_migration_rows(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    if APP_MIGRATION_LEDGER_TABLE not in _sqlite_table_names(conn):
        return []
    try:
        rows = conn.execute(
            f"SELECT version, name FROM {APP_MIGRATION_LEDGER_TABLE} ORDER BY version"
        ).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseValidationError(f"Application migration ledger is not readable: {exc}") from exc
    parsed = []
    for row in rows:
        version = row["version"] if isinstance(row, sqlite3.Row) else row[0]
        name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        try:
            parsed.append((int(version), str(name)))
        except (TypeError, ValueError) as exc:
            raise DatabaseValidationError(f"Application migration ledger has invalid version: {version!r}") from exc
    return parsed


def _duplicate_versions(rows: list[tuple[int, str]]) -> list[int]:
    seen = set()
    duplicates = set()
    for version, _name in rows:
        if version in seen:
            duplicates.add(version)
        seen.add(version)
    return sorted(duplicates)


def _validate_schema_for_version(conn: sqlite3.Connection, schema_version: int) -> None:
    if schema_version < LEGACY_SCHEMA_VERSION or schema_version > CURRENT_SCHEMA_VERSION:
        raise DatabaseSchemaError(f"Unsupported schema version: {schema_version}")

    _validate_required_tables(conn, CORE_REQUIRED_TABLES)
    _validate_required_columns_for_tables(conn, set(CORE_REQUIRED_TABLES))

    if schema_version >= 2:
        _validate_required_indexes(conn, schema_version)
    if schema_version >= 3:
        _validate_required_tables(conn, (APP_MIGRATION_LEDGER_TABLE,))
        _validate_application_migration_ledger(conn, schema_version)
    if schema_version >= 4:
        _validate_required_tables(conn, (DOWNLOAD_STATE_ARCHIVE_TABLE,))
        _validate_required_columns_for_tables(conn, {DOWNLOAD_STATE_ARCHIVE_TABLE})
        _validate_video_identity_consistency(conn)


def _validate_required_tables(conn: sqlite3.Connection, table_names: tuple[str, ...]) -> None:
    existing = _sqlite_table_names(conn)
    missing = [table_name for table_name in table_names if table_name not in existing]
    if missing:
        raise DatabaseValidationError(f"Missing required tables: {', '.join(missing)}")


def _validate_required_indexes(conn: sqlite3.Connection, schema_version: int) -> None:
    for index_name, (table_name, expected_columns) in _required_indexes_for_version(schema_version).items():
        expected_unique = index_name in REQUIRED_UNIQUE_INDEXES
        metadata = _get_index_metadata(conn, index_name)
        if (
            metadata is None
            or metadata.table_name != table_name
            or metadata.columns != expected_columns
            or metadata.unique is not expected_unique
            or metadata.partial
        ):
            raise DatabaseValidationError(
                f"Index {index_name} on {table_name} is missing or does not match required schema"
            )


def _required_indexes_for_version(schema_version: int) -> dict[str, tuple[str, tuple[str, ...]]]:
    if schema_version >= 4:
        return REQUIRED_INDEXES
    return {
        index_name: index_spec
        for index_name, index_spec in REQUIRED_INDEXES.items()
        if index_name not in {VIDEO_IDENTITY_INDEX, ARCHIVE_VIDEO_INDEX}
    }


def _validate_video_identity_consistency(conn: sqlite3.Connection) -> None:
    duplicates = conn.execute(
        """
        SELECT DISTINCT a.platform, a.channel_id, a.video_id
        FROM download_items a
        JOIN download_items b
          ON b.platform = a.platform
         AND b.channel_id = a.channel_id
         AND b.video_id = a.video_id
         AND b.id <> a.id
        ORDER BY a.platform, a.channel_id, a.video_id
        LIMIT 10
        """
    ).fetchall()
    if duplicates:
        sample = ", ".join(
            f"{row['platform']}/{row['channel_id']}/{row['video_id']}"
            for row in duplicates
        )
        raise DatabaseValidationError(f"Duplicate video identity rows found: {sample}")

    orphan = conn.execute(
        """
        SELECT df.id
        FROM download_files df
        LEFT JOIN download_items di ON di.id = df.item_id
        WHERE di.id IS NULL
        LIMIT 1
        """
    ).fetchone()
    if orphan is not None:
        raise DatabaseValidationError("download_files contains orphan rows")

    invalid_parts = conn.execute(
        f"""
        SELECT DISTINCT part
        FROM download_files
        WHERE part NOT IN ({','.join('?' for _ in FILE_PARTS)})
        ORDER BY part
        LIMIT 10
        """,
        FILE_PARTS,
    ).fetchall()
    if invalid_parts:
        sample = ", ".join(str(row["part"]) for row in invalid_parts)
        raise DatabaseValidationError(f"download_files contains invalid parts: {sample}")

    duplicate_parts = conn.execute(
        """
        SELECT DISTINCT a.item_id, a.part
        FROM download_files a
        JOIN download_files b
          ON b.item_id = a.item_id
         AND b.part = a.part
         AND b.id <> a.id
        ORDER BY a.item_id, a.part
        LIMIT 10
        """
    ).fetchall()
    if duplicate_parts:
        sample = ", ".join(
            f"{row['item_id']}/{row['part']}" for row in duplicate_parts
        )
        raise DatabaseValidationError(f"download_files contains duplicate item/part rows: {sample}")


def _detect_legacy_schema_version(
    conn: sqlite3.Connection,
    existing_user_tables: set[str],
) -> int:
    required_legacy_tables = {"app_meta", "channels", "download_items", "download_files"}
    if not required_legacy_tables.issubset(existing_user_tables):
        raise DatabaseSchemaError("Unversioned database is not a supported legacy schema")
    _validate_required_columns_for_tables(conn, required_legacy_tables)
    return LEGACY_SCHEMA_VERSION


def _validate_required_columns_for_tables(conn: sqlite3.Connection, table_names: set[str]) -> None:
    for table_name in table_names:
        existing_columns = _table_columns(conn, table_name)
        required_columns = REQUIRED_COLUMNS[table_name]
        missing_columns = [column for column in required_columns if column not in existing_columns]
        if missing_columns:
            raise DatabaseSchemaError(
                f"Table {table_name} is missing required columns: {', '.join(missing_columns)}"
            )


def _create_migration_backup(
    conn: sqlite3.Connection,
    db_path: Path,
    from_version: int,
    to_version: int,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime(MIGRATION_BACKUP_TIMESTAMP_FORMAT)
    backup_path = db_path.with_name(f"{db_path.name}.pre-migration-v{from_version}-to-v{to_version}-{timestamp}.bak")
    try:
        with closing(sqlite3.connect(backup_path)) as backup_conn:
            conn.backup(backup_conn)
    except Exception as exc:
        raise DatabaseBackupError(f"Failed to create migration backup at {backup_path}: {exc}") from exc
    return backup_path


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({quote_identifier(table_name)})").fetchall()
    return {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in rows}


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
    for pragma_row in conn.execute(f"PRAGMA index_list({quote_identifier(table_name)})").fetchall():
        pragma_name = _row_value(pragma_row, "name", 1)
        if pragma_name == index_name:
            index_list_row = pragma_row
            break
    if index_list_row is None:
        return None

    xinfo_rows = conn.execute(f"PRAGMA index_xinfo({quote_identifier(index_name)})").fetchall()
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


def _user_table_names(conn: sqlite3.Connection) -> set[str]:
    return {table for table in _sqlite_table_names(conn) if not table.startswith("sqlite_")}


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _open_existing_database_connection(
    db_path: Path,
    expected_identity: DatabaseFileIdentity,
    *,
    allow_retry: bool,
) -> sqlite3.Connection:
    try:
        conn = _connect_configured(db_path)
    except sqlite3.Error as exc:
        return _handle_connection_open_failure(
            db_path,
            expected_identity,
            exc,
            allow_retry=allow_retry,
        )

    try:
        try:
            current_identity = _capture_database_identity(db_path)
        except DatabaseFileChangedError as exc:
            _invalidate_cached_database_identity(db_path, expected_identity)
            raise DatabaseFileChangedError(
                f"Database file disappeared before post-open validation: {db_path}"
            ) from exc
        except DatabasePathError as exc:
            _invalidate_cached_database_identity(db_path, expected_identity)
            raise DatabaseFileChangedError(
                f"Database path changed before post-open validation: {db_path}"
            ) from exc

        if current_identity == expected_identity:
            return conn

        conn.close()
        conn = None
        _invalidate_cached_database_identity(db_path, expected_identity)
        if not allow_retry:
            raise DatabaseFileChangedError(f"Database file kept changing during open: {db_path}")
        replacement_identity = _revalidate_replacement_database(db_path)
        return _open_existing_database_connection(
            db_path,
            replacement_identity,
            allow_retry=False,
        )
    except Exception:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        raise


def _handle_connection_open_failure(
    db_path: Path,
    expected_identity: DatabaseFileIdentity,
    exc: sqlite3.Error,
    *,
    allow_retry: bool,
) -> sqlite3.Connection:
    try:
        current_identity = _capture_database_identity(db_path)
    except DatabaseFileChangedError as identity_exc:
        _invalidate_cached_database_identity(db_path, expected_identity)
        raise DatabaseFileChangedError(
            f"Database file disappeared before the connection could be opened: {db_path}"
        ) from identity_exc
    except DatabasePathError as identity_exc:
        _invalidate_cached_database_identity(db_path, expected_identity)
        raise DatabaseFileChangedError(
            f"Database path is no longer a regular initialized file: {db_path}"
        ) from identity_exc

    if current_identity != expected_identity:
        _invalidate_cached_database_identity(db_path, expected_identity)
        if allow_retry:
            replacement_identity = _revalidate_replacement_database(db_path)
            return _open_existing_database_connection(
                db_path,
                replacement_identity,
                allow_retry=False,
            )
        raise DatabaseFileChangedError(f"Database file kept changing during open: {db_path}") from exc

    if _is_sqlite_lock_error(exc):
        raise DatabaseLockError(f"Database is locked after busy timeout: {db_path}") from exc

    raise DatabasePathError(f"Could not open SQLite database: {db_path}: {exc}") from exc


def _revalidate_replacement_database(db_path: Path) -> DatabaseFileIdentity:
    try:
        return _ensure_initialized_database(db_path, allow_new=False)
    except (
        DatabaseTooNewError,
        DatabaseFileChangedError,
        DatabaseMigrationError,
        DatabaseSchemaError,
    ):
        raise
    except sqlite3.DatabaseError as exc:
        raise DatabaseSchemaError(f"Replacement database is not a valid SQLite database: {db_path}") from exc


def _invalidate_cached_database_identity(
    db_path: Path,
    expected_identity: DatabaseFileIdentity | None = None,
) -> None:
    normalized_path = _resolve_database_path(db_path)
    with _INITIALIZATION_LOCK:
        if expected_identity is None:
            _INITIALIZED_DATABASES.pop(normalized_path, None)
            return
        if _INITIALIZED_DATABASES.get(normalized_path) == expected_identity:
            _INITIALIZED_DATABASES.pop(normalized_path, None)


def _is_sqlite_lock_error(exc: sqlite3.Error) -> bool:
    message = str(exc).casefold()
    return "locked" in message or "busy" in message


def _capture_database_identity_for_cache_hit(
    db_path: Path,
    cached_identity: DatabaseFileIdentity,
) -> DatabaseFileIdentity:
    try:
        return _capture_database_identity(db_path)
    except DatabaseFileChangedError:
        _invalidate_cached_database_identity(db_path, cached_identity)
        raise DatabaseFileChangedError(f"Initialized database file disappeared: {db_path}")
    except DatabasePathError:
        _invalidate_cached_database_identity(db_path, cached_identity)
        raise


def _capture_database_identity(db_path: Path) -> DatabaseFileIdentity:
    try:
        stat_result = db_path.stat()
    except FileNotFoundError as exc:
        raise DatabaseFileChangedError(f"Database file disappeared: {db_path}") from exc
    except OSError as exc:
        raise DatabasePathError(f"Could not stat database file: {db_path}") from exc
    if not db_path.is_file():
        raise DatabasePathError(f"Database path is not a regular file: {db_path}")

    device = int(getattr(stat_result, "st_dev", 0))
    inode = int(getattr(stat_result, "st_ino", 0) or 0)
    creation_marker_ns = None
    if inode == 0:
        creation_marker_ns = getattr(stat_result, "st_birthtime_ns", None)
        if creation_marker_ns is None and os.name == "nt":
            creation_marker_ns = getattr(stat_result, "st_ctime_ns", None)
        if creation_marker_ns is None:
            raise DatabaseFileChangedError(
                f"Database file identity is not stable on this filesystem: {db_path}"
            )
    return DatabaseFileIdentity(device=device, inode=inode, creation_marker_ns=creation_marker_ns)


def quick_sqlite_state_check(path: Path | None = None) -> dict:
    db_path = _resolve_database_path(path)
    result = {
        "ok": False,
        "db_path": str(db_path),
        "exists": db_path.exists(),
        "can_open": False,
        "required_tables_present": False,
        "missing_tables": list(REQUIRED_TABLES),
        "download_items_count": 0,
        "video_identity_duplicates": 0,
        "archive_rows": 0,
        "simple_read_succeeded": False,
        "reasons": [],
    }
    if not db_path.exists():
        result["reasons"].append("db_missing")
        return result

    try:
        with closing(_connect_read_only(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            result["can_open"] = True
            table_names = _sqlite_table_names(conn)
            missing_tables = [table for table in REQUIRED_TABLES if table not in table_names]
            result["missing_tables"] = missing_tables
            result["required_tables_present"] = not missing_tables
            if missing_tables:
                result["reasons"].append("missing_required_tables")
                return result

            item_count = conn.execute("SELECT COUNT(*) AS count FROM download_items").fetchone()["count"]
            result["download_items_count"] = item_count
            result["video_identity_duplicates"] = _video_identity_duplicate_count(conn)
            if result["video_identity_duplicates"]:
                result["reasons"].append("duplicate_video_identities")
                return result
            result["archive_rows"] = conn.execute(
                f"SELECT COUNT(*) AS count FROM {DOWNLOAD_STATE_ARCHIVE_TABLE}"
            ).fetchone()["count"]

            conn.execute("SELECT id FROM download_items LIMIT 1").fetchone()
            result["simple_read_succeeded"] = True
    except sqlite3.Error as exc:
        result["reasons"].append(f"sqlite_error:{type(exc).__name__}: {exc}")
        return result

    result["ok"] = (
        result["exists"]
        and result["can_open"]
        and result["required_tables_present"]
        and result["simple_read_succeeded"]
    )
    return result


def is_sqlite_state_usable(path: Path | None = None) -> bool:
    return bool(quick_sqlite_state_check(path).get("ok"))


def sqlite_has_any_items(path: Path | None = None) -> bool:
    db_path = _resolve_database_path(path)
    if not db_path.exists():
        return False

    try:
        with closing(_connect_read_only(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            table_names = _sqlite_table_names(conn)
            if any(table_name not in table_names for table_name in REQUIRED_TABLES):
                return False
            row = conn.execute("SELECT 1 FROM download_items LIMIT 1").fetchone()
            return row is not None
    except sqlite3.Error:
        return False


def get_sqlite_state_summary(path: Path | None = None) -> dict:
    db_path = _resolve_database_path(path)
    summary = {
        "db_path": str(db_path),
        "channels": 0,
        "download_items": 0,
        "download_files": 0,
        "video_identity_duplicates": 0,
        "archive_rows": 0,
        "archive_item_rows": 0,
        "archive_file_rows": 0,
        "status_counts": {},
        "manual_override_counts": {},
        "manual_status_counts": {},
    }
    if not db_path.exists():
        summary["error"] = "db_missing"
        return summary

    try:
        with closing(_connect_read_only(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            table_names = _sqlite_table_names(conn)
            for table_name in ("channels", "download_items", "download_files"):
                if table_name in table_names:
                    summary[table_name] = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()[
                        "count"
                    ]

            if "download_items" in table_names:
                summary["video_identity_duplicates"] = _video_identity_duplicate_count(conn)
                summary["status_counts"] = _counts_by_nullable_text(conn, "status")
                summary["manual_override_counts"] = _manual_override_counts(conn)
                summary["manual_status_counts"] = _counts_by_nullable_text(conn, "manual_status")
            if DOWNLOAD_STATE_ARCHIVE_TABLE in table_names:
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
                summary["archive_item_rows"] = archive_counts.get("download_item", 0)
                summary["archive_file_rows"] = archive_counts.get("download_file", 0)
                summary["archive_rows"] = summary["archive_item_rows"] + summary["archive_file_rows"]
    except sqlite3.Error as exc:
        summary["error"] = f"sqlite_error:{type(exc).__name__}: {exc}"
    return summary


def load_state(path: Path | None = None) -> dict:
    db_path = _resolve_database_path(path)
    if not db_path.exists():
        return _empty_state()

    state = _empty_state()
    with closing(open_database_connection(db_path)) as conn:
        item_rows = conn.execute(
            """
            SELECT
                di.id,
                di.platform,
                di.channel_id,
                di.video_id,
                di.save_base_folder_raw,
                di.save_base_folder_norm,
                di.original_title,
                di.sanitized_filename_base,
                di.display_order_at_download,
                di.status,
                di.manual_status,
                di.manual_override,
                di.downloaded_at,
                di.updated_at,
                c.channel_name,
                c.save_base_folder_raw AS channel_save_base_folder_raw
            FROM download_items di
            LEFT JOIN channels c ON c.id = di.channel_db_id
            WHERE di.platform = ?
            ORDER BY di.id
            """,
            (PLATFORM_YOUTUBE,),
        ).fetchall()
        item_id_to_entry: dict[int, dict] = {}

        for row in item_rows:
            channel = _ensure_state_channel(state, row)
            entry = _row_to_item_entry(row)
            channel["videos"][row["video_id"]] = entry
            item_id_to_entry[row["id"]] = entry

        if item_id_to_entry:
            placeholders = ",".join("?" for _ in item_id_to_entry)
            file_rows = conn.execute(
                f"""
                SELECT item_id, part, status, filename_raw, path_raw
                FROM download_files
                WHERE item_id IN ({placeholders})
                ORDER BY item_id, part
                """,
                tuple(item_id_to_entry),
            ).fetchall()
            for row in file_rows:
                entry = item_id_to_entry.get(row["item_id"])
                if entry is not None:
                    _apply_file_row(entry, row)

    return state


def get_channel_video_entries(
    channel_id: str,
    path: Path | None = None,
    save_base_folder: str | None = None,
) -> dict:
    if not channel_id:
        return {}

    db_path = _resolve_database_path(path)
    if not db_path.exists():
        return {}

    where_sql, params = _channel_items_where(channel_id, save_base_folder)
    item_id_to_entry: dict[int, dict] = {}
    entries_by_video_id: dict[str, dict] = {}

    with closing(open_database_connection(db_path)) as conn:
        item_rows = conn.execute(
            f"""
            SELECT
                di.id,
                di.platform,
                di.channel_id,
                di.video_id,
                di.save_base_folder_raw,
                di.save_base_folder_norm,
                di.original_title,
                di.sanitized_filename_base,
                di.display_order_at_download,
                di.status,
                di.manual_status,
                di.manual_override,
                di.downloaded_at,
                di.updated_at,
                c.channel_name,
                c.save_base_folder_raw AS channel_save_base_folder_raw
            FROM download_items di
            LEFT JOIN channels c ON c.id = di.channel_db_id
            WHERE {where_sql}
            ORDER BY di.id
            """,
            params,
        ).fetchall()

        for row in item_rows:
            entry = _row_to_item_entry(row)
            item_id_to_entry[row["id"]] = entry
            entries_by_video_id[row["video_id"]] = entry

        if item_id_to_entry:
            file_rows = conn.execute(
                f"""
                SELECT item_id, part, status, filename_raw, path_raw
                FROM download_files
                WHERE item_id IN ({",".join("?" for _ in item_id_to_entry)})
                ORDER BY item_id, part
                """,
                tuple(item_id_to_entry),
            ).fetchall()
            for row in file_rows:
                entry = item_id_to_entry.get(row["item_id"])
                if entry is not None:
                    _apply_file_row(entry, row)

    return entries_by_video_id


def get_video_entry(
    channel_id: str,
    video_id: str,
    path: Path | None = None,
    save_base_folder: str | None = None,
) -> dict | None:
    if not channel_id or not video_id:
        return None

    db_path = _resolve_database_path(path)
    if not db_path.exists():
        return None

    with closing(open_database_connection(db_path)) as conn:
        entry = _entry_for_video(conn, channel_id, video_id)
        return entry if entry else None


def update_manual_status(
    channel_id: str,
    video_id: str,
    manual_status: str,
    path: Path | None = None,
    save_base_folder: str | None = None,
) -> None:
    from core.state_store import SUPPORTED_STATUS_VALUES

    if manual_status not in SUPPORTED_STATUS_VALUES:
        raise ValueError("Unsupported status")
    if not channel_id or not video_id:
        return

    now = _now_iso()
    with closing(open_database_connection(path)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            item_id = _ensure_item(conn, channel_id, video_id, save_base_folder, now=now)
            _clear_manual_status_for_video(conn, channel_id, video_id, now)
            conn.execute(
                """
                UPDATE download_items
                SET manual_status = ?,
                    manual_override = 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (manual_status, now, item_id),
            )
            conn.commit()
        except Exception:
            _abort_transaction(conn)
            raise


def clear_manual_status(
    channel_id: str,
    video_id: str,
    path: Path | None = None,
    save_base_folder: str | None = None,
) -> None:
    if not channel_id or not video_id:
        return

    db_path = _resolve_database_path(path)
    if not db_path.exists():
        return

    now = _now_iso()
    with closing(open_database_connection(db_path)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            item_id = _resolve_canonical_item_id(conn, channel_id, video_id)
            if item_id is None:
                conn.commit()
                return
            old_entry = _entry_for_item_id(conn, item_id)
            previous_manual_status = old_entry.get("manual_status")
            conn.execute(
                """
                UPDATE download_items
                SET manual_status = NULL,
                    manual_override = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, item_id),
            )
            entry = _entry_for_item_id(conn, item_id)
            new_status = _status_after_manual_clear(entry, previous_manual_status)
            conn.execute(
                "UPDATE download_items SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, item_id),
            )
            conn.commit()
        except Exception:
            _abort_transaction(conn)
            raise


def update_video_part_state(
    channel_id: str,
    video_id: str,
    part: str,
    filename: str | None = None,
    file_path: str | Path | None = None,
    status: str | None = None,
    path: Path | None = None,
    save_base_folder: str | None = None,
    **kwargs,
) -> None:
    from core.download_modes import MODE_VIDEO_THUMB
    from core.state_store import STATUS_DOWNLOADED, STATUS_ERROR, STATUS_NOT_DOWNLOADED

    if part not in FILE_PARTS:
        raise ValueError("Unsupported file part")
    if status is not None and status not in (STATUS_NOT_DOWNLOADED, STATUS_DOWNLOADED, STATUS_ERROR):
        raise ValueError("Unsupported part status")
    if not channel_id or not video_id:
        return

    download_mode = kwargs.get("download_mode") or MODE_VIDEO_THUMB
    item_updates = _common_item_updates_from_kwargs(kwargs)
    now = _now_iso()
    with closing(open_database_connection(path)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            item_id = _ensure_item(
                conn,
                channel_id,
                video_id,
                save_base_folder,
                now=now,
                updates=item_updates,
            )
            _apply_item_updates(conn, item_id, item_updates, now)
            _upsert_file_part(
                conn,
                item_id,
                part,
                filename_raw=filename,
                path_raw=str(file_path) if file_path is not None else None,
                status=status,
                now=now,
            )
            if status == STATUS_DOWNLOADED:
                _clear_manual_status_for_video(conn, channel_id, video_id, now, downloaded_at=now)
            entry = _entry_for_item_id(conn, item_id)
            new_status = _get_effective_status(entry, download_mode)
            conn.execute(
                "UPDATE download_items SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, item_id),
            )
            conn.commit()
        except Exception:
            _abort_transaction(conn)
            raise


def reconcile_downloaded_item_state(
    channel_id: str,
    video_id: str,
    download_mode: str | None = None,
    path: Path | None = None,
    save_base_folder: str | None = None,
) -> tuple[str, str]:
    from core.download_modes import MODE_VIDEO_THUMB, required_parts
    from core.state_store import STATUS_DOWNLOADED, STATUS_NOT_DOWNLOADED, part_status_from_entry

    if not channel_id or not video_id:
        return STATUS_NOT_DOWNLOADED, STATUS_NOT_DOWNLOADED

    mode = download_mode or MODE_VIDEO_THUMB
    now = _now_iso()
    with closing(open_database_connection(path)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            item_id = _ensure_item(conn, channel_id, video_id, save_base_folder, now=now)
            old_entry = _entry_for_item_id(conn, item_id)
            old_status = _get_effective_status(old_entry, mode)
            has_downloaded_required_part = any(
                part_status_from_entry(old_entry, part) == STATUS_DOWNLOADED
                for part in required_parts(mode)
            )
            if has_downloaded_required_part:
                _clear_manual_status_for_video(conn, channel_id, video_id, now)
            new_entry = _entry_for_item_id(conn, item_id)
            new_status = _get_effective_status(new_entry, mode)
            if new_status == STATUS_DOWNLOADED:
                conn.execute(
                    "UPDATE download_items SET status = ?, downloaded_at = ?, updated_at = ? WHERE id = ?",
                    (new_status, now, now, item_id),
                )
            else:
                conn.execute(
                    "UPDATE download_items SET status = ?, updated_at = ? WHERE id = ?",
                    (new_status, now, item_id),
                )
            conn.commit()
            return old_status, new_status
        except Exception:
            _abort_transaction(conn)
            raise


def update_video_state(
    channel_id: str,
    video_id: str,
    updates: dict,
    path: Path | None = None,
    save_base_folder: str | None = None,
) -> None:
    from core.state_store import STATUS_DOWNLOADED, STATUS_ERROR

    if not channel_id or not video_id:
        return
    if not isinstance(updates, dict):
        raise ValueError("updates must be a dict")

    now = _now_iso()
    with closing(open_database_connection(path)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            item_id = _ensure_item(
                conn,
                channel_id,
                video_id,
                save_base_folder,
                now=now,
                updates=updates,
            )
            _apply_item_updates(conn, item_id, updates, now)
            _apply_file_updates_from_entry(conn, item_id, updates, now)

            aggregate_status = updates.get("status") if "status" in updates else None
            if aggregate_status == STATUS_DOWNLOADED:
                _set_existing_or_updated_part_statuses(
                    conn,
                    item_id,
                    (("video", updates), ("thumb", updates)),
                    STATUS_DOWNLOADED,
                    now,
                )
                conn.execute(
                    """
                    UPDATE download_items
                    SET manual_status = NULL,
                        manual_override = NULL,
                        downloaded_at = COALESCE(?, downloaded_at)
                    WHERE id = ?
                    """,
                    (updates.get("downloaded_at") or now, item_id),
                )
                _clear_manual_status_for_video(
                    conn,
                    channel_id,
                    video_id,
                    now,
                    downloaded_at=updates.get("downloaded_at") or now,
                )
            elif aggregate_status == STATUS_ERROR:
                _set_existing_or_updated_part_statuses(
                    conn,
                    item_id,
                    (("video", updates), ("thumb", updates)),
                    STATUS_ERROR,
                    now,
                )

            if "status" in updates:
                conn.execute(
                    "UPDATE download_items SET status = ?, updated_at = ? WHERE id = ?",
                    (updates.get("status"), now, item_id),
                )
            else:
                conn.execute("UPDATE download_items SET updated_at = ? WHERE id = ?", (now, item_id))
            conn.commit()
        except Exception:
            _abort_transaction(conn)
            raise


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    _apply_connection_pragmas(conn)


def _abort_transaction(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        pass


def _connect_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
        factory=ManagedSQLiteConnection,
    )
    conn.row_factory = sqlite3.Row
    _apply_connection_pragmas(conn)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _sqlite_table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] if isinstance(row, sqlite3.Row) else row[0] for row in rows}


def _counts_by_nullable_text(conn: sqlite3.Connection, column_name: str) -> dict[str, int]:
    if column_name not in {"status", "manual_status"}:
        raise ValueError("Unsupported count column")
    rows = conn.execute(
        f"""
        SELECT COALESCE({column_name}, ?) AS key, COUNT(*) AS count
        FROM download_items
        GROUP BY key
        ORDER BY key
        """,
        (NULL_COUNTER_KEY,),
    ).fetchall()
    return {row["key"]: row["count"] for row in rows}


def _manual_override_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
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
    ).fetchall()
    return {row["key"]: row["count"] for row in rows}


def _video_identity_duplicate_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM (
            SELECT 1
            FROM download_items
            GROUP BY platform, channel_id, video_id
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()
    return int(row["count"] if isinstance(row, sqlite3.Row) else row[0])


def _channel_items_where(channel_id: str, save_base_folder: str | None = None) -> tuple[str, tuple]:
    clauses = ["di.platform = ?", "di.channel_id = ?"]
    params: list[str] = [PLATFORM_YOUTUBE, channel_id]
    return " AND ".join(clauses), tuple(params)


def _resolve_item_id_for_read(
    conn: sqlite3.Connection,
    channel_id: str,
    video_id: str,
    save_base_folder: str | None = None,
) -> int | None:
    return _resolve_canonical_item_id(conn, channel_id, video_id)


def _resolve_item_id(
    conn: sqlite3.Connection,
    channel_id: str,
    video_id: str,
    save_base_folder: str | None = None,
) -> int | None:
    return _resolve_canonical_item_id(conn, channel_id, video_id)


def _resolve_canonical_item_id(conn: sqlite3.Connection, channel_id: str, video_id: str) -> int | None:
    rows = conn.execute(
        """
        SELECT id
        FROM download_items
        WHERE platform = ? AND channel_id = ? AND video_id = ?
        ORDER BY id
        """,
        (PLATFORM_YOUTUBE, channel_id, video_id),
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise DatabaseValidationError(
            f"Duplicate video identity rows found for {PLATFORM_YOUTUBE}/{channel_id}/{video_id}"
        )
    return int(rows[0]["id"] if isinstance(rows[0], sqlite3.Row) else rows[0][0])


def _clear_manual_status_for_video(
    conn: sqlite3.Connection,
    channel_id: str,
    video_id: str,
    now: str,
    downloaded_at: str | None = None,
) -> None:
    item_id = _resolve_canonical_item_id(conn, channel_id, video_id)
    if item_id is None:
        return
    assignments = ["manual_status = NULL", "manual_override = NULL", "updated_at = ?"]
    params: list = [now]
    if downloaded_at is not None:
        assignments.append("downloaded_at = COALESCE(?, downloaded_at)")
        params.append(downloaded_at)
    params.append(item_id)
    conn.execute(
        f"""
        UPDATE download_items
        SET {", ".join(assignments)}
        WHERE id = ?
        """,
        tuple(params),
    )


def _ensure_item(
    conn: sqlite3.Connection,
    channel_id: str,
    video_id: str,
    save_base_folder: str | None,
    now: str,
    updates: dict | None = None,
) -> int:
    item_id = _resolve_canonical_item_id(conn, channel_id, video_id)
    if item_id is not None:
        return item_id

    updates = updates or {}
    save_base_folder_raw = _text_or_empty(save_base_folder)
    save_base_folder_norm = _normalize_path_text(save_base_folder_raw)
    channel_name = _nullable_text(updates.get("channel_name"))
    channel_db_id = _ensure_channel_row(
        conn,
        channel_id,
        channel_name,
        save_base_folder_raw,
        save_base_folder_norm,
        now,
    )
    sanitized_filename_base_raw = _text_or_empty(updates.get("sanitized_filename_base")).strip()
    if not sanitized_filename_base_raw:
        sanitized_filename_base_raw = f"yt_{video_id}"
    sanitized_filename_base = normalize_output_stem(sanitized_filename_base_raw)

    cursor = conn.execute(
        """
        INSERT INTO download_items(
            channel_db_id,
            platform,
            channel_id,
            video_id,
            save_base_folder_raw,
            save_base_folder_norm,
            original_title,
            sanitized_filename_base,
            display_order_at_download,
            status,
            manual_status,
            manual_override,
            downloaded_at,
            updated_at,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            channel_db_id,
            PLATFORM_YOUTUBE,
            channel_id,
            video_id,
            save_base_folder_raw,
            save_base_folder_norm,
            _nullable_text(updates.get("original_title")),
            sanitized_filename_base,
            _optional_int(updates.get("display_order_at_download")),
            updates.get("status") if updates.get("status") is not None else None,
            updates.get("manual_status") if updates.get("manual_status") is not None else None,
            _manual_override_to_db(updates.get("manual_override")),
            _nullable_text(updates.get("downloaded_at")),
            _nullable_text(updates.get("updated_at")) or now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def _ensure_channel_row(
    conn: sqlite3.Connection,
    channel_id: str,
    channel_name: str | None,
    save_base_folder_raw: str,
    save_base_folder_norm: str,
    now: str,
) -> int:
    row = conn.execute(
        """
        SELECT id
        FROM channels
        WHERE platform = ? AND channel_id = ? AND save_base_folder_norm = ?
        """,
        (PLATFORM_YOUTUBE, channel_id, save_base_folder_norm),
    ).fetchone()
    if row:
        channel_db_id = int(row["id"] if isinstance(row, sqlite3.Row) else row[0])
        if channel_name is not None:
            conn.execute(
                "UPDATE channels SET channel_name = ?, updated_at = ? WHERE id = ?",
                (channel_name, now, channel_db_id),
            )
        return channel_db_id

    cursor = conn.execute(
        """
        INSERT INTO channels(
            platform,
            channel_id,
            channel_name,
            save_base_folder_raw,
            save_base_folder_norm,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (PLATFORM_YOUTUBE, channel_id, channel_name, save_base_folder_raw, save_base_folder_norm, now, now),
    )
    return int(cursor.lastrowid)


def _apply_item_updates(conn: sqlite3.Connection, item_id: int, updates: dict, now: str) -> None:
    if not updates:
        return

    assignments = []
    params = []
    for key in (
        "original_title",
        "display_order_at_download",
        "status",
        "manual_status",
        "downloaded_at",
    ):
        if key in updates and updates.get(key) is not None:
            assignments.append(f"{key} = ?")
            params.append(updates.get(key))

    if "sanitized_filename_base" in updates and _has_text(updates.get("sanitized_filename_base")):
        assignments.append("sanitized_filename_base = ?")
        params.append(normalize_output_stem(str(updates.get("sanitized_filename_base"))))

    if "manual_override" in updates and updates.get("manual_override") is not None:
        assignments.append("manual_override = ?")
        params.append(_manual_override_to_db(updates.get("manual_override")))

    if assignments:
        assignments.append("updated_at = ?")
        params.append(now)
        params.append(item_id)
        conn.execute(
            f"UPDATE download_items SET {', '.join(assignments)} WHERE id = ?",
            tuple(params),
        )

    if "channel_name" in updates and updates.get("channel_name") is not None:
        row = conn.execute("SELECT channel_db_id FROM download_items WHERE id = ?", (item_id,)).fetchone()
        if row:
            channel_db_id = row["channel_db_id"] if isinstance(row, sqlite3.Row) else row[0]
            conn.execute(
                "UPDATE channels SET channel_name = ?, updated_at = ? WHERE id = ?",
                (updates.get("channel_name"), now, channel_db_id),
            )


def _common_item_updates_from_kwargs(kwargs: dict) -> dict:
    allowed_keys = (
        "channel_name",
        "original_title",
        "sanitized_filename_base",
        "display_order_at_download",
        "downloaded_at",
        "updated_at",
    )
    return {key: kwargs[key] for key in allowed_keys if key in kwargs}


def _upsert_file_part(
    conn: sqlite3.Connection,
    item_id: int,
    part: str,
    filename_raw: str | None = None,
    path_raw: str | None = None,
    status: str | None = None,
    now: str | None = None,
) -> None:
    if part not in FILE_PARTS:
        raise ValueError("Unsupported file part")
    now = now or _now_iso()
    row = conn.execute(
        """
        SELECT filename_raw, path_raw, status, created_at
        FROM download_files
        WHERE item_id = ? AND part = ?
        """,
        (item_id, part),
    ).fetchone()
    if row is None and filename_raw is None and path_raw is None and status is None:
        return

    existing_filename = row["filename_raw"] if isinstance(row, sqlite3.Row) and row else (row[0] if row else None)
    existing_path = row["path_raw"] if isinstance(row, sqlite3.Row) and row else (row[1] if row else None)
    existing_status = row["status"] if isinstance(row, sqlite3.Row) and row else (row[2] if row else None)

    new_filename_raw = str(filename_raw) if filename_raw is not None else existing_filename
    new_path_raw = str(path_raw) if path_raw is not None else existing_path
    new_status = status if status is not None else existing_status
    filename_norm = _normalize_filename_text(new_filename_raw) if new_filename_raw is not None else None
    path_norm = _normalize_path_text(new_path_raw) if new_path_raw is not None else None
    is_valid, validation_reason = _file_validity(new_filename_raw, new_path_raw, new_status)

    if row is None:
        conn.execute(
            """
            INSERT INTO download_files(
                item_id,
                part,
                status,
                filename_raw,
                filename_norm,
                path_raw,
                path_norm,
                is_valid,
                validation_reason,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                part,
                new_status,
                new_filename_raw,
                filename_norm,
                new_path_raw,
                path_norm,
                is_valid,
                validation_reason,
                now,
                now,
            ),
        )
        return

    conn.execute(
        """
        UPDATE download_files
        SET status = ?,
            filename_raw = ?,
            filename_norm = ?,
            path_raw = ?,
            path_norm = ?,
            is_valid = ?,
            validation_reason = ?,
            updated_at = ?
        WHERE item_id = ? AND part = ?
        """,
        (
            new_status,
            new_filename_raw,
            filename_norm,
            new_path_raw,
            path_norm,
            is_valid,
            validation_reason,
            now,
            item_id,
            part,
        ),
    )


def _apply_file_updates_from_entry(conn: sqlite3.Connection, item_id: int, updates: dict, now: str) -> None:
    for part in FILE_PARTS:
        filename_key = f"{part}_filename"
        path_key = f"{part}_path"
        status_key = f"{part}_status"
        if filename_key not in updates and path_key not in updates and status_key not in updates:
            continue
        _upsert_file_part(
            conn,
            item_id,
            part,
            filename_raw=updates.get(filename_key) if updates.get(filename_key) is not None else None,
            path_raw=updates.get(path_key) if updates.get(path_key) is not None else None,
            status=updates.get(status_key) if updates.get(status_key) is not None else None,
            now=now,
        )


def _set_existing_or_updated_part_statuses(
    conn: sqlite3.Connection,
    item_id: int,
    parts_with_updates,
    status: str,
    now: str,
) -> None:
    for part, updates in parts_with_updates:
        has_part_update = any(
            key in updates for key in (f"{part}_filename", f"{part}_path", f"{part}_status")
        )
        if not has_part_update and not _file_part_exists(conn, item_id, part):
            continue
        _upsert_file_part(
            conn,
            item_id,
            part,
            filename_raw=updates.get(f"{part}_filename") if updates.get(f"{part}_filename") is not None else None,
            path_raw=updates.get(f"{part}_path") if updates.get(f"{part}_path") is not None else None,
            status=status,
            now=now,
        )


def _file_part_exists(conn: sqlite3.Connection, item_id: int, part: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM download_files WHERE item_id = ? AND part = ?",
        (item_id, part),
    ).fetchone()
    return row is not None


def _entry_for_item_id(conn: sqlite3.Connection, item_id: int) -> dict:
    row = conn.execute(
        """
        SELECT
            di.id,
            di.platform,
            di.channel_id,
            di.video_id,
            di.save_base_folder_raw,
            di.save_base_folder_norm,
            di.original_title,
            di.sanitized_filename_base,
            di.display_order_at_download,
            di.status,
            di.manual_status,
            di.manual_override,
            di.downloaded_at,
            di.updated_at,
            c.channel_name,
            c.save_base_folder_raw AS channel_save_base_folder_raw
        FROM download_items di
        LEFT JOIN channels c ON c.id = di.channel_db_id
        WHERE di.id = ?
        """,
        (item_id,),
    ).fetchone()
    if row is None:
        return {}
    entry = _row_to_item_entry(row)
    file_rows = conn.execute(
        """
        SELECT item_id, part, status, filename_raw, path_raw
        FROM download_files
        WHERE item_id = ?
        ORDER BY part
        """,
        (item_id,),
    ).fetchall()
    for file_row in file_rows:
        _apply_file_row(entry, file_row)
    return entry


def _entry_for_video(conn: sqlite3.Connection, channel_id: str, video_id: str) -> dict | None:
    item_id = _resolve_canonical_item_id(conn, channel_id, video_id)
    if item_id is None:
        return None
    entry = _entry_for_item_id(conn, item_id)
    return entry if entry else None


def _get_effective_status(entry: dict, download_mode: str | None = None) -> str:
    from core.download_modes import MODE_VIDEO_THUMB
    from core.state_store import get_effective_status

    return get_effective_status(entry, download_mode or MODE_VIDEO_THUMB)


def _status_after_manual_clear(
    entry: dict,
    previous_manual_status: str | None,
    download_mode: str | None = None,
) -> str:
    from core.download_modes import MODE_VIDEO_THUMB
    from core.state_store import _status_after_manual_clear

    return _status_after_manual_clear(entry, previous_manual_status, download_mode or MODE_VIDEO_THUMB)


def _file_validity(filename_raw: str | None, path_raw: str | None, status: str | None) -> tuple[int, str | None]:
    if status is None:
        return 1, None
    reasons = []
    if not _has_text(filename_raw):
        reasons.append("missing_filename")
    if not _has_text(path_raw):
        reasons.append("missing_path")
    if not reasons:
        return 1, None
    return 0, ";".join(reasons)


def _manual_override_to_db(value) -> int | None:
    if value is True:
        return 1
    if value is False:
        return 0
    if value in (0, 1):
        return int(value)
    return None


def _ensure_state_channel(state: dict, row: sqlite3.Row) -> dict:
    channels = state.setdefault("channels", {})
    channel_id = row["channel_id"]
    if channel_id not in channels:
        channels[channel_id] = {
            "channel_id": channel_id,
            "channel_name": row["channel_name"],
            "save_base_folder": row["channel_save_base_folder_raw"] or row["save_base_folder_raw"] or "",
            "videos": {},
        }
    return channels[channel_id]


def _row_to_item_entry(row: sqlite3.Row) -> dict:
    entry = {
        "channel_id": row["channel_id"],
        "channel_name": row["channel_name"],
        "save_base_folder": row["save_base_folder_raw"] or "",
        "video_id": row["video_id"],
        "original_title": row["original_title"],
        "sanitized_filename_base": row["sanitized_filename_base"],
    }
    _set_if_not_none(entry, "display_order_at_download", row["display_order_at_download"])
    _set_if_not_none(entry, "status", row["status"])
    _set_if_not_none(entry, "manual_status", row["manual_status"])
    manual_override = _manual_override_from_db(row["manual_override"])
    if manual_override is not None:
        entry["manual_override"] = manual_override
    _set_if_not_none(entry, "downloaded_at", row["downloaded_at"])
    _set_if_not_none(entry, "updated_at", row["updated_at"])
    return entry


def _manual_override_from_db(value) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _apply_file_row(entry: dict, row: sqlite3.Row) -> None:
    part = row["part"]
    if part not in FILE_PARTS:
        return
    _set_if_not_none(entry, f"{part}_filename", row["filename_raw"])
    _set_if_not_none(entry, f"{part}_path", row["path_raw"])
    _set_if_not_none(entry, f"{part}_status", row["status"])


def _set_if_not_none(target: dict, key: str, value) -> None:
    if value is not None:
        target[key] = value


def _normalize_path_text(value) -> str:
    text = _text_or_empty(value).strip().replace("\\", "/")
    while text.endswith("/") and not _is_drive_root(text) and text != "/":
        text = text[:-1]
    return text.casefold()


def _normalize_filename_text(value) -> str:
    return _text_or_empty(value).strip().casefold()


def _nullable_text(value) -> str | None:
    if not _has_text(value):
        return None
    return str(value)


def _text_or_empty(value) -> str:
    if value is None:
        return ""
    return str(value)


def _has_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) or value is not None and not isinstance(value, str)


def _optional_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_drive_root(text: str) -> bool:
    return len(text) == 3 and text[0].isalpha() and text[1] == ":" and text[2] == "/"


def _empty_state() -> dict:
    return {"version": SCHEMA_VERSION, "channels": {}}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _print_summary(path: Path | None = None) -> int:
    db_path = _resolve_database_path(path)
    print(f"DB path: {db_path}")
    if not db_path.exists():
        print("Status: missing")
        return 2
    with closing(_connect_read_only(db_path)) as conn:
        channels_count = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
        items_count = conn.execute("SELECT COUNT(*) FROM download_items").fetchone()[0]
        files_count = conn.execute("SELECT COUNT(*) FROM download_files").fetchone()[0]
    print(f"channels count: {channels_count}")
    print(f"items count: {items_count}")
    print(f"files count: {files_count}")
    return 0


def _print_quick_check(path: Path | None = None) -> int:
    result = quick_sqlite_state_check(path)
    summary = get_sqlite_state_summary(path)
    print(f"status: {'OK' if result.get('ok') else 'FAIL'}")
    print(f"DB path: {result.get('db_path')}")
    if summary.get("error"):
        print(f"summary_error: {summary['error']}")
    print("counts:")
    print(f"  channels: {summary.get('channels', 0)}")
    print(f"  download_items: {summary.get('download_items', result.get('download_items_count', 0))}")
    print(f"  download_files: {summary.get('download_files', 0)}")
    if result.get("ok"):
        return 0

    reasons = result.get("reasons") or ["unknown"]
    print(f"reason: {', '.join(str(reason) for reason in reasons)}")
    missing_tables = result.get("missing_tables") or []
    if missing_tables:
        print(f"missing_tables: {', '.join(missing_tables)}")
    return 2 if "db_missing" in reasons else 1


def _dump_one(channel_id: str, video_id: str, path: Path | None = None) -> int:
    entry = get_video_entry(channel_id, video_id, path=path)
    if entry is None:
        print("Entry not found")
        return 1
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


def _self_test_write(test_path: Path) -> int:
    from core.state_store import STATUS_DOWNLOADED, STATUS_MISSING_THUMB, STATUS_NOT_DOWNLOADED, get_effective_status

    real_db_path = db_file().resolve(strict=False)
    if test_path.resolve(strict=False) == real_db_path:
        print("Refusing to run write self-test against the real SQLite state DB.")
        return 2
    if test_path.exists():
        print(f"Refusing to overwrite existing test DB: {test_path}")
        return 2

    init_db(test_path)
    update_manual_status("channel", "manual-video", STATUS_DOWNLOADED, path=test_path, save_base_folder="D:/A")
    entry = get_video_entry("channel", "manual-video", path=test_path, save_base_folder="D:/A")
    _assert_self_test(entry.get("manual_override") is True, "manual_override was not set")
    _assert_self_test(entry.get("manual_status") == STATUS_DOWNLOADED, "manual_status was not preserved")
    _assert_self_test(get_effective_status(entry) == STATUS_DOWNLOADED, "manual effective status was not applied")

    clear_manual_status("channel", "manual-video", path=test_path, save_base_folder="D:/A")
    entry = get_video_entry("channel", "manual-video", path=test_path, save_base_folder="D:/A")
    _assert_self_test("manual_override" not in entry, "manual_override was not cleared")
    _assert_self_test("manual_status" not in entry, "manual_status was not cleared")

    update_video_part_state(
        "channel",
        "part-video",
        "video",
        filename="video.mp4",
        file_path="D:/A/Channel/video/video.mp4",
        status=STATUS_DOWNLOADED,
        path=test_path,
        save_base_folder="D:/A",
    )
    entry = get_video_entry("channel", "part-video", path=test_path, save_base_folder="D:/A")
    _assert_self_test(entry.get("video_filename") == "video.mp4", "video filename was not written")
    _assert_self_test(entry.get("video_status") == STATUS_DOWNLOADED, "video status was not written")
    _assert_self_test(entry.get("status") == STATUS_MISSING_THUMB, "aggregate status did not match video-only state")

    update_video_part_state(
        "channel",
        "part-video",
        "thumb",
        filename="video.jpg",
        file_path="D:/A/Channel/thumb/video.jpg",
        status=STATUS_DOWNLOADED,
        path=test_path,
        save_base_folder="D:/A",
    )
    update_video_part_state(
        "channel",
        "part-video",
        "audio",
        filename="video.mp3",
        file_path="D:/A/Channel/audio/video.mp3",
        path=test_path,
        save_base_folder="D:/A",
    )
    entry = get_video_entry("channel", "part-video", path=test_path, save_base_folder="D:/A")
    _assert_self_test(entry.get("status") == STATUS_DOWNLOADED, "aggregate status did not become downloaded")
    _assert_self_test(entry.get("video_filename") == "video.mp4", "existing video filename was dropped")
    _assert_self_test(entry.get("audio_filename") == "video.mp3", "audio filename was not written")
    _assert_self_test("audio_status" not in entry, "audio status was invented from filename/path")

    update_video_state(
        "channel",
        "ambiguous-video",
        {"status": STATUS_NOT_DOWNLOADED, "sanitized_filename_base": "video-b"},
        path=test_path,
        save_base_folder="D:/B",
    )
    update_video_state(
        "channel",
        "ambiguous-video",
        {"status": STATUS_NOT_DOWNLOADED, "sanitized_filename_base": "video-a"},
        path=test_path,
        save_base_folder="D:/A",
    )
    update_manual_status("channel", "ambiguous-video", STATUS_DOWNLOADED, path=test_path)
    entry = get_video_entry("channel", "ambiguous-video", path=test_path)
    _assert_self_test(entry.get("manual_override") is True, "video-scoped manual update was not applied")
    _assert_self_test(get_effective_status(entry) == STATUS_DOWNLOADED, "video-scoped manual status was not effective")

    print(f"Write self-test passed: {test_path}")
    return 0


def _assert_self_test(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Manual SQLite state-store smoke tools.")
    parser.add_argument("--summary", action="store_true", help="Print row counts from the SQLite state DB.")
    parser.add_argument("--quick-check", action="store_true", help="Run a fast SQLite state usability check.")
    parser.add_argument(
        "--dump-one",
        nargs=2,
        metavar=("CHANNEL_ID", "VIDEO_ID"),
        help="Print one reconstructed JSON-shaped video entry.",
    )
    parser.add_argument(
        "--self-test-write",
        metavar="TEMP_DB_PATH",
        type=Path,
        help="Run write API smoke tests against a new temporary DB path.",
    )
    args = parser.parse_args()

    if args.summary:
        return _print_summary()
    if args.quick_check:
        return _print_quick_check()
    if args.dump_one:
        return _dump_one(args.dump_one[0], args.dump_one[1])
    if args.self_test_write:
        return _self_test_write(args.self_test_write)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
