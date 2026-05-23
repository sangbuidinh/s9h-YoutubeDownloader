import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from core.filename_utils import normalize_output_stem
from core.runtime_paths import db_file


SCHEMA_VERSION = 1
PLATFORM_YOUTUBE = "youtube"
FILE_PARTS = ("video", "thumb", "audio")
REQUIRED_TABLES = (
    "app_meta",
    "schema_migrations",
    "channels",
    "download_items",
    "download_files",
    "import_warnings",
)
NULL_COUNTER_KEY = "<NULL>"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

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
);

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
);

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
);

CREATE TABLE IF NOT EXISTS import_warnings (
    id INTEGER PRIMARY KEY,
    migration_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    warning_code TEXT NOT NULL,
    platform TEXT NULL,
    channel_id TEXT NULL,
    video_id TEXT NULL,
    save_base_folder_norm TEXT NULL,
    part TEXT NULL,
    message TEXT NOT NULL,
    source_json TEXT NULL,
    created_at TEXT NOT NULL
);

DROP INDEX IF EXISTS idx_download_items_identity;

CREATE INDEX IF NOT EXISTS idx_download_items_channel
ON download_items(channel_db_id);

CREATE INDEX IF NOT EXISTS idx_download_items_channel_folder
ON download_items(platform, channel_id, save_base_folder_norm);

DROP INDEX IF EXISTS idx_download_files_item_part;

CREATE INDEX IF NOT EXISTS idx_download_files_path_norm
ON download_files(path_norm);

