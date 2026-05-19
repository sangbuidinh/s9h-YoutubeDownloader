import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.runtime_paths import db_file, state_file
from scripts.migrate_download_state_to_sqlite import PLATFORM, PARTS, normalize_filename_text, normalize_path_text


EXIT_VALID = 0
EXIT_MISMATCH = 1
EXIT_MISSING_FILE = 2
EXIT_EMPTY_DB = 3

MANUAL_OVERRIDE_NULL = "missing/NULL"
MANUAL_OVERRIDE_FALSE = "false/0"
MANUAL_OVERRIDE_TRUE = "true/1"
NULL_KEY = "<NULL>"
GENERATED_UPDATED_AT = "<generated non-empty>"


@dataclass
class ExpectedState:
    channels: set[tuple[str, str, str]] = field(default_factory=set)
    items: dict[tuple[str, str, str, str], dict] = field(default_factory=dict)
    item_order: list[tuple[str, str, str, str]] = field(default_factory=list)
    status_counts: Counter = field(default_factory=Counter)
    manual_override_counts: Counter = field(default_factory=Counter)
    manual_status_counts: Counter = field(default_factory=Counter)
    files_by_part: Counter = field(default_factory=Counter)
    invalid_files_count: int = 0
    warnings_by_code: Counter = field(default_factory=Counter)
    json_channel_count: int = 0
    empty_video_save_base_folder_count: int = 0
    inherited_save_base_folder_count: int = 0
    inherited_identities: set[tuple[str, str, str, str]] = field(default_factory=set)


@dataclass
class DbState:
    channel_count: int = 0
    items: dict[tuple[str, str, str, str], dict] = field(default_factory=dict)
    status_counts: Counter = field(default_factory=Counter)
    manual_override_counts: Counter = field(default_factory=Counter)
    manual_status_counts: Counter = field(default_factory=Counter)
    files_by_part: Counter = field(default_factory=Counter)
    invalid_files_count: int = 0
    warnings_by_code: Counter = field(default_factory=Counter)
    migration_id: str | None = None


@dataclass
class ValidationResult:
    source_json_path: Path
    db_path: Path
    passed: bool
    missing_reason: str | None = None
    empty_reason: str | None = None
    counts: dict = field(default_factory=dict)
    warning_summary: dict = field(default_factory=dict)
    mismatches: list[dict] = field(default_factory=list)
    sample: dict = field(default_factory=dict)
    mode_behavior: dict = field(default_factory=dict)
    recommended_next_action: str = ""


def main() -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(
        description="Validate data/download_state.json against data/download_state.sqlite3 after manual migration."
    )
    parser.add_argument("--verbose", action="store_true", help="Print detailed mismatch examples.")
    parser.add_argument(
        "--sample-size",
        type=int,
        default=20,
        help="Number of deterministic sample items to compare in detail. Default: 20.",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="Optional path for a machine-readable JSON validation report.",
    )
    args = parser.parse_args()

    if args.sample_size < 1:
        print("[ERROR] --sample-size must be at least 1")
        return EXIT_MISMATCH

    result, exit_code = validate_download_state_migration(sample_size=args.sample_size)
    print_report(result, verbose=args.verbose)
    if args.json_report:
        write_json_report(result, args.json_report)
    return exit_code


