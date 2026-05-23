import json
import os
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, state_migration, state_store


def main() -> int:
    _configure_stdio()
    real_runtime_before = _snapshot_real_runtime_files()
    _test_existing_sqlite_uses_lightweight_probe()
    _test_legacy_json_only_migrates_at_initialize()
    _test_forced_json_skips_migration()
    _test_no_sqlite_no_json_does_not_migrate()
    _test_corrupt_json_fails_safely()
    _test_initialize_result_is_cached()
    _assert(
        real_runtime_before == _snapshot_real_runtime_files(),
        "real runtime state files were mutated by temp-file smoke tests",
    )
    print("startup state warmup smoke tests passed")
    return 0


def _test_existing_sqlite_uses_lightweight_probe() -> None:
    with _temp_runtime() as paths:
        _seed_sqlite_many_rows(paths["db_path"], rows=1000)
        recorder = _SqlRecorder(db_store.sqlite3.connect)
        with _patched_state_paths(paths), _patched_env(None), _patched_sqlite_connect(recorder):
            result = state_store.initialize_state_backend()

        statements = recorder.statements_text()
        _assert(result["backend"] == "sqlite", "existing SQLite did not select sqlite backend")
        _assert(result["reason"] == "default_sqlite", f"unexpected reason: {result['reason']}")
        _assert("SELECT 1 FROM download_items LIMIT 1" in statements, "startup did not use SELECT 1 LIMIT 1")
        _assert("COUNT(" not in statements.upper(), "startup used COUNT(*) for existing SQLite")
        _assert(not list(paths["data_dir"].glob("state_migration.log")), "migration log was written unexpectedly")


def _test_legacy_json_only_migrates_at_initialize() -> None:
    with _temp_runtime() as paths:
        _write_json_state(paths["json_path"])
        with _patched_state_paths(paths), _patched_env(None):
            result = state_store.initialize_state_backend()

        _assert(result["backend"] == "sqlite", "legacy JSON did not select sqlite after migration")
        _assert(result["reason"] == "migrated_json_to_sqlite", f"unexpected migration reason: {result['reason']}")
        _assert(db_store.sqlite_has_any_items(paths["db_path"]), "migration did not create a usable SQLite DB")
        _assert(list((paths["data_dir"] / "backups").glob("download_state.json.bak.*")), "JSON backup was not created")


def _test_forced_json_skips_migration() -> None:
    with _temp_runtime() as paths:
        _write_json_state(paths["json_path"])
        with _patched_state_paths(paths), _patched_env("json"):
            result = state_store.initialize_state_backend()

        _assert(result["backend"] == "json", "forced JSON did not select json backend")
        _assert(result["reason"] == "forced_json", f"unexpected forced JSON reason: {result['reason']}")
        _assert(not paths["db_path"].exists(), "forced JSON created SQLite unexpectedly")


def _test_no_sqlite_no_json_does_not_migrate() -> None:
    with _temp_runtime() as paths:
        with _patched_state_paths(paths), _patched_env(None):
            result = state_store.initialize_state_backend()

        _assert(result["backend"] == "json", "no-state startup did not fall back to JSON")
        _assert(result["reason"] == "sqlite_unavailable_fallback_json", f"unexpected no-state reason: {result['reason']}")
        _assert(not paths["db_path"].exists(), "no-state startup created SQLite unexpectedly")


def _test_corrupt_json_fails_safely() -> None:
    with _temp_runtime() as paths:
        paths["data_dir"].mkdir(parents=True, exist_ok=True)
        paths["json_path"].write_text("{", encoding="utf-8")
        with _patched_state_paths(paths), _patched_env(None):
            result = state_store.initialize_state_backend()

        backup_dir = paths["data_dir"] / "backups"
        _assert(result["backend"] == "json", "corrupt JSON did not fall back to JSON")
        _assert(
            result["reason"] == "migration_failed_fallback_json",
            f"unexpected corrupt JSON reason: {result['reason']}",
        )
        _assert(paths["json_path"].exists(), "corrupt JSON was removed")
        _assert(list(backup_dir.glob("download_state.json.bak.*")), "corrupt JSON was not backed up")
        _assert(list(backup_dir.glob("download_state.sqlite3.bad.*")), "failed SQLite DB was not quarantined")


def _test_initialize_result_is_cached() -> None:
    with _temp_runtime() as paths:
        _seed_sqlite_many_rows(paths["db_path"], rows=1)
        with _patched_state_paths(paths), _patched_env(None):
            first = state_store.initialize_state_backend()
            original_resolve = state_store._resolve_state_backend

            def fail_if_called():
                raise AssertionError("backend resolution was called after initialization")

            try:
                state_store._resolve_state_backend = fail_if_called
                second = state_store.initialize_state_backend()
            finally:
                state_store._resolve_state_backend = original_resolve

        _assert(first == second, "second initialize call did not return cached result")


