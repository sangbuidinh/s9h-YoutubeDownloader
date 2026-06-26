from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import app_settings


DEFAULT_BRIDGE_DIAGNOSTICS_LOG = Path(
    r"D:\s9h-youtube-cookie-bridge\data\runtime\bridge_diagnostics.log"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Downloader bridge cookie path with Cookie Bridge metadata diagnostics."
    )
    parser.add_argument(
        "--bridge-log",
        default=str(DEFAULT_BRIDGE_DIAGNOSTICS_LOG),
        help="Path to bridge_diagnostics.log.",
    )
    args = parser.parse_args()

    downloader_path = app_settings.load_bridge_cookie_path()
    bridge_log_path = Path(args.bridge_log)
    latest_record = _latest_bridge_record(bridge_log_path)
    latest_bridge_path = _record_path(latest_record)

    print("Downloader Cookie Bridge path diagnostics")
    print(f"downloader_settings_path: {app_settings.app_settings_file()}")
    print(f"downloader_cookie_source: {app_settings.load_cookie_source()}")
    print(f"downloader_bridge_cookie_path: {downloader_path}")
    print(f"downloader_bridge_cookie_metadata: {_metadata_text(Path(downloader_path))}")
    print(f"bridge_diagnostics_log: {bridge_log_path}")
    print(f"bridge_diagnostics_log_metadata: {_metadata_text(bridge_log_path)}")
    print(f"latest_bridge_record_timestamp: {_record_value(latest_record, 'timestamp')}")
    print(f"latest_bridge_record_action: {_record_value(latest_record, 'action')}")
    print(f"latest_bridge_record_success: {_record_value(latest_record, 'success')}")
    print(f"latest_bridge_output_path: {latest_bridge_path or '-'}")
    if latest_bridge_path:
        print(f"latest_bridge_output_metadata: {_metadata_text(Path(latest_bridge_path))}")
        print(f"same_path: {'yes' if _same_path(downloader_path, latest_bridge_path) else 'no'}")
    else:
        print("same_path: unknown")
    print("downloader_saw_mtime_update_after_error: unknown; compare [COOKIE-DIAG] timestamps in Downloader log")
    print("bridge_exported_during_downloader_wait_window: unknown; compare bridge log timestamp with [COOKIE-DIAG] waiting")
    print("retry_used_same_cookie_path: unknown; check [COOKIE-DIAG] retry_uses_same_cookie_path")
    return 0


def _metadata_text(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return "exists=false size=0 mtime_ns=- mtime=-"
    if not path.is_file():
        return "exists=false size=0 mtime_ns=- mtime=-"
    return (
        f"exists=true size={stat.st_size} mtime_ns={stat.st_mtime_ns} "
        f"mtime={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime_ns / 1_000_000_000))}"
    )


def _latest_bridge_record(path: Path) -> dict[str, Any] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in reversed(lines):
        record = _parse_record(line)
        if record:
            return record
    return None


def _parse_record(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text:
        return None
    if text.startswith("[BRIDGE-DIAG]"):
        text = text[len("[BRIDGE-DIAG]") :].strip()
    try:
        record = json.loads(text)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def _record_path(record: dict[str, Any] | None) -> str:
    if not record:
        return ""
    for key in ("output_cookie_path", "path", "written_to"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _record_value(record: dict[str, Any] | None, key: str) -> str:
    if not record:
        return "-"
    value = record.get(key)
    return "-" if value is None else str(value)


def _same_path(left: str, right: str) -> bool:
    try:
        left_path = Path(left).expanduser().resolve(strict=False)
        right_path = Path(right).expanduser().resolve(strict=False)
    except OSError:
        return False
    return os.path.normcase(str(left_path)) == os.path.normcase(str(right_path))


if __name__ == "__main__":
    raise SystemExit(main())