CREATE INDEX IF NOT EXISTS idx_import_warnings_migration
ON import_warnings(migration_id);
"""


def connect_db(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or db_file()
    conn = sqlite3.connect(db_path)
    _apply_pragmas(conn)
    return conn


def init_db(path: Path | None = None) -> Path:
    db_path = path or db_file()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    now = _now_iso()
    with closing(connect_db(db_path)) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            """
            INSERT OR IGNORE INTO schema_migrations(version, name, applied_at)
            VALUES (?, ?, ?)
            """,
            (SCHEMA_VERSION, "initial_schema", now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO app_meta(key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            ("schema_version", str(SCHEMA_VERSION), now),
        )
        conn.commit()
    return db_path


def quick_sqlite_state_check(path: Path | None = None) -> dict:
    db_path = path or db_file()
    result = {
        "ok": False,
        "db_path": str(db_path),
        "exists": db_path.exists(),
        "can_open": False,
        "required_tables_present": False,
        "missing_tables": list(REQUIRED_TABLES),
        "download_items_count": 0,
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
            if item_count <= 0:
                result["reasons"].append("download_items_empty")

            conn.execute("SELECT id FROM download_items LIMIT 1").fetchone()
            result["simple_read_succeeded"] = True
    except sqlite3.Error as exc:
        result["reasons"].append(f"sqlite_error:{type(exc).__name__}: {exc}")
        return result

    result["ok"] = (
        result["exists"]
        and result["can_open"]
        and result["required_tables_present"]
        and result["download_items_count"] > 0
        and result["simple_read_succeeded"]
    )
    return result


def is_sqlite_state_usable(path: Path | None = None) -> bool:
    return bool(quick_sqlite_state_check(path).get("ok"))


def sqlite_has_any_items(path: Path | None = None) -> bool:
    db_path = path or db_file()
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
    db_path = path or db_file()
    summary = {
        "db_path": str(db_path),
        "channels": 0,
        "download_items": 0,
        "download_files": 0,
        "import_warnings": 0,
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
            for table_name in ("channels", "download_items", "download_files", "import_warnings"):
                if table_name in table_names:
                    summary[table_name] = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()[
                        "count"
                    ]

            if "download_items" in table_names:
                summary["status_counts"] = _counts_by_nullable_text(conn, "status")
                summary["manual_override_counts"] = _manual_override_counts(conn)
                summary["manual_status_counts"] = _counts_by_nullable_text(conn, "manual_status")
    except sqlite3.Error as exc:
        summary["error"] = f"sqlite_error:{type(exc).__name__}: {exc}"
    return summary


def load_state(path: Path | None = None) -> dict:
    db_path = path or db_file()
    if not db_path.exists():
        return _empty_state()

    state = _empty_state()
    video_key_norms: dict[str, dict[str, str]] = {}
    with closing(_connect_read_only(db_path)) as conn:
        conn.row_factory = sqlite3.Row
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
            video_key = _state_video_key(
                channel["videos"],
                video_key_norms.setdefault(row["channel_id"], {}),
                row["video_id"],
                row["save_base_folder_norm"],
            )
            channel["videos"][video_key] = entry
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

    db_path = path or db_file()
    if not db_path.exists():
        return {}

    where_sql, params = _channel_items_where(channel_id, save_base_folder)
    videos: dict = {}
    video_key_norms: dict[str, str] = {}
    item_id_to_entry: dict[int, dict] = {}

    with closing(_connect_read_only(db_path)) as conn:
        conn.row_factory = sqlite3.Row
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
            video_key = _state_video_key(videos, video_key_norms, row["video_id"], row["save_base_folder_norm"])
            videos[video_key] = entry
            item_id_to_entry[row["id"]] = entry

        if item_id_to_entry:
            file_rows = conn.execute(
                f"""
                SELECT df.item_id, df.part, df.status, df.filename_raw, df.path_raw
                FROM download_files df
                JOIN download_items di ON di.id = df.item_id
                WHERE {where_sql}
                ORDER BY di.id, df.part
                """,
                params,
            ).fetchall()
            for row in file_rows:
                entry = item_id_to_entry.get(row["item_id"])
                if entry is not None:
                    _apply_file_row(entry, row)

    if save_base_folder is None:
        return videos

    return videos


def get_video_entry(
    channel_id: str,
    video_id: str,
    path: Path | None = None,
    save_base_folder: str | None = None,
) -> dict | None:
    if not channel_id or not video_id:
        return None

    db_path = path or db_file()
    if not db_path.exists():
        return None

    with closing(_connect_read_only(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        item_id = _resolve_item_id_for_read(conn, channel_id, video_id, save_base_folder)
        if item_id is None:
            return None
        entry = _entry_for_item_id(conn, item_id)
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

    db_path = init_db(path or db_file())
    now = _now_iso()
    with closing(connect_db(db_path)) as conn:
        try:
            conn.execute("BEGIN")
            item_id = _ensure_item(conn, channel_id, video_id, save_base_folder, now=now)
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
            conn.rollback()
            raise


def clear_manual_status(
    channel_id: str,
    video_id: str,
    path: Path | None = None,
    save_base_folder: str | None = None,
) -> None:
    if not channel_id or not video_id:
        return

    db_path = path or db_file()
    if not db_path.exists():
        return

    now = _now_iso()
    with closing(connect_db(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            item_id = _resolve_item_id(conn, channel_id, video_id, save_base_folder)
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
            conn.rollback()
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
    db_path = init_db(path or db_file())
    now = _now_iso()
    with closing(connect_db(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
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
                conn.execute(
                    """
                    UPDATE download_items
                    SET manual_status = NULL,
                        manual_override = NULL,
                        downloaded_at = ?
                    WHERE id = ?
                    """,
                    (now, item_id),
                )
            entry = _entry_for_item_id(conn, item_id)
            new_status = _get_effective_status(entry, download_mode)
            conn.execute(
                "UPDATE download_items SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, item_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
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
    db_path = init_db(path or db_file())
    now = _now_iso()
    with closing(connect_db(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            item_id = _ensure_item(conn, channel_id, video_id, save_base_folder, now=now)
            old_entry = _entry_for_item_id(conn, item_id)
            old_status = _get_effective_status(old_entry, mode)
            has_downloaded_required_part = any(
                part_status_from_entry(old_entry, part) == STATUS_DOWNLOADED
                for part in required_parts(mode)
            )
            if has_downloaded_required_part:
                conn.execute(
                    "UPDATE download_items SET manual_status = NULL, manual_override = NULL WHERE id = ?",
                    (item_id,),
                )
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
            conn.rollback()
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

    db_path = init_db(path or db_file())
    now = _now_iso()
    with closing(connect_db(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
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
            conn.rollback()
            raise


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


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


def _channel_items_where(channel_id: str, save_base_folder: str | None = None) -> tuple[str, tuple]:
    clauses = ["di.platform = ?", "di.channel_id = ?"]
    params: list[str] = [PLATFORM_YOUTUBE, channel_id]
    if save_base_folder is not None:
        clauses.append("di.save_base_folder_norm = ?")
        params.append(_normalize_path_text(save_base_folder))
    return " AND ".join(clauses), tuple(params)


def _resolve_item_id_for_read(
    conn: sqlite3.Connection,
    channel_id: str,
    video_id: str,
    save_base_folder: str | None = None,
) -> int | None:
    if save_base_folder is not None:
        return _resolve_item_id(conn, channel_id, video_id, save_base_folder)

    row = conn.execute(
        """
        SELECT id
        FROM download_items
        WHERE platform = ? AND channel_id = ? AND video_id = ?
        ORDER BY id
        LIMIT 1
        """,
        (PLATFORM_YOUTUBE, channel_id, video_id),
    ).fetchone()
    return int(row["id"] if isinstance(row, sqlite3.Row) else row[0]) if row else None


def _resolve_item_id(
    conn: sqlite3.Connection,
    channel_id: str,
    video_id: str,
    save_base_folder: str | None = None,
) -> int | None:
    if save_base_folder is not None:
        row = conn.execute(
            """
            SELECT id
            FROM download_items
            WHERE platform = ?
              AND channel_id = ?
              AND video_id = ?
              AND save_base_folder_norm = ?
            """,
            (PLATFORM_YOUTUBE, channel_id, video_id, _normalize_path_text(save_base_folder)),
        ).fetchone()
        return int(row["id"] if isinstance(row, sqlite3.Row) else row[0]) if row else None

    rows = conn.execute(
        """
        SELECT id, save_base_folder_norm
        FROM download_items
        WHERE platform = ? AND channel_id = ? AND video_id = ?
        ORDER BY id
        """,
        (PLATFORM_YOUTUBE, channel_id, video_id),
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        folders = [row["save_base_folder_norm"] if isinstance(row, sqlite3.Row) else row[1] for row in rows]
        raise ValueError(
            "Multiple SQLite items match channel_id/video_id; pass save_base_folder. "
            f"save_base_folder_norm values: {folders}"
        )
    row = rows[0]
    return int(row["id"] if isinstance(row, sqlite3.Row) else row[0])


def _ensure_item(
    conn: sqlite3.Connection,
    channel_id: str,
    video_id: str,
    save_base_folder: str | None,
    now: str,
    updates: dict | None = None,
) -> int:
    item_id = _resolve_item_id(conn, channel_id, video_id, save_base_folder)
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


def _state_video_key(
    videos: dict,
    video_key_norms: dict[str, str],
    video_id: str,
    save_base_folder_norm: str,
) -> str:
    if video_id not in videos:
        video_key_norms[video_id] = save_base_folder_norm
        return video_id
    if video_key_norms.get(video_id) == save_base_folder_norm:
        return video_id

    # JSON groups videos only by video_id, while SQLite identity also includes save_base_folder_norm.
    # Keep the first JSON-compatible key and use a stable composite key for later duplicates.
    composite_key = f"{video_id}::{save_base_folder_norm}"
    if composite_key not in videos:
        return composite_key

    suffix = 2
    while f"{composite_key}::{suffix}" in videos:
        suffix += 1
    return f"{composite_key}::{suffix}"


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
    db_path = path or db_file()
    print(f"DB path: {db_path}")
    if not db_path.exists():
        print("Status: missing")
        return 2
    with closing(_connect_read_only(db_path)) as conn:
        channels_count = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
        items_count = conn.execute("SELECT COUNT(*) FROM download_items").fetchone()[0]
        files_count = conn.execute("SELECT COUNT(*) FROM download_files").fetchone()[0]
        warnings_count = conn.execute("SELECT COUNT(*) FROM import_warnings").fetchone()[0]
    print(f"channels count: {channels_count}")
    print(f"items count: {items_count}")
    print(f"files count: {files_count}")
    print(f"warnings count: {warnings_count}")
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
    print(f"  import_warnings: {summary.get('import_warnings', 0)}")
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
    try:
        update_manual_status("channel", "ambiguous-video", STATUS_DOWNLOADED, path=test_path)
    except ValueError:
        pass
    else:
        raise AssertionError("ambiguous update without save_base_folder was not refused")

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
