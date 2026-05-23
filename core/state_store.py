import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from core.download_modes import (
    MODE_VIDEO_THUMB,
    PART_AUDIO,
    PART_THUMB,
    PART_VIDEO,
    required_parts,
)
from core.filename_utils import normalize_output_stem
from core.runtime_paths import data_dir, db_file, state_file

STATE_BACKEND_ENV = "YTDL_STATE_BACKEND"
BACKEND_JSON = "json"
BACKEND_SQLITE = "sqlite"
DEFAULT_BACKEND = BACKEND_SQLITE
_BACKEND_WARNING_KEYS: set[str] = set()
_MIGRATION_RESULTS_BY_PATH: dict[tuple[str, str], dict] = {}
_STATE_BACKEND_INIT_RESULT: dict | None = None

STATUS_NOT_DOWNLOADED = "Chưa tải"
STATUS_DOWNLOADED = "Đã tải"
STATUS_MISSING_THUMB = "Thiếu thumbnail"
STATUS_MISSING_VIDEO = "Thiếu video"
STATUS_MISSING_AUDIO = "Thiếu audio"
STATUS_MISSING_VIDEO_AUDIO = "Thiếu video/audio"
STATUS_MISSING_VIDEO_THUMB = "Thiếu video/thumbnail"
STATUS_MISSING_AUDIO_THUMB = "Thiếu audio/thumbnail"
STATUS_ERROR = "Lỗi tải"

SUPPORTED_STATUS_VALUES = (
    STATUS_NOT_DOWNLOADED,
    STATUS_DOWNLOADED,
    STATUS_MISSING_THUMB,
    STATUS_MISSING_VIDEO,
    STATUS_MISSING_AUDIO,
    STATUS_MISSING_VIDEO_AUDIO,
    STATUS_MISSING_VIDEO_THUMB,
    STATUS_MISSING_AUDIO_THUMB,
    STATUS_ERROR,
)

PART_STATUS_KEYS = {
    PART_VIDEO: "video_status",
    PART_THUMB: "thumb_status",
    PART_AUDIO: "audio_status",
}

PART_PATH_KEYS = {
    PART_VIDEO: "video_path",
    PART_THUMB: "thumb_path",
    PART_AUDIO: "audio_path",
}

PART_FILENAME_KEYS = {
    PART_VIDEO: "video_filename",
    PART_THUMB: "thumb_filename",
    PART_AUDIO: "audio_filename",
}

MISSING_STATUS_BY_PART = {
    PART_VIDEO: STATUS_MISSING_VIDEO,
    PART_THUMB: STATUS_MISSING_THUMB,
    PART_AUDIO: STATUS_MISSING_AUDIO,
}

MISSING_STATUS_BY_PARTS = {
    frozenset({PART_VIDEO}): STATUS_MISSING_VIDEO,
    frozenset({PART_THUMB}): STATUS_MISSING_THUMB,
    frozenset({PART_AUDIO}): STATUS_MISSING_AUDIO,
    frozenset({PART_VIDEO, PART_AUDIO}): STATUS_MISSING_VIDEO_AUDIO,
    frozenset({PART_VIDEO, PART_THUMB}): STATUS_MISSING_VIDEO_THUMB,
    frozenset({PART_AUDIO, PART_THUMB}): STATUS_MISSING_AUDIO_THUMB,
}

PARTS_BY_MISSING_STATUS = {
    STATUS_MISSING_VIDEO: (PART_VIDEO,),
    STATUS_MISSING_THUMB: (PART_THUMB,),
    STATUS_MISSING_AUDIO: (PART_AUDIO,),
    STATUS_MISSING_VIDEO_AUDIO: (PART_VIDEO, PART_AUDIO),
    STATUS_MISSING_VIDEO_THUMB: (PART_VIDEO, PART_THUMB),
    STATUS_MISSING_AUDIO_THUMB: (PART_AUDIO, PART_THUMB),
}


def initialize_state_backend() -> dict:
    global _STATE_BACKEND_INIT_RESULT
    if _STATE_BACKEND_INIT_RESULT is not None:
        return dict(_STATE_BACKEND_INIT_RESULT)

    result = _resolve_state_backend()
    _STATE_BACKEND_INIT_RESULT = dict(result)
    return dict(result)


