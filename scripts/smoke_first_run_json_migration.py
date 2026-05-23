import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store, state_migration, state_store


def main() -> int:
    _configure_stdio()
    _test_legacy_json_only_migrates_to_sqlite()
    _test_existing_non_empty_sqlite_skips_migration()
    _test_forced_json_skips_migration()
    _test_corrupt_json_fails_safely_to_json()
    _test_empty_sqlite_migrates_to_sqlite()
    print("first-run JSON migration smoke tests passed")
    return 0


def _test_legacy_json_only_migrates_to_sqlite() -> None:
    with _temp_runtime() as paths:
        _write_json_state(paths["json_path"])
        with _patched_state_paths(paths["json_path"], paths["db_path"]), _patched_env(None):
            backend = state_store.get_state_backend_name()
            reason = state_store.get_state_backend_reason()
            state = state_store.load_state()

        _assert(backend == "sqlite", "legacy JSON only did not switch to SQLite")
        _assert(reason == "migrated_json_to_sqlite", f"unexpected migration reason: {reason}")
        _assert(db_store.is_sqlite_state_usable(paths["db_path"]), "migrated SQLite DB is not usable")
        _assert(len(state.get("channels", {})) == 1, "migrated SQLite state was not loaded")


def _test_existing_non_empty_sqlite_skips_migration() -> None:
    with _temp_runtime() as paths:
        _write_json_state(paths["json_path"])
        db_store.init_db(paths["db_path"])
        db_store.update_video_state(
            "channel",
            "existing-video",
            {
                "channel_name": "Channel",
                "sanitized_filename_base": "existing-video",
                "status": state_store.STATUS_NOT_DOWNLOADED,
            },
            path=paths["db_path"],
            save_base_folder="D:/Out",
        )

        result = state_migration.migrate_json_to_sqlite_if_needed(
            json_path=paths["json_path"],
            db_path=paths["db_path"],
        )
        summary = db_store.get_sqlite_state_summary(paths["db_path"])

        _assert(result.get("skipped") is True, "non-empty SQLite was not skipped")
        _assert(summary["download_items"] == 1, "non-empty SQLite was modified during skipped migration")


def _test_forced_json_skips_migration() -> None:
    with _temp_runtime() as paths:
        _write_json_state(paths["json_path"])
        with _patched_state_paths(paths["json_path"], paths["db_path"]), _patched_env("json"):
            backend = state_store.get_state_backend_name()
            reason = state_store.get_state_backend_reason()

        _assert(backend == "json", "forced JSON did not use JSON backend")
        _assert(reason == "forced_json", f"unexpected forced JSON reason: {reason}")
        _assert(not paths["db_path"].exists(), "forced JSON created SQLite unexpectedly")


def _test_corrupt_json_fails_safely_to_json() -> None:
    with _temp_runtime() as paths:
        paths["json_path"].parent.mkdir(parents=True, exist_ok=True)
        paths["json_path"].write_text("{", encoding="utf-8")

        with _patched_state_paths(paths["json_path"], paths["db_path"]), _patched_env(None):
            backend = state_store.get_state_backend_name()
            reason = state_store.get_state_backend_reason()

        backup_dir = paths["data_dir"] / "backups"
        json_backups = list(backup_dir.glob("download_state.json.bak.*"))
        quarantined_dbs = list(backup_dir.glob("download_state.sqlite3.bad.*"))

        _assert(backend == "json", "corrupt JSON did not fall back to JSON")
        _assert(reason == "migration_failed_fallback_json", f"unexpected failure reason: {reason}")
        _assert(paths["json_path"].exists(), "corrupt JSON was deleted")
        _assert(json_backups, "corrupt JSON was not backed up before migration attempt")
        _assert(quarantined_dbs, "failed migration DB was not quarantined")
        _assert(not db_store.is_sqlite_state_usable(paths["db_path"]), "failed migration left a usable DB")


def _test_empty_sqlite_migrates_to_sqlite() -> None:
    with _temp_runtime() as paths:
        _write_json_state(paths["json_path"])
        db_store.init_db(paths["db_path"])

        with _patched_state_paths(paths["json_path"], paths["db_path"]), _patched_env(None):
            backend = state_store.get_state_backend_name()
            reason = state_store.get_state_backend_reason()

        _assert(backend == "sqlite", "empty SQLite with valid JSON did not migrate")
        _assert(reason == "migrated_json_to_sqlite", f"unexpected empty DB migration reason: {reason}")
        _assert(db_store.is_sqlite_state_usable(paths["db_path"]), "empty SQLite migration result is not usable")


@contextmanager
def _temp_runtime():
    with TemporaryDirectory() as temp_dir:
        data_dir = Path(temp_dir) / "data"
        yield {
            "data_dir": data_dir,
            "json_path": data_dir / "download_state.json",
            "db_path": data_dir / "download_state.sqlite3",
        }


@contextmanager
def _patched_state_paths(json_path: Path, db_path: Path):
    old_state_file = state_store.state_file
    old_state_db_file = state_store.db_file
    old_db_store_db_file = db_store.db_file
    _reset_state_store_caches()
    try:
        state_store.state_file = lambda: json_path
        state_store.db_file = lambda: db_path
        db_store.db_file = lambda: db_path
        yield
    finally:
        state_store.state_file = old_state_file
        state_store.db_file = old_state_db_file
        db_store.db_file = old_db_store_db_file
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
