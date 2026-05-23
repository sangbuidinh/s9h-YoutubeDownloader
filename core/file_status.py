from dataclasses import dataclass
from pathlib import Path

from core.download_modes import MODE_VIDEO_THUMB, mode_includes_audio
from core.filename_utils import (
    assign_unique_title_bases,
    expected_audio_name,
    expected_thumb_name,
    expected_video_name,
    normalize_output_stem,
    sanitize_channel_name,
)
from core.state_store import (
    STATUS_DOWNLOADED,
    STATUS_ERROR,
    STATUS_MISSING_AUDIO,
    STATUS_MISSING_AUDIO_THUMB,
    STATUS_MISSING_THUMB,
    STATUS_MISSING_VIDEO,
    STATUS_MISSING_VIDEO_AUDIO,
    STATUS_MISSING_VIDEO_THUMB,
    STATUS_NOT_DOWNLOADED,
    get_effective_status,
    get_channel_video_entries,
)


@dataclass(frozen=True)
class OutputPaths:
    channel_dir: Path
    video_dir: Path
    thumb_dir: Path
    audio_dir: Path
    video_path: Path
    thumb_path: Path
    audio_path: Path


def channel_dir_for(base_folder: str | Path, channel_name: str) -> Path:
    return Path(base_folder) / sanitize_channel_name(channel_name)


def build_output_paths(
    base_folder: str | Path,
    channel_name: str,
    filename_base: str,
) -> OutputPaths:
    channel_dir = channel_dir_for(base_folder, channel_name)
    video_dir = channel_dir / "video"
    thumb_dir = channel_dir / "thumb"
    audio_dir = channel_dir / "audio"
    safe_filename_base = normalize_output_stem(filename_base)
    return OutputPaths(
        channel_dir=channel_dir,
        video_dir=video_dir,
        thumb_dir=thumb_dir,
        audio_dir=audio_dir,
        video_path=video_dir / expected_video_name(safe_filename_base),
        thumb_path=thumb_dir / expected_thumb_name(safe_filename_base),
        audio_path=audio_dir / expected_audio_name(safe_filename_base),
    )


def ensure_output_dirs(
    base_folder: str | Path,
    channel_name: str,
    download_mode: str = MODE_VIDEO_THUMB,
) -> tuple[Path, Path, Path]:
    channel_dir = channel_dir_for(base_folder, channel_name)
    video_dir = channel_dir / "video"
    thumb_dir = channel_dir / "thumb"
    audio_dir = channel_dir / "audio"
    video_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    if mode_includes_audio(download_mode):
        audio_dir.mkdir(parents=True, exist_ok=True)
    return channel_dir, video_dir, thumb_dir


def apply_statuses(
    videos: list,
    base_folder: str,
    channel_name: str,
    channel_id: str = "",
    download_mode: str = MODE_VIDEO_THUMB,
    warning_callback=None,
) -> None:
    assign_unique_title_bases(videos)
    state_entries = get_channel_video_entries(channel_id) if channel_id else {}
    for video in videos:
        state_entry = state_entries.get(video.video_id) if state_entries else None
        if isinstance(state_entry, dict):
            video.status = get_effective_status(state_entry, download_mode)
            if state_entry.get("manual_override") is True and warning_callback:
                warning_callback(f"[INFO] Manual status override applied: {video.title} -> {video.status}")
            continue

        video.status = STATUS_NOT_DOWNLOADED


def should_show_not_downloaded(video) -> bool:
    return getattr(video, "status", STATUS_NOT_DOWNLOADED) != STATUS_DOWNLOADED