def get_state_backend_name() -> str:
    return str(initialize_state_backend().get("backend", BACKEND_JSON))


def get_state_backend_reason() -> str:
    return str(initialize_state_backend().get("reason", "unknown"))


def is_sqlite_backend_enabled() -> bool:
    return get_state_backend_name() == BACKEND_SQLITE


def load_state() -> dict:
    if is_sqlite_backend_enabled():
        from core import db_store

        return db_store.load_state()
    return _json_load_state()


def get_channel_video_entries(channel_id: str) -> dict:
    if is_sqlite_backend_enabled():
        from core import db_store

        return db_store.get_channel_video_entries(channel_id)
    return _json_get_channel_video_entries(channel_id)


def get_video_entry(channel_id: str, video_id: str) -> dict | None:
    if is_sqlite_backend_enabled():
        from core import db_store

        return db_store.get_video_entry(channel_id, video_id)
    return _json_get_video_entry(channel_id, video_id)


def _json_load_state() -> dict:
    state_path = state_file()
    if not state_path.exists():
        return _empty_state()
    try:
        with state_path.open("r", encoding="utf-8") as state_file_handle:
            data = json.load(state_file_handle)
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    data.setdefault("version", 1)
    data.setdefault("channels", {})
    if not isinstance(data["channels"], dict):
        data["channels"] = {}
    return data


def _json_get_channel_video_entries(channel_id: str) -> dict:
    if not channel_id:
        return {}
    channel = _json_load_state().get("channels", {}).get(channel_id, {})
    videos = channel.get("videos", {})
    return videos if isinstance(videos, dict) else {}


def _json_get_video_entry(channel_id: str, video_id: str) -> dict | None:
    if not channel_id or not video_id:
        return None
    entry = _json_get_channel_video_entries(channel_id).get(video_id)
    return entry if isinstance(entry, dict) else None


def _resolve_state_backend() -> dict:
    requested = (os.environ.get(STATE_BACKEND_ENV, "") or "").strip().lower()
    if requested == BACKEND_JSON:
        return _backend_result(BACKEND_JSON, "forced_json", requested=requested)
    if requested == BACKEND_SQLITE:
        if _sqlite_backend_ready():
            return _backend_result(BACKEND_SQLITE, _sqlite_ready_reason("forced_sqlite"), requested=requested)
        migration = _try_first_run_migration()
        if migration.get("ok"):
            return _backend_result(
                BACKEND_SQLITE,
                "migrated_json_to_sqlite",
                requested=requested,
                migration=migration,
            )
        _warn_backend_once(
            "sqlite_unavailable_forced",
            _sqlite_unavailable_warning(migration, forced=True),
        )
        return _backend_result(
            BACKEND_JSON,
            _sqlite_fallback_reason(migration, forced=True),
            requested=requested,
            migration=migration,
        )
    if requested:
        _warn_backend_once(
            f"invalid:{requested}",
            f"[WARNING] Unsupported {STATE_BACKEND_ENV}={requested!r}; trying SQLite state backend first.",
        )

    if DEFAULT_BACKEND == BACKEND_SQLITE and _sqlite_backend_ready():
        if requested:
            return _backend_result(
                BACKEND_SQLITE,
                _sqlite_ready_reason("invalid_env_fallback_sqlite"),
                requested=requested,
            )
        return _backend_result(BACKEND_SQLITE, _sqlite_ready_reason("default_sqlite"), requested=requested)

    migration = _try_first_run_migration()
    if migration.get("ok"):
        return _backend_result(
            BACKEND_SQLITE,
            "migrated_json_to_sqlite",
            requested=requested,
            migration=migration,
        )

    if requested:
        reason = _sqlite_fallback_reason(migration, forced=False, invalid_env=True)
    else:
        reason = _sqlite_fallback_reason(migration, forced=False)
    _warn_backend_once(
        "sqlite_unavailable_default",
        _sqlite_unavailable_warning(migration, forced=False),
    )
    return _backend_result(BACKEND_JSON, reason, requested=requested, migration=migration)


def _backend_result(
    backend: str,
    reason: str,
    requested: str = "",
    migration: dict | None = None,
) -> dict:
    return {
        "backend": backend,
        "reason": reason,
        "requested": requested or "",
        "db_path": str(db_file()),
        "json_path": str(state_file()),
        "migration": migration or {},
    }