def validate_download_state_migration(
    source_json_path: Path | None = None,
    sqlite_path: Path | None = None,
    sample_size: int = 20,
) -> tuple[ValidationResult, int]:
    source_path = source_json_path or state_file()
    target_db_path = sqlite_path or db_file()
    result = ValidationResult(source_json_path=source_path, db_path=target_db_path, passed=False)

    if not source_path.exists():
        result.missing_reason = f"JSON state file is missing: {source_path}"
        result.recommended_next_action = "Create or restore data/download_state.json before validating."
        return result, EXIT_MISSING_FILE
    if not target_db_path.exists():
        result.missing_reason = f"SQLite database file is missing: {target_db_path}"
        result.recommended_next_action = "Run scripts/migrate_download_state_to_sqlite.py first."
        return result, EXIT_MISSING_FILE

    try:
        state = _read_json_state(source_path)
        expected = build_expected_state(state)
        db_state = read_db_state(target_db_path)
    except EmptyDatabaseError as exc:
        result.empty_reason = str(exc)
        result.recommended_next_action = "Run scripts/migrate_download_state_to_sqlite.py first."
        return result, EXIT_EMPTY_DB
    except Exception as exc:
        result.mismatches.append(
            {
                "section": "load",
                "message": f"{type(exc).__name__}: {exc}",
            }
        )
        result.recommended_next_action = "Fix the reported load error, then run validation again."
        return result, EXIT_MISMATCH

    compare_counts(expected, db_state, result)
    compare_identities(expected, db_state, result)
    compare_samples(expected, db_state, sample_size, result)
    compare_mode_behavior(expected, db_state, result)

    result.warning_summary = {
        "expected": dict(sorted(expected.warnings_by_code.items())),
        "sqlite": dict(sorted(db_state.warnings_by_code.items())),
    }
    result.passed = not result.mismatches
    if result.passed:
        result.recommended_next_action = "Validation passed. Keep runtime on JSON until the SQLite backend milestone is implemented."
        return result, EXIT_VALID

    result.recommended_next_action = (
        "Review mismatches. If the schema is unchanged, re-run migration with --force and validate again."
    )
    return result, EXIT_MISMATCH


def build_expected_state(state: dict) -> ExpectedState:
    expected = ExpectedState()
    channels = state.get("channels", {})
    if not isinstance(channels, dict):
        raise ValueError("download_state.json field 'channels' must be an object")
    expected.json_channel_count = len(channels)

    seen_paths: dict[str, dict] = {}
    seen_filenames: dict[str, dict] = {}

    for channel_key, channel_record in channels.items():
        channel_id = str(channel_key)
        if not isinstance(channel_record, dict):
            expected.warnings_by_code["malformed_channel_record"] += 1
            continue

        embedded_channel_id = channel_record.get("channel_id")
        if _has_text(embedded_channel_id) and str(embedded_channel_id) != channel_id:
            expected.warnings_by_code["embedded_channel_id_mismatch"] += 1

        channel_name = _nullable_text(channel_record.get("channel_name"))
        channel_save_raw = _text_or_empty(channel_record.get("save_base_folder"))
        channel_save_norm = normalize_path_text(channel_save_raw)
        expected.channels.add((PLATFORM, channel_id, channel_save_norm))

        videos = channel_record.get("videos", {})
        if not isinstance(videos, dict):
            expected.warnings_by_code["malformed_channel_record"] += 1
            continue

        for video_key, video_record in videos.items():
            video_id = str(video_key)
            if not isinstance(video_record, dict):
                expected.warnings_by_code["malformed_video_record"] += 1
                continue

            embedded_video_channel_id = video_record.get("channel_id")
            if _has_text(embedded_video_channel_id) and str(embedded_video_channel_id) != channel_id:
                expected.warnings_by_code["embedded_channel_id_mismatch"] += 1

            embedded_video_id = video_record.get("video_id")
            if _has_text(embedded_video_id) and str(embedded_video_id) != video_id:
                expected.warnings_by_code["embedded_video_id_mismatch"] += 1

            video_save_raw = _text_or_empty(video_record.get("save_base_folder"))
            inherited_save_base_folder = False
            if not _has_text(video_save_raw):
                expected.empty_video_save_base_folder_count += 1
                expected.warnings_by_code["missing_save_base_folder"] += 1
                if _has_text(channel_save_raw):
                    video_save_raw = channel_save_raw
                    inherited_save_base_folder = True
                    expected.inherited_save_base_folder_count += 1
                else:
                    video_save_raw = ""
            video_save_norm = normalize_path_text(video_save_raw)
            expected.channels.add((PLATFORM, channel_id, video_save_norm))

            sanitized_filename_base = _text_or_empty(video_record.get("sanitized_filename_base"))
            if not _has_text(sanitized_filename_base):
                sanitized_filename_base = f"yt_{video_id}"
                expected.warnings_by_code["missing_sanitized_filename_base"] += 1

            identity = (PLATFORM, channel_id, video_id, video_save_norm)
            expected_entry = {
                "platform": PLATFORM,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "video_id": video_id,
                "save_base_folder_raw": video_save_raw,
                "save_base_folder_norm": video_save_norm,
                "original_title": _nullable_text(video_record.get("original_title")),
                "sanitized_filename_base": sanitized_filename_base,
                "status": video_record.get("status") if "status" in video_record else None,
                "manual_status": video_record.get("manual_status") if "manual_status" in video_record else None,
                "manual_override": _manual_override_value(video_record),
                "downloaded_at": _nullable_text(video_record.get("downloaded_at")),
                "updated_at": _nullable_text(video_record.get("updated_at")) or GENERATED_UPDATED_AT,
                "files": {},
                "json_entry": video_record,
            }
            expected.items[identity] = expected_entry
            expected.item_order.append(identity)
            if inherited_save_base_folder:
                expected.inherited_identities.add(identity)

            expected.status_counts[_counter_key(expected_entry["status"])] += 1
            expected.manual_override_counts[_manual_override_counter_key(expected_entry["manual_override"])] += 1
            expected.manual_status_counts[_counter_key(expected_entry["manual_status"])] += 1

            _add_expected_file_parts(
                expected,
                identity,
                video_record,
                seen_paths,
                seen_filenames,
            )

    return expected


