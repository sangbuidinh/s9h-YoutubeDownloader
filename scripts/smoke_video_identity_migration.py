import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, state_store


PLATFORM = "youtube"
CHANNEL = "channel"
CHANNEL_ALT = "channel-alt"
NOW = "2026-01-01T00:00:00+00:00"


def main() -> int:
    _configure_stdio()
    _test_valid_full_unique_identity_index_metadata()
    _test_partial_identity_index_is_rejected()
    _test_wrong_table_identity_index_is_rejected()
    _test_non_unique_identity_index_is_rejected()
    _test_wrong_order_identity_index_is_rejected()
    _test_v3_without_duplicates_migrates_once()
    _test_complementary_parts_are_consolidated_and_archived()
    _test_manual_override_conflicts_are_deterministic()
    _test_file_metadata_selection()
    _test_missing_recorded_files_do_not_downgrade()
    _test_lowest_item_id_survives()
    _test_v4_migration_rolls_back_on_failure()
    _test_unique_video_identity_index()
    _test_legacy_tables_are_preserved()
    print("video identity migration smoke tests passed")
    return 0


def _test_valid_full_unique_identity_index_metadata() -> None:
    with TemporaryDirectory(prefix="identity_index_valid_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_valid_v4_database(db_path)
        metadata = _identity_index_metadata(db_path)

        _assert(metadata is not None, "valid identity index metadata missing")
        _assert(metadata.table_name == "download_items", "valid identity index has wrong table")
        _assert(metadata.columns == ("platform", "channel_id", "video_id"), "valid identity index has wrong columns")
        _assert(metadata.unique is True, "valid identity index is not unique")
        _assert(metadata.partial is False, "valid identity index is partial")
        _forget_initialized(db_path)
        db_store.initialize_database(db_path)


def _test_partial_identity_index_is_rejected() -> None:
    with TemporaryDirectory(prefix="identity_index_partial_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_valid_v4_database(db_path)
        before_rows = _table_rows(db_path, "download_items")
        before_ledger = _app_migration_rows(db_path)
        _replace_identity_index(
            db_path,
            """
            CREATE UNIQUE INDEX uq_download_items_video_identity
            ON download_items(platform, channel_id, video_id)
            WHERE video_id LIKE 'x%'
            """,
        )
        _insert_out_of_predicate_duplicate(db_path, "partial-risk")
        _assert(_item_count(db_path, CHANNEL, "partial-risk") == 2, "partial index did not permit out-of-predicate duplicate")

        _expect_validation_error(db_path, "partial identity index was accepted")

        metadata = _identity_index_metadata(db_path)
        _assert(metadata is not None and metadata.partial is True, "partial index was repaired or replaced")
        _assert(_table_rows(db_path, "download_items")[: len(before_rows)] == before_rows, "existing user rows changed")
        _assert(_app_migration_rows(db_path) == before_ledger, "validation changed migration ledger")


def _test_wrong_table_identity_index_is_rejected() -> None:
    with TemporaryDirectory(prefix="identity_index_wrong_table_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_valid_v4_database(db_path)
        _replace_identity_index(
            db_path,
            """
            CREATE UNIQUE INDEX uq_download_items_video_identity
            ON download_state_archive(platform, channel_id, video_id)
            """,
        )

        _expect_validation_error(db_path, "wrong-table identity index was accepted")

        metadata = _identity_index_metadata(db_path)
        _assert(metadata is not None and metadata.table_name == "download_state_archive", "wrong-table index was repaired")


def _test_non_unique_identity_index_is_rejected() -> None:
    with TemporaryDirectory(prefix="identity_index_nonunique_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_valid_v4_database(db_path)
        _replace_identity_index(
            db_path,
            """
            CREATE INDEX uq_download_items_video_identity
            ON download_items(platform, channel_id, video_id)
            """,
        )

        _expect_validation_error(db_path, "non-unique identity index was accepted")

        metadata = _identity_index_metadata(db_path)
        _assert(metadata is not None and metadata.unique is False, "non-unique index was repaired")


def _test_wrong_order_identity_index_is_rejected() -> None:
    with TemporaryDirectory(prefix="identity_index_wrong_order_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_valid_v4_database(db_path)
        _replace_identity_index(
            db_path,
            """
            CREATE UNIQUE INDEX uq_download_items_video_identity
            ON download_items(channel_id, platform, video_id)
            """,
        )

        _expect_validation_error(db_path, "wrong-order identity index was accepted")

        metadata = _identity_index_metadata(db_path)
        _assert(metadata is not None and metadata.columns == ("channel_id", "platform", "video_id"), "wrong-order index was repaired")


def _test_v3_without_duplicates_migrates_once() -> None:
    with TemporaryDirectory(prefix="identity_v3_clean_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v3_database(db_path)
        channel_id = _insert_channel_raw(db_path, CHANNEL, "D:/A")
        item_id = _insert_item_raw(
            db_path,
            channel_id,
            CHANNEL,
            "clean-video",
            "D:/A",
            status=state_store.STATUS_DOWNLOADED,
            sanitized="clean-video",
        )
        _insert_file_raw(
            db_path,
            item_id,
            "video",
            state_store.STATUS_DOWNLOADED,
            "clean-video.mp4",
            "D:/A/video/clean-video.mp4",
        )
        before_items = _table_rows(db_path, "download_items")
        before_files = _table_rows(db_path, "download_files")

        db_store.initialize_database(db_path)

        _assert(_schema_version(db_path) == 4, "clean v3 did not advance to v4")
        _assert(len(_backup_files(db_path)) == 1, "clean v3 migration did not create exactly one backup")
        _assert(_table_exists(db_path, db_store.DOWNLOAD_STATE_ARCHIVE_TABLE), "archive table missing")
        _assert(db_store.VIDEO_IDENTITY_INDEX in _index_names(db_path), "video identity index missing")
        _assert(_archive_count(db_path) == 0, "clean v3 migration archived rows without duplicates")
        _assert(_table_rows(db_path, "download_items") == before_items, "clean item row changed")
        _assert(_table_rows(db_path, "download_files") == before_files, "clean file row changed")

        backups = _backup_files(db_path)
        _forget_initialized(db_path)
        db_store.initialize_database(db_path)
        _assert(_backup_files(db_path) == backups, "second startup created another migration backup")


def _test_complementary_parts_are_consolidated_and_archived() -> None:
    with TemporaryDirectory(prefix="identity_parts_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v3_database(db_path)
        channel_a = _insert_channel_raw(db_path, CHANNEL, "D:/A")
        channel_b = _insert_channel_raw(db_path, CHANNEL, "D:/B")
        item_a = _insert_item_raw(db_path, channel_a, CHANNEL, "parts-video", "D:/A")
        item_b = _insert_item_raw(db_path, channel_b, CHANNEL, "parts-video", "D:/B")
        _insert_file_raw(db_path, item_a, "video", state_store.STATUS_DOWNLOADED, "a.mp4", "D:/A/a.mp4")
        _insert_file_raw(db_path, item_a, "thumb", state_store.STATUS_ERROR, "a.jpg", "D:/A/a.jpg")
        _insert_file_raw(db_path, item_b, "video", state_store.STATUS_ERROR, "b.mp4", "D:/B/b.mp4")
        _insert_file_raw(db_path, item_b, "thumb", state_store.STATUS_DOWNLOADED, "b.jpg", "D:/B/b.jpg")
        original_items = _table_rows(db_path, "download_items")
        original_files = _table_rows(db_path, "download_files")

        db_store.initialize_database(db_path)

        entry = db_store.get_video_entry(CHANNEL, "parts-video", path=db_path)
        _assert(_item_count(db_path, CHANNEL, "parts-video") == 1, "parts duplicate items remain")
        _assert(entry["video_status"] == state_store.STATUS_DOWNLOADED, "downloaded video part did not win")
        _assert(entry["thumb_status"] == state_store.STATUS_DOWNLOADED, "downloaded thumb part did not win")
        _assert(state_store.get_effective_status(entry) == state_store.STATUS_DOWNLOADED, "merged status is not downloaded")
        _assert(_archive_count(db_path, "download_item") == 2, "not all duplicate items were archived")
        _assert(_archive_count(db_path, "download_file") == 4, "not all duplicate file rows were archived")
        _assert_archive_payloads(db_path, "download_items", original_items)
        _assert_archive_payloads(db_path, "download_files", original_files)
        _assert(_duplicate_identity_count(db_path) == 0, "duplicate identity remains after parts migration")


def _test_manual_override_conflicts_are_deterministic() -> None:
    with TemporaryDirectory(prefix="identity_manual_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v3_database(db_path)
        channel_a = _insert_channel_raw(db_path, CHANNEL, "D:/A")
        channel_b = _insert_channel_raw(db_path, CHANNEL, "D:/B")
        _insert_item_raw(
            db_path,
            channel_a,
            CHANNEL,
            "manual-newer",
            "D:/A",
            manual_status=state_store.STATUS_DOWNLOADED,
            manual_override=1,
            updated_at="2026-01-01T00:00:00+00:00",
        )
        _insert_item_raw(
            db_path,
            channel_b,
            CHANNEL,
            "manual-newer",
            "D:/B",
            manual_status=state_store.STATUS_NOT_DOWNLOADED,
            manual_override=1,
            updated_at="2026-01-02T00:00:00+00:00",
        )

        channel_c = _insert_channel_raw(db_path, CHANNEL, "D:/C")
        channel_d = _insert_channel_raw(db_path, CHANNEL, "D:/D")
        _insert_item_raw(
            db_path,
            channel_c,
            CHANNEL,
            "manual-invalid",
            "D:/C",
            manual_status=state_store.STATUS_DOWNLOADED,
            manual_override=1,
            updated_at="2026-01-01T00:00:00+00:00",
        )
        _insert_item_raw(
            db_path,
            channel_d,
            CHANNEL,
            "manual-invalid",
            "D:/D",
            manual_status="invalid",
            manual_override=1,
            updated_at="2026-01-02T00:00:00+00:00",
        )

        db_store.initialize_database(db_path)

        newer = db_store.get_video_entry(CHANNEL, "manual-newer", path=db_path)
        invalid = db_store.get_video_entry(CHANNEL, "manual-invalid", path=db_path)
        _assert(newer["manual_override"] is True, "newer valid manual override missing")
        _assert(newer["manual_status"] == state_store.STATUS_NOT_DOWNLOADED, "newer valid manual override did not win")
        _assert(state_store.get_effective_status(newer) == state_store.STATUS_NOT_DOWNLOADED, "newer manual status not effective")
        _assert(invalid["manual_status"] == state_store.STATUS_DOWNLOADED, "older valid manual override did not win")
        _assert(state_store.get_effective_status(invalid) == state_store.STATUS_DOWNLOADED, "invalid manual data became active")
        _assert(_archive_payload_has(db_path, "manual_status", "invalid"), "invalid manual data was not archived")


def _test_file_metadata_selection() -> None:
    with TemporaryDirectory(prefix="identity_file_meta_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v3_database(db_path)
        item_valid = _insert_item_with_folder(db_path, "file-meta", "D:/A")
        item_missing = _insert_item_with_folder(db_path, "file-meta", "D:/B")
        item_error = _insert_item_with_folder(db_path, "file-meta", "D:/C")
        _insert_file_raw(
            db_path,
            item_valid,
            "video",
            state_store.STATUS_DOWNLOADED,
            "Good.MP4",
            "D:/A/Good.MP4",
            filename_norm="wrong",
            path_norm="wrong",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        _insert_file_raw(
            db_path,
            item_missing,
            "video",
            state_store.STATUS_DOWNLOADED,
            "Missing.MP4",
            None,
            updated_at="2026-01-02T00:00:00+00:00",
        )
        _insert_file_raw(
            db_path,
            item_error,
            "video",
            state_store.STATUS_ERROR,
            "Error.MP4",
            "D:/C/Error.MP4",
            updated_at="2026-01-03T00:00:00+00:00",
        )

        db_store.initialize_database(db_path)

        file_row = _file_row(db_path, CHANNEL, "file-meta", "video")
        _assert(file_row["status"] == state_store.STATUS_DOWNLOADED, "downloaded status did not beat error")
        _assert(file_row["filename_raw"] == "Good.MP4", "structurally valid downloaded metadata did not win")
        _assert(file_row["path_raw"] == "D:/A/Good.MP4", "selected path was not retained")
        _assert(file_row["filename_norm"] == "good.mp4", "filename_norm was not recomputed")
        _assert(file_row["path_norm"] == "d:/a/good.mp4", "path_norm was not recomputed")
        _assert(file_row["is_valid"] == 1, "selected complete metadata was not valid")


def _test_missing_recorded_files_do_not_downgrade() -> None:
    with TemporaryDirectory(prefix="identity_missing_paths_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        missing_path = Path(temp_dir) / "does-not-exist.mp4"
        _create_v3_database(db_path)
        item_a = _insert_item_with_folder(db_path, "missing-path", "D:/A")
        item_b = _insert_item_with_folder(db_path, "missing-path", "D:/B")
        _insert_file_raw(db_path, item_a, "video", state_store.STATUS_DOWNLOADED, "missing.mp4", str(missing_path))
        _insert_file_raw(db_path, item_b, "thumb", state_store.STATUS_DOWNLOADED, "missing.jpg", str(missing_path.with_suffix(".jpg")))

        db_store.initialize_database(db_path)

        entry = db_store.get_video_entry(CHANNEL, "missing-path", path=db_path)
        _assert(not missing_path.exists(), "migration created a missing recorded file")
        _assert(entry["video_status"] == state_store.STATUS_DOWNLOADED, "missing file path downgraded video status")
        _assert(entry["thumb_status"] == state_store.STATUS_DOWNLOADED, "missing file path downgraded thumb status")
        _assert(entry["video_path"] == str(missing_path), "missing historical path was not retained")


def _test_lowest_item_id_survives() -> None:
    with TemporaryDirectory(prefix="identity_survivor_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v3_database(db_path)
        low = _insert_item_with_folder(
            db_path,
            "survivor",
            "D:/A",
            status=state_store.STATUS_NOT_DOWNLOADED,
            updated_at="2026-01-01T00:00:00+00:00",
        )
        _insert_item_with_folder(
            db_path,
            "survivor",
            "D:/B",
            status=state_store.STATUS_DOWNLOADED,
            manual_status=state_store.STATUS_DOWNLOADED,
            manual_override=1,
            updated_at="2026-01-03T00:00:00+00:00",
        )
        _insert_item_with_folder(
            db_path,
            "survivor",
            "D:/C",
            status=state_store.STATUS_ERROR,
            updated_at="2026-01-02T00:00:00+00:00",
        )

        db_store.initialize_database(db_path)

        survivor_id = _single_item_id(db_path, CHANNEL, "survivor")
        entry = db_store.get_video_entry(CHANNEL, "survivor", path=db_path)
        _assert(survivor_id == low, "lowest item ID did not survive")
        _assert(entry["manual_status"] == state_store.STATUS_DOWNLOADED, "merged logical data was not retained")


def _test_v4_migration_rolls_back_on_failure() -> None:
    with TemporaryDirectory(prefix="identity_rollback_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v3_database(db_path)
        _insert_item_with_folder(db_path, "rollback", "D:/A")
        _insert_item_with_folder(db_path, "rollback", "D:/B")
        original_apply = db_store._apply_duplicate_video_merge_plan
        try:
            def failing_apply(_conn, _plan):
                raise RuntimeError("injected v4 failure")

            db_store._apply_duplicate_video_merge_plan = failing_apply
            try:
                db_store.initialize_database(db_path)
            except db_store.DatabaseMigrationError:
                pass
            else:
                raise AssertionError("injected v4 migration failure did not propagate")
        finally:
            db_store._apply_duplicate_video_merge_plan = original_apply

        _assert(_schema_version(db_path) == 3, "failed v4 migration rewrote schema version")
        _assert(_item_count(db_path, CHANNEL, "rollback") == 2, "failed v4 migration deleted originals")
        _assert(_archive_count(db_path) == 0, "failed v4 migration left archive rows")
        _assert(db_store.VIDEO_IDENTITY_INDEX not in _index_names(db_path), "failed v4 migration left unique index")
        _assert(4 not in dict(_app_migration_rows(db_path)), "failed v4 migration recorded ledger row")
        _assert(len(_backup_files(db_path)) == 1, "failed v4 migration did not create backup")


def _test_unique_video_identity_index() -> None:
    with TemporaryDirectory(prefix="identity_unique_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v3_database(db_path)
        channel_a = _insert_channel_raw(db_path, CHANNEL, "D:/A")
        _insert_item_raw(db_path, channel_a, CHANNEL, "unique-video", "D:/A")
        db_store.initialize_database(db_path)

        with closing(sqlite3.connect(db_path)) as conn:
            channel_b = _insert_channel(conn, CHANNEL, "D:/B")
            try:
                _insert_item(conn, channel_b, CHANNEL, "unique-video", "D:/B")
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError("duplicate video identity insert succeeded")
            _insert_item(conn, channel_b, CHANNEL, "other-video", "D:/B")
            channel_c = _insert_channel(conn, CHANNEL_ALT, "D:/B")
            _insert_item(conn, channel_c, CHANNEL_ALT, "unique-video", "D:/B")
            conn.commit()

        _assert(_item_count(db_path, CHANNEL, "unique-video") == 1, "duplicate insert changed original row")
        _assert(_item_count(db_path, CHANNEL, "other-video") == 1, "different video insert failed")
        _assert(_item_count(db_path, CHANNEL_ALT, "unique-video") == 1, "different channel insert failed")


def _test_legacy_tables_are_preserved() -> None:
    with TemporaryDirectory(prefix="identity_legacy_tables_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v3_database(db_path, with_legacy_tables=True)
        before = _legacy_snapshots(db_path)

        db_store.initialize_database(db_path)

        _assert(_legacy_snapshots(db_path) == before, "legacy or unknown tables were modified")
        _assert(_schema_version(db_path) == 4, "legacy preservation database did not migrate")


def _create_v3_database(db_path: Path, *, with_legacy_tables: bool = False) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE app_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
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
            CREATE INDEX idx_download_items_channel ON download_items(channel_db_id);
            CREATE INDEX idx_download_items_channel_folder ON download_items(platform, channel_id, save_base_folder_norm);
            CREATE INDEX idx_download_files_path_norm ON download_files(path_norm);
            """
        )
        conn.execute(
            "INSERT INTO app_meta(key, value, updated_at) VALUES (?, ?, ?)",
            (db_store.SCHEMA_VERSION_KEY, "3", NOW),
        )
        for version in (1, 2, 3):
            conn.execute(
                "INSERT INTO app_schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (version, db_store.APPLICATION_MIGRATION_NAMES[version], NOW),
            )
        if with_legacy_tables:
            conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            conn.execute("INSERT INTO schema_migrations(version, name) VALUES (99, 'legacy-only')")
            conn.execute("CREATE TABLE import_warnings(id INTEGER PRIMARY KEY, message TEXT NOT NULL)")
            conn.execute("INSERT INTO import_warnings(id, message) VALUES (1, 'keep')")
            conn.execute("CREATE TABLE unknown_extra(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("INSERT INTO unknown_extra(id, value) VALUES (1, 'keep-extra')")
        conn.commit()


def _create_valid_v4_database(db_path: Path) -> None:
    db_store.initialize_database(db_path)
    channel_id = _insert_channel_raw(db_path, CHANNEL, "D:/Valid")
    _insert_item_raw(
        db_path,
        channel_id,
        CHANNEL,
        "valid-video",
        "D:/Valid",
        status=state_store.STATUS_NOT_DOWNLOADED,
        sanitized="valid-video",
    )
    _forget_initialized(db_path)


def _replace_identity_index(db_path: Path, create_sql: str) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(f"DROP INDEX {db_store.VIDEO_IDENTITY_INDEX}")
        conn.execute(create_sql)
        conn.commit()
    _forget_initialized(db_path)


def _insert_out_of_predicate_duplicate(db_path: Path, video_id: str) -> None:
    channel_a = _insert_channel_raw(db_path, CHANNEL, "D:/RiskA")
    channel_b = _insert_channel_raw(db_path, CHANNEL, "D:/RiskB")
    _insert_item_raw(db_path, channel_a, CHANNEL, video_id, "D:/RiskA")
    _insert_item_raw(db_path, channel_b, CHANNEL, video_id, "D:/RiskB")


def _expect_validation_error(db_path: Path, message: str) -> None:
    _forget_initialized(db_path)
    try:
        db_store.initialize_database(db_path)
    except db_store.DatabaseValidationError:
        pass
    else:
        raise AssertionError(message)
    _assert(db_path.resolve(strict=False) not in db_store._INITIALIZED_DATABASES, "invalid schema was cached as initialized")


def _identity_index_metadata(db_path: Path):
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return db_store._get_index_metadata(conn, db_store.VIDEO_IDENTITY_INDEX)


def _insert_item_with_folder(
    db_path: Path,
    video_id: str,
    folder: str,
    **kwargs,
) -> int:
    channel_id = _insert_channel_raw(db_path, CHANNEL, folder)
    return _insert_item_raw(db_path, channel_id, CHANNEL, video_id, folder, **kwargs)


def _insert_channel_raw(db_path: Path, channel_id: str, folder: str) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        row_id = _insert_channel(conn, channel_id, folder)
        conn.commit()
        return row_id


def _insert_channel(conn: sqlite3.Connection, channel_id: str, folder: str) -> int:
    return conn.execute(
        """
        INSERT INTO channels(platform, channel_id, channel_name, save_base_folder_raw, save_base_folder_norm, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (PLATFORM, channel_id, f"Channel {channel_id}", folder, _norm(folder), NOW, NOW),
    ).lastrowid


def _insert_item_raw(
    db_path: Path,
    channel_db_id: int,
    channel_id: str,
    video_id: str,
    folder: str,
    *,
    status: str | None = None,
    manual_status: str | None = None,
    manual_override: int | None = None,
    sanitized: str | None = None,
    updated_at: str = NOW,
) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        row_id = _insert_item(
            conn,
            channel_db_id,
            channel_id,
            video_id,
            folder,
            status=status,
            manual_status=manual_status,
            manual_override=manual_override,
            sanitized=sanitized,
            updated_at=updated_at,
        )
        conn.commit()
        return row_id


def _insert_item(
    conn: sqlite3.Connection,
    channel_db_id: int,
    channel_id: str,
    video_id: str,
    folder: str,
    *,
    status: str | None = None,
    manual_status: str | None = None,
    manual_override: int | None = None,
    sanitized: str | None = None,
    updated_at: str = NOW,
) -> int:
    return conn.execute(
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
            updated_at,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            channel_db_id,
            PLATFORM,
            channel_id,
            video_id,
            folder,
            _norm(folder),
            f"Title {video_id}",
            sanitized or video_id,
            1,
            status,
            manual_status,
            manual_override,
            updated_at,
            NOW,
        ),
    ).lastrowid


def _insert_file_raw(
    db_path: Path,
    item_id: int,
    part: str,
    status: str | None,
    filename: str | None,
    path: str | None,
    *,
    filename_norm: str | None = None,
    path_norm: str | None = None,
    is_valid: int | None = None,
    updated_at: str = NOW,
) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        row_id = conn.execute(
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
                status,
                filename,
                filename_norm if filename_norm is not None else (filename.casefold() if filename else None),
                path,
                path_norm if path_norm is not None else (_norm(path) if path else None),
                is_valid if is_valid is not None else (1 if filename and path else 0),
                None if filename and path else "missing_filename" if not filename else "missing_path",
                NOW,
                updated_at,
            ),
        ).lastrowid
        conn.commit()
        return row_id


def _table_rows(db_path: Path, table_name: str) -> list[dict]:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in conn.execute(f"SELECT * FROM {db_store.quote_identifier(table_name)} ORDER BY id")
        ]


def _assert_archive_payloads(db_path: Path, source_table: str, originals: list[dict]) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            f"""
            SELECT payload_json
            FROM {db_store.DOWNLOAD_STATE_ARCHIVE_TABLE}
            WHERE source_table = ?
            ORDER BY source_row_id
            """,
            (source_table,),
        ).fetchall()
    archived = [json.loads(row[0]) for row in rows]
    _assert(archived == originals, f"archive payloads do not match originals for {source_table}")


def _archive_payload_has(db_path: Path, key: str, value) -> bool:
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(f"SELECT payload_json FROM {db_store.DOWNLOAD_STATE_ARCHIVE_TABLE}").fetchall()
    return any(json.loads(row[0]).get(key) == value for row in rows)


def _file_row(db_path: Path, channel_id: str, video_id: str, part: str) -> sqlite3.Row:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT df.*
            FROM download_files df
            JOIN download_items di ON di.id = df.item_id
            WHERE di.channel_id = ? AND di.video_id = ? AND df.part = ?
            """,
            (channel_id, video_id, part),
        ).fetchone()
    _assert(row is not None, f"missing file row for {video_id}/{part}")
    return row


def _item_count(db_path: Path, channel_id: str, video_id: str) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM download_items WHERE platform = ? AND channel_id = ? AND video_id = ?",
            (PLATFORM, channel_id, video_id),
        ).fetchone()[0]


def _single_item_id(db_path: Path, channel_id: str, video_id: str) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id FROM download_items WHERE platform = ? AND channel_id = ? AND video_id = ?",
            (PLATFORM, channel_id, video_id),
        ).fetchall()
    _assert(len(rows) == 1, f"expected one item row for {video_id}, got {len(rows)}")
    return int(rows[0][0])


def _duplicate_identity_count(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT 1
                FROM download_items
                GROUP BY platform, channel_id, video_id
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]


def _archive_count(db_path: Path, entity_type: str | None = None) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        if not _table_exists_conn(conn, db_store.DOWNLOAD_STATE_ARCHIVE_TABLE):
            return 0
        if entity_type is None:
            return conn.execute(f"SELECT COUNT(*) FROM {db_store.DOWNLOAD_STATE_ARCHIVE_TABLE}").fetchone()[0]
        return conn.execute(
            f"SELECT COUNT(*) FROM {db_store.DOWNLOAD_STATE_ARCHIVE_TABLE} WHERE entity_type = ?",
            (entity_type,),
        ).fetchone()[0]


def _schema_version(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return int(conn.execute("SELECT value FROM app_meta WHERE key = ?", (db_store.SCHEMA_VERSION_KEY,)).fetchone()[0])


def _app_migration_rows(db_path: Path) -> list[tuple[int, str]]:
    with closing(sqlite3.connect(db_path)) as conn:
        return [
            (int(row[0]), str(row[1]))
            for row in conn.execute("SELECT version, name FROM app_schema_migrations ORDER BY version")
        ]


def _legacy_snapshots(db_path: Path) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {
            "schema_migrations": list(conn.execute("SELECT version, name FROM schema_migrations ORDER BY version")),
            "import_warnings": list(conn.execute("SELECT id, message FROM import_warnings ORDER BY id")),
            "unknown_extra": list(conn.execute("SELECT id, value FROM unknown_extra ORDER BY id")),
        }


def _table_exists(db_path: Path, table_name: str) -> bool:
    with closing(sqlite3.connect(db_path)) as conn:
        return _table_exists_conn(conn, table_name)


def _table_exists_conn(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _index_names(db_path: Path) -> set[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}


def _backup_files(db_path: Path) -> list[Path]:
    return sorted(db_path.parent.glob(f"{db_path.name}.pre-migration-v*-to-v*-*.bak"))


def _forget_initialized(db_path: Path) -> None:
    db_store._INITIALIZED_DATABASES.pop(db_path.resolve(strict=False), None)


def _norm(value) -> str:
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