def _try_first_run_migration() -> dict:
    json_path = state_file()
    sqlite_path = db_file()
    cache_key = (str(json_path), str(sqlite_path))
    cached = _MIGRATION_RESULTS_BY_PATH.get(cache_key)
    if cached is not None:
        return cached

    if not json_path.exists():
        result = {"ok": False, "attempted": False, "skipped": True, "reason": "json_missing"}
        _MIGRATION_RESULTS_BY_PATH[cache_key] = result
        return result

    try:
        from core.state_migration import migrate_json_to_sqlite_if_needed

        result = migrate_json_to_sqlite_if_needed(json_path=json_path, db_path=sqlite_path, backup=True)
    except Exception as exc:
        result = {
            "ok": False,
            "attempted": True,
            "skipped": False,
            "reason": f"migration_error:{type(exc).__name__}: {exc}",
        }
    _MIGRATION_RESULTS_BY_PATH[cache_key] = result
    return result


def _sqlite_ready_reason(default_reason: str) -> str:
    cache_key = (str(state_file()), str(db_file()))
    cached = _MIGRATION_RESULTS_BY_PATH.get(cache_key)
    if cached and cached.get("ok"):
        return "migrated_json_to_sqlite"
    return default_reason


def _sqlite_fallback_reason(
    migration: dict,
    forced: bool = False,
    invalid_env: bool = False,
) -> str:
    if migration.get("attempted"):
        return "migration_failed_fallback_json"
    if forced:
        return "forced_sqlite_unavailable_fallback_json"
    if invalid_env:
        return "invalid_env_sqlite_unavailable_fallback_json"
    return "sqlite_unavailable_fallback_json"


def _sqlite_unavailable_warning(migration: dict, forced: bool) -> str:
    if migration.get("attempted"):
        reason = migration.get("reason") or "unknown migration failure"
        return f"[WARNING] SQLite first-run migration failed ({reason}); using JSON state backend."
    if migration.get("reason") == "json_missing":
        return f"[WARNING] SQLite state DB {db_file()} is missing, empty, or invalid and JSON state is missing; using JSON state backend."
    if forced:
        return f"[WARNING] {STATE_BACKEND_ENV}=sqlite but {db_file()} is missing, empty, or invalid; using JSON state backend."
    return f"[WARNING] SQLite state DB {db_file()} is missing, empty, or invalid; using JSON state backend."


def _sqlite_backend_ready() -> bool:
    try:
        from core import db_store

        return db_store.sqlite_has_any_items(db_file())
    except Exception:
        return False


def _warn_backend_once(key: str, message: str) -> None:
    if key in _BACKEND_WARNING_KEYS:
        return
    _BACKEND_WARNING_KEYS.add(key)
    try:
        print(message, file=sys.stderr)
    except OSError:
        pass


def get_effective_status(entry: dict | None, download_mode: str = MODE_VIDEO_THUMB) -> str:
    """Use download_state.json values only; saved paths are references."""
    if not isinstance(entry, dict):
        return STATUS_NOT_DOWNLOADED

    if entry.get("manual_override") is True:
        manual_status = entry.get("manual_status")
        if isinstance(manual_status, str) and manual_status:
            return manual_status
        return STATUS_NOT_DOWNLOADED

    parts = required_parts(download_mode)
    part_statuses = {part: part_status_from_entry(entry, part) for part in parts}
    if all(status == STATUS_DOWNLOADED for status in part_statuses.values()):
        return STATUS_DOWNLOADED

    downloaded_parts = [part for part, status in part_statuses.items() if status == STATUS_DOWNLOADED]
    failed_parts = [part for part, status in part_statuses.items() if status == STATUS_ERROR]
    if not downloaded_parts and failed_parts:
        return STATUS_ERROR
    if not downloaded_parts and not failed_parts:
        return STATUS_NOT_DOWNLOADED

    missing_parts = [part for part, status in part_statuses.items() if status != STATUS_DOWNLOADED]
    return MISSING_STATUS_BY_PARTS.get(frozenset(missing_parts), STATUS_NOT_DOWNLOADED)


def status_from_entry(entry: dict | None, download_mode: str = MODE_VIDEO_THUMB) -> str:
    return get_effective_status(entry, download_mode)


