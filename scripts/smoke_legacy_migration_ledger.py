import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, state_store


def main() -> int:
    _configure_stdio()
    _test_legacy_schema_migrations_foreign_shape_is_preserved()
    _test_legacy_phase2a_shape_is_preserved_not_used()
    _test_current_database_with_both_ledgers_uses_application_ledger()
    _test_application_ledger_name_mismatch_rejected()
    print("legacy migration ledger smoke tests passed")
    return 0


def _test_legacy_schema_migrations_foreign_shape_is_preserved() -> None:
    with TemporaryDirectory(prefix="legacy_ledger_foreign_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_core_database(db_path, schema_version=1)
        with closing(sqlite3.connect(db_path)) as conn:
            conn.executescript(
                """
                CREATE TABLE schema_migrations (
                    migration_id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    details TEXT NULL
                );
                CREATE TABLE import_warnings (
                    id INTEGER PRIMARY KEY,
                    migration_id TEXT NOT NULL,
                    warning TEXT NOT NULL
                );
                CREATE TABLE unknown_legacy_table (
                    id INTEGER PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(migration_id, applied_at, details) VALUES ('json_import', '2026-01-01T00:00:00+00:00', 'legacy')"
            )
            conn.execute("INSERT INTO import_warnings(id, migration_id, warning) VALUES (1, 'json_import', 'keep warning')")
            conn.execute("INSERT INTO unknown_legacy_table(id, value) VALUES (1, 'keep extra')")
            conn.commit()
        before = _legacy_snapshots(db_path)

        db_store.initialize_database(db_path)

        _assert_current_app_ledger(db_path)
        _assert(_legacy_snapshots(db_path) == before, "legacy foreign-shaped ledger tables were modified")


def _test_legacy_phase2a_shape_is_preserved_not_used() -> None:
    with TemporaryDirectory(prefix="legacy_ledger_phase2a_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_core_database(db_path, schema_version=2, with_current_indexes=True)
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (1, 'initial_schema', '2026-01-01T00:00:00+00:00')"
            )
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (99, 'legacy_extra', '2026-01-02T00:00:00+00:00')"
            )
            conn.commit()
        before = _legacy_schema_migrations_rows(db_path)

        db_store.initialize_database(db_path)

        _assert_current_app_ledger(db_path)
        _assert(_legacy_schema_migrations_rows(db_path) == before, "legacy phase2a-shaped ledger rows were modified")


def _test_current_database_with_both_ledgers_uses_application_ledger() -> None:
    with TemporaryDirectory(prefix="legacy_ledger_current_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        db_store.initialize_database(db_path)
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (42, 'legacy_only', '2026-01-01T00:00:00+00:00')"
            )
            conn.commit()
        _forget_initialized(db_path)
        backups_before = _backup_files(db_path)

        db_store.initialize_database(db_path)

        _assert_current_app_ledger(db_path)
        _assert(_legacy_schema_migrations_rows(db_path) == [(42, "legacy_only")], "current startup rewrote legacy schema_migrations")
        _assert(_backup_files(db_path) == backups_before, "current startup created a migration backup")


def _test_application_ledger_name_mismatch_rejected() -> None:
    with TemporaryDirectory(prefix="legacy_ledger_bad_app_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        db_store.initialize_database(db_path)
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                "UPDATE app_schema_migrations SET name = 'wrong_name' WHERE version = ?",
                (db_store.CURRENT_SCHEMA_VERSION,),
            )
            conn.commit()
        _forget_initialized(db_path)

        try:
            db_store.initialize_database(db_path)
        except db_store.DatabaseSchemaError:
            pass
        else:
            raise AssertionError("bad application migration name was silently accepted")

        _assert(
            dict(_app_migration_rows(db_path))[db_store.CURRENT_SCHEMA_VERSION] == "wrong_name",
            "bad application ledger name was silently repaired",
        )


def _create_core_database(
    db_path: Path,
    *,
    schema_version: int,
    with_current_indexes: bool = False,
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    now = "2026-01-01T00:00:00+00:00"
    with closing(sqlite3.connect(db_path)) as conn:
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
        if with_current_indexes:
            conn.execute("CREATE INDEX idx_download_items_channel ON download_items(channel_db_id)")
            conn.execute(
                """
                CREATE INDEX idx_download_items_channel_folder
                ON download_items(platform, channel_id, save_base_folder_norm)
                """
            )
            conn.execute("CREATE INDEX idx_download_files_path_norm ON download_files(path_norm)")
        conn.execute(
            "INSERT INTO app_meta(key, value, updated_at) VALUES ('schema_version', ?, ?)",
            (str(schema_version), now),
        )
        channel_id = conn.execute(
            """
            INSERT INTO channels(platform, channel_id, channel_name, save_base_folder_raw, save_base_folder_norm, created_at, updated_at)
            VALUES ('youtube', 'legacy', 'Legacy', 'D:/Legacy', 'd:/legacy', ?, ?)
            """,
            (now, now),
        ).lastrowid
        item_id = conn.execute(
            """
            INSERT INTO download_items(channel_db_id, platform, channel_id, video_id, save_base_folder_raw, save_base_folder_norm, sanitized_filename_base, status, updated_at, created_at)
            VALUES (?, 'youtube', 'legacy', 'legacy-video', 'D:/Legacy', 'd:/legacy', 'legacy-video', ?, ?, ?)
            """,
            (channel_id, state_store.STATUS_DOWNLOADED, now, now),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO download_files(item_id, part, status, filename_raw, filename_norm, path_raw, path_norm, created_at, updated_at)
            VALUES (?, 'video', ?, 'legacy-video.mp4', 'legacy-video.mp4', 'D:/Legacy/legacy-video.mp4', 'd:/legacy/legacy-video.mp4', ?, ?)
            """,
            (item_id, state_store.STATUS_DOWNLOADED, now, now),
        )
        conn.commit()


def _assert_current_app_ledger(db_path: Path) -> None:
    expected = [
        (version, db_store.APPLICATION_MIGRATION_NAMES[version])
        for version in range(db_store.LEGACY_SCHEMA_VERSION, db_store.CURRENT_SCHEMA_VERSION + 1)
    ]
    with closing(db_store.open_database_connection(db_path)) as conn:
        db_store.validate_database_schema(conn)
    _assert(_app_migration_rows(db_path) == expected, "application migration ledger is not current")


def _app_migration_rows(db_path: Path) -> list[tuple[int, str]]:
    with closing(sqlite3.connect(db_path)) as conn:
        return [
            (int(row[0]), str(row[1]))
            for row in conn.execute("SELECT version, name FROM app_schema_migrations ORDER BY version")
        ]


def _legacy_schema_migrations_rows(db_path: Path) -> list[tuple[int, str]]:
    with closing(sqlite3.connect(db_path)) as conn:
        return [
            (int(row[0]), str(row[1]))
            for row in conn.execute("SELECT version, name FROM schema_migrations ORDER BY version")
        ]


def _legacy_snapshots(db_path: Path) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {
            "schema_migrations_columns": [
                (row[1], row[2], row[3], row[5])
                for row in conn.execute("PRAGMA table_info(schema_migrations)")
            ],
            "schema_migrations_rows": [
                tuple(row)
                for row in conn.execute("SELECT migration_id, applied_at, details FROM schema_migrations ORDER BY migration_id")
            ],
            "import_warnings_rows": [
                tuple(row)
                for row in conn.execute("SELECT id, migration_id, warning FROM import_warnings ORDER BY id")
            ],
            "unknown_rows": [
                tuple(row)
                for row in conn.execute("SELECT id, value FROM unknown_legacy_table ORDER BY id")
            ],
        }


def _backup_files(db_path: Path) -> list[Path]:
    return sorted(db_path.parent.glob(f"{db_path.name}.pre-migration-v*-to-v*-*.bak"))


def _forget_initialized(db_path: Path) -> None:
    db_store._INITIALIZED_DATABASES.pop(db_path.resolve(strict=False), None)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