def _add_expected_file_parts(
    expected: ExpectedState,
    identity: tuple[str, str, str, str],
    video_record: dict,
    seen_paths: dict[str, dict],
    seen_filenames: dict[str, dict],
) -> None:
    _platform, channel_id, video_id, save_base_folder_norm = identity
    for part in PARTS:
        filename_value = video_record.get(f"{part}_filename")
        path_value = video_record.get(f"{part}_path")
        status = video_record.get(f"{part}_status") if f"{part}_status" in video_record else None
        filename_exists = _has_text(filename_value)
        path_exists = _has_text(path_value)
        status_exists = _has_text(status)
        if not filename_exists and not path_exists and not status_exists:
            continue

        filename_raw = str(filename_value) if filename_exists else None
        path_raw = str(path_value) if path_exists else None
        filename_norm = normalize_filename_text(filename_raw) if filename_exists else None
        path_norm = normalize_path_text(path_raw) if path_exists else None
        is_valid = 1

        if status_exists and not filename_exists:
            is_valid = 0
            expected.warnings_by_code["missing_filename_with_status"] += 1
        if status_exists and not path_exists:
            is_valid = 0
            expected.warnings_by_code["missing_path_with_status"] += 1

        if path_norm:
            current_source = _file_warning_source(channel_id, video_id, part, path_raw, path_norm)
            if path_norm in seen_paths:
                expected.warnings_by_code["duplicate_file_path"] += 1
            else:
                seen_paths[path_norm] = current_source

        if filename_norm:
            current_source = _file_warning_source(channel_id, video_id, part, filename_raw, filename_norm)
            if filename_norm in seen_filenames:
                expected.warnings_by_code["duplicate_filename"] += 1
            else:
                seen_filenames[filename_norm] = current_source

        expected.items[identity]["files"][part] = {
            "filename_raw": filename_raw,
            "path_raw": path_raw,
            "status": status if status_exists else None,
            "is_valid": is_valid,
        }
        expected.files_by_part[part] += 1
        if not is_valid:
            expected.invalid_files_count += 1