def part_status_from_entry(entry: dict | None, part: str) -> str:
    if not isinstance(entry, dict):
        return STATUS_NOT_DOWNLOADED

    key = PART_STATUS_KEYS.get(part, "")
    status = entry.get(key)
    if status in (STATUS_NOT_DOWNLOADED, STATUS_DOWNLOADED, STATUS_ERROR):
        return status

    legacy = entry.get("status")
    if legacy == STATUS_DOWNLOADED and part in (PART_VIDEO, PART_THUMB):
        return STATUS_DOWNLOADED
    if legacy == STATUS_MISSING_THUMB:
        return STATUS_DOWNLOADED if part == PART_VIDEO else STATUS_NOT_DOWNLOADED
    if legacy == STATUS_MISSING_VIDEO:
        return STATUS_DOWNLOADED if part == PART_THUMB else STATUS_NOT_DOWNLOADED
    if legacy == STATUS_ERROR:
        return STATUS_ERROR
    return STATUS_NOT_DOWNLOADED


def is_mode_complete(entry: dict | None, download_mode: str) -> bool:
    return get_effective_status(entry, download_mode) == STATUS_DOWNLOADED


def missing_parts_for_mode(entry: dict | None, download_mode: str) -> tuple[str, ...]:
    parts = required_parts(download_mode)
    if not isinstance(entry, dict):
        return parts

    if entry.get("manual_override") is True:
        manual_status = entry.get("manual_status")
        if manual_status == STATUS_DOWNLOADED:
            return ()
        if manual_status in (STATUS_NOT_DOWNLOADED, STATUS_ERROR):
            return parts
        manual_missing = tuple(part for part in PARTS_BY_MISSING_STATUS.get(manual_status, ()) if part in parts)
        if manual_missing:
            return manual_missing
        return parts

    return tuple(part for part in parts if part_status_from_entry(entry, part) != STATUS_DOWNLOADED)


def _status_after_manual_clear(entry: dict, previous_manual_status: str | None, download_mode: str) -> str:
    if (
        isinstance(previous_manual_status, str)
        and previous_manual_status
        and entry.get("status") == previous_manual_status
    ):
        return _effective_status_from_explicit_part_states(entry, download_mode)
    return get_effective_status(entry, download_mode)


def _effective_status_from_explicit_part_states(entry: dict | None, download_mode: str) -> str:
    if not isinstance(entry, dict):
        return STATUS_NOT_DOWNLOADED

    parts = required_parts(download_mode)
    part_statuses = {part: _explicit_part_status_from_entry(entry, part) for part in parts}
    if all(status == STATUS_DOWNLOADED for status in part_statuses.values()):
        return STATUS_DOWNLOADED

    downloaded_parts = [part for part, status in part_statuses.items() if status == STATUS_DOWNLOADED]
    failed_parts = [part for part, status in part_statuses.items() if status == STATUS_ERROR]
    if not downloaded_parts and failed_parts:
        return STATUS_ERROR
    if not downloaded_parts and not failed_parts:
        return STATUS_NOT_DOWNLOADED

    missing_parts = [part for part, status in part_statuses.items() if status != STATUS_DOWNLOADED]
    return MISSING_STATUS_BY_PARTS.get(frozenset(missing_parts), STATUS_NOT_DOWNLOADED)


def _explicit_part_status_from_entry(entry: dict | None, part: str) -> str:
    if not isinstance(entry, dict):
        return STATUS_NOT_DOWNLOADED
    status = entry.get(PART_STATUS_KEYS.get(part, ""))
    if status in (STATUS_NOT_DOWNLOADED, STATUS_DOWNLOADED, STATUS_ERROR):
        return status
    return STATUS_NOT_DOWNLOADED


def update_manual_status(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    status: str,
    paths=None,
) -> None:
    if is_sqlite_backend_enabled():
        _sqlite_update_common_video_fields(channel_id, channel_name, save_base_folder, video, paths)
        from core import db_store

        db_store.update_manual_status(
            channel_id,
            getattr(video, "video_id", ""),
            status,
            save_base_folder=save_base_folder,
        )
        return
    _json_update_manual_status(channel_id, channel_name, save_base_folder, video, status, paths)


