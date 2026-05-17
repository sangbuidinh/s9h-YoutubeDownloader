import json
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
from core.runtime_paths import data_dir, state_file

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


def load_state() -> dict:
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


def get_channel_video_entries(channel_id: str) -> dict:
    if not channel_id:
        return {}
    channel = load_state().get("channels", {}).get(channel_id, {})
    videos = channel.get("videos", {})
    return videos if isinstance(videos, dict) else {}


def get_video_entry(channel_id: str, video_id: str) -> dict | None:
    if not channel_id or not video_id:
        return None
    entry = get_channel_video_entries(channel_id).get(video_id)
    return entry if isinstance(entry, dict) else None


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

    state = load_state()
    channel = _ensure_channel(state, channel_id, channel_name, save_base_folder)
    videos = channel.setdefault("videos", {})
    existing = videos.get(video.video_id, {})
    entry = dict(existing) if isinstance(existing, dict) else {}
    _apply_common_video_fields(entry, channel_id, channel_name, save_base_folder, video, paths)
    entry["status"] = status
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
    if not channel_id or not getattr(video, "video_id", ""):
        return

    state = load_state()
    channel = _ensure_channel(state, channel_id, channel_name, save_base_folder)
    videos = channel.setdefault("videos", {})
    existing = videos.get(video.video_id, {})
    if not isinstance(existing, dict) and paths is None and status is None:
        return
    entry = dict(existing) if isinstance(existing, dict) else {}
    _apply_common_video_fields(entry, channel_id, channel_name, save_base_folder, video, paths)
    entry.pop("manual_status", None)
    entry.pop("manual_override", None)
    if status in SUPPORTED_STATUS_VALUES:
        entry["status"] = status
    else:
        entry["status"] = get_effective_status(entry, download_mode)
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
    if part not in PART_STATUS_KEYS:
        raise ValueError("Unsupported file part")
    if part_status not in (STATUS_NOT_DOWNLOADED, STATUS_DOWNLOADED, STATUS_ERROR):
        raise ValueError("Unsupported part status")
    if not channel_id or not getattr(video, "video_id", ""):
        return

    state = load_state()
    channel = _ensure_channel(state, channel_id, channel_name, save_base_folder)
    videos = channel.setdefault("videos", {})
    existing = videos.get(video.video_id, {})
    entry = dict(existing) if isinstance(existing, dict) else {}
    _apply_common_video_fields(entry, channel_id, channel_name, save_base_folder, video, paths)
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
) -> tuple[str, str]:
    """Reconcile the current run's final files back into download_state.json."""
    if not channel_id or not getattr(video, "video_id", ""):
        return STATUS_NOT_DOWNLOADED, STATUS_NOT_DOWNLOADED

    state = load_state()
    channel = _ensure_channel(state, channel_id, channel_name, save_base_folder)
    videos = channel.setdefault("videos", {})
    existing = videos.get(video.video_id, {})
    entry = dict(existing) if isinstance(existing, dict) else {}
    old_status = get_effective_status(entry, download_mode)

    _apply_common_video_fields(entry, channel_id, channel_name, save_base_folder, video, paths)

    has_downloaded_required_part = False
    for part in required_parts(download_mode):
        part_path = _part_final_path(paths, part)
        part_status = STATUS_DOWNLOADED if _file_exists_with_size(part_path) else STATUS_NOT_DOWNLOADED
        has_downloaded_required_part = has_downloaded_required_part or part_status == STATUS_DOWNLOADED
        _apply_part_fields(entry, paths, part, part_status)

    if has_downloaded_required_part:
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
    if not channel_id or not getattr(video, "video_id", ""):
        return

    state = load_state()
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
    entry["channel_id"] = channel_id
    entry["channel_name"] = channel_name
    entry["save_base_folder"] = str(save_base_folder)
    entry["video_id"] = video.video_id
    entry["original_title"] = getattr(video, "title", "")
    filename_base = getattr(video, "sanitized_filename_base", "")
    if paths is not None:
        filename_base = filename_base or paths.video_path.stem
    entry["sanitized_filename_base"] = filename_base
    if hasattr(video, "display_order"):
        entry.setdefault("display_order_at_download", video.display_order)
    if paths is not None:
        entry["video_filename"] = paths.video_path.name
        entry["thumb_filename"] = paths.thumb_path.name
        entry["audio_filename"] = paths.audio_path.name
        entry["video_path"] = str(paths.video_path)
        entry["thumb_path"] = str(paths.thumb_path)
        entry["audio_path"] = str(paths.audio_path)


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