def read_db_state(sqlite_path: Path) -> DbState:
    db_state = DbState()
    with closing(_connect_read_only(sqlite_path)) as conn:
        conn.row_factory = sqlite3.Row
        table_names = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        required_tables = {
            "app_meta",
            "channels",
            "download_items",
            "download_files",
            "import_warnings",
        }
        if not required_tables <= table_names:
            raise EmptyDatabaseError("SQLite schema is missing required migration tables.")

        migration_row = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'download_state_json_migration_id'"
        ).fetchone()
        db_state.migration_id = migration_row["value"] if migration_row else None
        item_count = conn.execute("SELECT COUNT(*) AS count FROM download_items").fetchone()["count"]
        if item_count == 0 and not db_state.migration_id:
            raise EmptyDatabaseError("SQLite download_items is empty and migration metadata is missing.")

        db_state.channel_count = conn.execute("SELECT COUNT(*) AS count FROM channels").fetchone()["count"]

        item_rows = conn.execute(
            """
            SELECT
                di.id,
                di.platform,
                di.channel_id,
                di.video_id,
                di.save_base_folder_raw,
                di.save_base_folder_norm,
                di.original_title,
                di.sanitized_filename_base,
                di.status,
                di.manual_status,
                di.manual_override,
                di.downloaded_at,
                di.updated_at,
                c.channel_name
            FROM download_items di
            LEFT JOIN channels c ON c.id = di.channel_db_id
            ORDER BY di.id
            """
        ).fetchall()

        item_id_to_identity = {}
        for row in item_rows:
            identity = (
                row["platform"],
                row["channel_id"],
                row["video_id"],
                row["save_base_folder_norm"],
            )
            item_id_to_identity[row["id"]] = identity
            entry = {
                "platform": row["platform"],
                "channel_id": row["channel_id"],
                "channel_name": row["channel_name"],
                "video_id": row["video_id"],
                "save_base_folder_raw": row["save_base_folder_raw"],
                "save_base_folder_norm": row["save_base_folder_norm"],
                "original_title": row["original_title"],
                "sanitized_filename_base": row["sanitized_filename_base"],
                "status": row["status"],
                "manual_status": row["manual_status"],
                "manual_override": row["manual_override"],
                "downloaded_at": row["downloaded_at"],
                "updated_at": row["updated_at"],
                "files": {},
            }
            db_state.items[identity] = entry
            db_state.status_counts[_counter_key(row["status"])] += 1
            db_state.manual_override_counts[_manual_override_counter_key(row["manual_override"])] += 1
            db_state.manual_status_counts[_counter_key(row["manual_status"])] += 1

        file_rows = conn.execute(
            """
            SELECT item_id, part, status, filename_raw, path_raw, is_valid
            FROM download_files
            ORDER BY item_id, part
            """
        ).fetchall()
        for row in file_rows:
            identity = item_id_to_identity.get(row["item_id"])
            if identity is None:
                continue
            db_state.items[identity]["files"][row["part"]] = {
                "filename_raw": row["filename_raw"],
                "path_raw": row["path_raw"],
                "status": row["status"],
                "is_valid": row["is_valid"],
            }
            db_state.files_by_part[row["part"]] += 1
            if row["is_valid"] == 0:
                db_state.invalid_files_count += 1

        warning_rows = conn.execute(
            """
            SELECT warning_code, COUNT(*) AS count
            FROM import_warnings
            GROUP BY warning_code
            ORDER BY warning_code
            """
        ).fetchall()
        db_state.warnings_by_code = Counter({row["warning_code"]: row["count"] for row in warning_rows})

    return db_state


