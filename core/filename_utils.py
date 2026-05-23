import re
from pathlib import Path


MAX_FILENAME_BASE_LENGTH = 120
MEDIA_OUTPUT_SUFFIXES = (
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".mp3",
    ".m4a",
    ".aac",
    ".wav",
)
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _avoid_reserved_name(name: str) -> str:
    if name.upper() in WINDOWS_RESERVED_NAMES:
        return f"{name}_"
    return name


def sanitize_channel_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(" .")
    if not cleaned:
        cleaned = "Channel"
    cleaned = _avoid_reserved_name(cleaned)
    return cleaned[:120].rstrip(" .") or "Channel"


def sanitize_video_filename_base(title: str, max_length: int = MAX_FILENAME_BASE_LENGTH) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", title or "")
    cleaned = cleaned.strip(" .")
    if not cleaned:
        cleaned = "Untitled"
    cleaned = _avoid_reserved_name(cleaned)
    return _trim_base(cleaned, max_length)


def strip_known_media_suffixes(value: str) -> str:
    cleaned = (value or "").strip()
    while cleaned:
        lowered = cleaned.casefold()
        matched_suffix = next(
            (suffix for suffix in MEDIA_OUTPUT_SUFFIXES if lowered.endswith(suffix.casefold())),
            None,
        )
        if matched_suffix is None:
            break
        cleaned = cleaned[: -len(matched_suffix)].rstrip(" .")
    return cleaned


def normalize_output_stem(value: str, max_length: int = MAX_FILENAME_BASE_LENGTH) -> str:
    cleaned = sanitize_video_filename_base(value, max_length)
    cleaned = strip_known_media_suffixes(cleaned)
    if not cleaned:
        cleaned = "Untitled"
    cleaned = _avoid_reserved_name(cleaned)
    return _trim_base(cleaned, max_length)


def assign_unique_title_bases(videos: list, max_length: int = MAX_FILENAME_BASE_LENGTH) -> None:
    next_suffix_by_base: dict[str, int] = {}
    used_bases: set[str] = set()
    for video in videos:
        original_base = normalize_output_stem(getattr(video, "title", ""), max_length)
        key = original_base.casefold()
        suffix_number = next_suffix_by_base.get(key, 1)
        filename_base = original_base
        while filename_base.casefold() in used_bases:
            suffix_number += 1
            suffix = f" ({suffix_number})"
            filename_base = f"{_trim_base(original_base, max_length - len(suffix))}{suffix}"
        next_suffix_by_base[key] = suffix_number
        used_bases.add(filename_base.casefold())
        video.sanitized_filename_base = filename_base


def expected_video_name(filename_base: str) -> str:
    return f"{normalize_output_stem(filename_base)}.mp4"


def expected_thumb_name(filename_base: str) -> str:
    return f"{normalize_output_stem(filename_base)}.jpg"


def expected_audio_name(filename_base: str) -> str:
    return f"{normalize_output_stem(filename_base)}.mp3"


def normalize_path_text(path_text: str) -> Path:
    return Path((path_text or "").strip()).expanduser()


def _trim_base(base: str, max_length: int) -> str:
    trimmed = (base or "Untitled")[:max(1, max_length)].rstrip(" .")
    return trimmed or "Untitled"