def _json_update_manual_status(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    status: str,
    paths=None,
) -> None:
    if status not in SUPPORTED_STATUS_VALUES:
        raise ValueError("Unsupported status")
    if not channel_id or not getattr(video, "video_id", ""):
        return

    state = _json_load_state()
    channel = _ensure_channel(state, channel_id, channel_name, save_base_folder)
    videos = channel.setdefault("videos", {})
    existing = videos.get(video.video_id, {})
    entry = dict(existing) if isinstance(existing, dict) else {}
    _apply_common_video_metadata_fields(entry, channel_id, channel_name, save_base_folder, video, paths)
    entry["manual_status"] = status
    entry["manual_override"] = True
    entry["updated_at"] = _now_iso()
    videos[video.video_id] = entry
    _save_state(state)


def clear_manual_status(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths=None,
    status: str | None = None,
    download_mode: str = MODE_VIDEO_THUMB,
) -> None:
    if is_sqlite_backend_enabled():
        from core import db_store

        if paths is not None or status is not None:
            _sqlite_update_common_video_fields(channel_id, channel_name, save_base_folder, video, paths)
        db_store.clear_manual_status(
            channel_id,
            getattr(video, "video_id", ""),
            save_base_folder=save_base_folder,
        )
        if status in SUPPORTED_STATUS_VALUES:
            db_store.update_video_state(
                channel_id,
                getattr(video, "video_id", ""),
                {"status": status},
                save_base_folder=save_base_folder,
            )
        return
    _json_clear_manual_status(channel_id, channel_name, save_base_folder, video, paths, status, download_mode)


def _json_clear_manual_status(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths=None,
    status: str | None = None,
    download_mode: str = MODE_VIDEO_THUMB,
) -> None:
    if not channel_id or not getattr(video, "video_id", ""):
        return

    state = _json_load_state()
    channel = _ensure_channel(state, channel_id, channel_name, save_base_folder)
    videos = channel.setdefault("videos", {})
    existing = videos.get(video.video_id, {})
    if not isinstance(existing, dict) and paths is None and status is None:
        return
    entry = dict(existing) if isinstance(existing, dict) else {}
    previous_manual_status = entry.get("manual_status")
    _apply_common_video_metadata_fields(entry, channel_id, channel_name, save_base_folder, video, paths)
    entry.pop("manual_status", None)
    entry.pop("manual_override", None)
    if status in SUPPORTED_STATUS_VALUES:
        entry["status"] = status
    else:
        entry["status"] = _status_after_manual_clear(entry, previous_manual_status, download_mode)
    entry["updated_at"] = _now_iso()
    videos[video.video_id] = entry
    _save_state(state)


def update_video_part_state(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths,
    part: str,
    part_status: str,
    download_mode: str = MODE_VIDEO_THUMB,
) -> None:
    if is_sqlite_backend_enabled():
        _sqlite_update_video_part_state(
            channel_id,
            channel_name,
            save_base_folder,
            video,
            paths,
            part,
            part_status,
            download_mode,
        )
        return
    _json_update_video_part_state(
        channel_id,
        channel_name,
        save_base_folder,
        video,
        paths,
        part,
        part_status,
        download_mode,
    )


def _json_update_video_part_state(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths,
    part: str,
    part_status: str,
    download_mode: str = MODE_VIDEO_THUMB,
) -> None:
    if part not in PART_STATUS_KEYS:
        raise ValueError("Unsupported file part")
    if part_status not in (STATUS_NOT_DOWNLOADED, STATUS_DOWNLOADED, STATUS_ERROR):
        raise ValueError("Unsupported part status")
    if not channel_id or not getattr(video, "video_id", ""):
        return

    state = _json_load_state()
    channel = _ensure_channel(state, channel_id, channel_name, save_base_folder)
    videos = channel.setdefault("videos", {})
    existing = videos.get(video.video_id, {})
    entry = dict(existing) if isinstance(existing, dict) else {}
    _apply_common_video_metadata_fields(entry, channel_id, channel_name, save_base_folder, video, paths)
    _apply_part_fields(entry, paths, part, part_status)
    if part_status == STATUS_DOWNLOADED:
        entry.pop("manual_status", None)
        entry.pop("manual_override", None)
        entry["downloaded_at"] = _now_iso()
    entry["updated_at"] = _now_iso()
    entry["status"] = get_effective_status(entry, download_mode)
    videos[video.video_id] = entry
    _save_state(state)