def compare_counts(expected: ExpectedState, db_state: DbState, result: ValidationResult) -> None:
    counts = {
        "json_channel_records": expected.json_channel_count,
        "channels": _count_pair(len(expected.channels), db_state.channel_count),
        "videos_or_items": _count_pair(len(expected.items), len(db_state.items)),
        "status_counts": _count_pair(dict(sorted(expected.status_counts.items())), dict(sorted(db_state.status_counts.items()))),
        "manual_override_counts": _count_pair(
            _manual_override_counts_dict(expected.manual_override_counts),
            _manual_override_counts_dict(db_state.manual_override_counts),
        ),
        "manual_status_counts": _count_pair(
            dict(sorted(expected.manual_status_counts.items())),
            dict(sorted(db_state.manual_status_counts.items())),
        ),
        "download_files_by_part": _count_pair(
            _part_counts_dict(expected.files_by_part),
            _part_counts_dict(db_state.files_by_part),
        ),
        "invalid_download_files": _count_pair(expected.invalid_files_count, db_state.invalid_files_count),
        "import_warnings_by_code": _count_pair(
            dict(sorted(expected.warnings_by_code.items())),
            dict(sorted(db_state.warnings_by_code.items())),
        ),
        "json_empty_video_save_base_folder": expected.empty_video_save_base_folder_count,
        "items_inherited_save_base_folder_expected": expected.inherited_save_base_folder_count,
    }
    actual_inherited = 0
    for identity in expected.inherited_identities:
        actual_item = db_state.items.get(identity)
        expected_item = expected.items.get(identity)
        if actual_item and expected_item and actual_item["save_base_folder_raw"] == expected_item["save_base_folder_raw"]:
            actual_inherited += 1
    counts["items_inherited_save_base_folder"] = _count_pair(
        expected.inherited_save_base_folder_count,
        actual_inherited,
    )
    result.counts = counts

    _add_count_mismatch(result, "channels", len(expected.channels), db_state.channel_count)
    _add_count_mismatch(result, "videos_or_items", len(expected.items), len(db_state.items))
    _add_count_mismatch(result, "status_counts", dict(expected.status_counts), dict(db_state.status_counts))
    _add_count_mismatch(
        result,
        "manual_override_counts",
        _manual_override_counts_dict(expected.manual_override_counts),
        _manual_override_counts_dict(db_state.manual_override_counts),
    )
    _add_count_mismatch(result, "manual_status_counts", dict(expected.manual_status_counts), dict(db_state.manual_status_counts))
    _add_count_mismatch(result, "download_files_by_part", _part_counts_dict(expected.files_by_part), _part_counts_dict(db_state.files_by_part))
    _add_count_mismatch(result, "invalid_download_files", expected.invalid_files_count, db_state.invalid_files_count)
    _add_count_mismatch(result, "import_warnings_by_code", dict(expected.warnings_by_code), dict(db_state.warnings_by_code))
    _add_count_mismatch(
        result,
        "items_inherited_save_base_folder",
        expected.inherited_save_base_folder_count,
        actual_inherited,
    )


def compare_identities(expected: ExpectedState, db_state: DbState, result: ValidationResult) -> None:
    expected_identities = set(expected.items)
    db_identities = set(db_state.items)
    missing = sorted(expected_identities - db_identities)
    extra = sorted(db_identities - expected_identities)
    if missing:
        result.mismatches.append(
            {
                "section": "identity",
                "message": "SQLite is missing expected item identities.",
                "examples": [_identity_text(identity) for identity in missing[:10]],
                "count": len(missing),
            }
        )
    if extra:
        result.mismatches.append(
            {
                "section": "identity",
                "message": "SQLite has unexpected item identities.",
                "examples": [_identity_text(identity) for identity in extra[:10]],
                "count": len(extra),
            }
        )


def compare_samples(
    expected: ExpectedState,
    db_state: DbState,
    sample_size: int,
    result: ValidationResult,
) -> None:
    sample_identities = choose_sample_identities(expected, sample_size)
    mismatches = []
    for identity in sample_identities:
        expected_item = expected.items.get(identity)
        actual_item = db_state.items.get(identity)
        if actual_item is None:
            mismatches.append(
                {
                    "identity": _identity_text(identity),
                    "field": "item",
                    "expected": "present",
                    "actual": "missing",
                }
            )
            continue
        mismatches.extend(compare_sample_item(identity, expected_item, actual_item))

    result.sample = {
        "requested": sample_size,
        "checked": len(sample_identities),
        "passed": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:50],
    }
    if mismatches:
        result.mismatches.append(
            {
                "section": "sample",
                "message": "Sample item comparison found mismatches.",
                "count": len(mismatches),
                "examples": mismatches[:10],
            }
        )


