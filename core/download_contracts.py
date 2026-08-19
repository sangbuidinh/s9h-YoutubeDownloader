"""Stable data contracts shared by the download facade and implementation.

This module owns identifiers and lightweight types only. It must not acquire UI,
subprocess, network, or application-state responsibilities.
"""

from dataclasses import dataclass
from enum import Enum

from core.download_modes import MODE_VIDEO_THUMB


YTDLP_STAGE_EXTRACT = "extract"
YTDLP_STAGE_DOWNLOAD = "download"
YTDLP_STAGE_POSTPROCESS = "postprocess"
YTDLP_STAGE_UNKNOWN = "unknown"
YTDLP_PART_UNKNOWN = "unknown"

COOKIE_SOURCE_FILE = "file"
COOKIE_SOURCE_BRIDGE = "bridge"

DOWNLOAD_ENGINE_STABLE = "stable"
DOWNLOAD_ENGINE_ARIA2_FAST = "aria2_fast"
DEFAULT_DOWNLOAD_ENGINE = DOWNLOAD_ENGINE_STABLE


class YtdlpFailureKind(str, Enum):
    HTTP_401 = "http_401"
    RATE_LIMIT = "rate_limit_429"
    BOT_CHECK = "bot_check"
    COOKIE_SESSION = "cookie_session"
    LOGIN_REQUIRED = "login_required"
    PO_TOKEN_OR_VISITOR_DATA = "po_token_or_visitor_data"
    HTTP_403 = "http_403"
    FORMAT_UNAVAILABLE = "format_unavailable"
    PERMANENT_VIDEO = "permanent_video"
    TOOL_CONFIGURATION = "tool_configuration"
    NETWORK_TIMEOUT = "network_timeout"
    NETWORK = "network"
    OUTPUT_PATH = "output_path"
    UNKNOWN = "unknown"


class FFmpegFailureKind(str, Enum):
    INVALID_INPUT = "invalid_input"
    NO_AUDIO_STREAM = "no_audio_stream"
    ENCODER_UNAVAILABLE = "encoder_unavailable"
    DISK_FULL = "disk_full"
    PERMISSION_DENIED = "permission_denied"
    OUTPUT_PATH = "output_path"
    INTERRUPTED_WRITE = "interrupted_write"
    UNKNOWN = "unknown"


class BatchDecision(str, Enum):
    RETRY_CURRENT = "retry_current"
    SKIP_CURRENT = "skip_current"
    STOP_BATCH = "stop_batch"


class DownloadError(Exception):
    pass


class DownloadCancelled(DownloadError):
    pass


class SkipCurrentVideo(DownloadError):
    pass


@dataclass(frozen=True)
class SystemicBlockContext:
    block_id: str
    failure_kind: YtdlpFailureKind
    retry_allowed: bool
    reason: str
    video_id: str = ""
    title: str = ""
    part: str = ""
    cookie_source: str = ""
    cookie_path: str = ""
    cookie_changed: bool = False
    refreshed_retry_used: bool = False
    output_lines: tuple[str, ...] = ()
    stage: str = YTDLP_STAGE_UNKNOWN
    exit_code: int | None = None


@dataclass
class DownloadOptions:
    base_folder: str
    channel_id: str
    channel_name: str
    cookies_enabled: bool = False
    cookies_path: str = ""
    speed_limit: str | None = None
    download_mode: str = MODE_VIDEO_THUMB
    cookie_source: str = COOKIE_SOURCE_FILE
    bridge_cookie_path: str = ""
    download_engine: str = DEFAULT_DOWNLOAD_ENGINE
    file_start_number: int | None = None


__all__ = (
    "BatchDecision",
    "COOKIE_SOURCE_BRIDGE",
    "COOKIE_SOURCE_FILE",
    "DEFAULT_DOWNLOAD_ENGINE",
    "DOWNLOAD_ENGINE_ARIA2_FAST",
    "DOWNLOAD_ENGINE_STABLE",
    "DownloadCancelled",
    "DownloadError",
    "DownloadOptions",
    "FFmpegFailureKind",
    "SkipCurrentVideo",
    "SystemicBlockContext",
    "YTDLP_PART_UNKNOWN",
    "YTDLP_STAGE_DOWNLOAD",
    "YTDLP_STAGE_EXTRACT",
    "YTDLP_STAGE_POSTPROCESS",
    "YTDLP_STAGE_UNKNOWN",
    "YtdlpFailureKind",
)
