import argparse
import json
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import db_store
from core.runtime_paths import data_dir, db_file, state_file


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_MISSING_DB = 2
FILE_PARTS = ("video", "thumb", "audio")


def main() -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Export SQLite download state to a JSON snapshot.")
    parser.add_argument(
        "--output",
        metavar="PATH",
        type=Path,
        default=None,
        help="Output JSON path. Defaults to data/download_state.sqlite_export.YYYYMMDD-HHMMSS.json.",
    )
    parser.add_argument(
        "--overwrite-runtime-json",
        action="store_true",
        help="Replace data/download_state.json after backing up the existing runtime JSON first.",
    )
    args = parser.parse_args()

    try:
        export_path, backup_path, summary = export_sqlite_state_to_json(
            output_path=args.output,
            overwrite_runtime_json=args.overwrite_runtime_json,
        )
    except FileNotFoundError as exc:
        print(f"[MISSING] {exc}")
        return EXIT_MISSING_DB
    except Exception as exc:
        print(f"[ERROR] Export failed: {type(exc).__name__}: {exc}")
        return EXIT_ERROR

    print("SQLite state exported to JSON")
    print(f"db_path: {db_file()}")
    print(f"export_path: {export_path}")
    print(f"runtime_json_overwritten: {args.overwrite_runtime_json}")
    print(f"runtime_json_backup_path: {backup_path if backup_path else '<not created>'}")
    print("counts:")
    print(f"  channels: {summary['channels']}")
    print(f"  videos: {summary['videos']}")
    print(f"  files_by_part: {dict(sorted(summary['files_by_part'].items()))}")
    print(f"  status_counts: {dict(sorted(summary['status_counts'].items()))}")
    print(f"  manual_override_counts: {dict(sorted(summary['manual_override_counts'].items()))}")
    print(f"  manual_status_counts: {dict(sorted(summary['manual_status_counts'].items()))}")
    return EXIT_OK


def export_sqlite_state_to_json(
    output_path: Path | None = None,
    overwrite_runtime_json: bool = False,
) -> tuple[Path, Path | None, dict]:
    sqlite_path = db_file()
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {sqlite_path}")

    state = db_store.load_state(sqlite_path)
    summary = _state_summary(state)
    timestamp = _timestamp()

    if overwrite_runtime_json:
        target_path = state_file()
        backup_path = _backup_runtime_json(timestamp)
    else:
        target_path = output_path or data_dir() / f"download_state.sqlite_export.{timestamp}.json"
        backup_path = None
        if output_path is None:
            target_path = _unique_path(target_path)
        elif target_path.exists():
            raise FileExistsError(f"Refusing to overwrite existing output file: {target_path}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(target_path, state)
    return target_path, backup_path, summary


def _backup_runtime_json(timestamp: str) -> Path | None:
    runtime_json_path = state_file()
    if not runtime_json_path.exists():
        return None
    backup_dir = data_dir() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = _unique_path(backup_dir / f"download_state.json.bak.{timestamp}")
    shutil.copy2(runtime_json_path, backup_path)
    return backup_path


def _write_json_atomic(path: Path, state: dict) -> None:
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with open(fd, "w", encoding="utf-8") as temp_file:
            json.dump(state, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _state_summary(state: dict) -> dict:
    channels = state.get("channels", {})
    if not isinstance(channels, dict):
        channels = {}

    video_count = 0
    files_by_part = Counter()
    status_counts = Counter()
    manual_override_counts = Counter()
    manual_status_counts = Counter()

    for channel in channels.values():
        if not isinstance(channel, dict):
            continue
        videos = channel.get("videos", {})
        if not isinstance(videos, dict):
            continue
        for entry in videos.values():
            if not isinstance(entry, dict):
                continue
            video_count += 1
            status_counts[_counter_key(entry.get("status"))] += 1
            manual_override_counts[_manual_override_key(entry.get("manual_override"))] += 1
            manual_status_counts[_counter_key(entry.get("manual_status"))] += 1
            for part in FILE_PARTS:
                if any(entry.get(f"{part}_{field}") is not None for field in ("filename", "path", "status")):
                    files_by_part[part] += 1

    return {
        "channels": len(channels),
        "videos": video_count,
        "files_by_part": files_by_part,
        "status_counts": status_counts,
        "manual_override_counts": manual_override_counts,
        "manual_status_counts": manual_status_counts,
    }


def _counter_key(value) -> str:
    return "<NULL>" if value is None else str(value)


def _manual_override_key(value) -> str:
    if value is True:
        return "true/1"
    if value is False:
        return "false/0"
    return "missing/NULL"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    suffix = 2
    while True:
        candidate = path.with_name(f"{path.stem}.{suffix}{path.suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
