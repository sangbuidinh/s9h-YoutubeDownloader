import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, state_store


def main() -> int:
    _configure_stdio()
    _test_versioned_v1_migrates_sequentially_with_backup()
    _test_migration_failure_rolls_back_and_retries()
    _test_newer_database_rejected_without_modification()
    _test_unversioned_legacy_migrates()
    _test_ambiguous_unversioned_schema_rejected()
    _test_legacy_and_unknown_tables_preserved()
    _test_application_ledger_name_mismatch_rejected()
    print("schema migration smoke tests passed")
    return 0


def _test_versioned_v1_migrates_sequentially_with_backup() -> None:
    with TemporaryDirectory(prefix="schema_migrate_v1_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v1_database(db_path, schema_version=1, with_ledger=True)

        result = db_store.initialize_database(db_path)

        _assert(result == db_path.resolve(strict=False), "migration returned wrong normalized path")
        _assert_current_version(db_path)
        _assert_legacy_rows_survive(db_path)
        _assert_app_migrations_current(db_path)
        _assert(_legacy_migration_rows(db_path) == [(1, "initial_schema")], "legacy schema_migrations row was changed")
        _assert(len(_backup_files(db_path)) == 1, "version-changing migration did not create exactly one backup")
        _assert("idx_download_items_identity" not in _index_names(db_path), "obsolete item index survived migration")
        _assert("idx_download_files_item_part" not in _index_names(db_path), "obsolete file index survived migration")
        _assert(db_store.VIDEO_IDENTITY_INDEX in _index_names(db_path), "video identity index missing after migration")
        _assert(_table_exists_path(db_path, db_store.DOWNLOAD_STATE_ARCHIVE_TABLE), "archive table missing after migration")


def _test_migration_failure_rolls_back_and_retries() -> None:
    with TemporaryDirectory(prefix="schema_migrate_fail_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v1_database(db_path, schema_version=1, with_ledger=True)
        original = db_store.MIGRATIONS_BY_VERSION[db_store.CURRENT_SCHEMA_VERSION]
        try:
            def failing_migration(conn: sqlite3.Connection) -> None:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS import_warnings (
                        id INTEGER PRIMARY KEY,
                        migration_id TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        warning_code TEXT NOT NULL,
                        message TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                raise RuntimeError("mid-migration failure")

            db_store.MIGRATIONS_BY_VERSION[db_store.CURRENT_SCHEMA_VERSION] = db_store.Migration(
                db_store.CURRENT_SCHEMA_VERSION,
                "failing_v2",
                failing_migration,
            )
            try:
                db_store.initialize_database(db_path)
            except db_store.DatabaseMigrationError:
                pass
            else:
                raise AssertionError("migration failure was not reported")
        finally:
            db_store.MIGRATIONS_BY_VERSION[db_store.CURRENT_SCHEMA_VERSION] = original

        _assert(_schema_version_raw(db_path) == 1, "failed migration rewrote schema_version")
        _assert(not _app_migration_rows(db_path), "failed migration left application migration ledger rows")
        _assert(_legacy_migration_rows(db_path) == [(1, "initial_schema")], "failed migration changed legacy schema_migrations")
        _assert_legacy_rows_survive(db_path)
        _assert(len(_backup_files(db_path)) == 1, "failed migration did not leave a safety backup")
        _assert(db_path.resolve(strict=False) not in db_store._INITIALIZED_DATABASES, "failed migration was cached")

        db_store.initialize_database(db_path)
        _assert_current_version(db_path)
        _assert_legacy_rows_survive(db_path)
        _assert_app_migrations_current(db_path)


def _test_newer_database_rejected_without_modification() -> None:
    with TemporaryDirectory(prefix="schema_newer_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_metadata_only_database(db_path, db_store.CURRENT_SCHEMA_VERSION + 1)
        before = _sqlite_master_snapshot(db_path)
        recorder = _SqlRecorder()
        with _patched_sqlite_connect(recorder):
            try:
                db_store.initialize_database(db_path)
            except db_store.DatabaseTooNewError:
                pass
            else:
                raise AssertionError("newer database was not rejected")

        _assert(_schema_version_raw(db_path) == db_store.CURRENT_SCHEMA_VERSION + 1, "newer version was rewritten")
        _assert(_sqlite_master_snapshot(db_path) == before, "newer database schema was modified")
        _assert(not _backup_files(db_path), "newer database created a migration backup")
        _assert(not _ddl_statements(recorder.statements), f"newer database path ran DDL: {recorder.statements}")


def _test_unversioned_legacy_migrates() -> None:
    with TemporaryDirectory(prefix="schema_unversioned_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v1_database(db_path, schema_version=None, with_ledger=False)

        db_store.initialize_database(db_path)

        _assert_current_version(db_path)
        _assert_legacy_rows_survive(db_path)
        _assert_app_migrations_current(db_path)


def _test_ambiguous_unversioned_schema_rejected() -> None:
    with TemporaryDirectory(prefix="schema_ambiguous_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("CREATE TABLE mystery(id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO mystery(value) VALUES ('keep')")
            conn.commit()
        before = _sqlite_master_snapshot(db_path)

        try:
            db_store.initialize_database(db_path)
        except db_store.DatabaseSchemaError:
            pass
        else:
            raise AssertionError("ambiguous unversioned schema was silently upgraded")

        _assert(_sqlite_master_snapshot(db_path) == before, "ambiguous schema was modified")
        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute("SELECT value FROM mystery WHERE id = 1").fetchone()
            app_meta_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'app_meta'"
            ).fetchone()
        _assert(row and row[0] == "keep", "ambiguous schema row was changed")
        _assert(app_meta_exists is None, "ambiguous schema received app_meta")


def _test_legacy_and_unknown_tables_preserved() -> None:
    with TemporaryDirectory(prefix="schema_preserve_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v1_database(
            db_path,
            schema_version=1,
            with_ledger=True,
            with_import_warnings=True,
            with_unknown_table=True,
        )

        db_store.initialize_database(db_path)

        _assert_current_version(db_path)
        with closing(sqlite3.connect(db_path)) as conn:
            warning = conn.execute(
                "SELECT migration_id, warning_code, message FROM import_warnings WHERE id = 1"
            ).fetchone()
            extra = conn.execute("SELECT value FROM unknown_legacy_table WHERE id = 1").fetchone()
            initial = conn.execute("SELECT name FROM schema_migrations WHERE version = 1").fetchone()
        _assert(tuple(warning) == ("json_to_sqlite_test", "legacy_warning", "preserve me"), "import_warnings row was not preserved")
        _assert(extra and extra[0] == "unknown row", "unknown legacy table row was not preserved")
        _assert(initial and initial[0] == "initial_schema", "existing schema_migrations row was not preserved")
        _assert_app_migrations_current(db_path)


def _test_application_ledger_name_mismatch_rejected() -> None:
    with TemporaryDirectory(prefix="schema_disagree_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        db_store.initialize_database(db_path)
        _forget_initialized(db_path)
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                "UPDATE app_schema_migrations SET name = 'wrong_name' WHERE version = ?",
                (db_store.CURRENT_SCHEMA_VERSION,),
            )
            conn.commit()

        try:
            db_store.initialize_database(db_path)
        except db_store.DatabaseSchemaError:
            pass
        else:
            raise AssertionError("metadata/ledger disagreement was silently repaired")

        _assert(_schema_version_raw(db_path) == db_store.CURRENT_SCHEMA_VERSION, "ledger failure rewrote schema version")
        _assert(dict(_app_migration_rows(db_path))[db_store.CURRENT_SCHEMA_VERSION] == "wrong_name", "ledger mismatch was silently rewritten")


def _create_v1_database(
    db_path: Path,
    *,
    schema_version: int | None,
    with_ledger: bool,
    with_import_warnings: bool = False,
    with_unknown_table: bool = False,
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
            CREATE INDEX idx_download_items_identity
            ON download_items(platform, channel_id, video_id, save_base_folder_norm);
            CREATE INDEX idx_download_files_item_part
            ON download_files(item_id, part);
            """
        )
        if schema_version is not None:
            conn.execute(
                "INSERT INTO app_meta(key, value, updated_at) VALUES ('schema_version', ?, ?)",
                (str(schema_version), now),
            )
        if with_ledger:
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
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (1, 'initial_schema', ?)",
                (now,),
            )
        if with_import_warnings:
            conn.execute(
                """
                CREATE TABLE import_warnings (
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
                )
                """
            )
            conn.execute(
                """
                INSERT INTO import_warnings(
                    id,
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
                VALUES (1, 'json_to_sqlite_test', 'warning', 'legacy_warning', 'youtube', 'legacy', 'legacy-video', 'd:/legacy', 'video', 'preserve me', '{}', ?)
                """,
                (now,),
            )
        if with_unknown_table:
            conn.execute("CREATE TABLE unknown_legacy_table(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("INSERT INTO unknown_legacy_table(id, value) VALUES (1, 'unknown row')")

        channel_id = conn.execute(
            """
            INSERT INTO channels(platform, channel_id, channel_name, save_base_folder_raw, save_base_folder_norm, created_at, updated_at)
            VALUES ('youtube', 'legacy', 'Legacy', 'D:/Legacy', 'd:/legacy', ?, ?)
            """,
            (now, now),
        ).lastrowid
        item_id = conn.execute(
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
                status,
                updated_at,
                created_at
            )
            VALUES (?, 'youtube', 'legacy', 'legacy-video', 'D:/Legacy', 'd:/legacy', 'Legacy Video', 'legacy-video', ?, ?, ?)
            """,
            (channel_id, state_store.STATUS_DOWNLOADED, now, now),
        ).lastrowid
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
                created_at,
                updated_at
            )
            VALUES (?, 'video', ?, 'legacy-video.mp4', 'legacy-video.mp4', 'D:/Legacy/video/legacy-video.mp4', 'd:/legacy/video/legacy-video.mp4', ?, ?)
            """,
            (item_id, state_store.STATUS_DOWNLOADED, now, now),
        )
        conn.commit()


def _create_metadata_only_database(db_path: Path, version: int) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE app_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO app_meta(key, value, updated_at) VALUES ('schema_version', ?, '2026-01-01T00:00:00+00:00')",
            (str(version),),
        )
        conn.execute("CREATE TABLE keep_me(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO keep_me(id, value) VALUES (1, 'newer')")
        conn.commit()


def _assert_current_version(db_path: Path) -> None:
    with closing(db_store.open_database_connection(db_path)) as conn:
        db_store.validate_database_schema(conn)
        version = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()[0]
    _assert(version == str(db_store.CURRENT_SCHEMA_VERSION), f"schema version is not current: {version}")


def _assert_legacy_rows_survive(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        item = conn.execute(
            "SELECT status, sanitized_filename_base FROM download_items WHERE channel_id = 'legacy' AND video_id = 'legacy-video'"
        ).fetchone()
        file_row = conn.execute(
            """
            SELECT df.status, df.filename_raw
            FROM download_files df
            JOIN download_items di ON di.id = df.item_id
            WHERE di.channel_id = 'legacy' AND di.video_id = 'legacy-video' AND df.part = 'video'
            """
        ).fetchone()
    _assert(item and item[0] == state_store.STATUS_DOWNLOADED and item[1] == "legacy-video", "legacy item row did not survive")
    _assert(file_row and file_row[0] == state_store.STATUS_DOWNLOADED and file_row[1] == "legacy-video.mp4", "legacy file row did not survive")


def _schema_version_raw(db_path: Path) -> int | None:
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
    return int(row[0]) if row else None


def _assert_app_migrations_current(db_path: Path) -> None:
    expected = [
        (version, db_store.APPLICATION_MIGRATION_NAMES[version])
        for version in range(db_store.LEGACY_SCHEMA_VERSION, db_store.CURRENT_SCHEMA_VERSION + 1)
    ]
    _assert(_app_migration_rows(db_path) == expected, "application migration rows were not sequential")


def _app_migration_rows(db_path: Path) -> list[tuple[int, str]]:
    with closing(sqlite3.connect(db_path)) as conn:
        if not _table_exists(conn, db_store.APP_MIGRATION_LEDGER_TABLE):
            return []
        return [
            (int(row[0]), str(row[1]))
            for row in conn.execute(
                f"SELECT version, name FROM {db_store.APP_MIGRATION_LEDGER_TABLE} ORDER BY version"
            )
        ]


def _legacy_migration_rows(db_path: Path) -> list[tuple[int, str]]:
    with closing(sqlite3.connect(db_path)) as conn:
        if not _table_exists(conn, "schema_migrations"):
            return []
        return [(int(row[0]), str(row[1])) for row in conn.execute("SELECT version, name FROM schema_migrations ORDER BY version")]


def _forget_initialized(db_path: Path) -> None:
    db_store._INITIALIZED_DATABASES.pop(db_path.resolve(strict=False), None)


def _index_names(db_path: Path) -> set[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}


def _sqlite_master_snapshot(db_path: Path) -> list[tuple[str, str, str | None]]:
    with closing(sqlite3.connect(db_path)) as conn:
        return [
            (str(row[0]), str(row[1]), row[2])
            for row in conn.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE type IN ('table', 'index') ORDER BY type, name"
            )
        ]


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _table_exists_path(db_path: Path, table_name: str) -> bool:
    with closing(sqlite3.connect(db_path)) as conn:
        return _table_exists(conn, table_name)


def _backup_files(db_path: Path) -> list[Path]:
    return sorted(db_path.parent.glob(f"{db_path.name}.pre-migration-v*-to-v*-*.bak"))


def _ddl_statements(statements: list[str]) -> list[str]:
    markers = ("CREATE TABLE", "CREATE INDEX", "DROP INDEX", "ALTER TABLE", "PRAGMA JOURNAL_MODE")
    offenders = []
    for statement in statements:
        normalized = " ".join(statement.split()).upper()
        if any(marker in normalized for marker in markers):
            offenders.append(statement)
    return offenders


@contextmanager
def _patched_sqlite_connect(recorder):
    original = db_store.sqlite3.connect
    try:
        recorder.set_connect(original)
        db_store.sqlite3.connect = recorder.connect
        yield
    finally:
        db_store.sqlite3.connect = original


class _SqlRecorder:
    def __init__(self) -> None:
        self.statements = []
        self._connect = None

    def set_connect(self, connect) -> None:
        self._connect = connect

    def connect(self, *args, **kwargs):
        conn = self._connect(*args, **kwargs)
        conn.set_trace_callback(lambda statement: self.statements.append(statement))
        return conn


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