def compare_sample_item(identity: tuple[str, str, str, str], expected_item: dict, actual_item: dict) -> list[dict]:
    mismatches = []
    fields = (
        "channel_id",
        "video_id",
        "save_base_folder_norm",
        "original_title",
        "sanitized_filename_base",
        "status",
        "manual_status",
        "manual_override",
        "downloaded_at",
        "updated_at",
    )
    for field_name in fields:
        expected_value = expected_item.get(field_name)
        actual_value = actual_item.get(field_name)
        if field_name == "updated_at" and expected_value == GENERATED_UPDATED_AT:
            if _has_text(actual_value):
                continue
        elif expected_value == actual_value:
            continue
        mismatches.append(
            _field_mismatch(identity, field_name, expected_value, actual_value)
        )

    for part in PARTS:
        expected_file = expected_item["files"].get(part)
        actual_file = actual_item["files"].get(part)
        if expected_file is None and actual_file is None:
            continue
        if expected_file is None or actual_file is None:
            mismatches.append(
                _field_mismatch(
                    identity,
                    f"{part}_file",
                    "present" if expected_file else "missing",
                    "present" if actual_file else "missing",
                )
            )
            continue
        for field_name in ("filename_raw", "path_raw", "status", "is_valid"):
            if expected_file.get(field_name) != actual_file.get(field_name):
                mismatches.append(
                    _field_mismatch(
                        identity,
                        f"{part}.{field_name}",
                        expected_file.get(field_name),
                        actual_file.get(field_name),
                    )
                )
    return mismatches


def compare_mode_behavior(expected: ExpectedState, db_state: DbState, result: ValidationResult) -> None:
    try:
        from core.download_modes import DOWNLOAD_MODES
        from core.state_store import get_effective_status, missing_parts_for_mode
    except Exception as exc:
        result.mode_behavior = {
            "passed": False,
            "partial": True,
            "reason": f"Could not import status helpers: {type(exc).__name__}: {exc}",
        }
        result.mismatches.append(
            {
                "section": "mode_behavior",
                "message": result.mode_behavior["reason"],
            }
        )
        return

    mismatches = []
    checked = 0
    for identity in sorted(set(expected.items) & set(db_state.items)):
        json_entry = expected.items[identity]["json_entry"]
        sqlite_entry = sqlite_item_to_state_entry(db_state.items[identity])
        for mode in DOWNLOAD_MODES:
            checked += 1
            json_status = get_effective_status(json_entry, mode)
            sqlite_status = get_effective_status(sqlite_entry, mode)
            if json_status != sqlite_status:
                mismatches.append(
                    {
                        "identity": _identity_text(identity),
                        "mode": mode,
                        "field": "effective_status",
                        "expected": json_status,
                        "actual": sqlite_status,
                    }
                )
            json_missing = missing_parts_for_mode(json_entry, mode)
            sqlite_missing = missing_parts_for_mode(sqlite_entry, mode)
            if json_missing != sqlite_missing:
                mismatches.append(
                    {
                        "identity": _identity_text(identity),
                        "mode": mode,
                        "field": "missing_parts_for_mode",
                        "expected": list(json_missing),
                        "actual": list(sqlite_missing),
                    }
                )

    result.mode_behavior = {
        "passed": not mismatches,
        "partial": False,
        "checked": checked,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:50],
    }
    if mismatches:
        result.mismatches.append(
            {
                "section": "mode_behavior",
                "message": "Mode status behavior comparison found mismatches.",
                "count": len(mismatches),
                "examples": mismatches[:10],
            }
        )


