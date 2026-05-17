PART_VIDEO = "video"
PART_THUMB = "thumb"
PART_AUDIO = "audio"

MODE_VIDEO_THUMB = "Video + Thumb"
MODE_AUDIO_THUMB = "Audio MP3 + Thumb"
MODE_VIDEO_AUDIO_THUMB = "Video + Audio MP3 + Thumb"

DOWNLOAD_MODES = (
    MODE_VIDEO_THUMB,
    MODE_AUDIO_THUMB,
    MODE_VIDEO_AUDIO_THUMB,
)

REQUIRED_PARTS_BY_MODE = {
    MODE_VIDEO_THUMB: (PART_VIDEO, PART_THUMB),
    MODE_AUDIO_THUMB: (PART_AUDIO, PART_THUMB),
    MODE_VIDEO_AUDIO_THUMB: (PART_VIDEO, PART_AUDIO, PART_THUMB),
}


def required_parts(download_mode: str) -> tuple[str, ...]:
    return REQUIRED_PARTS_BY_MODE.get(download_mode, REQUIRED_PARTS_BY_MODE[MODE_VIDEO_THUMB])


def mode_includes_video(download_mode: str) -> bool:
    return PART_VIDEO in required_parts(download_mode)


def mode_includes_audio(download_mode: str) -> bool:
    return PART_AUDIO in required_parts(download_mode)


def mode_includes_thumb(download_mode: str) -> bool:
    return PART_THUMB in required_parts(download_mode)
