import os
import sqlite3
import sys
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
    _test_stale_failed_revalidation_preserves_new_cache_identity()
    _test_delete_between_cache_check_and_connect()
    _test_non_sqlite_replacement_before_connect()
    _test_empty_file_replacement_before_connect()
    _test_valid_replacement_before_connect()
    _test_too_new_replacement_before_connect()
    _test_genuine_lock_same_identity_is_not_file_change()
    _test_other_open_error_same_identity_is_normalized()
    _test_identity_safe_invalidation_race()
    _test_failed_revalidation_without_new_cache_leaves_no_success_entry()
    _test_cached_file_delete_is_rejected_without_recreate()
    _test_valid_replacement_revalidates_and_refreshes_cache()
    _test_too_new_replacement_is_rejected_not_cached()
    _test_empty_replacement_is_rejected_without_bootstrap()
    _test_cached_open_does_not_bootstrap_or_emit_ddl()
    _test_replacement_between_cache_check_and_open_retries()
    _test_continuous_replacement_is_bounded()
    _test_normal_connection_regression()
    print("database identity cache smoke tests passed")
    return 0


def _test_stale_failed_revalidation_preserves_new_cache_identity() -> None:
    with TemporaryDirectory(prefix="db_identity_stale_fail_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        normalized = db_path.resolve(strict=False)
        old_identity = db_store.DatabaseFileIdentity(device=1, inode=100)
        new_identity = db_store.DatabaseFileIdentity(device=1, inode=200)
        db_store._INITIALIZED_DATABASES[normalized] = old_identity
        original_ensure = db_store._ensure_initialized_database
        calls = []

        def failing_ensure(path: Path, *, allow_new: bool) -> db_store.DatabaseFileIdentity:
            calls.append((path.resolve(strict=False), allow_new))
            db_store._INITIALIZED_DATABASES[normalized] = new_identity
            raise sqlite3.DatabaseError("file is not a database")

        try:
            db_store._ensure_initialized_database = failing_ensure
            try:
                db_store._revalidate_replacement_database(db_path)
            except db_store.DatabaseSchemaError:
                pass
            else:
                raise AssertionError("stale failed validation was silently accepted")
        finally:
            db_store._ensure_initialized_database = original_ensure

        _assert(calls == [(normalized, False)], f"replacement validation did not use strict mode: {calls}")
        _assert(db_store._INITIALIZED_DATABASES.get(normalized) == new_identity, "stale failed validation removed newer cache identity")
        _assert(not db_path.exists(), "stale failed validation created a database file")
        db_store._INITIALIZED_DATABASES.pop(normalized, None)


def _test_delete_between_cache_check_and_connect() -> None:
    with TemporaryDirectory(prefix="db_identity_preopen_delete_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_current_database_with_video(db_path, "delete", "delete-video")
        normalized = db_path.resolve(strict=False)
        triggered = {"value": False}
        original = db_store._connect_configured

        def deleting_connect(path: Path) -> sqlite3.Connection:
            if path.resolve(strict=False) == normalized and not triggered["value"]:
                triggered["value"] = True
                _checkpoint_database(db_path)
                db_path.unlink()
            return original(path)

        try:
            db_store._connect_configured = deleting_connect
            try:
                db_store.open_database_connection(db_path)
            except db_store.DatabaseFileChangedError:
                pass
            else:
                raise AssertionError("pre-connect delete was not normalized")
        finally:
            db_store._connect_configured = original

        _assert(triggered["value"], "delete hook did not run")
        _assert(not db_path.exists(), "pre-connect delete recreated the database")
        _assert(normalized not in db_store._INITIALIZED_DATABASES, "stale identity remained after pre-connect delete")


def _test_non_sqlite_replacement_before_connect() -> None:
    with TemporaryDirectory(prefix="db_identity_preopen_bad_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        replacement_path = Path(temp_dir) / "bad.sqlite3"
        bad_bytes = b"NOT A SQLITE DATABASE"
        replacement_path.write_bytes(bad_bytes)
        _create_current_database_with_video(db_path, "bad-old", "bad-video")
        normalized = db_path.resolve(strict=False)
        triggered = {"value": False}
        original = db_store._connect_configured

        def replacing_connect(path: Path) -> sqlite3.Connection:
            if path.resolve(strict=False) == normalized and not triggered["value"]:
                triggered["value"] = True
                _replace_database_file(db_path, replacement_path, source_is_sqlite=False)
            return original(path)

        try:
            db_store._connect_configured = replacing_connect
            try:
                db_store.get_video_entry("bad-old", "bad-video", path=db_path)
            except db_store.DatabaseSchemaError:
                pass
            else:
                raise AssertionError("non-SQLite replacement was accepted")
        finally:
            db_store._connect_configured = original

        _assert(triggered["value"], "non-SQLite replacement hook did not run")
        _assert(db_path.read_bytes() == bad_bytes, "non-SQLite replacement bytes were modified")
        _assert(normalized not in db_store._INITIALIZED_DATABASES, "invalid replacement was cached")


def _test_empty_file_replacement_before_connect() -> None:
    with TemporaryDirectory(prefix="db_identity_preopen_empty_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        replacement_path = Path(temp_dir) / "empty.sqlite3"
        replacement_path.write_bytes(b"")
        _create_current_database_with_video(db_path, "empty-old", "empty-video")
        normalized = db_path.resolve(strict=False)
        triggered = {"value": False}
        original = db_store._connect_configured

        def replacing_connect(path: Path) -> sqlite3.Connection:
            if path.resolve(strict=False) == normalized and not triggered["value"]:
                triggered["value"] = True
                _replace_database_file(db_path, replacement_path, source_is_sqlite=False)
            return original(path)

        try:
            db_store._connect_configured = replacing_connect
            try:
                db_store.get_video_entry("empty-old", "empty-video", path=db_path)
            except db_store.DatabaseSchemaError:
                pass
            else:
                raise AssertionError("empty replacement was accepted")
        finally:
            db_store._connect_configured = original

        with closing(sqlite3.connect(db_path)) as conn:
            app_meta_exists = _table_exists(conn, "app_meta")
        _assert(triggered["value"], "empty replacement hook did not run")
        _assert(not app_meta_exists, "empty replacement was initialized as a new database")
        _assert(normalized not in db_store._INITIALIZED_DATABASES, "empty replacement was cached")


def _test_valid_replacement_before_connect() -> None:
    with TemporaryDirectory(prefix="db_identity_preopen_valid_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        replacement_path = Path(temp_dir) / "replacement.sqlite3"
        _create_current_database_with_video(db_path, "valid-old", "old-video")
        _create_current_database_with_video(replacement_path, "valid-new", "new-video")
        normalized = db_path.resolve(strict=False)
        old_identity = db_store._INITIALIZED_DATABASES[normalized]
        triggered = {"value": False}
        connect_calls = {"count": 0}
        original = db_store._connect_configured

        def replacing_connect(path: Path) -> sqlite3.Connection:
            if path.resolve(strict=False) == normalized:
                connect_calls["count"] += 1
                if not triggered["value"]:
                    triggered["value"] = True
                    _replace_database_file(db_path, replacement_path)
            return original(path)

        try:
            db_store._connect_configured = replacing_connect
            entry = db_store.get_video_entry("valid-new", "new-video", path=db_path)
        finally:
            db_store._connect_configured = original

        _assert(triggered["value"], "valid replacement hook did not run")
        _assert(connect_calls["count"] == 2, f"valid replacement was not retried exactly once: {connect_calls}")
        _assert(entry and entry["video_id"] == "new-video", "valid replacement row was not visible")
        _assert(db_store._INITIALIZED_DATABASES[normalized] != old_identity, "valid replacement identity was not cached")
        _assert(not _backup_files(db_path), "valid current replacement created an unnecessary backup")


def _test_too_new_replacement_before_connect() -> None:
    with TemporaryDirectory(prefix="db_identity_preopen_too_new_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        replacement_path = Path(temp_dir) / "too_new.sqlite3"
        _create_current_database_with_video(db_path, "too-new-old", "old-video")
        _create_too_new_database(replacement_path)
        normalized = db_path.resolve(strict=False)
        triggered = {"value": False}
        original = db_store._connect_configured

        def replacing_connect(path: Path) -> sqlite3.Connection:
            if path.resolve(strict=False) == normalized and not triggered["value"]:
                triggered["value"] = True
                _replace_database_file(db_path, replacement_path)
            return original(path)

        try:
            db_store._connect_configured = replacing_connect
            try:
                db_store.open_database_connection(db_path)
            except db_store.DatabaseTooNewError:
                pass
            else:
                raise AssertionError("too-new pre-connect replacement was accepted")
        finally:
            db_store._connect_configured = original

        _assert(triggered["value"], "too-new replacement hook did not run")
        _assert(_schema_version_raw(db_path) == db_store.CURRENT_SCHEMA_VERSION + 1, "too-new replacement was mutated")
        _assert(normalized not in db_store._INITIALIZED_DATABASES, "too-new replacement was cached")


def _test_genuine_lock_same_identity_is_not_file_change() -> None:
    with TemporaryDirectory(prefix="db_identity_lock_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_current_database_with_video(db_path, "locked", "locked-video")
        normalized = db_path.resolve(strict=False)
        cached_identity = db_store._INITIALIZED_DATABASES[normalized]
        bootstrap_calls = []
        original = db_store._connect_configured

        def locked_connect(path: Path) -> sqlite3.Connection:
            if path.resolve(strict=False) == normalized:
                raise sqlite3.OperationalError("database schema is locked")
            return original(path)

        try:
            db_store._connect_configured = locked_connect
            with _patched_bootstrap_counter(bootstrap_calls):
                try:
                    db_store.open_database_connection(db_path)
                except db_store.DatabaseLockError:
                    pass
                else:
                    raise AssertionError("lock was not classified as DatabaseLockError")
        finally:
            db_store._connect_configured = original

        _assert(db_store._INITIALIZED_DATABASES.get(normalized) == cached_identity, "lock invalidated a valid cache identity")
        _assert(not bootstrap_calls, f"lock triggered bootstrap: {bootstrap_calls}")


def _test_other_open_error_same_identity_is_normalized() -> None:
    with TemporaryDirectory(prefix="db_identity_open_error_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_current_database_with_video(db_path, "open-error", "open-error-video")
        normalized = db_path.resolve(strict=False)
        cached_identity = db_store._INITIALIZED_DATABASES[normalized]
        bootstrap_calls = []
        original = db_store._connect_configured

        def failing_connect(path: Path) -> sqlite3.Connection:
            if path.resolve(strict=False) == normalized:
                raise sqlite3.OperationalError("disk I/O error")
            return original(path)

        try:
            db_store._connect_configured = failing_connect
            with _patched_bootstrap_counter(bootstrap_calls):
                try:
                    db_store.open_database_connection(db_path)
                except db_store.DatabasePathError:
                    pass
                else:
                    raise AssertionError("same-identity open error was not normalized")
        finally:
            db_store._connect_configured = original

        _assert(db_store._INITIALIZED_DATABASES.get(normalized) == cached_identity, "same-identity open error invalidated cache")
        _assert(not bootstrap_calls, f"same-identity open error triggered bootstrap: {bootstrap_calls}")


def _test_identity_safe_invalidation_race() -> None:
    with TemporaryDirectory(prefix="db_identity_invalidate_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        replacement_path = Path(temp_dir) / "replacement.sqlite3"
        _create_current_database_with_video(db_path, "old-cache", "old-video")
        normalized = db_path.resolve(strict=False)
        old_identity = db_store._INITIALIZED_DATABASES[normalized]
        _create_current_database_with_video(replacement_path, "new-cache", "new-video")
        _replace_database_file(db_path, replacement_path)
        db_store.initialize_database(db_path)
        new_identity = db_store._INITIALIZED_DATABASES[normalized]

        db_store._invalidate_cached_database_identity(db_path, old_identity)

        _assert(new_identity != old_identity, "replacement identity did not change")
        _assert(db_store._INITIALIZED_DATABASES.get(normalized) == new_identity, "stale invalidation removed a newer cache identity")
        db_store._invalidate_cached_database_identity(db_path, new_identity)
        _assert(normalized not in db_store._INITIALIZED_DATABASES, "matching identity invalidation did not remove cache entry")


def _test_failed_revalidation_without_new_cache_leaves_no_success_entry() -> None:
    with TemporaryDirectory(prefix="db_identity_revalidate_fail_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        normalized = db_path.resolve(strict=False)
        bad_bytes = b"NOT A SQLITE DATABASE"
        db_path.write_bytes(bad_bytes)
        old_identity = db_store.DatabaseFileIdentity(device=1, inode=100)
        db_store._INITIALIZED_DATABASES[normalized] = old_identity
        db_store._invalidate_cached_database_identity(db_path, old_identity)

        try:
            db_store._revalidate_replacement_database(db_path)
        except db_store.DatabaseSchemaError:
            pass
        else:
            raise AssertionError("failed replacement validation was silently accepted")

        _assert(normalized not in db_store._INITIALIZED_DATABASES, "failed validation left a successful cache entry")
        _assert(db_path.read_bytes() == bad_bytes, "failed validation modified replacement bytes")


def _test_cached_file_delete_is_rejected_without_recreate() -> None:
    with TemporaryDirectory(prefix="db_identity_delete_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        db_store.initialize_database(db_path)
        _checkpoint_database(db_path)
        db_path.unlink()

        try:
            db_store.open_database_connection(db_path)
        except db_store.DatabaseFileChangedError:
            pass
        else:
            raise AssertionError("deleted cached database was silently recreated")

        _assert(not db_path.exists(), "deleted cached database was recreated")
        _assert(db_path.resolve(strict=False) not in db_store._INITIALIZED_DATABASES, "deleted database remained cached")


def _test_valid_replacement_revalidates_and_refreshes_cache() -> None:
    with TemporaryDirectory(prefix="db_identity_replace_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        replacement_path = Path(temp_dir) / "replacement.sqlite3"
        _create_current_database_with_video(db_path, "original", "original-video")
        old_identity = db_store._INITIALIZED_DATABASES[db_path.resolve(strict=False)]
        _create_current_database_with_video(replacement_path, "replacement", "replacement-video")
        _replace_database_file(db_path, replacement_path)

        entry = db_store.get_video_entry("replacement", "replacement-video", path=db_path)

        _assert(entry and entry["video_id"] == "replacement-video", "valid replacement was not opened")
        _assert(db_store.get_video_entry("original", "original-video", path=db_path) is None, "stale original database was still used")
        new_identity = db_store._INITIALIZED_DATABASES[db_path.resolve(strict=False)]
        _assert(new_identity != old_identity, "replacement identity did not refresh the cache")


def _test_too_new_replacement_is_rejected_not_cached() -> None:
    with TemporaryDirectory(prefix="db_identity_too_new_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        replacement_path = Path(temp_dir) / "too_new.sqlite3"
        _create_current_database_with_video(db_path, "original", "original-video")
        _create_too_new_database(replacement_path)
        _replace_database_file(db_path, replacement_path)

        try:
            db_store.initialize_database(db_path)
        except db_store.DatabaseTooNewError:
            pass
        else:
            raise AssertionError("too-new replacement was silently accepted")

        _assert(_schema_version_raw(db_path) == db_store.CURRENT_SCHEMA_VERSION + 1, "too-new version was rewritten")
        _assert(db_path.resolve(strict=False) not in db_store._INITIALIZED_DATABASES, "too-new replacement was cached")
        _assert(not _backup_files(db_path), "too-new replacement created a migration backup")


def _test_empty_replacement_is_rejected_without_bootstrap() -> None:
    with TemporaryDirectory(prefix="db_identity_empty_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        replacement_path = Path(temp_dir) / "empty.sqlite3"
        _create_current_database_with_video(db_path, "original", "original-video")
        replacement_path.write_bytes(b"")
        _replace_database_file(db_path, replacement_path, source_is_sqlite=False)

        try:
            db_store.initialize_database(db_path)
        except db_store.DatabaseFileChangedError:
            pass
        else:
            raise AssertionError("empty replacement was bootstrapped as a new database")

        with closing(sqlite3.connect(db_path)) as conn:
            app_meta_exists = _table_exists(conn, "app_meta")
        _assert(not app_meta_exists, "empty replacement received app_meta")
        _assert(db_path.resolve(strict=False) not in db_store._INITIALIZED_DATABASES, "empty replacement was cached")


def _test_cached_open_does_not_bootstrap_or_emit_ddl() -> None:
    with TemporaryDirectory(prefix="db_identity_hot_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_current_database_with_video(db_path, "hot", "hot-video")
        recorder = _SqlRecorder()
        bootstrap_calls = []

        with _patched_bootstrap_counter(bootstrap_calls), _patched_sqlite_connect(recorder):
            with closing(db_store.open_database_connection(db_path)) as conn:
                conn.execute("SELECT COUNT(*) FROM download_items").fetchone()
            db_store.get_video_entry("hot", "hot-video", path=db_path)

        _assert(not bootstrap_calls, f"cached open ran bootstrap: {bootstrap_calls}")
        offenders = _statements_with_markers(recorder.statements, DDL_MARKERS)
        _assert(not offenders, f"cached open emitted DDL/journal statements: {offenders}")


def _test_replacement_between_cache_check_and_open_retries() -> None:
    with TemporaryDirectory(prefix="db_identity_race_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        replacement_path = Path(temp_dir) / "replacement.sqlite3"
        _create_current_database_with_video(db_path, "race-old", "old-video")
        old_identity = db_store._INITIALIZED_DATABASES[db_path.resolve(strict=False)]
        _create_current_database_with_video(replacement_path, "race-new", "new-video")
        triggered = {"value": False}
        original_connect_configured = db_store._connect_configured

        def replacing_connect_configured(path: Path) -> sqlite3.Connection:
            if path.resolve(strict=False) == db_path.resolve(strict=False) and not triggered["value"]:
                triggered["value"] = True
                _replace_database_file(db_path, replacement_path)
            return original_connect_configured(path)

        try:
            db_store._connect_configured = replacing_connect_configured
            entry = db_store.get_video_entry("race-new", "new-video", path=db_path)
        finally:
            db_store._connect_configured = original_connect_configured

        _assert(triggered["value"], "replacement race was not triggered")
        _assert(entry and entry["video_id"] == "new-video", "replacement race did not retry against the new database")
        new_identity = db_store._INITIALIZED_DATABASES[db_path.resolve(strict=False)]
        _assert(new_identity != old_identity, "replacement race left the stale identity cached")


def _test_continuous_replacement_is_bounded() -> None:
    with TemporaryDirectory(prefix="db_identity_continuous_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        replacement_b = Path(temp_dir) / "replacement_b.sqlite3"
        replacement_c = Path(temp_dir) / "replacement_c.sqlite3"
        _create_current_database_with_video(db_path, "continuous-a", "video-a")
        _create_current_database_with_video(replacement_b, "continuous-b", "video-b")
        _create_current_database_with_video(replacement_c, "continuous-c", "video-c")
        normalized = db_path.resolve(strict=False)
        replacements = [replacement_b, replacement_c]
        connect_calls = {"count": 0}
        original = db_store._connect_configured

        def replacing_connect(path: Path) -> sqlite3.Connection:
            if path.resolve(strict=False) == normalized:
                connect_calls["count"] += 1
                if replacements:
                    _replace_database_file(db_path, replacements.pop(0))
            return original(path)

        try:
            db_store._connect_configured = replacing_connect
            try:
                db_store.open_database_connection(db_path)
            except db_store.DatabaseFileChangedError:
                pass
            else:
                raise AssertionError("continuous replacement was accepted")
        finally:
            db_store._connect_configured = original

        _assert(connect_calls["count"] == 2, f"continuous replacement was not bounded to two opens: {connect_calls}")
        _assert(normalized not in db_store._INITIALIZED_DATABASES, "continuous replacement left a successful stale cache")


def _test_normal_connection_regression() -> None:
    with TemporaryDirectory(prefix="db_identity_normal_") as temp_dir:
        db_path = Path(temp_dir) / "state.sqlite3"
        _create_current_database_with_video(db_path, "normal", "normal-video")
        normalized = db_path.resolve(strict=False)
        cached_identity = db_store._INITIALIZED_DATABASES[normalized]
        backups_before = _backup_files(db_path)
        bootstrap_calls = []
        connect_calls = {"count": 0}
        original = db_store._connect_configured

        def counting_connect(path: Path) -> sqlite3.Connection:
            if path.resolve(strict=False) == normalized:
                connect_calls["count"] += 1
            return original(path)

        try:
            db_store._connect_configured = counting_connect
            with _patched_bootstrap_counter(bootstrap_calls):
                with closing(db_store.open_database_connection(db_path)) as conn:
                    fk_enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]
                    busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                    row = conn.execute(
                        "SELECT video_id FROM download_items WHERE channel_id = 'normal'"
                    ).fetchone()
        finally:
            db_store._connect_configured = original

        _assert(connect_calls["count"] == 1, f"normal open count was wrong: {connect_calls}")
        _assert(not bootstrap_calls, f"normal cached open reran bootstrap: {bootstrap_calls}")
        _assert(_backup_files(db_path) == backups_before, "normal cached open created a backup")
        _assert(db_store._INITIALIZED_DATABASES.get(normalized) == cached_identity, "normal open changed cache identity")
        _assert(fk_enabled == 1, "foreign_keys was not enabled")
        _assert(busy_timeout > 0, "busy_timeout was not configured")
        _assert(row and row[0] == "normal-video", "normal cached connection did not read existing data")


def _create_current_database_with_video(db_path: Path, channel_id: str, video_id: str) -> None:
    db_store.initialize_database(db_path)
    db_store.update_video_state(
        channel_id,
        video_id,
        {
            "channel_name": channel_id,
            "sanitized_filename_base": video_id,
            "status": state_store.STATUS_NOT_DOWNLOADED,
        },
        path=db_path,
        save_base_folder=f"D:/{channel_id}",
    )
    _checkpoint_database(db_path)


def _create_too_new_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE app_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO app_meta(key, value, updated_at) VALUES ('schema_version', ?, '2026-01-01T00:00:00+00:00')",
            (str(db_store.CURRENT_SCHEMA_VERSION + 1),),
        )
        conn.execute("CREATE TABLE keep_me(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO keep_me(id, value) VALUES (1, 'too-new')")
        conn.commit()


def _replace_database_file(target: Path, source: Path, *, source_is_sqlite: bool = True) -> None:
    _checkpoint_database(target)
    if source_is_sqlite:
        _checkpoint_database(source)
    _remove_sqlite_sidecars(target)
    os.replace(source, target)
    _remove_sqlite_sidecars(source)


def _checkpoint_database(db_path: Path) -> None:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    _remove_sqlite_sidecars(db_path)


def _remove_sqlite_sidecars(db_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{db_path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()


def _schema_version_raw(db_path: Path) -> int | None:
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
    return int(row[0]) if row else None


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _backup_files(db_path: Path) -> list[Path]:
    return sorted(db_path.parent.glob(f"{db_path.name}.pre-migration-v*-to-v*-*.bak"))


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


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
