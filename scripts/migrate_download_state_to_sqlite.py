import argparse
import json
import shutil
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.db_store import connect_db, init_db
from core.runtime_paths import db_file, state_file


PLATFORM = "youtube"
PARTS = ("video", "thumb", "audio")
MIGRATION_ID_PREFIX = "json_to_sqlite"


class MigrationRefused(Exception):
    pass


@dataclass
class MigrationSummary:
    migration_id: str
    source_json_path: Path
    db_path: Path
    backup_path: Path | None = None
    backup_created: bool = False
    committed: bool = False
    channels_imported: int = 0
    videos_imported: int = 0
    files_by_part: Counter = field(default_factory=Counter)
    warnings_by_code: Counter = field(default_factory=Counter)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manually migrate data/download_state.json into data/download_state.sqlite3."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Clear existing migrated rows before importing JSON state again.",
    )
    args = parser.parse_args()

    try:
        summary = migrate_json_to_sqlite(force=args.force)
    except MigrationRefused as exc:
        print(f"[REFUSED] {exc}")
        print("Re-run with --force to clear migrated rows and import again.")
        return 2
    except Exception as exc:
        print(f"[ERROR] Migration failed: {type(exc).__name__}: {exc}")
        return 1

    print_summary(summary)
    return 0


def migrate_json_to_sqlite(
    source_json_path: Path | None = None,
    sqlite_path: Path | None = None,
    force: bool = False,
    backup: bool = True,
    backup_dir: Path | None = None,
) -> MigrationSummary:
    source_path = source_json_path or state_file()
    target_db_path = init_db(sqlite_path or db_file())
    migration_id = _new_migration_id()
    summary = MigrationSummary(
        migration_id=migration_id,
        source_json_path=source_path,
        db_path=target_db_path,
    )

    if not source_path.exists():
        raise FileNotFoundError(source_path)

    with closing(connect_db(target_db_path)) as conn:
        existing_item = conn.execute("SELECT 1 FROM download_items LIMIT 1").fetchone()
        if existing_item is not None and not force:
            raise MigrationRefused(f"{target_db_path} already contains download_items rows.")

    if backup:
        summary.backup_path = _create_json_backup(source_path, backup_dir=backup_dir)
        summary.backup_created = True

    with source_path.open("r", encoding="utf-8") as state_file_handle:
        state = json.load(state_file_handle)
    if not isinstance(state, dict):
        raise ValueError("download_state.json must contain a JSON object")
    channels = state.get("channels", {})
    if not isinstance(channels, dict):
        raise ValueError("download_state.json field 'channels' must be an object")

    with closing(connect_db(target_db_path)) as conn:
        try:
            conn.execute("BEGIN")
            if force:
                _clear_migrated_rows(conn)
            _migrate_state(conn, state, summary)
            _write_app_meta(conn, summary)
            conn.commit()
            summary.committed = True
        except Exception:
            conn.rollback()
            raise

    return summary