def reconcile_downloaded_item_state(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths,
    download_mode: str = MODE_VIDEO_THUMB,
    run_parts: tuple[str, ...] | None = None,
) -> tuple[str, str]:
    if is_sqlite_backend_enabled():
        return _sqlite_reconcile_downloaded_item_state(
            channel_id,
            channel_name,
            save_base_folder,
            video,
            paths,
            download_mode,
            run_parts,
        )
    return _json_reconcile_downloaded_item_state(
        channel_id,
        channel_name,
        save_base_folder,
        video,
        paths,
        download_mode,
        run_parts,
    )


def _json_reconcile_downloaded_item_state(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths,
    download_mode: str = MODE_VIDEO_THUMB,
    run_parts: tuple[str, ...] | None = None,
) -> tuple[str, str]:
    """Reconcile the current run's final files back into download_state.json."""
    if not channel_id or not getattr(video, "video_id", ""):
        return STATUS_NOT_DOWNLOADED, STATUS_NOT_DOWNLOADED

    state = _json_load_state()
    channel = _ensure_channel(state, channel_id, channel_name, save_base_folder)
    videos = channel.setdefault("videos", {})
    existing = videos.get(video.video_id, {})
    entry = dict(existing) if isinstance(existing, dict) else {}
    old_status = get_effective_status(entry, download_mode)

    _apply_common_video_metadata_fields(entry, channel_id, channel_name, save_base_folder, video, paths)

    has_downloaded_run_part = False
    for part in _normalize_run_parts(run_parts, download_mode):
        part_path = _part_final_path(paths, part)
        if not _file_exists_with_size(part_path):
            continue
        _apply_part_fields(entry, paths, part, STATUS_DOWNLOADED)
        has_downloaded_run_part = True

    if has_downloaded_run_part:
        entry.pop("manual_status", None)
        entry.pop("manual_override", None)

    new_status = get_effective_status(entry, download_mode)
    entry["status"] = new_status
    entry["updated_at"] = _now_iso()
    if new_status == STATUS_DOWNLOADED:
        entry["downloaded_at"] = _now_iso()

    videos[video.video_id] = entry
    _save_state(state)
    return old_status, new_status


def update_video_state(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths,
    status: str,
) -> None:
    if is_sqlite_backend_enabled():
        _sqlite_update_video_state(channel_id, channel_name, save_base_folder, video, paths, status)
        return
    _json_update_video_state(channel_id, channel_name, save_base_folder, video, paths, status)


def _json_update_video_state(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths,
    status: str,
) -> None:
    if not channel_id or not getattr(video, "video_id", ""):
        return

    state = _json_load_state()
    channel = _ensure_channel(state, channel_id, channel_name, save_base_folder)
    videos = channel.setdefault("videos", {})
    existing = videos.get(video.video_id, {})
    entry = dict(existing) if isinstance(existing, dict) else {}
    _apply_common_video_fields(entry, channel_id, channel_name, save_base_folder, video, paths)
    if status == STATUS_DOWNLOADED:
        _apply_part_fields(entry, paths, PART_VIDEO, STATUS_DOWNLOADED)
        _apply_part_fields(entry, paths, PART_THUMB, STATUS_DOWNLOADED)
        entry.pop("manual_status", None)
        entry.pop("manual_override", None)
        entry["downloaded_at"] = _now_iso()
    elif status == STATUS_ERROR:
        _apply_part_fields(entry, paths, PART_VIDEO, STATUS_ERROR)
        _apply_part_fields(entry, paths, PART_THUMB, STATUS_ERROR)
    entry["status"] = status
    entry["updated_at"] = _now_iso()
    videos[video.video_id] = entry
    _save_state(state)


def _sqlite_update_common_video_fields(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths=None,
) -> None:
    if not channel_id or not getattr(video, "video_id", ""):
        return
    from core import db_store

    updates = _sqlite_common_video_updates(channel_name, video, paths)
    db_store.update_video_state(
        channel_id,
        video.video_id,
        updates,
        save_base_folder=save_base_folder,
    )