def sqlite_item_to_state_entry(item: dict) -> dict:
    entry = {
        "channel_id": item["channel_id"],
        "channel_name": item.get("channel_name") or "",
        "save_base_folder": item.get("save_base_folder_raw") or "",
        "video_id": item["video_id"],
        "original_title": item.get("original_title") or "",
        "sanitized_filename_base": item.get("sanitized_filename_base") or "",
    }
    for key in ("status", "manual_status", "downloaded_at", "updated_at"):
        if item.get(key) is not None:
            entry[key] = item[key]
    if item.get("manual_override") is not None:
        entry["manual_override"] = item["manual_override"] == 1
    for part, file_row in item["files"].items():
        if file_row.get("filename_raw") is not None:
            entry[f"{part}_filename"] = file_row["filename_raw"]
        if file_row.get("path_raw") is not None:
            entry[f"{part}_path"] = file_row["path_raw"]
        if file_row.get("status") is not None:
            entry[f"{part}_status"] = file_row["status"]
    return entry


def choose_sample_identities(expected: ExpectedState, sample_size: int) -> list[tuple[str, str, str, str]]:
    if not expected.item_order:
        return []
    selected: list[tuple[str, str, str, str]] = []

    def add(identity: tuple[str, str, str, str]) -> None:
        if identity not in selected:
            selected.append(identity)

    for identity in expected.item_order[:5]:
        add(identity)
    for identity in expected.item_order[-5:]:
        add(identity)

    remaining_count = max(0, sample_size - len(selected))
    remaining = [identity for identity in expected.items if identity not in selected]
    pseudo_random = sorted(remaining, key=lambda identity: (_stable_hash(identity), identity))
    pseudo_random = sorted(pseudo_random[:remaining_count])
    for identity in pseudo_random:
        add(identity)

    return selected[:sample_size]


def print_report(result: ValidationResult, verbose: bool = False) -> None:
    print("Download state migration validation")
    print(f"source_json_path: {result.source_json_path}")
    print(f"db_path: {result.db_path}")
    if result.missing_reason:
        print("status: FAIL")
        print(f"reason: {result.missing_reason}")
        print(f"recommended_next_action: {result.recommended_next_action}")
        return
    if result.empty_reason:
        print("status: FAIL")
        print(f"reason: {result.empty_reason}")
        print(f"recommended_next_action: {result.recommended_next_action}")
        return

    print(f"status: {'PASS' if result.passed else 'FAIL'}")
    print("counts:")
    for key, value in result.counts.items():
        if isinstance(value, dict) and "expected" in value and "sqlite" in value:
            print(f"  {key}: expected={value['expected']} sqlite={value['sqlite']}")
        else:
            print(f"  {key}: {value}")

    print("warning_summary:")
    expected_warnings = result.warning_summary.get("expected", {})
    sqlite_warnings = result.warning_summary.get("sqlite", {})
    all_warning_codes = sorted(set(expected_warnings) | set(sqlite_warnings))
    if all_warning_codes:
        for warning_code in all_warning_codes:
            print(
                f"  {warning_code}: expected={expected_warnings.get(warning_code, 0)} "
                f"sqlite={sqlite_warnings.get(warning_code, 0)}"
            )
    else:
        print("  none: 0")

    sample = result.sample
    print(
        "sample_comparison: "
        f"{'PASS' if sample.get('passed') else 'FAIL'} "
        f"checked={sample.get('checked', 0)} mismatches={sample.get('mismatch_count', 0)}"
    )
    mode_behavior = result.mode_behavior
    if mode_behavior.get("partial"):
        print(f"mode_behavior_comparison: PARTIAL reason={mode_behavior.get('reason', '')}")
    else:
        print(
            "mode_behavior_comparison: "
            f"{'PASS' if mode_behavior.get('passed') else 'FAIL'} "
            f"checked={mode_behavior.get('checked', 0)} mismatches={mode_behavior.get('mismatch_count', 0)}"
        )

    if result.mismatches:
        print(f"blocking_mismatches: {len(result.mismatches)}")
        limit = len(result.mismatches) if verbose else min(10, len(result.mismatches))
        for mismatch in result.mismatches[:limit]:
            print(f"  - [{mismatch.get('section')}] {mismatch.get('message')}")
            if verbose:
                detail = {key: value for key, value in mismatch.items() if key not in ("section", "message")}
                if detail:
                    print(f"    detail: {json.dumps(detail, ensure_ascii=False, default=str)}")
        if not verbose and len(result.mismatches) > limit:
            print(f"  ... {len(result.mismatches) - limit} more; rerun with --verbose for details")

    print(f"recommended_next_action: {result.recommended_next_action}")