def _migrate_state(conn: sqlite3.Connection, state: dict, summary: MigrationSummary) -> None:
    now = _now_iso()
    imported_channel_keys: set[tuple[str, str, str]] = set()
    seen_paths: dict[str, dict] = {}
    seen_filenames: dict[str, dict] = {}
    channels = state.get("channels", {})

    for channel_key, channel_record in channels.items():
        channel_id = str(channel_key)
        if not isinstance(channel_record, dict):
            _add_warning(
                conn,
                summary,
                "error",
                "malformed_channel_record",
                channel_id=channel_id,
                message="Channel record is not a JSON object; skipped.",
                source_json=channel_record,
            )
            continue

        embedded_channel_id = channel_record.get("channel_id")
        if _has_text(embedded_channel_id) and str(embedded_channel_id) != channel_id:
            _add_warning(
                conn,
                summary,
                "warning",
                "embedded_channel_id_mismatch",
                channel_id=channel_id,
                message="Embedded channel_id disagrees with JSON object key; object key was used.",
                source_json=channel_record,
            )

        channel_name = _nullable_text(channel_record.get("channel_name"))
        channel_save_raw = _text_or_empty(channel_record.get("save_base_folder"))
        channel_save_norm = normalize_path_text(channel_save_raw)
        channel_db_id = _upsert_channel(
            conn,
            channel_id,
            channel_name,
            channel_save_raw,
            channel_save_norm,
            now,
        )
        imported_channel_keys.add((PLATFORM, channel_id, channel_save_norm))

        videos = channel_record.get("videos", {})
        if not isinstance(videos, dict):
            _add_warning(
                conn,
                summary,
                "error",
                "malformed_channel_record",
                channel_id=channel_id,
                save_base_folder_norm=channel_save_norm,
                message="Channel videos field is not a JSON object; channel imported without videos.",
                source_json=channel_record,
            )
            continue

        for video_key, video_record in videos.items():
            video_id = str(video_key)
            if not isinstance(video_record, dict):
                _add_warning(
                    conn,
                    summary,
                    "error",
                    "malformed_video_record",
                    channel_id=channel_id,
                    video_id=video_id,
                    save_base_folder_norm=channel_save_norm,
                    message="Video record is not a JSON object; skipped.",
                    source_json=video_record,
                )
                continue

            embedded_video_channel_id = video_record.get("channel_id")
            if _has_text(embedded_video_channel_id) and str(embedded_video_channel_id) != channel_id:
                _add_warning(
                    conn,
                    summary,
                    "warning",
                    "embedded_channel_id_mismatch",
                    channel_id=channel_id,
                    video_id=video_id,
                    save_base_folder_norm=channel_save_norm,
                    message="Embedded video channel_id disagrees with channel object key; object key was used.",
                    source_json=video_record,
                )

            embedded_video_id = video_record.get("video_id")
            if _has_text(embedded_video_id) and str(embedded_video_id) != video_id:
                _add_warning(
                    conn,
                    summary,
                    "warning",
                    "embedded_video_id_mismatch",
                    channel_id=channel_id,
                    video_id=video_id,
                    save_base_folder_norm=channel_save_norm,
                    message="Embedded video_id disagrees with JSON object key; object key was used.",
                    source_json=video_record,
                )

            video_save_raw = _text_or_empty(video_record.get("save_base_folder"))
            if not _has_text(video_save_raw):
                if _has_text(channel_save_raw):
                    _add_warning(
                        conn,
                        summary,
                        "warning",
                        "missing_save_base_folder",
                        channel_id=channel_id,
                        video_id=video_id,
                        save_base_folder_norm=channel_save_norm,
                        message="Video save_base_folder is empty; inherited channel save_base_folder.",
                        source_json=video_record,
                    )
                    video_save_raw = channel_save_raw
                else:
                    _add_warning(
                        conn,
                        summary,
                        "warning",
                        "missing_save_base_folder",
                        channel_id=channel_id,
                        video_id=video_id,
                        save_base_folder_norm="",
                        message="Video and channel save_base_folder are empty; empty string was used.",
                        source_json=video_record,
                    )
                    video_save_raw = ""
            video_save_norm = normalize_path_text(video_save_raw)

            item_channel_db_id = channel_db_id
            if video_save_norm != channel_save_norm:
                item_channel_db_id = _upsert_channel(
                    conn,
                    channel_id,
                    channel_name,
                    video_save_raw,
                    video_save_norm,
                    now,
                )
                imported_channel_keys.add((PLATFORM, channel_id, video_save_norm))

            sanitized_filename_base = _text_or_empty(video_record.get("sanitized_filename_base"))
            if not _has_text(sanitized_filename_base):
                sanitized_filename_base = f"yt_{video_id}"
                _add_warning(
                    conn,
                    summary,
                    "warning",
                    "missing_sanitized_filename_base",
                    channel_id=channel_id,
                    video_id=video_id,
                    save_base_folder_norm=video_save_norm,
                    message="sanitized_filename_base is empty; generated fallback from video_id.",
                    source_json=video_record,
                )

            item_id = _insert_download_item(
                conn,
                item_channel_db_id,
                channel_id,
                video_id,
                video_save_raw,
                video_save_norm,
                video_record,
                sanitized_filename_base,
                now,
            )
            summary.videos_imported += 1

            _migrate_file_parts(
                conn,
                summary,
                item_id,
                channel_id,
                video_id,
                video_save_norm,
                video_record,
                seen_paths,
                seen_filenames,
                now,
            )

    summary.channels_imported = len(imported_channel_keys)