def _sqlite_update_video_part_state(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths,
    part: str,
    part_status: str,
    download_mode: str,
) -> None:
    if part not in PART_STATUS_KEYS:
        raise ValueError("Unsupported file part")
    if part_status not in (STATUS_NOT_DOWNLOADED, STATUS_DOWNLOADED, STATUS_ERROR):
        raise ValueError("Unsupported part status")
    if not channel_id or not getattr(video, "video_id", ""):
        return
    from core import db_store

    _sqlite_update_common_video_fields(channel_id, channel_name, save_base_folder, video, paths)
    filename, file_path = _sqlite_part_file_values(paths, part)
    db_store.update_video_part_state(
        channel_id,
        video.video_id,
        part,
        filename=filename,
        file_path=file_path,
        status=part_status,
        save_base_folder=save_base_folder,
        download_mode=download_mode,
        **_sqlite_common_video_updates(channel_name, video, paths),
    )


def _sqlite_reconcile_downloaded_item_state(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths,
    download_mode: str,
    run_parts: tuple[str, ...] | None = None,
) -> tuple[str, str]:
    if not channel_id or not getattr(video, "video_id", ""):
        return STATUS_NOT_DOWNLOADED, STATUS_NOT_DOWNLOADED
    from core import db_store

    existing_entry = db_store.get_video_entry(channel_id, video.video_id, save_base_folder=save_base_folder)
    old_status = get_effective_status(existing_entry, download_mode)
    _sqlite_update_common_video_fields(channel_id, channel_name, save_base_folder, video, paths)

    has_downloaded_run_part = False
    for part in _normalize_run_parts(run_parts, download_mode):
        part_path = _part_final_path(paths, part)
        if not _file_exists_with_size(part_path):
            continue
        filename, file_path = _sqlite_part_file_values(paths, part)
        db_store.update_video_part_state(
            channel_id,
            video.video_id,
            part,
            filename=filename,
            file_path=file_path,
            status=STATUS_DOWNLOADED,
            save_base_folder=save_base_folder,
            download_mode=download_mode,
            **_sqlite_common_video_updates(channel_name, video, paths),
        )
        has_downloaded_run_part = True

    if has_downloaded_run_part:
        db_store.clear_manual_status(channel_id, video.video_id, save_base_folder=save_base_folder)

    new_entry = db_store.get_video_entry(channel_id, video.video_id, save_base_folder=save_base_folder)
    new_status = get_effective_status(new_entry, download_mode)
    db_store.update_video_state(
        channel_id,
        video.video_id,
        {"status": new_status, **({"downloaded_at": _now_iso()} if new_status == STATUS_DOWNLOADED else {})},
        save_base_folder=save_base_folder,
    )
    return old_status, new_status


def _sqlite_update_video_state(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths,
    status: str,
) -> None:
    if not channel_id or not getattr(video, "video_id", ""):
        return
    from core import db_store

    updates = _sqlite_common_video_updates(channel_name, video, paths)
    if paths is not None:
        updates.update(
            {
                "video_filename": paths.video_path.name,
                "thumb_filename": paths.thumb_path.name,
                "audio_filename": paths.audio_path.name,
                "video_path": str(paths.video_path),
                "thumb_path": str(paths.thumb_path),
                "audio_path": str(paths.audio_path),
            }
        )
    if status == STATUS_DOWNLOADED:
        updates["video_status"] = STATUS_DOWNLOADED
        updates["thumb_status"] = STATUS_DOWNLOADED
        updates["downloaded_at"] = _now_iso()
    elif status == STATUS_ERROR:
        updates["video_status"] = STATUS_ERROR
        updates["thumb_status"] = STATUS_ERROR
    updates["status"] = status
    db_store.update_video_state(
        channel_id,
        video.video_id,
        updates,
        save_base_folder=save_base_folder,
    )


def _sqlite_common_video_updates(channel_name: str, video, paths=None) -> dict:
    updates = {
        "channel_name": channel_name,
        "original_title": getattr(video, "title", ""),
    }
    filename_base = getattr(video, "sanitized_filename_base", "")
    if paths is not None:
        filename_base = filename_base or paths.video_path.stem
    if filename_base:
        updates["sanitized_filename_base"] = normalize_output_stem(filename_base)
    if hasattr(video, "display_order"):
        updates["display_order_at_download"] = video.display_order
    return updates