def write_json_report(result: ValidationResult, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result_to_json(result), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def result_to_json(result: ValidationResult) -> dict:
    return {
        "source_json_path": str(result.source_json_path),
        "db_path": str(result.db_path),
        "passed": result.passed,
        "missing_reason": result.missing_reason,
        "empty_reason": result.empty_reason,
        "counts": result.counts,
        "warning_summary": result.warning_summary,
        "mismatches": result.mismatches,
        "sample": result.sample,
        "mode_behavior": result.mode_behavior,
        "recommended_next_action": result.recommended_next_action,
    }


def _read_json_state(source_path: Path) -> dict:
    with source_path.open("r", encoding="utf-8") as state_file_handle:
        state = json.load(state_file_handle)
    if not isinstance(state, dict):
        raise ValueError("download_state.json must contain a JSON object")
    return state


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _connect_read_only(sqlite_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{sqlite_path.resolve().as_uri()}?mode=ro", uri=True)


def _count_pair(expected, actual) -> dict:
    return {"expected": expected, "sqlite": actual}


def _add_count_mismatch(result: ValidationResult, section: str, expected, actual) -> None:
    if expected == actual:
        return
    result.mismatches.append(
        {
            "section": section,
            "message": "Count mismatch.",
            "expected": expected,
            "actual": actual,
        }
    )


def _part_counts_dict(counter: Counter) -> dict:
    return {part: counter.get(part, 0) for part in PARTS}


def _manual_override_counts_dict(counter: Counter) -> dict:
    return {
        MANUAL_OVERRIDE_NULL: counter.get(MANUAL_OVERRIDE_NULL, 0),
        MANUAL_OVERRIDE_FALSE: counter.get(MANUAL_OVERRIDE_FALSE, 0),
        MANUAL_OVERRIDE_TRUE: counter.get(MANUAL_OVERRIDE_TRUE, 0),
    }


def _manual_override_value(record: dict):
    if "manual_override" not in record:
        return None
    if record.get("manual_override") is True:
        return 1
    if record.get("manual_override") is False:
        return 0
    return None


def _manual_override_counter_key(value) -> str:
    if value == 1:
        return MANUAL_OVERRIDE_TRUE
    if value == 0:
        return MANUAL_OVERRIDE_FALSE
    return MANUAL_OVERRIDE_NULL


def _counter_key(value) -> str:
    return NULL_KEY if value is None else str(value)


def _nullable_text(value) -> str | None:
    if not _has_text(value):
        return None
    return str(value)


def _text_or_empty(value) -> str:
    if value is None:
        return ""
    return str(value)


def _has_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) or value is not None and not isinstance(value, str)


def _file_warning_source(channel_id: str, video_id: str, part: str, raw_value, norm_value: str) -> dict:
    return {
        "platform": PLATFORM,
        "channel_id": channel_id,
        "video_id": video_id,
        "part": part,
        "raw_value": raw_value,
        "normalized_value": norm_value,
    }


def _field_mismatch(identity: tuple[str, str, str, str], field_name: str, expected, actual) -> dict:
    return {
        "identity": _identity_text(identity),
        "field": field_name,
        "expected": expected,
        "actual": actual,
    }


def _identity_text(identity: tuple[str, str, str, str]) -> str:
    platform, channel_id, video_id, save_base_folder_norm = identity
    return (
        f"platform={platform}; channel_id={channel_id}; "
        f"video_id={video_id}; save_base_folder_norm={save_base_folder_norm}"
    )


def _stable_hash(identity: tuple[str, str, str, str]) -> str:
    return hashlib.sha256(_identity_text(identity).encode("utf-8")).hexdigest()


class EmptyDatabaseError(Exception):
    pass


if __name__ == "__main__":
    raise SystemExit(main())
