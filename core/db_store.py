import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from core.runtime_paths import db_file


SCHEMA_VERSION = 1
PLATFORM_YOUTUBE = "youtube"
FILE_PARTS = ("video", "thumb", "audio")

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

CREATE INDEX IF NOT EXISTS idx_download_items_identity
ON download_items(platform, channel_id, video_id, save_base_folder_norm);

CREATE INDEX IF NOT EXISTS idx_download_items_channel
ON download_items(channel_db_id);

CREATE INDEX IF NOT EXISTS idx_download_files_item_part
ON download_files(item_id, part);

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

    videos = load_state(path).get("channels", {}).get(channel_id, {}).get("videos", {})
    if not isinstance(videos, dict):
        return {}
    if save_base_folder is None:
        return videos

    save_base_folder_norm = _normalize_path_text(save_base_folder)
    return {
        video_key: entry
        for video_key, entry in videos.items()
        if isinstance(entry, dict)
        and _normalize_path_text(entry.get("save_base_folder", "")) == save_base_folder_norm
    }


def get_video_entry(
    channel_id: str,
    video_id: str,
    path: Path | None = None,
    save_base_folder: str | None = None,
) -> dict | None:
    if not channel_id or not video_id:
        return None

    videos = get_channel_video_entries(channel_id, path=path, save_base_folder=save_base_folder)
    entry = videos.get(video_id)
    if isinstance(entry, dict):
        return entry

    for candidate in videos.values():
        if isinstance(candidate, dict) and candidate.get("video_id") == video_id:
            return candidate
    return None


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
    sanitized_filename_base = _text_or_empty(updates.get("sanitized_filename_base")).strip()
    if not sanitized_filename_base:
        sanitized_filename_base = f"yt_{video_id}"

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
        params.append(str(updates.get("sanitized_filename_base")))

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
    if args.dump_one:
        return _dump_one(args.dump_one[0], args.dump_one[1])
    if args.self_test_write:
        return _self_test_write(args.self_test_write)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
