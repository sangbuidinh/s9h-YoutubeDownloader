import runpy
import sqlite3
import sys
import threading
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, state_store


DDL_MARKERS = (
    "CREATE TABLE",
    "CREATE INDEX",
    "DROP INDEX",
    "ALTER TABLE",
    "PRAGMA JOURNAL_MODE",
)


def main() -> int:
    _configure_stdio()
    _test_new_database_schema_and_cached_initialization()
    _test_multithreaded_initialization_once()
    _test_different_database_paths_are_independent()
    _test_failed_initialization_can_retry()
    _test_current_database_restart_does_not_migrate()
    _test_hot_path_operations_do_not_run_ddl()
    _test_connection_settings_and_concurrent_writes()
    _test_transaction_rollback_closes_cleanly()
    _test_startup_failure_blocks_ui_import()
    print("database lifecycle smoke tests passed")
    return 0


def _test_new_database_schema_and_cached_initialization() -> None:
    with TemporaryDirectory(prefix="db_lifecycle_new_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        calls = []
        with _patched_bootstrap_counter(calls):
            first = db_store.initialize_database(db_path)
            second = db_store.initialize_database(db_path)

        _assert(first == db_path.resolve(strict=False), "new DB init returned wrong normalized path")
        _assert(second == first, "cached init returned a different path")
        _assert(calls == [first], f"bootstrap did not run exactly once: {calls}")
        _assert(not _backup_files(db_path), "new DB initialization created a migration backup")
        _assert_current_schema(db_path)

        with closing(db_store.open_database_connection(db_path)) as conn:
            fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
            version = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()[0]
            ledger = [row[0] for row in conn.execute("SELECT version FROM app_schema_migrations ORDER BY version")]

        _assert(version == str(db_store.CURRENT_SCHEMA_VERSION), "schema version was not current")
        _assert(
            ledger == list(range(db_store.LEGACY_SCHEMA_VERSION, db_store.CURRENT_SCHEMA_VERSION + 1)),
            f"migration ledger was inconsistent: {ledger}",
        )
        _assert(not fk_rows, f"foreign_key_check returned rows: {fk_rows}")


def _test_multithreaded_initialization_once() -> None:
    with TemporaryDirectory(prefix="db_lifecycle_threads_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        calls = []
        results = []
        errors = []
        with _patched_bootstrap_counter(calls):
            threads = [
                threading.Thread(
                    target=lambda: _threaded_initialize(db_path, results, errors),
                    daemon=False,
                )
                for _ in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        _assert(not errors, f"threaded init raised errors: {errors}")
        _assert(len(results) == len(threads), "not every init thread returned")
        _assert(len(set(results)) == 1, f"threads returned different paths: {results}")
        _assert(calls == [db_path.resolve(strict=False)], f"bootstrap raced more than once: {calls}")
        _assert_current_schema(db_path)
        with closing(db_store.open_database_connection(db_path)) as conn:
            rows = conn.execute("SELECT version, COUNT(*) FROM app_schema_migrations GROUP BY version").fetchall()
        _assert(all(row[1] == 1 for row in rows), f"duplicate migration rows found: {rows}")


def _test_different_database_paths_are_independent() -> None:
    with TemporaryDirectory(prefix="db_lifecycle_paths_") as temp_dir:
        path_a = Path(temp_dir) / "a.sqlite3"
        path_b = Path(temp_dir) / "nested" / "b.sqlite3"
        calls = []
        with _patched_bootstrap_counter(calls):
            db_store.initialize_database(path_a)
            db_store.initialize_database(path_b)
            db_store.initialize_database(path_a)

        _assert(calls == [path_a.resolve(strict=False), path_b.resolve(strict=False)], f"path cache leaked: {calls}")
        _assert_current_schema(path_a)
        _assert_current_schema(path_b)


def _test_failed_initialization_can_retry() -> None:
    with TemporaryDirectory(prefix="db_lifecycle_retry_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_v1_database(db_path)
        original = db_store.MIGRATIONS_BY_VERSION[db_store.CURRENT_SCHEMA_VERSION]
        try:
            db_store.MIGRATIONS_BY_VERSION[db_store.CURRENT_SCHEMA_VERSION] = db_store.Migration(
                db_store.CURRENT_SCHEMA_VERSION,
                "injected_failure",
                lambda conn: (_ for _ in ()).throw(RuntimeError("injected migration failure")),
            )
            try:
                db_store.initialize_database(db_path)
            except db_store.DatabaseMigrationError:
                pass
            else:
                raise AssertionError("injected migration failure did not propagate")
        finally:
            db_store.MIGRATIONS_BY_VERSION[db_store.CURRENT_SCHEMA_VERSION] = original

        normalized = db_path.resolve(strict=False)
        _assert(normalized not in db_store._INITIALIZED_DATABASES, "failed initialization was cached as success")
        _assert(_schema_version_raw(db_path) == 1, "failed migration wrote the current schema version")
        db_store.initialize_database(db_path)
        _assert_current_schema(db_path)


def _test_current_database_restart_does_not_migrate() -> None:
    with TemporaryDirectory(prefix="db_lifecycle_current_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        db_store.initialize_database(db_path)
        db_store.update_video_state(
            "channel",
            "current-video",
            {
                "channel_name": "Channel",
                "sanitized_filename_base": "current-video",
                "status": state_store.STATUS_NOT_DOWNLOADED,
            },
            path=db_path,
            save_base_folder="D:/Out",
        )
        backups_before = _backup_files(db_path)
        _forget_initialized(db_path)
        recorder = _SqlRecorder()
        with _patched_sqlite_connect(recorder):
            db_store.initialize_database(db_path)

        _assert(not _statements_with_markers(recorder.statements, ("CREATE TABLE", "CREATE INDEX", "DROP INDEX", "ALTER TABLE")), "current DB startup ran DDL")
        _assert(_backup_files(db_path) == backups_before, "current DB startup created a migration backup")
        entry = db_store.get_video_entry("channel", "current-video", path=db_path)
        _assert(entry and entry["video_id"] == "current-video", "current DB data was not preserved")


def _test_hot_path_operations_do_not_run_ddl() -> None:
    with TemporaryDirectory(prefix="db_lifecycle_hot_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        db_store.initialize_database(db_path)
        recorder = _SqlRecorder()
        with _patched_sqlite_connect(recorder):
            db_store.update_video_state(
                "hot-channel",
                "hot-video",
                {
                    "channel_name": "Hot Channel",
                    "sanitized_filename_base": "hot-video",
                    "status": state_store.STATUS_NOT_DOWNLOADED,
                },
                path=db_path,
                save_base_folder="D:/Hot",
            )
            db_store.get_video_entry("hot-channel", "hot-video", path=db_path)
            db_store.update_video_part_state(
                "hot-channel",
                "hot-video",
                "video",
                filename="hot-video.mp4",
                file_path="D:/Hot/Channel/video/hot-video.mp4",
                status=state_store.STATUS_DOWNLOADED,
                path=db_path,
                save_base_folder="D:/Hot",
                channel_name="Hot Channel",
                sanitized_filename_base="hot-video",
            )
            db_store.update_manual_status(
                "hot-channel",
                "hot-video",
                state_store.STATUS_NOT_DOWNLOADED,
                path=db_path,
                save_base_folder="D:/Hot",
            )
            db_store.clear_manual_status("hot-channel", "hot-video", path=db_path, save_base_folder="D:/Hot")
            db_store.get_channel_video_entries("hot-channel", path=db_path)
            db_store.reconcile_downloaded_item_state(
                "hot-channel",
                "hot-video",
                path=db_path,
                save_base_folder="D:/Hot",
            )

        offenders = _statements_with_markers(recorder.statements, DDL_MARKERS)
        _assert(not offenders, f"hot path emitted DDL/journal statements: {offenders}")


def _test_connection_settings_and_concurrent_writes() -> None:
    with TemporaryDirectory(prefix="db_lifecycle_concurrent_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        db_store.initialize_database(db_path)
        with closing(db_store.open_database_connection(db_path)) as conn:
            _assert(conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1, "foreign_keys was not enabled")
            _assert(conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0, "busy_timeout was not configured")

        errors = []
        threads = [
            threading.Thread(
                target=lambda index=index: _write_video_part(db_path, index, errors),
                daemon=False,
            )
            for index in range(12)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        _assert(not errors, f"concurrent writes failed: {errors}")
        with closing(db_store.open_database_connection(db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM download_items WHERE channel_id = 'concurrent'").fetchone()[0]
            invalid = conn.execute(
                """
                SELECT COUNT(*)
                FROM download_files
                WHERE item_id IN (
                    SELECT id FROM download_items WHERE channel_id = 'concurrent'
                )
                  AND part != 'video'
                """
            ).fetchone()[0]
        _assert(count == len(threads), f"concurrent write row count was wrong: {count}")
        _assert(invalid == 0, "concurrent writes produced invalid part rows")


def _test_transaction_rollback_closes_cleanly() -> None:
    with TemporaryDirectory(prefix="db_lifecycle_rollback_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        db_store.initialize_database(db_path)
        original = db_store._apply_item_updates
        try:
            db_store._apply_item_updates = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("injected update failure")
            )
            try:
                db_store.update_video_state(
                    "rollback",
                    "rollback-video",
                    {
                        "channel_name": "Rollback",
                        "sanitized_filename_base": "rollback-video",
                        "status": state_store.STATUS_NOT_DOWNLOADED,
                    },
                    path=db_path,
                    save_base_folder="D:/Rollback",
                )
            except RuntimeError:
                pass
            else:
                raise AssertionError("injected update failure did not propagate")
        finally:
            db_store._apply_item_updates = original

        with closing(db_store.open_database_connection(db_path)) as conn:
            rows = conn.execute("SELECT COUNT(*) FROM download_items WHERE channel_id = 'rollback'").fetchone()[0]
        _assert(rows == 0, "rollback left a partial download item")

        db_store.update_video_state(
            "rollback",
            "rollback-video",
            {
                "channel_name": "Rollback",
                "sanitized_filename_base": "rollback-video",
                "status": state_store.STATUS_NOT_DOWNLOADED,
            },
            path=db_path,
            save_base_folder="D:/Rollback",
        )
        _assert(db_store.get_video_entry("rollback", "rollback-video", path=db_path), "later operation failed after rollback")


def _test_startup_failure_blocks_ui_import() -> None:
    import tkinter
    from tkinter import messagebox

    old_init = state_store.initialize_sqlite_state
    old_tk = tkinter.Tk
    old_showerror = messagebox.showerror
    old_ui_module = sys.modules.pop("ui.main_window", None)
    shown_errors = []
    try:
        state_store.initialize_sqlite_state = lambda: (_ for _ in ()).throw(state_store.SQLiteStateError("boom"))
        tkinter.Tk = _FakeTk
        messagebox.showerror = lambda title, message: shown_errors.append((title, message))
        try:
            runpy.run_path(str(REPO_ROOT / "app.py"), run_name="__main__")
        except SystemExit as exc:
            _assert(exc.code == 1, f"startup failure exited with wrong code: {exc.code}")
        else:
            raise AssertionError("startup failure did not exit")
    finally:
        state_store.initialize_sqlite_state = old_init
        tkinter.Tk = old_tk
        messagebox.showerror = old_showerror
        if old_ui_module is not None:
            sys.modules["ui.main_window"] = old_ui_module

    _assert(shown_errors, "startup error was not reported")
    _assert("ui.main_window" not in sys.modules or sys.modules.get("ui.main_window") is old_ui_module, "UI was imported after startup DB failure")


def _threaded_initialize(db_path: Path, results: list[Path], errors: list[str]) -> None:
    try:
        results.append(db_store.initialize_database(db_path))
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")


def _write_video_part(db_path: Path, index: int, errors: list[str]) -> None:
    try:
        db_store.update_video_part_state(
            "concurrent",
            f"video-{index}",
            "video",
            filename=f"video-{index}.mp4",
            file_path=f"D:/Concurrent/video-{index}.mp4",
            status=state_store.STATUS_DOWNLOADED,
            path=db_path,
            save_base_folder="D:/Concurrent",
            channel_name="Concurrent",
            sanitized_filename_base=f"video-{index}",
        )
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")


def _create_v1_database(db_path: Path) -> None:
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
        conn.execute("INSERT INTO app_meta(key, value, updated_at) VALUES ('schema_version', '1', ?)", (now,))
        channel_id = conn.execute(
            """
            INSERT INTO channels(platform, channel_id, channel_name, save_base_folder_raw, save_base_folder_norm, created_at, updated_at)
            VALUES ('youtube', 'legacy', 'Legacy', 'D:/Legacy', 'd:/legacy', ?, ?)
            """,
            (now, now),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO download_items(channel_db_id, platform, channel_id, video_id, save_base_folder_raw, save_base_folder_norm, sanitized_filename_base, status, updated_at, created_at)
            VALUES (?, 'youtube', 'legacy', 'legacy-video', 'D:/Legacy', 'd:/legacy', 'legacy-video', ?, ?, ?)
            """,
            (channel_id, state_store.STATUS_NOT_DOWNLOADED, now, now),
        )
        conn.commit()


def _assert_current_schema(db_path: Path) -> None:
    with closing(db_store.open_database_connection(db_path)) as conn:
        db_store.validate_database_schema(conn)
        table_names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        index_names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    for table_name in db_store.REQUIRED_TABLES:
        _assert(table_name in table_names, f"missing required table: {table_name}")
    for index_name in db_store.REQUIRED_INDEXES:
        _assert(index_name in index_names, f"missing required index: {index_name}")


def _schema_version_raw(db_path: Path) -> int | None:
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
    return int(row[0]) if row else None


def _backup_files(db_path: Path) -> list[Path]:
    return sorted(db_path.parent.glob(f"{db_path.name}.pre-migration-v*-to-v*-*.bak"))


def _forget_initialized(db_path: Path) -> None:
    db_store._INITIALIZED_DATABASES.pop(db_path.resolve(strict=False), None)


def _statements_with_markers(statements: list[str], markers: tuple[str, ...]) -> list[str]:
    offenders = []
    for statement in statements:
        normalized = " ".join(statement.split()).upper()
        if any(marker in normalized for marker in markers):
            offenders.append(statement)
    return offenders


@contextmanager
def _patched_bootstrap_counter(calls: list[Path]):
    original = db_store._bootstrap_database
    try:
        def wrapper(path: Path, *, allow_new: bool) -> None:
            calls.append(path)
            original(path, allow_new=allow_new)

        db_store._bootstrap_database = wrapper
        yield
    finally:
        db_store._bootstrap_database = original


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


class _FakeTk:
    def withdraw(self) -> None:
        pass

    def destroy(self) -> None:
        pass


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
