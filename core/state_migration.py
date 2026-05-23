import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from core import db_store
from core.runtime_paths import db_file, state_file


MIGRATION_LOG_NAME = "state_migration.log"


def migrate_json_to_sqlite_if_needed(
    json_path: Path | None = None,
    db_path: Path | None = None,
    backup: bool = True,
) -> dict:
    source_path = json_path or state_file()
    target_path = db_path or db_file()
    result = {
        "ok": False,
        "attempted": False,
        "migrated": False,
        "skipped": False,
        "reason": "",
        "json_path": str(source_path),
        "db_path": str(target_path),
        "backup_path": None,
        "quarantine_paths": [],
        "summary": {},
    }

    if not source_path.exists():
        result.update({"skipped": True, "reason": "json_missing"})
        return result

    if not is_sqlite_missing_or_empty(target_path):
        result.update({"skipped": True, "reason": "sqlite_has_items_or_unavailable"})
        return result

    result["attempted"] = True
    backup_dir = target_path.parent / "backups"
    pre_existing_empty_db = target_path.exists()
    _write_migration_log("start", source_path, target_path, "attempting first-run JSON to SQLite migration")

    try:
        from scripts.migrate_download_state_to_sqlite import migrate_json_to_sqlite

        summary = migrate_json_to_sqlite(
            source_json_path=source_path,
            sqlite_path=target_path,
            force=False,
            backup=backup,
            backup_dir=backup_dir if backup else None,
        )
        result["backup_path"] = str(summary.backup_path) if summary.backup_path else None
        result["summary"] = {
            "migration_id": summary.migration_id,
            "channels_imported": summary.channels_imported,
            "videos_imported": summary.videos_imported,
            "files_imported": sum(summary.files_by_part.values()),
            "warnings": sum(summary.warnings_by_code.values()),
        }
        _validate_first_run_migration(target_path)
    except Exception as exc:
        result["reason"] = f"migration_failed:{type(exc).__name__}: {exc}"
        result["quarantine_paths"] = [
            str(path) for path in _quarantine_sqlite_files_if_safe(target_path, pre_existing_empty_db)
        ]
        _write_migration_log("failed", source_path, target_path, result["reason"])
        return result

    result.update({"ok": True, "migrated": True, "reason": "migrated_json_to_sqlite"})
    _write_migration_log("success", source_path, target_path, json.dumps(result["summary"], ensure_ascii=False))
    return result


def is_sqlite_missing_or_empty(db_path: Path | None = None) -> bool:
    target_path = db_path or db_file()
    if not target_path.exists():
        return True

    try:
        with closing(sqlite3.connect(f"{target_path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
            table_row = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'download_items'
                """
            ).fetchone()
            if table_row is None:
                return True
            row = conn.execute("SELECT 1 FROM download_items LIMIT 1").fetchone()
            return row is None
    except sqlite3.Error:
        return False


def _validate_first_run_migration(target_path: Path) -> None:
    if not db_store.sqlite_has_any_items(target_path):
        raise RuntimeError("SQLite has no download_items after migration")

    with closing(sqlite3.connect(f"{target_path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        indexes = conn.execute("PRAGMA index_list(download_items)").fetchall()
    if not any(row[2] for row in indexes):
        raise RuntimeError("download_items unique identity index is missing after migration")


def _quarantine_sqlite_files_if_safe(target_path: Path, pre_existing_empty_db: bool) -> list[Path]:
    if not target_path.exists() and not Path(f"{target_path}-wal").exists() and not Path(f"{target_path}-shm").exists():
        return []

    if target_path.exists() and not pre_existing_empty_db and not is_sqlite_missing_or_empty(target_path):
        return []

    quarantine_dir = target_path.parent / "backups"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    moved_paths = []

    for path in (target_path, Path(f"{target_path}-wal"), Path(f"{target_path}-shm")):
        if not path.exists():
            continue
        destination = _unique_path(quarantine_dir / f"{path.name}.bad.{timestamp}")
        shutil.move(str(path), destination)
        moved_paths.append(destination)
    return moved_paths


def _write_migration_log(action: str, source_path: Path, target_path: Path, message: str) -> None:
    log_path = target_path.parent / MIGRATION_LOG_NAME
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(
                f"{timestamp}\taction={action}\tsource={source_path}\ttarget={target_path}\tresult={message}\n"
            )
    except OSError:
        pass


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    suffix = 1
    while True:
        candidate = path.with_name(f"{path.name}.{suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1
