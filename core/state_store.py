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
from core.runtime_paths import db_file


STATUS_NOT_DOWNLOADED = "Chưa tải"
STATUS_DOWNLOADED = "Đã tải"
STATUS_MISSING_THUMB = "Thiếu thumbnail"
STATUS_MISSING_VIDEO = "Thiếu video"
STATUS_MISSING_AUDIO = "Thiếu audio"
STATUS_MISSING_VIDEO_AUDIO = "Thiếu video/audio"
STATUS_MISSING_VIDEO_THUMB = "Thiếu video/thumbnail"
STATUS_MISSING_AUDIO_THUMB = "Thiếu audio/thumbnail"
STATUS_ERROR = "Lỗi tải"

SQLITE_OPEN_ERROR_MESSAGE = "Không thể mở cơ sở dữ liệu SQLite."

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


class SQLiteStateError(RuntimeError):
    pass


def initialize_sqlite_state() -> Path:
    from core import db_store

    try:
        return db_store.initialize_database(db_file())
    except db_store.DatabaseTooNewError as exc:
        raise SQLiteStateError(f"Phiên bản cơ sở dữ liệu SQLite mới hơn ứng dụng này. {exc}") from exc
    except db_store.DatabaseBackupError as exc:
        raise SQLiteStateError(f"Không thể tạo bản sao lưu SQLite trước khi nâng cấp. {exc}") from exc
    except db_store.DatabaseMigrationError as exc:
        raise SQLiteStateError(f"Không thể nâng cấp lược đồ SQLite. {exc}") from exc
    except db_store.DatabaseLockError as exc:
        raise SQLiteStateError(f"Cơ sở dữ liệu SQLite đang bị khóa sau thời gian chờ giới hạn. {exc}") from exc
    except db_store.DatabasePathError as exc:
        raise SQLiteStateError(f"Không thể ghi vào đường dẫn cơ sở dữ liệu SQLite. {exc}") from exc
    except db_store.DatabaseFileChangedError as exc:
        raise SQLiteStateError(f"Tệp cơ sở dữ liệu SQLite đã bị xóa hoặc thay thế. {exc}") from exc
    except db_store.DatabaseSchemaError as exc:
        raise SQLiteStateError(f"Lược đồ SQLite không hợp lệ. {exc}") from exc
    except Exception as exc:
        raise SQLiteStateError(f"{SQLITE_OPEN_ERROR_MESSAGE} {type(exc).__name__}: {exc}") from exc


def load_state() -> dict:
    from core import db_store

    initialize_sqlite_state()
    return db_store.load_state()


def get_channel_video_entries(channel_id: str, save_base_folder: str | None = None) -> dict:
    from core import db_store

    # save_base_folder is accepted for backward compatibility only.
    # Status is video-scoped, not folder-scoped.
    initialize_sqlite_state()
    return db_store.get_channel_video_entries(channel_id)


def get_video_entry(
    channel_id: str,
    video_id: str,
    save_base_folder: str | None = None,
) -> dict | None:
    from core import db_store

    # save_base_folder is accepted for backward compatibility only.
    # Status is video-scoped, not folder-scoped.
    initialize_sqlite_state()
    return db_store.get_video_entry(channel_id, video_id)


def get_effective_status(entry: dict | None, download_mode: str = MODE_VIDEO_THUMB) -> str:
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
        if _has_explicit_part_status(entry):
            return STATUS_NOT_DOWNLOADED
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


def _has_explicit_part_status(entry: dict | None) -> bool:
    if not isinstance(entry, dict):
        return False
    return any(
        entry.get(key) in (STATUS_NOT_DOWNLOADED, STATUS_DOWNLOADED, STATUS_ERROR)
        for key in PART_STATUS_KEYS.values()
    )


def update_manual_status(
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

    _sqlite_update_common_video_fields(channel_id, channel_name, save_base_folder, video, paths)
    from core import db_store

    db_store.update_manual_status(
        channel_id,
        getattr(video, "video_id", ""),
        status,
        save_base_folder=save_base_folder,
    )


def clear_manual_status(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths=None,
    status: str | None = None,
    download_mode: str = MODE_VIDEO_THUMB,
) -> None:
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


def reconcile_downloaded_item_state(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths,
    download_mode: str = MODE_VIDEO_THUMB,
    run_parts: tuple[str, ...] | None = None,
) -> tuple[str, str]:
    return _sqlite_reconcile_downloaded_item_state(
        channel_id,
        channel_name,
        save_base_folder,
        video,
        paths,
        download_mode,
        run_parts,
    )


def update_video_state(
    channel_id: str,
    channel_name: str,
    save_base_folder: str,
    video,
    paths,
    status: str,
) -> None:
    _sqlite_update_video_state(channel_id, channel_name, save_base_folder, video, paths, status)


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
