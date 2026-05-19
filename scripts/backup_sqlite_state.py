import argparse
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.runtime_paths import data_dir, db_file


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_MISSING_DB = 2


def main() -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Create a safe backup of the SQLite download state database.")
    parser.add_argument(
        "--db",
        metavar="PATH",
        type=Path,
        default=None,
        help="SQLite DB path. Defaults to data/download_state.sqlite3.",
    )
    args = parser.parse_args()

    try:
        backup_path, copied_files = backup_sqlite_state(args.db or db_file())
    except FileNotFoundError as exc:
        print(f"[MISSING] {exc}")
        return EXIT_MISSING_DB
    except Exception as exc:
        print(f"[ERROR] Backup failed: {type(exc).__name__}: {exc}")
        return EXIT_ERROR

    print("SQLite state backup created")
    print(f"backup_path: {backup_path}")
    print("files:")
    for path in copied_files:
        print(f"  {path} ({_format_size(path.stat().st_size)})")
    return EXIT_OK


def backup_sqlite_state(path: Path) -> tuple[Path, list[Path]]:
    source_path = path
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {source_path}")

    backup_dir = data_dir() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = _timestamp()
    backup_path = _unique_path(backup_dir / f"download_state.sqlite3.bak.{timestamp}")

    with closing(_connect_read_only(source_path)) as source_conn:
        with closing(sqlite3.connect(backup_path)) as backup_conn:
            source_conn.backup(backup_conn)

    copied_files = [backup_path]
    for suffix in ("-wal", "-shm"):
        sidecar_path = Path(f"{source_path}{suffix}")
        if not sidecar_path.exists():
            continue
        sidecar_backup_path = _unique_path(backup_dir / f"{sidecar_path.name}.bak.{timestamp}")
        shutil.copy2(sidecar_path, sidecar_backup_path)
        copied_files.append(sidecar_backup_path)

    return backup_path, copied_files


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    suffix = 2
    while True:
        candidate = path.with_name(f"{path.name}.{suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