def _seed_sqlite_many_rows(path: Path, rows: int) -> None:
    db_store.init_db(path)
    now = "2026-01-01T00:00:00+00:00"
    with closing(db_store.connect_db(path)) as conn:
        channel_id = conn.execute(
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
            ("youtube", "channel", "Channel", "D:/Out", "d:/out", now, now),
        ).lastrowid
        conn.executemany(
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
                (
                    channel_id,
                    "youtube",
                    "channel",
                    f"video-{index}",
                    "D:/Out",
                    "d:/out",
                    f"video-{index}",
                    state_store.STATUS_NOT_DOWNLOADED,
                    now,
                    now,
                )
                for index in range(rows)
            ),
        )
        conn.commit()


@contextmanager
def _temp_runtime():
    with TemporaryDirectory() as temp_dir:
        data_dir = Path(temp_dir) / "data"
        yield {
            "data_dir": data_dir,
            "json_path": data_dir / "download_state.json",
            "db_path": data_dir / "download_state.sqlite3",
        }


def _snapshot_real_runtime_files() -> dict[str, tuple[bool, int | None, int | None]]:
    paths = {
        "json": state_store.state_file(),
        "sqlite": state_store.db_file(),
        "wal": Path(f"{state_store.db_file()}-wal"),
        "shm": Path(f"{state_store.db_file()}-shm"),
        "migration_log": state_store.db_file().parent / state_migration.MIGRATION_LOG_NAME,
    }
    snapshot = {}
    for label, path in paths.items():
        try:
            stat = path.stat()
        except FileNotFoundError:
            snapshot[label] = (False, None, None)
        else:
            snapshot[label] = (True, stat.st_size, stat.st_mtime_ns)
    return snapshot


@contextmanager
def _patched_state_paths(paths: dict):
    old_state_file = state_store.state_file
    old_state_db_file = state_store.db_file
    old_db_store_db_file = db_store.db_file
    old_migration_state_file = state_migration.state_file
    old_migration_db_file = state_migration.db_file
    _reset_state_store_caches()
    try:
        state_store.state_file = lambda: paths["json_path"]
        state_store.db_file = lambda: paths["db_path"]
        db_store.db_file = lambda: paths["db_path"]
        state_migration.state_file = lambda: paths["json_path"]
        state_migration.db_file = lambda: paths["db_path"]
        yield
    finally:
        state_store.state_file = old_state_file
        state_store.db_file = old_state_db_file
        db_store.db_file = old_db_store_db_file
        state_migration.state_file = old_migration_state_file
        state_migration.db_file = old_migration_db_file
        _reset_state_store_caches()


@contextmanager
def _patched_env(value: str | None):
    previous = os.environ.get(state_store.STATE_BACKEND_ENV)
    try:
        if value is None:
            os.environ.pop(state_store.STATE_BACKEND_ENV, None)
        else:
            os.environ[state_store.STATE_BACKEND_ENV] = value
        yield
    finally:
        if previous is None:
            os.environ.pop(state_store.STATE_BACKEND_ENV, None)
        else:
            os.environ[state_store.STATE_BACKEND_ENV] = previous


@contextmanager
def _patched_sqlite_connect(recorder):
    old_connect = db_store.sqlite3.connect
    try:
        db_store.sqlite3.connect = recorder.connect
        yield
    finally:
        db_store.sqlite3.connect = old_connect


class _SqlRecorder:
    def __init__(self, connect):
        self._connect = connect
        self.statements = []

    def connect(self, *args, **kwargs):
        return _ConnectionProxy(self._connect(*args, **kwargs), self.statements)

    def statements_text(self) -> str:
        return "\n".join(self.statements)


class _ConnectionProxy:
    def __init__(self, conn, statements):
        self._conn = conn
        self._statements = statements

    def execute(self, sql, parameters=(), /):
        self._statements.append(" ".join(str(sql).split()))
        return self._conn.execute(sql, parameters)

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _write_json_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 1,
        "channels": {
            "channel": {
                "channel_id": "channel",
                "channel_name": "Channel",
                "save_base_folder": "D:/Out",
                "videos": {
                    "video": {
                        "channel_id": "channel",
                        "channel_name": "Channel",
                        "save_base_folder": "D:/Out",
                        "video_id": "video",
                        "original_title": "Video",
                        "sanitized_filename_base": "video",
                        "status": state_store.STATUS_NOT_DOWNLOADED,
                    }
                },
            }
        },
    }
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _reset_state_store_caches() -> None:
    state_store._BACKEND_WARNING_KEYS.clear()
    state_store._MIGRATION_RESULTS_BY_PATH.clear()
    state_store._STATE_BACKEND_INIT_RESULT = None


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