def _migrate_file_parts(
    conn: sqlite3.Connection,
    summary: MigrationSummary,
    item_id: int,
    channel_id: str,
    video_id: str,
    save_base_folder_norm: str,
    video_record: dict,
    seen_paths: dict[str, dict],
    seen_filenames: dict[str, dict],
    now: str,
) -> None:
    for part in PARTS:
        filename_key = f"{part}_filename"
        path_key = f"{part}_path"
        status_key = f"{part}_status"
        filename_raw_value = video_record.get(filename_key)
        path_raw_value = video_record.get(path_key)
        status = video_record.get(status_key) if status_key in video_record else None

        filename_exists = _has_text(filename_raw_value)
        path_exists = _has_text(path_raw_value)
        status_exists = _has_text(status)
        if not filename_exists and not path_exists and not status_exists:
            continue

        filename_raw = str(filename_raw_value) if filename_exists else None
        path_raw = str(path_raw_value) if path_exists else None
        filename_norm = normalize_filename_text(filename_raw) if filename_exists else None
        path_norm = normalize_path_text(path_raw) if path_exists else None
        is_valid = 1
        validation_reasons = []

        if status_exists and not filename_exists:
            is_valid = 0
            validation_reasons.append("missing_filename")
            _add_warning(
                conn,
                summary,
                "warning",
                "missing_filename_with_status",
                channel_id=channel_id,
                video_id=video_id,
                save_base_folder_norm=save_base_folder_norm,
                part=part,
                message=f"{part}_status exists but {part}_filename is empty.",
                source_json=video_record,
            )
        if status_exists and not path_exists:
            is_valid = 0
            validation_reasons.append("missing_path")
            _add_warning(
                conn,
                summary,
                "warning",
                "missing_path_with_status",
                channel_id=channel_id,
                video_id=video_id,
                save_base_folder_norm=save_base_folder_norm,
                part=part,
                message=f"{part}_status exists but {part}_path is empty.",
                source_json=video_record,
            )

        if path_norm:
            first_seen = seen_paths.get(path_norm)
            current_source = _file_warning_source(channel_id, video_id, part, path_raw, path_norm)
            if first_seen is not None:
                _add_warning(
                    conn,
                    summary,
                    "warning",
                    "duplicate_file_path",
                    channel_id=channel_id,
                    video_id=video_id,
                    save_base_folder_norm=save_base_folder_norm,
                    part=part,
                    message="Duplicate normalized file path detected; records were kept separate.",
                    source_json={"first": first_seen, "current": current_source},
                )
            else:
                seen_paths[path_norm] = current_source

        if filename_norm:
            first_seen = seen_filenames.get(filename_norm)
            current_source = _file_warning_source(channel_id, video_id, part, filename_raw, filename_norm)
            if first_seen is not None:
                _add_warning(
                    conn,
                    summary,
                    "warning",
                    "duplicate_filename",
                    channel_id=channel_id,
                    video_id=video_id,
                    save_base_folder_norm=save_base_folder_norm,
                    part=part,
                    message="Duplicate normalized filename detected; records were kept separate.",
                    source_json={"first": first_seen, "current": current_source},
                )
            else:
                seen_filenames[filename_norm] = current_source

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
                status if status_exists else None,
                filename_raw,
                filename_norm,
                path_raw,
                path_norm,
                is_valid,
                ";".join(validation_reasons) if validation_reasons else None,
                now,
                now,
            ),
        )
        summary.files_by_part[part] += 1


def _upsert_channel(
    conn: sqlite3.Connection,
    channel_id: str,
    channel_name: str | None,
    save_base_folder_raw: str,
    save_base_folder_norm: str,
    now: str,
) -> int:
    conn.execute(
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
        ON CONFLICT(platform, channel_id, save_base_folder_norm)
        DO UPDATE SET
            channel_name = excluded.channel_name,
            save_base_folder_raw = excluded.save_base_folder_raw,
            updated_at = excluded.updated_at
        """,
        (PLATFORM, channel_id, channel_name, save_base_folder_raw, save_base_folder_norm, now, now),
    )
    row = conn.execute(
        """
        SELECT id
        FROM channels
        WHERE platform = ? AND channel_id = ? AND save_base_folder_norm = ?
        """,
        (PLATFORM, channel_id, save_base_folder_norm),
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to resolve imported channel row")
    return int(row[0])


def _insert_download_item(
    conn: sqlite3.Connection,
    channel_db_id: int,
    channel_id: str,
    video_id: str,
    save_base_folder_raw: str,
    save_base_folder_norm: str,
    video_record: dict,
    sanitized_filename_base: str,
    now: str,
) -> int:
    manual_override = None
    if "manual_override" in video_record:
        if video_record.get("manual_override") is True:
            manual_override = 1
        elif video_record.get("manual_override") is False:
            manual_override = 0

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
            PLATFORM,
            channel_id,
            video_id,
            save_base_folder_raw,
            save_base_folder_norm,
            _nullable_text(video_record.get("original_title")),
            sanitized_filename_base,
            _optional_int(video_record.get("display_order_at_download")),
            video_record.get("status") if "status" in video_record else None,
            video_record.get("manual_status") if "manual_status" in video_record else None,
            manual_override,
            _nullable_text(video_record.get("downloaded_at")),
            _nullable_text(video_record.get("updated_at")) or now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def _add_warning(
    conn: sqlite3.Connection,
    summary: MigrationSummary,
    severity: str,
    warning_code: str,
    message: str,
    channel_id: str | None = None,
    video_id: str | None = None,
    save_base_folder_norm: str | None = None,
    part: str | None = None,
    source_json=None,
) -> None:
    conn.execute(
        """
        INSERT INTO import_warnings(
            migration_id,
            severity,
            warning_code,
            platform,
            channel_id,
            video_id,
            save_base_folder_norm,
            part,
            message,
            source_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            summary.migration_id,
            severity,
            warning_code,
            PLATFORM,
            channel_id,
            video_id,
            save_base_folder_norm,
            part,
            message,
            _source_json_text(source_json),
            _now_iso(),
        ),
    )
    summary.warnings_by_code[warning_code] += 1