def _sqlite_part_file_values(paths, part: str) -> tuple[str | None, str | None]:
    if paths is None:
        return None, None
    if part == PART_VIDEO:
        return paths.video_path.name, str(paths.video_path)
    if part == PART_THUMB:
        return paths.thumb_path.name, str(paths.thumb_path)
    if part == PART_AUDIO:
        return paths.audio_path.name, str(paths.audio_path)
    raise ValueError("Unsupported file part")


def _normalize_run_parts(run_parts, download_mode: str) -> tuple[str, ...]:
    required = tuple(required_parts(download_mode))
    if run_parts is None:
        return required

    normalized = []
    seen = set()
    for part in run_parts:
        if part not in required or part in seen:
            continue
        normalized.append(part)
        seen.add(part)
    return tuple(normalized)


def _ensure_channel(state: dict, channel_id: str, channel_name: str, save_base_folder: str) -> dict:
    channels = state.setdefault("channels", {})
    channel = channels.setdefault(
        channel_id,
        {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "save_base_folder": str(save_base_folder),
            "videos": {},
        },
    )
    channel["channel_id"] = channel_id
    channel["channel_name"] = channel_name
    channel["save_base_folder"] = str(save_base_folder)
    return channel


def _apply_common_video_fields(
    entry: dict,
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths=None,
) -> None:
    _apply_common_video_metadata_fields(entry, channel_id, channel_name, save_base_folder, video, paths)
    if paths is not None:
        entry["video_filename"] = paths.video_path.name
        entry["thumb_filename"] = paths.thumb_path.name
        entry["audio_filename"] = paths.audio_path.name
        entry["video_path"] = str(paths.video_path)
        entry["thumb_path"] = str(paths.thumb_path)
        entry["audio_path"] = str(paths.audio_path)


def _apply_common_video_metadata_fields(
    entry: dict,
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths=None,
) -> None:
    entry["channel_id"] = channel_id
    entry["channel_name"] = channel_name
    entry["save_base_folder"] = str(save_base_folder)
    entry["video_id"] = video.video_id
    entry["original_title"] = getattr(video, "title", "")
    filename_base = getattr(video, "sanitized_filename_base", "")
    if paths is not None:
        filename_base = filename_base or paths.video_path.stem
    entry["sanitized_filename_base"] = normalize_output_stem(filename_base)
    if hasattr(video, "display_order"):
        entry["display_order_at_download"] = video.display_order


def _apply_part_fields(entry: dict, paths, part: str, part_status: str) -> None:
    entry[PART_STATUS_KEYS[part]] = part_status
    if paths is None:
        return
    if part == PART_VIDEO:
        entry[PART_FILENAME_KEYS[part]] = paths.video_path.name
        entry[PART_PATH_KEYS[part]] = str(paths.video_path)
    elif part == PART_THUMB:
        entry[PART_FILENAME_KEYS[part]] = paths.thumb_path.name
        entry[PART_PATH_KEYS[part]] = str(paths.thumb_path)
    elif part == PART_AUDIO:
        entry[PART_FILENAME_KEYS[part]] = paths.audio_path.name
        entry[PART_PATH_KEYS[part]] = str(paths.audio_path)


def _part_final_path(paths, part: str) -> Path:
    if part == PART_VIDEO:
        return paths.video_path
    if part == PART_THUMB:
        return paths.thumb_path
    if part == PART_AUDIO:
        return paths.audio_path
    raise ValueError("Unsupported file part")


def _file_exists_with_size(path: Path) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _save_state(state: dict) -> None:
    state_data_dir = data_dir()
    state_data_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=".download_state_",
        suffix=".tmp",
        dir=str(state_data_dir),
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with open(fd, "w", encoding="utf-8") as temp_file:
            json.dump(state, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
        _replace_with_retry(temp_path, state_file())
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _replace_with_retry(temp_path: Path, target_path: Path) -> None:
    last_error: OSError | None = None
    for delay in (0, 1, 3, 5):
        if delay:
            time.sleep(delay)
        try:
            temp_path.replace(target_path)
            return
        except OSError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error


def _empty_state() -> dict:
    return {"version": 1, "channels": {}}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