def _write_app_meta(conn: sqlite3.Connection, summary: MigrationSummary) -> None:
    now = _now_iso()
    total_files = sum(summary.files_by_part.values())
    metadata = {
        "download_state_json_migration_id": summary.migration_id,
        "download_state_json_migration_source_path": str(summary.source_json_path),
        "download_state_json_migration_db_path": str(summary.db_path),
        "download_state_json_migration_backup_path": str(summary.backup_path or ""),
        "download_state_json_migration_channels_imported": str(summary.channels_imported),
        "download_state_json_migration_videos_imported": str(summary.videos_imported),
        "download_state_json_migration_files_imported": str(total_files),
        "download_state_json_migration_warnings": str(sum(summary.warnings_by_code.values())),
        "download_state_json_migration_committed_at": now,
    }
    for key, value in metadata.items():
        conn.execute(
            """
            INSERT INTO app_meta(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now),
        )


def _clear_migrated_rows(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM download_files")
    conn.execute("DELETE FROM download_items")
    conn.execute("DELETE FROM channels")
    conn.execute("DELETE FROM import_warnings WHERE migration_id LIKE ?", (f"{MIGRATION_ID_PREFIX}_%",))


def _create_json_backup(source_path: Path, backup_dir: Path | None = None) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target_dir = backup_dir or source_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    backup_path = target_dir / f"{source_path.name}.bak.{timestamp}"
    if not backup_path.exists():
        shutil.copy2(source_path, backup_path)
        return backup_path

    suffix = 1
    while True:
        candidate = target_dir / f"{source_path.name}.bak.{timestamp}.{suffix}"
        if not candidate.exists():
            shutil.copy2(source_path, candidate)
            return candidate
        suffix += 1


def normalize_path_text(value) -> str:
    text = _text_or_empty(value).strip().replace("\\", "/")
    while text.endswith("/") and not _is_drive_root(text) and text != "/":
        text = text[:-1]
    return text.casefold()


def normalize_filename_text(value) -> str:
    return _text_or_empty(value).strip().casefold()


def print_summary(summary: MigrationSummary) -> None:
    print("Migration summary")
    print(f"migration_id: {summary.migration_id}")
    print(f"source_json_path: {summary.source_json_path}")
    print(f"db_path: {summary.db_path}")
    print(f"channels_imported: {summary.channels_imported}")
    print(f"videos_imported: {summary.videos_imported}")
    print("download_files_imported_by_part:")
    for part in PARTS:
        print(f"  {part}: {summary.files_by_part.get(part, 0)}")
    print("warnings_by_code:")
    if summary.warnings_by_code:
        for warning_code, count in sorted(summary.warnings_by_code.items()):
            print(f"  {warning_code}: {count}")
    else:
        print("  none: 0")
    print(f"backup_created: {'yes' if summary.backup_created else 'no'}")
    print(f"backup_path: {summary.backup_path or ''}")
    print(f"migration_committed: {'yes' if summary.committed else 'no'}")


def _file_warning_source(identity_channel_id: str, video_id: str, part: str, raw_value, norm_value: str) -> dict:
    return {
        "platform": PLATFORM,
        "channel_id": identity_channel_id,
        "video_id": video_id,
        "part": part,
        "raw_value": raw_value,
        "normalized_value": norm_value,
    }


def _source_json_text(value) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False)


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_migration_id() -> str:
    return f"{MIGRATION_ID_PREFIX}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


if __name__ == "__main__":
    raise SystemExit(main())
