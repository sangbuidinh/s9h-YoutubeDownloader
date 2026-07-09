import os
import re
import hashlib
import inspect
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath

from core.download_modes import (
    MODE_VIDEO_AUDIO_THUMB,
    MODE_VIDEO_THUMB,
    PART_AUDIO,
    PART_THUMB,
    PART_VIDEO,
    required_parts,
)
from core.file_status import (
    STATUS_DOWNLOADED,
    STATUS_ERROR,
    build_output_paths,
    ensure_output_dirs,
)
from core.progress_status import ProgressEvent, parse_ytdlp_progress_line
from core.error_messages import (
    SHOW_TECHNICAL_WARNINGS,
    classify_general_error,
    classify_ytdlp_error,
    format_friendly_error,
    friendly_ffmpeg_failure_kind_error,
    friendly_ytdlp_failure_kind_error,
    missing_js_runtime_warning,
)
from core.filename_utils import normalize_output_stem
from core.runtime_paths import runtime_file
from core.state_store import (
    get_effective_status,
    get_video_entry,
    is_mode_complete,
    missing_parts_for_mode,
    reconcile_downloaded_item_state,
    update_video_part_state,
)


USER_AGENT = "YouTube Downloader Source/1.0"
PREMIERE_SAFE_VIDEO_FORMAT = (
    "bv*[height<=1080][ext=mp4][vcodec^=avc1]+ba[ext=m4a][acodec^=mp4a]/"
    "b[height<=1080][ext=mp4][vcodec^=avc1][acodec^=mp4a]"
)
MAX_FINAL_PATH_LENGTH = 240
FFMPEG_OUTPUT_LINE_LIMIT = 20
FFMPEG_OUTPUT_LINE_CHAR_LIMIT = 500
FFMPEG_COMBINED_OUTPUT_LIMIT = 8192
YTDLP_OUTPUT_TAIL_LIMIT = 200
YTDLP_FATAL_LINE_LIMIT = 12
YTDLP_STAGE_EXTRACT = "extract"
YTDLP_STAGE_DOWNLOAD = "download"
YTDLP_STAGE_POSTPROCESS = "postprocess"
YTDLP_STAGE_UNKNOWN = "unknown"
YTDLP_PART_UNKNOWN = "unknown"
COOKIE_MEDIA_RETRY_TARGET_SECONDS = (2, 5, 10, 30)
COOKIE_MEDIA_SHORT_PROBE_SECONDS = 2
COOKIE_MEDIA_PROBE_INTERVAL_VIDEOS = 10
_STANDALONE_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?P<name>key|api_key|token|access_token)="
)
OUTPUT_PATH_TOO_LONG_MESSAGE = (
    "Output path too long. Please choose a shorter save folder or shorten filename limit."
)
COOKIE_SOURCE_FILE = "file"
COOKIE_SOURCE_BRIDGE = "bridge"
YTDLP_COOKIES_OPTION = "--cookies"
DOWNLOAD_ENGINE_STABLE = "stable"
DOWNLOAD_ENGINE_ARIA2_FAST = "aria2_fast"
DEFAULT_DOWNLOAD_ENGINE = DOWNLOAD_ENGINE_STABLE
ARIA2_FAST_DOWNLOADER_ARGS = "aria2c:-x 8 -s 8 -j 4 -k 1M"
ARIA2_VERSION_TIMEOUT_SECONDS = 3.0
BRIDGE_COOKIE_FILE_MISSING_MESSAGE = (
    "Local Cookie Bridge cookie file not found. Open the bridge extension and click "
    "Export YouTube Cookies, then try again."
)
BRIDGE_COOKIE_SESSION_ERROR_MESSAGE = (
    "Local Cookie Bridge cookies may be expired. Open the bridge extension and click "
    "Export YouTube Cookies, then retry the failed download."
)
FILE_COOKIE_SESSION_ERROR_MESSAGE = (
    "Cookies may be expired. Export a fresh cookies.txt file, select it again if needed, "
    "then retry the failed download."
)


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
class _ProgressContext:
    callback: object
    video: object
    video_index: int
    video_total: int
    phase: str
    last_emit: float = 0.0


_PROGRESS_CONTEXT = threading.local()


class DownloadController:
    def __init__(self, systemic_block_callback=None):
        self._cancel_requested = threading.Event()
        self._process_lock = threading.Lock()
        self._decision_condition = threading.Condition()
        self._active_block_id = ""
        self._systemic_decision: BatchDecision | None = None
        self.systemic_block_callback = systemic_block_callback
        self.current_process: subprocess.Popen | None = None
        self._active_processes: set[subprocess.Popen] = set()

    def request_cancel(self) -> None:
        self._cancel_requested.set()
        with self._decision_condition:
            self._systemic_decision = BatchDecision.STOP_BATCH
            self._decision_condition.notify_all()
        with self._process_lock:
            processes = list(self._active_processes)
            if self.current_process is not None and self.current_process not in processes:
                processes.append(self.current_process)
        for process in processes:
            threading.Thread(target=self._terminate_process, args=(process,), daemon=True).start()

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def set_current_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            self._active_processes.add(process)
            self.current_process = process
        if self.is_cancel_requested():
            self._terminate_process(process)

    def clear_current_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            self._active_processes.discard(process)
            if self.current_process is process:
                self.current_process = next(iter(self._active_processes), None)

    def has_active_process(self) -> bool:
        with self._process_lock:
            processes = list(self._active_processes)
        for process in processes:
            try:
                if process.poll() is None:
                    return True
            except Exception:
                continue
        return False

    def is_idle(self) -> bool:
        with self._decision_condition:
            waiting_for_decision = bool(
                self._active_block_id
                and self._systemic_decision is None
                and not self.is_cancel_requested()
            )
        return not self.has_active_process() and not waiting_for_decision

    def wait_for_systemic_decision(self, context: SystemicBlockContext) -> BatchDecision:
        callback = self.systemic_block_callback
        if callback is None:
            return BatchDecision.STOP_BATCH

        with self._decision_condition:
            if self.is_cancel_requested():
                return BatchDecision.STOP_BATCH
            self._active_block_id = context.block_id
            self._systemic_decision = None

        try:
            callback(context)
        except Exception:
            return BatchDecision.STOP_BATCH

        with self._decision_condition:
            while self._systemic_decision is None and not self.is_cancel_requested():
                self._decision_condition.wait(timeout=0.25)
            decision = self._systemic_decision or BatchDecision.STOP_BATCH
            if self._active_block_id == context.block_id:
                self._active_block_id = ""
                self._systemic_decision = None
            return decision

    def submit_systemic_decision(self, block_id: str, decision: BatchDecision | str) -> bool:
        try:
            normalized = decision if isinstance(decision, BatchDecision) else BatchDecision(str(decision))
        except ValueError:
            return False
        with self._decision_condition:
            if block_id != self._active_block_id:
                return False
            self._systemic_decision = normalized
            self._decision_condition.notify_all()
            return True

    def is_systemic_block_active(self, block_id: str) -> bool:
        with self._decision_condition:
            return bool(
                block_id
                and block_id == self._active_block_id
                and self._systemic_decision is None
                and not self.is_cancel_requested()
            )

    def _terminate_process(self, process: subprocess.Popen) -> None:
        _terminate_process_tree(process)


class FileOperationError(DownloadError):
    def __init__(self, operation: str, source: Path, target: Path, original_error: BaseException):
        self.operation = operation
        self.source = source
        self.target = target
        self.original_error = original_error
        super().__init__(
            f"File operation failed during {operation}: {source} -> {target}; "
            f"{type(original_error).__name__}: {original_error}"
        )


class YtdlpExecutionError(DownloadError):
    def __init__(
        self,
        exit_code: int,
        message: str,
        output_lines: list[str],
        bot_check: bool = False,
        http_403: bool = False,
        missing_js_runtime: bool = False,
        combined_output: str = "",
        stream_interrupted: bool = False,
        failure_kind: YtdlpFailureKind | str | None = None,
        fatal_lines: list[str] | tuple[str, ...] | None = None,
        http_status: int | None = None,
        stage: str = YTDLP_STAGE_UNKNOWN,
        part: str = YTDLP_PART_UNKNOWN,
        command: list[str] | tuple[str, ...] | None = None,
    ):
        super().__init__(message)
        sanitized_output_lines = _sanitize_ytdlp_output_lines(output_lines)[-YTDLP_OUTPUT_TAIL_LIMIT:]
        sanitized_fatal_lines = _sanitize_ytdlp_output_lines(fatal_lines or [])
        self.exit_code = int(exit_code)
        self.output_lines = sanitized_output_lines
        self.fatal_lines = tuple((sanitized_fatal_lines or _extract_ytdlp_fatal_lines(sanitized_output_lines))[-YTDLP_FATAL_LINE_LIMIT:])
        self.combined_output = _bounded_sanitized_ytdlp_output(combined_output or "\n".join(sanitized_output_lines))
        self.failure_kind = _coerce_ytdlp_failure_kind(failure_kind)
        status_text = "\n".join(self.fatal_lines) or self.combined_output
        self.http_status = int(http_status) if http_status is not None else _http_status_from_text(
            status_text
        )
        self.stage = _normalize_ytdlp_stage(stage)
        self.part = _normalize_ytdlp_part(part)
        self.bot_check = bool(bot_check or self.failure_kind == YtdlpFailureKind.BOT_CHECK)
        self.http_403 = bool(http_403 or self.failure_kind == YtdlpFailureKind.HTTP_403 or self.http_status == 403)
        self.missing_js_runtime = bool(
            missing_js_runtime or self.failure_kind == YtdlpFailureKind.TOOL_CONFIGURATION
        )
        self.stream_interrupted = stream_interrupted
        self.command = tuple(str(value) for value in (command or ()))


class FFmpegExecutionError(DownloadError):
    def __init__(
        self,
        *,
        operation: str,
        exit_code: int,
        message: str,
        output_lines: list[str] | tuple[str, ...],
        combined_output: str = "",
    ):
        super().__init__(message)
        self.operation = operation
        self.exit_code = int(exit_code)
        self.output_lines = tuple(
            line
            for line in (
                _bound_subprocess_output_line(_sanitize_subprocess_output_line(output_line))
                for output_line in output_lines
            )
            if line
        )[-FFMPEG_OUTPUT_LINE_LIMIT:]
        self.combined_output = _bounded_sanitized_subprocess_output("", combined_output)


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


@dataclass(frozen=True)
class _CookieFileSnapshot:
    exists: bool
    size: int
    mtime_ns: int | None
    sha256: str

    @property
    def usable(self) -> bool:
        return self.exists and self.size > 0 and bool(self.sha256)


@dataclass(frozen=True)
class _PreparedCookieAttempt:
    command: list[str]
    canonical_path: str = ""
    canonical_snapshot: _CookieFileSnapshot | None = None
    temp_cookie_path: str = ""
    cookies_used: bool = False


@dataclass(frozen=True)
class _Aria2RuntimeValidation:
    requested: bool
    available: bool
    path: Path


@dataclass(frozen=True)
class _MediaDownloaderSelection:
    engine: str
    command_args: tuple[str, ...]
    aria2_requested: bool
    aria2_available: bool


@dataclass
class _CookieMediaPrefetch:
    video_id: str
    title: str
    staging_dir: Path
    done: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    info_json_path: Path | None = None
    ready_monotonic: float = 0.0
    error: BaseException | None = None
    cookie_snapshot_sha256: str = ""
    consumed: bool = False


@dataclass
class _YtdlpBatchState:
    cookie_bootstrap_media_mode: bool = False
    media_settle_delay_seconds: int = 0
    cookie_snapshot_sha256: str = ""
    media_videos_since_probe: int = 0
    prefetch_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    prefetch: _CookieMediaPrefetch | None = field(default=None, repr=False)


@dataclass
class _YtdlpAttemptState:
    verified_retry_used: bool = False
    cookieless_fallback_used: bool = False
    authenticated_infojson_fallback_used: bool = False
    batch_state: _YtdlpBatchState | None = None
    prefetched_media: _CookieMediaPrefetch | None = None
    lookahead_callback: object | None = None
    lookahead_started: bool = False


def _emit_progress_event(progress_callback, event: ProgressEvent) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(event)
    except Exception:
        pass


def _emit_progress(
    progress_callback,
    phase: str,
    video=None,
    video_index: int | None = None,
    video_total: int | None = None,
    message: str = "",
    kind: str = "phase",
    percent: str | None = None,
    speed: str | None = None,
    eta: str | None = None,
    fragment: str | None = None,
) -> None:
    _emit_progress_event(
        progress_callback,
        ProgressEvent(
            kind=kind,
            phase=phase,
            message=message,
            video_index=video_index,
            video_total=video_total,
            title=getattr(video, "sanitized_filename_base", None) or getattr(video, "title", None),
            percent=percent,
            speed=speed,
            eta=eta,
            fragment=fragment,
        ),
    )


def _set_progress_context(
    progress_callback,
    video,
    video_index: int,
    video_total: int,
    phase: str,
):
    previous = getattr(_PROGRESS_CONTEXT, "current", None)
    if progress_callback is None:
        _PROGRESS_CONTEXT.current = None
    else:
        _PROGRESS_CONTEXT.current = _ProgressContext(progress_callback, video, video_index, video_total, phase)
    return previous


def _restore_progress_context(previous) -> None:
    _PROGRESS_CONTEXT.current = previous


def _current_progress_context() -> _ProgressContext | None:
    return getattr(_PROGRESS_CONTEXT, "current", None)


def _emit_current_progress(phase: str | None = None, message: str = "", **kwargs) -> None:
    context = _current_progress_context()
    if context is None:
        return
    _emit_progress(
        context.callback,
        phase or context.phase,
        context.video,
        context.video_index,
        context.video_total,
        message=message,
        **kwargs,
    )


def _emit_general_error_progress(progress_callback, video, index: int, total: int, message: str) -> None:
    _emit_progress(
        progress_callback,
        "Error",
        video,
        index,
        total,
        message=classify_general_error(message).title,
        kind="error",
    )


def _emit_ytdlp_error_progress(
    progress_callback,
    video,
    index: int,
    total: int,
    exc: YtdlpExecutionError,
    cookies_enabled: bool,
) -> None:
    options = DownloadOptions("", "", "", cookies_enabled=cookies_enabled)
    failure_kind = classify_ytdlp_failure_kind(exc, options)
    if _uses_ytdlp_failure_kind_friendly_error(failure_kind):
        friendly = friendly_ytdlp_failure_kind_error(failure_kind.value)
    else:
        text = "\n".join(exc.fatal_lines or exc.output_lines) or exc.combined_output
        friendly = classify_ytdlp_error(
            text,
            cookies_enabled=cookies_enabled,
            bot_check=exc.bot_check,
            http_403=exc.http_403,
            missing_js_runtime=exc.missing_js_runtime,
        )
    _emit_progress(progress_callback, "Error", video, index, total, message=friendly.title, kind="error")


def _emit_ffmpeg_error_progress(
    progress_callback,
    video,
    index: int,
    total: int,
    exc: FFmpegExecutionError,
) -> None:
    failure_kind = classify_ffmpeg_failure_kind(exc)
    friendly = friendly_ffmpeg_failure_kind_error(failure_kind.value, exc.combined_output)
    _emit_progress(progress_callback, "Error", video, index, total, message=friendly.title, kind="error")


def _subprocess_creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _terminate_process_tree(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        if process.poll() is not None:
            return
    except Exception:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        except Exception:
            pass

    try:
        process.terminate()
    except Exception:
        pass

    if _wait_for_process_exit(process, 2.0) is not None:
        return

    try:
        if process.poll() is None:
            process.kill()
    except Exception:
        pass
    _wait_for_process_exit(process, 2.0)


def _wait_for_process_exit(process: subprocess.Popen, timeout: float) -> int | None:
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        return process.poll()


def validate_speed_limit(value: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.startswith("--") or text.startswith("-"):
        raise ValueError("Invalid speed limit")
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        if float(text) == 0:
            return None
        return f"{text}M"
    raise ValueError("Invalid speed limit")


def validate_download_environment(options: DownloadOptions) -> None:
    base_folder = Path(options.base_folder)
    ytdlp_path = runtime_file("yt-dlp.exe")
    ffmpeg_path = runtime_file("ffmpeg.exe")
    if not options.base_folder or not base_folder.exists() or not base_folder.is_dir():
        raise DownloadError("No save folder selected")
    if not ytdlp_path.exists():
        raise DownloadError("yt-dlp.exe missing")
    if not ffmpeg_path.exists():
        raise DownloadError("ffmpeg.exe missing")
    effective_cookies_path(options)


def _normalize_download_engine(value: object) -> str:
    if value == DOWNLOAD_ENGINE_ARIA2_FAST:
        return DOWNLOAD_ENGINE_ARIA2_FAST
    return DOWNLOAD_ENGINE_STABLE


def _media_downloader_selection(
    options: DownloadOptions,
    *,
    force_stable: bool = False,
    aria2_validation: _Aria2RuntimeValidation | None = None,
) -> _MediaDownloaderSelection:
    requested_engine = _normalize_download_engine(options.download_engine)
    if force_stable or requested_engine != DOWNLOAD_ENGINE_ARIA2_FAST:
        return _MediaDownloaderSelection(
            engine=DOWNLOAD_ENGINE_STABLE,
            command_args=("-N", "1"),
            aria2_requested=requested_engine == DOWNLOAD_ENGINE_ARIA2_FAST,
            aria2_available=False,
        )

    aria2_path = aria2_validation.path if aria2_validation is not None else runtime_file("aria2c.exe")
    aria2_available = (
        bool(aria2_validation.available)
        if aria2_validation is not None
        else aria2_path.exists() and aria2_path.is_file()
    )
    if not aria2_available:
        return _MediaDownloaderSelection(
            engine=DOWNLOAD_ENGINE_STABLE,
            command_args=("-N", "1"),
            aria2_requested=True,
            aria2_available=False,
        )

    return _MediaDownloaderSelection(
        engine=DOWNLOAD_ENGINE_ARIA2_FAST,
        command_args=(
            "--downloader",
            str(aria2_path),
            "--downloader-args",
            ARIA2_FAST_DOWNLOADER_ARGS,
        ),
        aria2_requested=True,
        aria2_available=True,
    )


def effective_cookies_path(options: DownloadOptions) -> str:
    if not options.cookies_enabled:
        return ""
    source = options.cookie_source if options.cookie_source in {COOKIE_SOURCE_FILE, COOKIE_SOURCE_BRIDGE} else COOKIE_SOURCE_FILE
    if source == COOKIE_SOURCE_BRIDGE:
        path_text = _normalized_cookie_option_path(options.bridge_cookie_path)
        error_message = BRIDGE_COOKIE_FILE_MISSING_MESSAGE
    else:
        path_text = _normalized_cookie_option_path(options.cookies_path)
        error_message = "Cookies file missing"
    if not path_text:
        raise DownloadError(error_message)
    try:
        path = Path(path_text)
        if not path.exists() or not path.is_file():
            raise OSError
        with path.open("rb"):
            pass
    except (OSError, ValueError) as exc:
        raise DownloadError(error_message) from exc
    return str(path)


def _normalized_cookie_option_path(value: object) -> str:
    if not isinstance(value, str):
        return ""
    path = value.strip()
    if not path or "\x00" in path or len(path) > 32767:
        return ""
    return path



def _find_cookie_media_lookahead_candidate(
    videos: list,
    start_index: int,
    options: DownloadOptions,
):
    """Return the next video that still needs an MP4 download."""
    for candidate in videos[max(0, int(start_index)):]:
        video_id = str(getattr(candidate, "video_id", "") or "").strip()
        if not video_id:
            continue
        raw_stem = getattr(candidate, "sanitized_filename_base", "") or getattr(candidate, "title", "")
        stem = normalize_output_stem(raw_stem)
        candidate.sanitized_filename_base = stem
        try:
            entry = get_video_entry(options.channel_id, video_id)
            if is_mode_complete(entry, options.download_mode):
                continue
            missing_parts = missing_parts_for_mode(entry, options.download_mode)
        except Exception:
            continue
        if PART_VIDEO not in missing_parts:
            continue
        paths = build_output_paths(options.base_folder, options.channel_name, stem)
        return candidate, stem, paths.channel_dir
    return None


def _make_cookie_media_lookahead_callback(
    videos: list,
    next_start_index: int,
    options: DownloadOptions,
    batch_state: _YtdlpBatchState,
    log,
    cancel_controller: DownloadController | None,
):
    candidate = _find_cookie_media_lookahead_candidate(videos, next_start_index, options)
    if candidate is None:
        return None

    video, stem, channel_dir = candidate

    def start_lookahead() -> None:
        _start_cookie_media_lookahead(
            batch_state,
            str(video.video_id),
            stem,
            channel_dir,
            options,
            log,
            cancel_controller,
        )

    return start_lookahead


def _start_cookie_media_lookahead(
    batch_state: _YtdlpBatchState | None,
    video_id: str,
    title: str,
    channel_dir: Path,
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None,
) -> None:
    if batch_state is None or not options.cookies_enabled or not video_id:
        return
    if _cancel_requested(cancel_controller):
        return

    stale: _CookieMediaPrefetch | None = None
    with batch_state.prefetch_lock:
        existing = batch_state.prefetch
        if existing is not None:
            if existing.video_id == video_id:
                return
            if not existing.done.is_set():
                return
            stale = existing
            batch_state.prefetch = None

        channel_dir.mkdir(parents=True, exist_ok=True)
        safe_id = _safe_temp_stem(video_id)[:32]
        staging_dir = Path(
            tempfile.mkdtemp(
                prefix=f".s9h-stage-lookahead-{safe_id}-",
                dir=str(channel_dir),
            )
        )
        _mark_staging_directory_hidden(staging_dir, log)
        snapshot = _options_cookie_snapshot(options)
        prefetch = _CookieMediaPrefetch(
            video_id=video_id,
            title=title,
            staging_dir=staging_dir,
            cookie_snapshot_sha256=(snapshot.sha256 if snapshot and snapshot.usable else ""),
        )
        batch_state.prefetch = prefetch

    if stale is not None:
        _cleanup_cookie_media_prefetch(stale, log)

    def worker() -> None:
        try:
            if _cancel_requested(cancel_controller):
                raise DownloadCancelled("download cancelled/interrupted")
            log(f"[COOKIE LOOKAHEAD] Preparing authenticated metadata for next video: {title}")
            command = _build_video_ytdlp_command(
                video_id,
                staging_dir,
                options,
                force_stable_downloader=True,
            )
            info_json_path = _extract_authenticated_infojson_path(
                command,
                options,
                log,
                cancel_controller,
                attempt_number=0,
                part=PART_VIDEO,
                log_start=False,
            )
            prefetch.info_json_path = info_json_path
            prefetch.ready_monotonic = time.monotonic()
            log(f"[COOKIE LOOKAHEAD] Authenticated metadata is ready for next video: {title}")
        except BaseException as exc:
            prefetch.error = exc
            if not isinstance(exc, DownloadCancelled):
                log(
                    "[COOKIE LOOKAHEAD] Could not prepare the next video in advance; "
                    "the normal download path will be used."
                )
        finally:
            prefetch.done.set()

    thread = threading.Thread(
        target=worker,
        name=f"cookie-lookahead-{_safe_temp_stem(video_id)[:20]}",
        daemon=True,
    )
    prefetch.thread = thread
    thread.start()


def _take_cookie_media_lookahead(
    batch_state: _YtdlpBatchState | None,
    video_id: str,
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None,
) -> _CookieMediaPrefetch | None:
    if batch_state is None or not batch_state.cookie_bootstrap_media_mode:
        return None

    with batch_state.prefetch_lock:
        prefetch = batch_state.prefetch
        if prefetch is None or prefetch.video_id != video_id:
            return None
        batch_state.prefetch = None
        prefetch.consumed = True

    if not prefetch.done.is_set():
        log("[COOKIE LOOKAHEAD] Waiting for prefetched metadata to finish.")
    while not prefetch.done.wait(timeout=0.1):
        _raise_if_cancelled(cancel_controller)

    if prefetch.error is not None:
        _cleanup_cookie_media_prefetch(prefetch, log)
        return None
    if prefetch.info_json_path is None or not prefetch.info_json_path.exists():
        _cleanup_cookie_media_prefetch(prefetch, log)
        return None

    current_snapshot = _options_cookie_snapshot(options)
    current_sha256 = current_snapshot.sha256 if current_snapshot and current_snapshot.usable else ""
    if prefetch.cookie_snapshot_sha256 and current_sha256 != prefetch.cookie_snapshot_sha256:
        log("[COOKIE LOOKAHEAD] Cookie source changed; discarding prefetched metadata.")
        _cleanup_cookie_media_prefetch(prefetch, log)
        return None

    age = max(0.0, time.monotonic() - prefetch.ready_monotonic)
    log(f"[COOKIE LOOKAHEAD] Reusing metadata prepared {age:.1f} seconds earlier.")
    return prefetch


def _cleanup_cookie_media_prefetch(prefetch: _CookieMediaPrefetch | None, log=None) -> None:
    if prefetch is None:
        return
    thread = prefetch.thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)
    if thread is not None and thread.is_alive():
        return
    _cleanup_media_staging_directory(prefetch.staging_dir, prefetch.staging_dir.parent, log)


def _shutdown_cookie_media_lookahead(batch_state: _YtdlpBatchState | None, log=None) -> None:
    if batch_state is None:
        return
    with batch_state.prefetch_lock:
        prefetch = batch_state.prefetch
        batch_state.prefetch = None
    _cleanup_cookie_media_prefetch(prefetch, log)


def _start_attempt_lookahead(attempt_state: _YtdlpAttemptState, log) -> None:
    if attempt_state.lookahead_started:
        return
    callback = attempt_state.lookahead_callback
    if callback is None:
        return
    attempt_state.lookahead_started = True
    try:
        callback()
    except Exception as exc:
        if SHOW_TECHNICAL_WARNINGS:
            log(f"[COOKIE LOOKAHEAD] Start failed: {type(exc).__name__}: {exc}")


def _video_attempt_state_for_batch(
    videos: list,
    current_index: int,
    video_id: str,
    options: DownloadOptions,
    batch_state: _YtdlpBatchState,
    log,
    cancel_controller: DownloadController | None,
) -> tuple[_YtdlpAttemptState, _CookieMediaPrefetch | None]:
    prefetched = _take_cookie_media_lookahead(
        batch_state,
        video_id,
        options,
        log,
        cancel_controller,
    )
    lookahead_callback = _make_cookie_media_lookahead_callback(
        videos,
        current_index,
        options,
        batch_state,
        log,
        cancel_controller,
    )
    return (
        _YtdlpAttemptState(
            batch_state=batch_state,
            prefetched_media=prefetched,
            lookahead_callback=lookahead_callback,
        ),
        prefetched,
    )


def download_items(
    videos: list,
    options: DownloadOptions,
    log,
    status_callback,
    cancel_controller: DownloadController | None = None,
    progress_callback=None,
) -> None:
    validate_download_environment(options)
    ensure_output_dirs(options.base_folder, options.channel_name, options.download_mode)
    _call_runtime_tool_summary(options, log, cancel_controller)
    aria2_validation = _prepare_media_downloader_runtime(options, log, cancel_controller)
    downloaded_count = 0
    failed_count = 0
    skipped_count = 0
    mode_parts = required_parts(options.download_mode)
    video_total = len(videos)
    cancelled = False
    ytdlp_batch_state = _YtdlpBatchState()

    if options.cookies_enabled:
        log("[INFO] Cookies enabled: yes")
        log("[INFO] yt-dlp will receive an isolated per-attempt cookies.txt copy.")
    if _deno_runtime_path().exists():
        log("[INFO] Deno runtime found. JavaScript challenge solving enabled.")

    for index, video in enumerate(videos, start=1):
        if _cancel_requested(cancel_controller):
            cancelled = True
            break

        raw_stem = getattr(video, "sanitized_filename_base", "") or getattr(video, "title", "")
        stem = normalize_output_stem(raw_stem)
        video.sanitized_filename_base = stem
        paths = build_output_paths(
            options.base_folder,
            options.channel_name,
            stem,
        )
        current_part = None
        run_parts_current_run: list[str] = []

        log(f"[INFO] Starting download: {index}/{len(videos)}")
        log(f"[INFO] Mode: {options.download_mode}")
        try:
            _validate_output_paths(paths, mode_parts)

            entry = get_video_entry(options.channel_id, video.video_id)
            if is_mode_complete(entry, options.download_mode):
                log(f"[SKIP] {stem} marked as downloaded in SQLite state")
                _emit_progress(progress_callback, "Skipped", video, index, video_total)
                skipped_count += 1
                video.status = get_effective_status(entry, options.download_mode)
                status_callback(video)
                continue

            missing_parts = missing_parts_for_mode(entry, options.download_mode)
            if not missing_parts:
                _emit_progress(progress_callback, "Skipped", video, index, video_total)
                skipped_count += 1
                video.status = get_effective_status(entry, options.download_mode)
                status_callback(video)
                continue

            with _media_staging_directory(paths.channel_dir, video.video_id, log) as temp_path:
                for part in missing_parts:
                    _raise_if_cancelled(cancel_controller)
                    current_part = part
                    if part == PART_VIDEO:
                        _remember_run_part(run_parts_current_run, part)
                        log(f"[INFO] Downloading {stem}.mp4")
                        log("[INFO] Premiere-safe mode: MP4 H.264/AAC only, max 1080p.")
                        _emit_progress(progress_callback, "Video", video, index, video_total, message="Downloading...")
                        previous_progress = _set_progress_context(
                            progress_callback, video, index, video_total, "Video"
                        )
                        video_attempt_state, prefetched_media = _video_attempt_state_for_batch(
                            videos,
                            index,
                            str(video.video_id),
                            options,
                            ytdlp_batch_state,
                            log,
                            cancel_controller,
                        )
                        try:
                            _download_video(
                                video.video_id,
                                stem,
                                temp_path,
                                paths.video_path,
                                options,
                                log,
                                cancel_controller,
                                video_attempt_state,
                                aria2_validation,
                            )
                        finally:
                            _cleanup_cookie_media_prefetch(prefetched_media, log)
                            _restore_progress_context(previous_progress)
                    elif part == PART_AUDIO:
                        log(f"[INFO] Downloading audio {stem}.mp3")
                        if options.download_mode == MODE_VIDEO_AUDIO_THUMB:
                            _emit_progress(progress_callback, "Validating MP4", video, index, video_total)
                            if not _premiere_safe_mp4_ready_for_download(paths.video_path, cancel_controller):
                                current_part = PART_VIDEO
                                _remember_run_part(run_parts_current_run, PART_VIDEO)
                                log(f"[INFO] Local MP4 missing or invalid; downloading {stem}.mp4 for MP3 extraction.")
                                log("[INFO] Premiere-safe mode: MP4 H.264/AAC only, max 1080p.")
                                _emit_progress(
                                    progress_callback, "Video", video, index, video_total, message="Downloading..."
                                )
                                previous_progress = _set_progress_context(
                                    progress_callback, video, index, video_total, "Video"
                                )
                                video_attempt_state, prefetched_media = _video_attempt_state_for_batch(
                                    videos,
                                    index,
                                    str(video.video_id),
                                    options,
                                    ytdlp_batch_state,
                                    log,
                                    cancel_controller,
                                )
                                try:
                                    _download_video(
                                        video.video_id,
                                        stem,
                                        temp_path,
                                        paths.video_path,
                                        options,
                                        log,
                                        cancel_controller,
                                        video_attempt_state,
                                        aria2_validation,
                                    )
                                finally:
                                    _cleanup_cookie_media_prefetch(prefetched_media, log)
                                    _restore_progress_context(previous_progress)
                                update_video_part_state(
                                    options.channel_id,
                                    options.channel_name,
                                    options.base_folder,
                                    video,
                                    paths,
                                    PART_VIDEO,
                                    STATUS_DOWNLOADED,
                                    options.download_mode,
                                )
                                entry = get_video_entry(options.channel_id, video.video_id)
                                video.status = get_effective_status(entry, options.download_mode)
                                status_callback(video)
                                log(_part_success_message(PART_VIDEO, stem))
                            current_part = PART_AUDIO
                            _remember_run_part(run_parts_current_run, PART_AUDIO)
                            _emit_progress(
                                progress_callback,
                                "MP3",
                                video,
                                index,
                                video_total,
                                message="Extracting audio from MP4",
                            )
                            previous_progress = _set_progress_context(
                                progress_callback, video, index, video_total, "MP3"
                            )
                            try:
                                _extract_mp3_from_video(
                                    paths.video_path,
                                    temp_path,
                                    paths.audio_path,
                                    log,
                                    cancel_controller,
                                )
                            finally:
                                _restore_progress_context(previous_progress)
                        else:
                            _remember_run_part(run_parts_current_run, PART_AUDIO)
                            _emit_progress(progress_callback, "MP3", video, index, video_total, message="Downloading...")
                            previous_progress = _set_progress_context(
                                progress_callback, video, index, video_total, "MP3"
                            )
                            try:
                                _download_audio(
                                    video.video_id,
                                    stem,
                                    temp_path,
                                    paths.audio_path,
                                    options,
                                    log,
                                    cancel_controller,
                                    _YtdlpAttemptState(),
                                    aria2_validation,
                                )
                            finally:
                                _restore_progress_context(previous_progress)
                    elif part == PART_THUMB:
                        _remember_run_part(run_parts_current_run, part)
                        log(f"[INFO] Downloading thumbnail {stem}.jpg")
                        _emit_progress(
                            progress_callback,
                            "Thumbnail",
                            video,
                            index,
                            video_total,
                            message="Downloading image",
                        )
                        previous_progress = _set_progress_context(
                            progress_callback, video, index, video_total, "Thumbnail"
                        )
                        try:
                            _download_thumbnail(
                                video,
                                stem,
                                temp_path,
                                paths.thumb_path,
                                options,
                                log,
                                cancel_controller,
                                _YtdlpAttemptState(),
                            )
                        finally:
                            _restore_progress_context(previous_progress)
                    update_video_part_state(
                        options.channel_id,
                        options.channel_name,
                        options.base_folder,
                        video,
                        paths,
                        part,
                        STATUS_DOWNLOADED,
                        options.download_mode,
                    )
                    entry = get_video_entry(options.channel_id, video.video_id)
                    video.status = get_effective_status(entry, options.download_mode)
                    status_callback(video)
                    log(_part_success_message(part, stem))
                    _emit_progress(
                        progress_callback,
                        _progress_phase_for_part(part),
                        video,
                        index,
                        video_total,
                        message="Completed",
                    )
                    current_part = None

            final_status = _reconcile_current_item(
                options,
                video,
                paths,
                log,
                status_callback,
                run_parts=tuple(run_parts_current_run),
            )
            if final_status == STATUS_DOWNLOADED:
                downloaded_count += 1
                log(f"[SUCCESS] Downloaded {_success_file_list(stem, options.download_mode)}")
            else:
                failed_count += 1
                _emit_progress(
                    progress_callback,
                    "Error",
                    video,
                    index,
                    video_total,
                    message="Not fully downloaded",
                    kind="error",
                )
        except PermissionError as exc:
            _remember_run_part(run_parts_current_run, current_part)
            _mark_part_error(options, video, paths, current_part)
            final_status = _reconcile_current_item(
                options,
                video,
                paths,
                log,
                status_callback,
                run_parts=tuple(run_parts_current_run) or None,
            )
            if final_status == STATUS_DOWNLOADED:
                downloaded_count += 1
                log(f"[SUCCESS] Downloaded {_success_file_list(stem, options.download_mode)}")
                continue
            failed_count += 1
            _emit_general_error_progress(
                progress_callback, video, index, video_total, f"{type(exc).__name__}: {exc}"
            )
            _log_friendly_general_error(log, f"{type(exc).__name__}: {exc}", [f"{type(exc).__name__}: {exc}"])
        except DownloadCancelled:
            cancelled = True
            _remember_run_part(run_parts_current_run, current_part)
            _reconcile_current_item(
                options,
                video,
                paths,
                log,
                status_callback,
                run_parts=tuple(run_parts_current_run) or None,
            )
            break
        except SkipCurrentVideo:
            _remember_run_part(run_parts_current_run, current_part)
            _reconcile_current_item(
                options,
                video,
                paths,
                log,
                status_callback,
                run_parts=tuple(run_parts_current_run) or None,
            )
            skipped_count += 1
            log(f"[SKIP] Skipped current video after user decision: {stem}")
            _emit_progress(progress_callback, "Skipped", video, index, video_total)
            continue
        except YtdlpExecutionError as exc:
            _remember_run_part(run_parts_current_run, current_part)
            _mark_part_error(options, video, paths, current_part)
            final_status = _reconcile_current_item(
                options,
                video,
                paths,
                log,
                status_callback,
                run_parts=tuple(run_parts_current_run) or None,
            )
            if final_status == STATUS_DOWNLOADED:
                downloaded_count += 1
                log(f"[SUCCESS] Downloaded {_success_file_list(stem, options.download_mode)}")
                continue
            failed_count += 1
            _emit_ytdlp_error_progress(progress_callback, video, index, video_total, exc, options.cookies_enabled)
            _log_friendly_ytdlp_error(log, exc, options)
            if exc.missing_js_runtime and not _deno_runtime_path().exists() and (exc.bot_check or exc.http_403):
                _log_missing_js_runtime_warning(log, exc.output_lines)
        except FFmpegExecutionError as exc:
            _remember_run_part(run_parts_current_run, current_part)
            _mark_part_error(options, video, paths, current_part)
            final_status = _reconcile_current_item(
                options,
                video,
                paths,
                log,
                status_callback,
                run_parts=tuple(run_parts_current_run) or None,
            )
            if final_status == STATUS_DOWNLOADED:
                downloaded_count += 1
                log(f"[SUCCESS] Downloaded {_success_file_list(stem, options.download_mode)}")
                continue
            failed_count += 1
            _emit_ffmpeg_error_progress(progress_callback, video, index, video_total, exc)
            _log_friendly_ffmpeg_error(log, exc)
        except DownloadError as exc:
            _remember_run_part(run_parts_current_run, current_part)
            _mark_part_error(options, video, paths, current_part)
            final_status = _reconcile_current_item(
                options,
                video,
                paths,
                log,
                status_callback,
                run_parts=tuple(run_parts_current_run) or None,
            )
            if final_status == STATUS_DOWNLOADED:
                downloaded_count += 1
                log(f"[SUCCESS] Downloaded {_success_file_list(stem, options.download_mode)}")
                continue
            failed_count += 1
            if str(exc) == OUTPUT_PATH_TOO_LONG_MESSAGE:
                _emit_general_error_progress(progress_callback, video, index, video_total, "Path too long")
                _log_friendly_general_error(log, "Path too long", [str(exc)])
            else:
                _emit_general_error_progress(progress_callback, video, index, video_total, str(exc))
                _log_friendly_general_error(log, str(exc), [str(exc)])
        except Exception as exc:
            _remember_run_part(run_parts_current_run, current_part)
            _mark_part_error(options, video, paths, current_part)
            final_status = _reconcile_current_item(
                options,
                video,
                paths,
                log,
                status_callback,
                run_parts=tuple(run_parts_current_run) or None,
            )
            if final_status == STATUS_DOWNLOADED:
                downloaded_count += 1
                log(f"[SUCCESS] Downloaded {_success_file_list(stem, options.download_mode)}")
                continue
            failed_count += 1
            technical = f"{type(exc).__name__}: {exc}"
            _emit_general_error_progress(progress_callback, video, index, video_total, technical)
            _log_friendly_general_error(log, technical, [technical])

    _shutdown_cookie_media_lookahead(ytdlp_batch_state, log)

    if downloaded_count > 0:
        log(f"[SUCCESS] Downloaded: {downloaded_count}")
    elif failed_count > 0 or skipped_count > 0:
        log("[WARNING] Batch finished with 0 successful downloads.")
    else:
        log("[INFO] Downloaded: 0")
    if failed_count > 0:
        log(f"[ERROR] Failed: {failed_count}")
    if skipped_count > 0:
        log(f"[SKIP] Skipped: {skipped_count}")
    if cancelled:
        _emit_progress_event(progress_callback, ProgressEvent(kind="stop_requested"))
    else:
        _emit_progress_event(progress_callback, ProgressEvent(kind="batch_complete"))


def _reconcile_current_item(
    options: DownloadOptions,
    video,
    paths,
    log,
    status_callback,
    run_parts: tuple[str, ...] | None = None,
) -> str:
    try:
        old_status, new_status = reconcile_downloaded_item_state(
            options.channel_id,
            options.channel_name,
            options.base_folder,
            video,
            paths,
            options.download_mode,
            run_parts=run_parts,
        )
    except OSError as exc:
        technical = f"File operation failed during state save: {type(exc).__name__}: {exc}"
        _log_friendly_general_error(log, technical, [technical])
        entry = get_video_entry(options.channel_id, video.video_id)
        new_status = get_effective_status(entry, options.download_mode)
        video.status = new_status
        status_callback(video)
        return new_status
    video.status = new_status
    if old_status != new_status:
        log(f"[INFO] State reconciled after current run: {old_status} -> {new_status}")
    log(f"[INFO] Final status: {new_status}")
    status_callback(video)
    return new_status


def _part_success_message(part: str, stem: str) -> str:
    if part == PART_VIDEO:
        return f"[SUCCESS] Video downloaded: {stem}.mp4"
    if part == PART_AUDIO:
        return f"[SUCCESS] Audio downloaded: {stem}.mp3"
    if part == PART_THUMB:
        return f"[SUCCESS] Thumbnail downloaded: {stem}.jpg"
    return f"[SUCCESS] Downloaded: {stem}"


def _progress_phase_for_part(part: str) -> str:
    if part == PART_VIDEO:
        return "Video"
    if part == PART_AUDIO:
        return "MP3"
    if part == PART_THUMB:
        return "Thumbnail"
    return "Status"


def _download_video(
    video_id: str,
    stem: str,
    temp_dir: Path,
    final_path: Path,
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
    cookie_retry_state: _YtdlpAttemptState | None = None,
    aria2_validation: _Aria2RuntimeValidation | None = None,
) -> None:
    if final_path.exists() and _premiere_safe_mp4_ready_for_download(final_path, cancel_controller):
        return
    _run_media_ytdlp_with_engine_fallback(
        lambda *, force_stable: _build_video_ytdlp_command(
            video_id,
            temp_dir,
            options,
            force_stable_downloader=force_stable,
            aria2_validation=aria2_validation,
        ),
        temp_dir,
        options,
        log,
        cancel_controller,
        cookie_retry_state,
    )
    staged_mp4_path = _select_staged_file(temp_dir, "*.mp4", ".mp4")
    _emit_current_progress("Validating MP4")
    _validate_premiere_safe_mp4_for_download(staged_mp4_path, log, True, cancel_controller)
    _atomic_promote_with_retry(
        staged_mp4_path,
        final_path,
        log,
        replace_existing=True,
        cancel_controller=cancel_controller,
    )
    if not _final_file_ready(final_path):
        raise DownloadError("video download failed")


def _build_video_ytdlp_command(
    video_id: str,
    temp_dir: Path,
    options: DownloadOptions,
    *,
    force_stable_downloader: bool = False,
    aria2_validation: _Aria2RuntimeValidation | None = None,
) -> list[str]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    output_template = str(temp_dir / f"{_safe_temp_stem(video_id)}.%(ext)s")
    downloader_selection = _media_downloader_selection(
        options,
        force_stable=force_stable_downloader,
        aria2_validation=aria2_validation,
    )
    return _base_ytdlp_command(options) + list(downloader_selection.command_args) + [
        "-f",
        PREMIERE_SAFE_VIDEO_FORMAT,
        "--merge-output-format",
        "mp4",
        "--no-write-info-json",
        "--no-write-description",
        "--no-write-thumbnail",
        "-o",
        output_template,
        url,
    ]


def _download_audio(
    video_id: str,
    stem: str,
    temp_dir: Path,
    final_path: Path,
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
    cookie_retry_state: _YtdlpAttemptState | None = None,
    aria2_validation: _Aria2RuntimeValidation | None = None,
) -> None:
    _run_media_ytdlp_with_engine_fallback(
        lambda *, force_stable: _build_audio_ytdlp_command(
            video_id,
            temp_dir,
            options,
            force_stable_downloader=force_stable,
            aria2_validation=aria2_validation,
        ),
        temp_dir,
        options,
        log,
        cancel_controller,
        cookie_retry_state,
    )
    _move_single_file(temp_dir, "*.mp3", final_path, log, cancel_controller=cancel_controller)


def _build_audio_ytdlp_command(
    video_id: str,
    temp_dir: Path,
    options: DownloadOptions,
    *,
    force_stable_downloader: bool = False,
    aria2_validation: _Aria2RuntimeValidation | None = None,
) -> list[str]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    output_template = str(temp_dir / f"{_safe_temp_stem(video_id)}.%(ext)s")
    downloader_selection = _media_downloader_selection(
        options,
        force_stable=force_stable_downloader,
        aria2_validation=aria2_validation,
    )
    return _base_ytdlp_command(options) + list(downloader_selection.command_args) + [
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "--no-write-info-json",
        "--no-write-description",
        "--no-write-thumbnail",
        "-o",
        output_template,
        url,
    ]


def _run_media_ytdlp_with_engine_fallback(
    build_command,
    temp_dir: Path,
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
    cookie_retry_state: _YtdlpAttemptState | None = None,
) -> None:
    command = build_command(force_stable=False)
    try:
        _run_ytdlp_with_retries(command, options, log, cancel_controller, cookie_retry_state)
        return
    except YtdlpExecutionError as exc:
        failure_kind = classify_ytdlp_failure_kind(exc, options)
        exc.failure_kind = failure_kind
        if not _eligible_for_aria2_stable_fallback(exc, failure_kind):
            raise

        log("[WARNING] aria2c media transfer failed; retrying the current part once with the stable yt-dlp internal downloader.")
        _cleanup_failed_media_attempt_partials(list(exc.command or command), temp_dir, log)
        _raise_if_cancelled(cancel_controller)
        stable_command = build_command(force_stable=True)
        _run_ytdlp_with_retries(stable_command, options, log, cancel_controller, cookie_retry_state)
        log("[INFO] Stable downloader fallback succeeded for the current part.")


def _eligible_for_aria2_stable_fallback(
    exc: YtdlpExecutionError,
    failure_kind: YtdlpFailureKind,
) -> bool:
    failed_command = list(exc.command)
    if not _command_uses_aria2_downloader(failed_command):
        return False
    if exc.stage != YTDLP_STAGE_DOWNLOAD:
        return False
    text = "\n".join([str(exc), exc.combined_output, *exc.output_lines, *exc.fatal_lines])
    if (
        _contains_bot_check_error(text)
        or is_cookie_session_error(text)
        or _contains_login_required_error(text)
        or _contains_po_token_or_visitor_data_error(text)
        or _contains_rate_limit_error(text)
    ):
        return False
    if failure_kind in {
        YtdlpFailureKind.HTTP_401,
        YtdlpFailureKind.RATE_LIMIT,
        YtdlpFailureKind.BOT_CHECK,
        YtdlpFailureKind.COOKIE_SESSION,
        YtdlpFailureKind.LOGIN_REQUIRED,
        YtdlpFailureKind.PO_TOKEN_OR_VISITOR_DATA,
        YtdlpFailureKind.FORMAT_UNAVAILABLE,
        YtdlpFailureKind.PERMANENT_VIDEO,
        YtdlpFailureKind.OUTPUT_PATH,
        YtdlpFailureKind.TOOL_CONFIGURATION,
    }:
        return False
    if failure_kind in {
        YtdlpFailureKind.NETWORK_TIMEOUT,
        YtdlpFailureKind.NETWORK,
        YtdlpFailureKind.HTTP_403,
    }:
        return True
    return _contains_aria2_transport_failure(text)


def _command_uses_aria2_downloader(command: list[str]) -> bool:
    downloader_value = _command_option_value(command, "--downloader").lower()
    downloader_args = _command_option_value(command, "--downloader-args").lower()
    return bool("aria2" in downloader_value or downloader_args.startswith("aria2"))


def _contains_aria2_transport_failure(text: str) -> bool:
    lower = (text or "").lower()
    return _contains_any(
        lower,
        (
            "aria2c",
            "aria2",
            "external downloader",
            "cuid#",
            "errorcode=",
            "download aborted",
            "download failed",
            "fragment",
            "connection reset",
            "connection aborted",
            "timed out",
            "timeout",
        ),
    )


def _cleanup_failed_media_attempt_partials(command: list[str], staging_dir: Path, log=None) -> None:
    output_template = _command_option_value(command, "-o")
    if not output_template:
        return
    template_path = Path(output_template)
    parent = template_path.parent if str(template_path.parent) else staging_dir
    try:
        resolved_parent = parent.resolve(strict=False)
        resolved_staging = staging_dir.resolve(strict=False)
    except OSError:
        return
    if not _is_path_relative_to(resolved_parent, resolved_staging):
        return

    template_name = template_path.name
    prefix = template_name.split("%", 1)[0]
    if not prefix:
        return

    removable_suffixes = {
        ".aria2",
        ".part",
        ".ytdl",
        ".mp4",
        ".m4a",
        ".webm",
        ".mp3",
        ".unknown_video",
        ".unknown_audio",
    }
    try:
        candidates = list(parent.glob(f"{prefix}*"))
    except OSError:
        return
    for path in candidates:
        try:
            if not path.is_file():
                continue
            name = path.name.lower()
            suffix = path.suffix.lower()
            if suffix not in removable_suffixes and ".part" not in name and ".ytdl" not in name:
                continue
            path.unlink()
        except OSError:
            if log:
                log("[WARNING] Could not remove a failed aria2 partial file before stable fallback.")


def _extract_mp3_from_video(
    source_video_path: Path,
    temp_dir: Path,
    final_audio_path: Path,
    log=None,
    cancel_controller: DownloadController | None = None,
) -> None:
    _emit_current_progress("Validating MP4")
    _validate_premiere_safe_mp4_for_download(source_video_path, log, False, cancel_controller)
    if _final_file_ready(final_audio_path):
        return

    _emit_current_progress("MP3", message="Extracting audio from MP4")
    temp_mp3_path = temp_dir / f"{_safe_temp_stem(final_audio_path.stem)}.mp3"
    command = [
        str(runtime_file("ffmpeg.exe")),
        "-y",
        "-i",
        str(source_video_path),
        "-vn",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "0",
        str(temp_mp3_path),
    ]
    _run_ffmpeg_for_audio(command, cancel_controller)
    if not _final_file_ready(temp_mp3_path):
        raise DownloadError("audio extraction failed")
    _atomic_promote_with_retry(temp_mp3_path, final_audio_path, log, cancel_controller=cancel_controller)
    if not _final_file_ready(final_audio_path):
        raise DownloadError("audio extraction failed")


def _run_ffmpeg_for_audio(command: list[str], cancel_controller: DownloadController | None = None) -> str:
    creationflags = _subprocess_creationflags()
    process = None
    stdout = ""
    stderr = ""
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        if cancel_controller is not None:
            cancel_controller.set_current_process(process)
        while True:
            if _cancel_requested(cancel_controller):
                _terminate_process_tree(process)
                raise DownloadCancelled("download cancelled/interrupted")
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
    except FileNotFoundError:
        raise DownloadError("ffmpeg.exe missing")
    except OSError as exc:
        reason = _sanitize_subprocess_output_line(str(exc)) or type(exc).__name__
        raise DownloadError(f"ffmpeg process creation failed during extract_mp3: {type(exc).__name__}: {reason}") from exc
    except KeyboardInterrupt:
        _terminate_process_tree(process)
        raise DownloadCancelled("download cancelled/interrupted")
    finally:
        if process is not None and cancel_controller is not None:
            cancel_controller.clear_current_process(process)

    if _cancel_requested(cancel_controller):
        raise DownloadCancelled("download cancelled/interrupted")
    if process.returncode == 0:
        return _bounded_sanitized_subprocess_output(stdout, stderr)

    output_lines = _ffmpeg_output_lines(stdout, stderr)
    combined_output = _bounded_sanitized_subprocess_output(stdout, stderr)
    return_code = process.returncode if process.returncode is not None else -1
    initial = FFmpegExecutionError(
        operation="extract_mp3",
        exit_code=return_code,
        message="ffmpeg extract_mp3 failed",
        output_lines=output_lines,
        combined_output=combined_output,
    )
    failure_kind = classify_ffmpeg_failure_kind(initial)
    raise FFmpegExecutionError(
        operation="extract_mp3",
        exit_code=return_code,
        message=f"ffmpeg extract_mp3 failed: {failure_kind.value}",
        output_lines=output_lines,
        combined_output=combined_output,
    )


def _download_thumbnail(
    video,
    stem: str,
    temp_dir: Path,
    final_path: Path,
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
    cookie_retry_state: _YtdlpAttemptState | None = None,
) -> None:
    url = f"https://www.youtube.com/watch?v={video.video_id}"
    output_template = str(temp_dir / f"{_safe_temp_stem(video.video_id)}.%(ext)s")
    if video.thumbnail_url:
        try:
            log("[INFO] Downloading thumbnail from API URL first.")
            _raise_if_cancelled(cancel_controller)
            _download_thumbnail_from_url(
                video.thumbnail_url,
                temp_dir / f"{_safe_temp_stem(video.video_id)}.jpg",
                final_path,
                log,
                cancel_controller,
            )
            return
        except DownloadCancelled:
            raise
        except DownloadError:
            log("[WARNING] API thumbnail download failed, falling back to yt-dlp thumbnail.")
            _emit_current_progress("Thumbnail", message="Using yt-dlp fallback")

    command = _base_ytdlp_command(options) + [
        "--skip-download",
        "--write-thumbnail",
        "--convert-thumbnails",
        "jpg",
        "-o",
        output_template,
        url,
    ]

    try:
        _run_ytdlp_with_retries(command, options, log, cancel_controller, cookie_retry_state)
        _move_single_file(temp_dir, "*.jpg", final_path, log, cancel_controller=cancel_controller)
        return
    except YtdlpExecutionError as exc:
        if exc.bot_check or exc.http_403:
            raise
        raise
    except DownloadError:
        raise DownloadError("thumbnail download failed")


def _base_ytdlp_command(options: DownloadOptions) -> list[str]:
    deno_path = _deno_runtime_path()
    command = [
        str(runtime_file("yt-dlp.exe")),
        "--no-playlist",
        "--newline",
        "--no-overwrites",
        "--retries",
        "30",
        "--fragment-retries",
        "30",
        "--file-access-retries",
        "10",
        "--socket-timeout",
        "60",
        "--http-chunk-size",
        "1M",
        "--ffmpeg-location",
        str(runtime_file("ffmpeg.exe").parent),
    ]
    if deno_path.exists():
        command.extend([
            "--js-runtimes",
            f"deno:{deno_path}",
            "--remote-components",
            "ejs:github",
        ])
    if options.speed_limit:
        command.extend(["--limit-rate", options.speed_limit])
    return command


def _premiere_safe_mp4_ready(path: Path, cancel_controller: DownloadController | None = None) -> bool:
    try:
        if cancel_controller is None:
            _validate_premiere_safe_mp4(path, delete_invalid=False)
        else:
            _validate_premiere_safe_mp4(path, delete_invalid=False, cancel_controller=cancel_controller)
    except DownloadCancelled:
        raise
    except DownloadError:
        return False
    return True


def _premiere_safe_mp4_ready_for_download(path: Path, cancel_controller: DownloadController | None) -> bool:
    if cancel_controller is None:
        return _premiere_safe_mp4_ready(path)
    return _premiere_safe_mp4_ready(path, cancel_controller)


def _validate_premiere_safe_mp4_for_download(
    path: Path,
    log,
    delete_invalid: bool,
    cancel_controller: DownloadController | None,
) -> None:
    if cancel_controller is None:
        _validate_premiere_safe_mp4(path, log, delete_invalid=delete_invalid)
        return
    _validate_premiere_safe_mp4(path, log, delete_invalid=delete_invalid, cancel_controller=cancel_controller)


def _validate_premiere_safe_mp4(
    path: Path,
    log=None,
    delete_invalid: bool = True,
    cancel_controller: DownloadController | None = None,
) -> None:
    _raise_if_cancelled(cancel_controller)
    try:
        if not path.exists():
            _fail_premiere_safe_validation(path, "file does not exist", log, delete_invalid=delete_invalid)
        if not path.is_file():
            _fail_premiere_safe_validation(path, "path is not a file", log, delete_invalid=delete_invalid)
        if path.suffix.lower() != ".mp4":
            _fail_premiere_safe_validation(path, "file extension is not .mp4", log, delete_invalid=delete_invalid)
        if path.stat().st_size <= 0:
            _fail_premiere_safe_validation(path, "file size is zero", log, delete_invalid=delete_invalid)
    except OSError as exc:
        _fail_premiere_safe_validation(
            path,
            f"file check failed: {type(exc).__name__}",
            log,
            delete_invalid=delete_invalid,
        )

    try:
        if cancel_controller is None:
            output = _probe_media_with_ffmpeg(path)
        else:
            output = _probe_media_with_ffmpeg(path, cancel_controller)
    except DownloadCancelled:
        raise
    except DownloadError as exc:
        message = str(exc)
        if message == "ffmpeg.exe missing":
            raise
        reason = message.removeprefix("premiere_safe_mp4_validation_failed: ").strip() or "unable to probe media"
        _fail_premiere_safe_validation(path, reason, log, delete_invalid=delete_invalid)
    _raise_if_cancelled(cancel_controller)
    ok, reason = _parse_premiere_safe_probe_output(output)
    if not ok:
        _fail_premiere_safe_validation(path, reason, log, delete_invalid=delete_invalid)


def _fail_premiere_safe_validation(
    path: Path,
    reason: str,
    log=None,
    delete_invalid: bool = True,
) -> None:
    if delete_invalid and path.suffix.lower() == ".mp4":
        _delete_invalid_file(path, log)
    raise DownloadError(f"premiere_safe_mp4_validation_failed: {reason}")


def _delete_invalid_file(path: Path, log=None) -> None:
    try:
        if path.exists() and path.is_file():
            path.unlink()
            if log:
                log("[WARNING] Removed invalid Premiere-safe MP4 output.")
    except OSError:
        if log:
            log("[WARNING] Invalid Premiere-safe MP4 output could not be removed.")


def _probe_media_with_ffmpeg(path: Path, cancel_controller: DownloadController | None = None) -> str:
    ffmpeg_path = runtime_file("ffmpeg.exe")
    ffprobe_path = ffmpeg_path.with_name("ffprobe.exe")
    if ffprobe_path.exists():
        command = [
            str(ffprobe_path),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,codec_tag_string,width,height",
            "-of",
            "compact=p=0:nk=0",
            str(path),
        ]
        output = (
            _run_probe_command(command)
            if cancel_controller is None
            else _run_probe_command(command, cancel_controller)
        )
        if output.strip():
            return output

    command = [str(ffmpeg_path), "-hide_banner", "-i", str(path)]
    output = _run_probe_command(command) if cancel_controller is None else _run_probe_command(command, cancel_controller)
    if output.strip():
        return output
    raise DownloadError("premiere_safe_mp4_validation_failed: unable to probe media")


def _run_probe_command(command: list[str], cancel_controller: DownloadController | None = None) -> str:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = None
    stdout = ""
    stderr = ""
    deadline = time.monotonic() + 30
    try:
        _raise_if_cancelled(cancel_controller)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        if cancel_controller is not None:
            cancel_controller.set_current_process(process)
        while True:
            if _cancel_requested(cancel_controller):
                _terminate_process_tree(process)
                raise DownloadCancelled("download cancelled/interrupted")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_tree(process)
                raise DownloadError("premiere_safe_mp4_validation_failed: media probe timed out")
            try:
                stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except FileNotFoundError:
        raise DownloadError("ffmpeg.exe missing")
    except KeyboardInterrupt:
        _terminate_process_tree(process)
        raise DownloadCancelled("download cancelled/interrupted")
    finally:
        if process is not None and cancel_controller is not None:
            cancel_controller.clear_current_process(process)

    _raise_if_cancelled(cancel_controller)
    return f"{stdout}\n{stderr}"


def _parse_premiere_safe_probe_output(output: str) -> tuple[bool, str]:
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    video_lines = [line for line in lines if _is_video_stream_line(line)]
    audio_lines = [line for line in lines if _is_audio_stream_line(line)]
    if not video_lines:
        return False, "no video stream"
    if not audio_lines:
        return False, "no audio stream"

    video_line = video_lines[0]
    audio_line = audio_lines[0]
    if not _contains_h264_video_codec(video_line):
        return False, "video codec is not H.264/AVC"
    if not _contains_aac_audio_codec(audio_line):
        return False, "audio codec is not AAC"

    height = _extract_video_height(video_line)
    if height is None:
        return False, "video height is unknown"
    if height > 1080:
        return False, "video height is above 1080p"
    return True, ""


def _is_video_stream_line(line: str) -> bool:
    lower = line.lower()
    return "video:" in lower or "codec_type=video" in lower


def _is_audio_stream_line(line: str) -> bool:
    lower = line.lower()
    return "audio:" in lower or "codec_type=audio" in lower


def _contains_h264_video_codec(line: str) -> bool:
    lower = line.lower()
    return "codec_name=h264" in lower or "codec_tag_string=avc1" in lower or "video: h264" in lower or "avc1" in lower


def _contains_aac_audio_codec(line: str) -> bool:
    lower = line.lower()
    return "codec_name=aac" in lower or "codec_tag_string=mp4a" in lower or "audio: aac" in lower or "mp4a" in lower


def _extract_video_height(line: str) -> int | None:
    height_match = re.search(r"(?:^|[|,\s])height=(\d{2,5})(?:$|[|,\s])", line.lower())
    if height_match:
        return int(height_match.group(1))
    resolution_match = re.search(r"(?<![0-9])(\d{2,5})x(\d{2,5})(?![0-9])", line.lower())
    if resolution_match:
        return int(resolution_match.group(2))
    return None


def _deno_runtime_path() -> Path:
    return runtime_file("deno.exe")


def _aria2_runtime_path() -> Path:
    return runtime_file("aria2c.exe")


def _prepare_media_downloader_runtime(
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
) -> _Aria2RuntimeValidation:
    engine = _normalize_download_engine(options.download_engine)
    aria2_path = _aria2_runtime_path()
    if engine != DOWNLOAD_ENGINE_ARIA2_FAST:
        log("[INFO] Download engine: stable yt-dlp internal")
        return _Aria2RuntimeValidation(False, False, aria2_path)

    if not aria2_path.exists() or not aria2_path.is_file():
        log("[WARNING] aria2c.exe is missing or unavailable; using stable yt-dlp internal downloader.")
        return _Aria2RuntimeValidation(True, False, aria2_path)

    _raise_if_cancelled(cancel_controller)
    version = _get_command_version(
        [str(aria2_path), "--version"],
        cancel_controller=cancel_controller,
        timeout_seconds=ARIA2_VERSION_TIMEOUT_SECONDS,
    )
    if not version:
        log("[WARNING] aria2c.exe is missing or unavailable; using stable yt-dlp internal downloader.")
        return _Aria2RuntimeValidation(True, False, aria2_path)

    log("[INFO] Download engine: aria2c fast experimental")
    log("[INFO] aria2c profile: connections=8 splits=8 jobs=4 piece=1M")
    return _Aria2RuntimeValidation(True, True, aria2_path)


def _call_runtime_tool_summary(
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
) -> None:
    summary = _log_runtime_tool_summary
    try:
        signature = inspect.signature(summary)
    except (TypeError, ValueError):
        summary(options, log, cancel_controller)
        return

    positional_capacity = 0
    has_varargs = False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            has_varargs = True
        elif parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }:
            positional_capacity += 1

    if has_varargs or positional_capacity >= 3:
        _log_runtime_tool_summary(options, log, cancel_controller)
    elif positional_capacity == 2:
        _log_runtime_tool_summary(options, log)
    else:
        _log_runtime_tool_summary(log)


def _log_runtime_tool_summary(
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
) -> None:
    _ = options
    ytdlp_path = runtime_file("yt-dlp.exe")
    ffmpeg_path = runtime_file("ffmpeg.exe")
    deno_path = _deno_runtime_path()

    _raise_if_cancelled(cancel_controller)
    ytdlp_version = _get_command_version(
        [str(ytdlp_path), "--version"],
        cancel_controller=cancel_controller,
    )
    if ytdlp_version:
        log(f"[INFO] yt-dlp version: {ytdlp_version}")
    else:
        log("[INFO] yt-dlp version: unavailable")

    _raise_if_cancelled(cancel_controller)
    ffmpeg_version = (
        _get_command_version([str(ffmpeg_path), "-version"], cancel_controller=cancel_controller)
        if ffmpeg_path.exists()
        else ""
    )
    if ffmpeg_path.exists() and ffmpeg_version:
        log(f"[INFO] ffmpeg found: yes ({ffmpeg_version})")
    elif ffmpeg_path.exists():
        log("[INFO] ffmpeg found: yes (version unavailable)")
    else:
        log("[INFO] ffmpeg found: no")

    _raise_if_cancelled(cancel_controller)
    deno_version = (
        _get_command_version([str(deno_path), "--version"], cancel_controller=cancel_controller)
        if deno_path.exists()
        else ""
    )
    if deno_path.exists() and deno_version:
        log(f"[INFO] deno found: yes ({deno_version})")
    elif deno_path.exists():
        log("[INFO] deno found: yes (version unavailable)")
    else:
        log("[INFO] deno found: no")


def _get_command_version(
    command: list[str],
    *,
    cancel_controller: DownloadController | None = None,
    timeout_seconds: float = 10.0,
) -> str:
    if not command:
        return ""
    creationflags = _subprocess_creationflags()
    process = None
    stdout = ""
    stderr = ""
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    try:
        _raise_if_cancelled(cancel_controller)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        if cancel_controller is not None:
            cancel_controller.set_current_process(process)
        if _cancel_requested(cancel_controller):
            _terminate_process_tree(process)
            raise DownloadCancelled("download cancelled/interrupted")
        while True:
            if _cancel_requested(cancel_controller):
                _terminate_process_tree(process)
                raise DownloadCancelled("download cancelled/interrupted")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_tree(process)
                return ""
            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except FileNotFoundError:
        if _cancel_requested(cancel_controller):
            raise DownloadCancelled("download cancelled/interrupted")
        return ""
    except OSError:
        if _cancel_requested(cancel_controller):
            raise DownloadCancelled("download cancelled/interrupted")
        return ""
    except KeyboardInterrupt:
        _terminate_process_tree(process)
        raise DownloadCancelled("download cancelled/interrupted")
    finally:
        if process is not None and cancel_controller is not None:
            cancel_controller.clear_current_process(process)

    if _cancel_requested(cancel_controller):
        raise DownloadCancelled("download cancelled/interrupted")
    output = stdout or stderr or ""
    for line in output.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _cancel_requested(cancel_controller: DownloadController | None) -> bool:
    return bool(cancel_controller and cancel_controller.is_cancel_requested())


def _raise_if_cancelled(cancel_controller: DownloadController | None) -> None:
    if _cancel_requested(cancel_controller):
        raise DownloadCancelled("download cancelled/interrupted")


def _sleep_with_cancel(seconds: int | float, cancel_controller: DownloadController | None) -> None:
    end_time = time.monotonic() + max(0, seconds)
    while True:
        _raise_if_cancelled(cancel_controller)
        remaining = end_time - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.25, remaining))


def _command_cookie_path(command: list[str]) -> str:
    for index, value in enumerate(command):
        if value == YTDLP_COOKIES_OPTION and index + 1 < len(command):
            return command[index + 1]
    return ""


def _command_video_id(command: list[str]) -> str:
    for value in command:
        match = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/)([^&?/]+)", value)
        if match:
            return match.group(1)
    return ""


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _cookie_file_snapshot(path: Path) -> _CookieFileSnapshot:
    try:
        stat_result = path.stat()
        if not path.is_file():
            return _CookieFileSnapshot(False, 0, None, "")
        return _CookieFileSnapshot(True, stat_result.st_size, stat_result.st_mtime_ns, _sha256_file(path))
    except OSError:
        return _CookieFileSnapshot(False, 0, None, "")


def _cookie_snapshot_log_text(snapshot: _CookieFileSnapshot | None) -> str:
    if snapshot is None:
        return "snapshot_available=no"
    return (
        f"snapshot_available={'yes' if snapshot.usable else 'no'}, "
        f"exists={str(snapshot.exists).lower()}, size={snapshot.size}, "
        f"mtime_ns={snapshot.mtime_ns if snapshot.mtime_ns is not None else '-'}"
    )


def _cookie_snapshots_content_equal(
    left: _CookieFileSnapshot | None,
    right: _CookieFileSnapshot | None,
) -> bool:
    return bool(
        left
        and right
        and left.usable
        and right.usable
        and left.size == right.size
        and left.sha256 == right.sha256
    )


def _cookie_snapshot_changed(
    old: _CookieFileSnapshot | None,
    current: _CookieFileSnapshot | None,
) -> bool:
    return bool(old and current and current.usable and not _cookie_snapshots_content_equal(old, current))


def _copy_cookie_file(source: Path, target: Path) -> None:
    shutil.copy2(source, target)


def _set_private_cookie_permissions(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _strip_cookie_options(command: list[str]) -> list[str]:
    stripped: list[str] = []
    index = 0
    while index < len(command):
        value = command[index]
        if value == YTDLP_COOKIES_OPTION:
            index += 2
            continue
        if value.startswith(f"{YTDLP_COOKIES_OPTION}="):
            index += 1
            continue
        stripped.append(value)
        index += 1
    return stripped


def _cookie_option_insert_index(command: list[str]) -> int:
    for index, value in enumerate(command):
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value or ""):
            return index
    return len(command)


def _insert_cookie_option(command: list[str], cookie_path: Path) -> list[str]:
    insert_at = _cookie_option_insert_index(command)
    return [
        *command[:insert_at],
        YTDLP_COOKIES_OPTION,
        str(cookie_path),
        *command[insert_at:],
    ]


@contextmanager
def _prepared_cookie_attempt(
    command: list[str],
    options: DownloadOptions,
    log=None,
    *,
    use_cookies: bool | None = None,
):
    base_command = _strip_cookie_options(list(command))
    cookies_requested = options.cookies_enabled if use_cookies is None else bool(use_cookies)
    if not cookies_requested:
        yield _PreparedCookieAttempt(base_command, cookies_used=False)
        return

    canonical_path = Path(effective_cookies_path(options))
    last_before: _CookieFileSnapshot | None = None
    last_after: _CookieFileSnapshot | None = None
    last_temp: _CookieFileSnapshot | None = None
    for attempt_index in range(3):
        last_before = _cookie_file_snapshot(canonical_path)
        if not last_before.usable:
            raise DownloadError("Cookies file missing")

        with tempfile.TemporaryDirectory(prefix="s9h_cookie_attempt_") as temp_dir:
            temp_cookie_path = Path(temp_dir) / "cookies.txt"
            try:
                _copy_cookie_file(canonical_path, temp_cookie_path)
                _set_private_cookie_permissions(temp_cookie_path)
            except OSError as exc:
                raise DownloadError("Could not prepare isolated cookies file") from exc

            last_temp = _cookie_file_snapshot(temp_cookie_path)
            last_after = _cookie_file_snapshot(canonical_path)
            if (
                _cookie_snapshots_content_equal(last_before, last_after)
                and _cookie_snapshots_content_equal(last_before, last_temp)
            ):
                yield _PreparedCookieAttempt(
                    _insert_cookie_option(base_command, temp_cookie_path),
                    str(canonical_path),
                    last_before,
                    str(temp_cookie_path),
                    cookies_used=True,
                )
                return

        if log:
            log(
                "[COOKIE-DIAG] Cookie source changed while preparing isolated copy; "
                f"retrying stable copy {attempt_index + 1}/3."
            )

    if log:
        log(f"[COOKIE-DIAG] canonical_before {_cookie_snapshot_log_text(last_before)}")
        log(f"[COOKIE-DIAG] temp_copy {_cookie_snapshot_log_text(last_temp)}")
        log(f"[COOKIE-DIAG] canonical_after {_cookie_snapshot_log_text(last_after)}")
    raise DownloadError("Cookie file changed while preparing isolated copy. Try again after export finishes.")


def _run_ytdlp_with_retries(
    command: list[str],
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
    cookie_retry_state: _YtdlpAttemptState | None = None,
) -> None:
    http_403_delays = [10, 30]
    http_403_retries = 0
    cookie_media_waited_seconds = 0
    cookie_media_metadata_ready_monotonic = 0.0
    cookie_media_probe_active = False
    attempt_state = cookie_retry_state or _YtdlpAttemptState()
    stream_interrupted_retried = False
    base_command = _strip_cookie_options(list(command))
    current_command = list(base_command)
    attempt_number = 0
    use_cookies_for_attempt = bool(options.cookies_enabled)
    attempt_part = _current_ytdlp_part()

    if _batch_cookie_source_changed(options, attempt_state.batch_state):
        _reset_cookie_bootstrap_batch_mode(attempt_state.batch_state)
        log(
            "[COOKIE BATCH MODE] Cookie source changed. "
            "Returning to the normal cookie-first strategy."
        )

    if _should_start_in_cookie_bootstrap_batch_mode(
        options,
        attempt_part,
        base_command,
        attempt_state.batch_state,
    ):
        attempt_state.cookieless_fallback_used = True
        attempt_state.authenticated_infojson_fallback_used = True
        bootstrap_prepared = False
        prefetched_media = attempt_state.prefetched_media
        if (
            prefetched_media is not None
            and prefetched_media.info_json_path is not None
            and prefetched_media.info_json_path.exists()
            and prefetched_media.ready_monotonic > 0
        ):
            current_command = _build_infojson_media_download_command(
                base_command,
                prefetched_media.info_json_path,
            )
            cookie_media_metadata_ready_monotonic = prefetched_media.ready_monotonic
            bootstrap_prepared = True
            log(
                "[COOKIE BATCH MODE] Using one-video lookahead metadata; "
                "the media transfer will begin as soon as its learned age is reached."
            )
        else:
            log(
                "[COOKIE BATCH MODE] Reusing the successful batch strategy: "
                "authenticated metadata first, then cookieless media transfer."
            )
            attempt_number += 1
            try:
                current_command = _prepare_authenticated_infojson_download_command(
                    base_command,
                    options,
                    log,
                    cancel_controller,
                    attempt_number,
                    attempt_part,
                )
                cookie_media_metadata_ready_monotonic = time.monotonic()
                bootstrap_prepared = True
            except YtdlpExecutionError as bootstrap_exc:
                _attach_ytdlp_attempt_context(bootstrap_exc, attempt_part)
                bootstrap_kind = classify_ytdlp_failure_kind(bootstrap_exc, options)
                bootstrap_exc.failure_kind = bootstrap_kind
                _log_ytdlp_attempt_failure(
                    log,
                    bootstrap_exc,
                    bootstrap_kind,
                    attempt_number,
                )
                _reset_cookie_bootstrap_batch_mode(attempt_state.batch_state)
                attempt_state.cookieless_fallback_used = False
                attempt_state.authenticated_infojson_fallback_used = False
                current_command = list(base_command)
                use_cookies_for_attempt = bool(options.cookies_enabled)
                log(
                    "[COOKIE BATCH MODE] Authenticated metadata extraction failed. "
                    "Falling back to the normal cookie-first strategy."
                )

        if bootstrap_prepared:
            use_cookies_for_attempt = False
            _start_attempt_lookahead(attempt_state, log)
            settle_delay, cookie_media_probe_active = _batch_cookie_media_initial_delay(
                attempt_state.batch_state,
            )
            current_age = _cookie_media_age_seconds(
                cookie_media_metadata_ready_monotonic,
                cookie_media_waited_seconds,
            )
            if cookie_media_probe_active and current_age > settle_delay:
                learned_delay = (
                    max(0, int(attempt_state.batch_state.media_settle_delay_seconds))
                    if attempt_state.batch_state is not None
                    else settle_delay
                )
                settle_delay = learned_delay
                cookie_media_probe_active = False
            remaining_delay = max(0, settle_delay - current_age)
            if remaining_delay:
                delay_kind = "short adaptive probe" if cookie_media_probe_active else "learned metadata age"
                log(
                    "[COOKIE BATCH MODE] Waiting "
                    f"{remaining_delay} seconds before the media transfer ({delay_kind}; "
                    f"metadata age target: {settle_delay} seconds)."
                )
                _sleep_with_cancel(remaining_delay, cancel_controller)
            elif settle_delay:
                log(
                    "[COOKIE LOOKAHEAD] Metadata already reached the learned age; "
                    "starting media transfer immediately."
                )
            cookie_media_waited_seconds = settle_delay

    while True:
        _raise_if_cancelled(cancel_controller)
        attempt_info: _PreparedCookieAttempt | None = None
        attempt_part = _current_ytdlp_part()
        try:
            with _prepared_cookie_attempt(
                current_command,
                options,
                log,
                use_cookies=use_cookies_for_attempt,
            ) as prepared_attempt:
                attempt_info = prepared_attempt
                attempt_number += 1
                attempt_part = _current_ytdlp_part()
                log(_ytdlp_start_log_line(prepared_attempt.command, options, attempt_number, attempt_part))
                stderr = _run_ytdlp(prepared_attempt.command, cancel_controller)
            if (
                SHOW_TECHNICAL_WARNINGS
                and stderr
                and _contains_missing_js_runtime_error(stderr)
                and not _deno_runtime_path().exists()
            ):
                _log_missing_js_runtime_warning(log, [stderr])
            if attempt_state.cookieless_fallback_used and not prepared_attempt.cookies_used:
                log(
                    "[COOKIE FALLBACK SUCCESS] Authenticated metadata was reused and the media "
                    "download completed without cookies."
                )
                _record_cookie_bootstrap_batch_success(
                    options,
                    attempt_state.batch_state,
                    cookie_media_waited_seconds,
                    cookie_media_probe_active,
                    log,
                )
            return
        except YtdlpExecutionError as exc:
            if not exc.command and attempt_info is not None:
                exc.command = tuple(str(value) for value in attempt_info.command)
            _attach_ytdlp_attempt_context(exc, attempt_part)
            failure_kind = classify_ytdlp_failure_kind(exc, options)
            exc.failure_kind = failure_kind
            _log_ytdlp_attempt_failure(log, exc, failure_kind, attempt_number)

            if _should_use_authenticated_infojson_fallback(
                exc,
                failure_kind,
                options,
                attempt_info,
                attempt_state,
            ):
                attempt_state.cookieless_fallback_used = True
                attempt_state.authenticated_infojson_fallback_used = True
                log(
                    "[COOKIE FALLBACK] Cookie-authenticated media request returned HTTP 403. "
                    "Extracting authenticated metadata, then downloading the saved media URLs "
                    "without cookies."
                )
                attempt_number += 1
                try:
                    current_command = _prepare_authenticated_infojson_download_command(
                        base_command,
                        options,
                        log,
                        cancel_controller,
                        attempt_number,
                        attempt_part,
                    )
                except YtdlpExecutionError as bootstrap_exc:
                    _attach_ytdlp_attempt_context(bootstrap_exc, attempt_part)
                    bootstrap_kind = classify_ytdlp_failure_kind(bootstrap_exc, options)
                    bootstrap_exc.failure_kind = bootstrap_kind
                    _log_ytdlp_attempt_failure(
                        log,
                        bootstrap_exc,
                        bootstrap_kind,
                        attempt_number,
                    )
                    raise
                cookie_media_metadata_ready_monotonic = time.monotonic()
                use_cookies_for_attempt = False
                _start_attempt_lookahead(attempt_state, log)
                continue

            cookieless_saved_media_403 = _is_cookieless_saved_media_http_403(
                exc,
                failure_kind,
                attempt_info,
                attempt_state,
                current_command,
            )
            if cookieless_saved_media_403:
                current_age = _cookie_media_age_seconds(
                    cookie_media_metadata_ready_monotonic,
                    cookie_media_waited_seconds,
                )
                retry_target = _next_cookie_media_retry_target(current_age)
                if retry_target is not None:
                    delay = max(0, retry_target - current_age)
                    if cookie_media_probe_active and retry_target > current_age:
                        learned_delay = (
                            max(0, int(attempt_state.batch_state.media_settle_delay_seconds))
                            if attempt_state.batch_state is not None
                            else retry_target
                        )
                        _record_cookie_media_probe_failure(attempt_state.batch_state)
                        cookie_media_probe_active = False
                        log(
                            "[COOKIE BATCH MODE] Short delay probe still received HTTP 403. "
                            f"Returning toward the learned {learned_delay}-second delay."
                        )
                    cookie_media_waited_seconds = retry_target
                    log(
                        "[WARNING] HTTP 403 during cookieless media transfer. "
                        f"Retrying in {delay} seconds "
                        f"(metadata age target: {retry_target} seconds)."
                    )
                    _sleep_with_cancel(delay, cancel_controller)
                    continue

            if (
                not cookieless_saved_media_403
                and failure_kind == YtdlpFailureKind.HTTP_403
                and not attempt_state.verified_retry_used
                and http_403_retries < len(http_403_delays)
            ):
                delay = http_403_delays[http_403_retries]
                http_403_retries += 1
                log(
                    f"[WARNING] HTTP 403 during {exc.part}/{exc.stage}. "
                    f"Retrying in {delay} seconds (retry {http_403_retries}/2)."
                )
                _sleep_with_cancel(delay, cancel_controller)
                continue

            if (
                failure_kind == YtdlpFailureKind.NETWORK
                and (exc.stream_interrupted or _contains_stream_interrupted_output(exc.combined_output))
            ) and not stream_interrupted_retried:
                stream_interrupted_retried = True
                current_command = _ensure_flag(
                    _replace_option(base_command, "--http-chunk-size", "512K"),
                    "--no-continue",
                )
                log("[WARNING] Stream interrupted. Retrying once with safer chunk settings.")
                continue

            if _is_systemic_ytdlp_failure(failure_kind):
                if _resolve_systemic_ytdlp_failure(
                    exc,
                    failure_kind,
                    options,
                    base_command,
                    log,
                    cancel_controller,
                    attempt_state,
                    attempt_info,
                ):
                    current_command = list(base_command)
                    use_cookies_for_attempt = bool(options.cookies_enabled)
                    continue

            raise


def _should_start_in_cookie_bootstrap_batch_mode(
    options: DownloadOptions,
    part: str,
    command: list[str],
    batch_state: _YtdlpBatchState | None,
) -> bool:
    return bool(
        batch_state is not None
        and batch_state.cookie_bootstrap_media_mode
        and options.cookies_enabled
        and part == PART_VIDEO
        and "--load-info-json" not in command
        and any(
            str(value).startswith(("https://www.youtube.com/", "https://youtu.be/"))
            for value in command
        )
    )


def _record_cookie_bootstrap_batch_success(
    options: DownloadOptions,
    batch_state: _YtdlpBatchState | None,
    media_waited_seconds: int,
    probe_active: bool,
    log,
) -> None:
    if batch_state is None or not options.cookies_enabled:
        return

    was_enabled = batch_state.cookie_bootstrap_media_mode
    batch_state.cookie_bootstrap_media_mode = True
    observed_delay = max(0, int(media_waited_seconds))

    if probe_active:
        batch_state.media_settle_delay_seconds = observed_delay
        batch_state.media_videos_since_probe = 0
        log(
            "[COOKIE BATCH MODE] Short delay probe succeeded. "
            f"Future videos will use a {observed_delay}-second settling delay."
        )
    elif not was_enabled:
        batch_state.media_settle_delay_seconds = observed_delay
        batch_state.media_videos_since_probe = (
            COOKIE_MEDIA_PROBE_INTERVAL_VIDEOS
            if observed_delay > COOKIE_MEDIA_SHORT_PROBE_SECONDS
            else 0
        )
    else:
        batch_state.media_settle_delay_seconds = observed_delay
        if observed_delay > COOKIE_MEDIA_SHORT_PROBE_SECONDS:
            batch_state.media_videos_since_probe += 1
        else:
            batch_state.media_videos_since_probe = 0

    snapshot = _options_cookie_snapshot(options)
    if snapshot and snapshot.usable:
        batch_state.cookie_snapshot_sha256 = snapshot.sha256

    if not was_enabled:
        delay_text = (
            f" The observed successful media delay was {observed_delay} seconds. "
            "A shorter delay will be probed on the next video before reusing it."
            if observed_delay > COOKIE_MEDIA_SHORT_PROBE_SECONDS
            else f" A {observed_delay}-second media settling delay will be used."
        )
        log(
            "[COOKIE BATCH MODE] Enabled for the remaining videos in this batch."
            + delay_text
        )


def _batch_cookie_media_initial_delay(
    batch_state: _YtdlpBatchState | None,
) -> tuple[int, bool]:
    if batch_state is None:
        return 0, False

    learned_delay = max(0, int(batch_state.media_settle_delay_seconds))
    if learned_delay <= COOKIE_MEDIA_SHORT_PROBE_SECONDS:
        return learned_delay, False

    if batch_state.media_videos_since_probe >= COOKIE_MEDIA_PROBE_INTERVAL_VIDEOS:
        return COOKIE_MEDIA_SHORT_PROBE_SECONDS, True

    return learned_delay, False


def _record_cookie_media_probe_failure(
    batch_state: _YtdlpBatchState | None,
) -> None:
    if batch_state is None:
        return
    batch_state.media_videos_since_probe = 0


def _cookie_media_age_seconds(
    metadata_ready_monotonic: float,
    waited_seconds: int,
) -> int:
    logical_wait = max(0, int(waited_seconds))
    if metadata_ready_monotonic <= 0:
        return logical_wait
    elapsed = max(0.0, time.monotonic() - metadata_ready_monotonic)
    return max(logical_wait, int(elapsed))


def _next_cookie_media_retry_target(waited_seconds: int) -> int | None:
    waited = max(0, int(waited_seconds))
    for target in COOKIE_MEDIA_RETRY_TARGET_SECONDS:
        if target > waited:
            return int(target)
    return None


def _is_cookieless_saved_media_http_403(
    exc: YtdlpExecutionError,
    failure_kind: YtdlpFailureKind,
    attempt_info: _PreparedCookieAttempt | None,
    attempt_state: _YtdlpAttemptState,
    command: list[str],
) -> bool:
    return bool(
        failure_kind == YtdlpFailureKind.HTTP_403
        and exc.part == PART_VIDEO
        and attempt_state.authenticated_infojson_fallback_used
        and attempt_info is not None
        and not attempt_info.cookies_used
        and "--load-info-json" in command
        and _is_video_data_http_403(exc)
    )


def _options_cookie_snapshot(options: DownloadOptions) -> _CookieFileSnapshot | None:
    if not options.cookies_enabled:
        return None
    try:
        cookie_path = effective_cookies_path(options)
    except DownloadError:
        return None
    return _cookie_file_snapshot(Path(cookie_path))


def _batch_cookie_source_changed(
    options: DownloadOptions,
    batch_state: _YtdlpBatchState | None,
) -> bool:
    if (
        batch_state is None
        or not batch_state.cookie_bootstrap_media_mode
        or not batch_state.cookie_snapshot_sha256
    ):
        return False
    snapshot = _options_cookie_snapshot(options)
    return bool(
        snapshot
        and snapshot.usable
        and snapshot.sha256 != batch_state.cookie_snapshot_sha256
    )


def _reset_cookie_bootstrap_batch_mode(
    batch_state: _YtdlpBatchState | None,
) -> None:
    if batch_state is None:
        return
    batch_state.cookie_bootstrap_media_mode = False
    batch_state.media_settle_delay_seconds = 0
    batch_state.cookie_snapshot_sha256 = ""
    batch_state.media_videos_since_probe = 0


def _should_use_authenticated_infojson_fallback(
    exc: YtdlpExecutionError,
    failure_kind: YtdlpFailureKind,
    options: DownloadOptions,
    attempt_info: _PreparedCookieAttempt | None,
    attempt_state: _YtdlpAttemptState,
) -> bool:
    return bool(
        options.cookies_enabled
        and attempt_info is not None
        and attempt_info.cookies_used
        and not attempt_state.authenticated_infojson_fallback_used
        and failure_kind == YtdlpFailureKind.HTTP_403
        and exc.part == PART_VIDEO
        and _is_video_data_http_403(exc)
    )


def _is_video_data_http_403(exc: YtdlpExecutionError) -> bool:
    text = "\n".join(exc.fatal_lines or exc.output_lines) or exc.combined_output
    lower = text.lower()
    return bool(
        (exc.http_status == 403 or _contains_http_403_error(text))
        and (
            "unable to download video data" in lower
            or "failed to download video data" in lower
            or "video data: http error 403" in lower
        )
    )


def _prepare_authenticated_infojson_download_command(
    command: list[str],
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None,
    attempt_number: int,
    part: str,
) -> list[str]:
    info_json_path = _extract_authenticated_infojson_path(
        command,
        options,
        log,
        cancel_controller,
        attempt_number,
        part,
        log_start=True,
    )
    log(
        "[COOKIE FALLBACK] Authenticated extraction completed. "
        "Downloading from saved media URLs without cookies."
    )
    return _build_infojson_media_download_command(command, info_json_path)


def _extract_authenticated_infojson_path(
    command: list[str],
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None,
    attempt_number: int,
    part: str,
    *,
    log_start: bool,
) -> Path:
    output_template = _command_option_value(command, "-o")
    if not output_template:
        raise DownloadError("yt-dlp output template missing for authenticated media fallback")

    staging_dir = Path(output_template).parent
    staging_dir.mkdir(parents=True, exist_ok=True)
    bootstrap_dir = Path(
        tempfile.mkdtemp(
            prefix=".s9h-auth-info-",
            dir=str(staging_dir),
        )
    )
    bootstrap_template = str(bootstrap_dir / "authenticated.%(ext)s")
    extract_command = _build_authenticated_infojson_extract_command(
        command,
        bootstrap_template,
    )

    with _prepared_cookie_attempt(
        extract_command,
        options,
        log,
        use_cookies=True,
    ) as prepared_attempt:
        if log_start:
            log(
                _ytdlp_start_log_line(
                    prepared_attempt.command,
                    options,
                    attempt_number,
                    part,
                )
            )
        _run_ytdlp(prepared_attempt.command, cancel_controller)

    return _select_authenticated_infojson(bootstrap_dir)


def _build_authenticated_infojson_extract_command(
    command: list[str],
    bootstrap_template: str,
) -> list[str]:
    prepared = _remove_command_flags(
        _strip_media_downloader_options(_strip_cookie_options(list(command))),
        {
            "--no-write-info-json",
            "--write-info-json",
            "--skip-download",
        },
    )
    prepared = _replace_option(prepared, "-o", bootstrap_template)
    prepared = _ensure_flag(prepared, "--write-info-json")
    prepared = _ensure_flag(prepared, "--skip-download")
    return prepared


def _build_infojson_media_download_command(
    command: list[str],
    info_json_path: Path,
) -> list[str]:
    prepared = _strip_url_arguments(_strip_cookie_options(list(command)))
    prepared = _remove_command_flags(
        prepared,
        {
            "--write-info-json",
            "--skip-download",
        },
    )
    prepared = _ensure_flag(prepared, "--no-write-info-json")
    prepared = _replace_option(
        prepared,
        "--load-info-json",
        str(info_json_path),
    )
    return prepared


def _select_authenticated_infojson(bootstrap_dir: Path) -> Path:
    matches = sorted(
        (
            path
            for path in bootstrap_dir.glob("*.info.json")
            if path.is_file() and path.stat().st_size > 0
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not matches:
        raise DownloadError("authenticated yt-dlp metadata file was not created")
    return matches[0]


def classify_ytdlp_failure_kind(exc: YtdlpExecutionError, options: DownloadOptions) -> YtdlpFailureKind:
    if exc.failure_kind is not None:
        return exc.failure_kind

    fatal_text = "\n".join(exc.fatal_lines or exc.output_lines)
    if fatal_text:
        kind = _classify_ytdlp_failure_text(fatal_text)
        if kind != YtdlpFailureKind.UNKNOWN:
            return kind

    text = fatal_text or exc.combined_output or "\n".join([str(exc), *exc.output_lines])
    if exc.bot_check and _contains_bot_check_error(text):
        return YtdlpFailureKind.BOT_CHECK
    if exc.http_403 and _contains_http_403_error(text):
        return YtdlpFailureKind.HTTP_403
    if exc.missing_js_runtime and _contains_missing_js_runtime_error(text):
        return YtdlpFailureKind.TOOL_CONFIGURATION
    if exc.stream_interrupted:
        if _contains_network_timeout_error(text):
            return YtdlpFailureKind.NETWORK_TIMEOUT
        if _contains_stream_interrupted_output(text) or _contains_network_error(text):
            return YtdlpFailureKind.NETWORK
    return _classify_ytdlp_failure_text(text)


def classify_ffmpeg_failure_kind(exc: FFmpegExecutionError) -> FFmpegFailureKind:
    text = "\n".join([str(exc), exc.combined_output, *exc.output_lines]).lower()
    if _contains_any(
        text,
        (
            "no space left on device",
            "disk full",
            "not enough space",
            "there is not enough space on the disk",
        ),
    ):
        return FFmpegFailureKind.DISK_FULL
    if _contains_any(
        text,
        (
            "permission denied",
            "access is denied",
            "operation not permitted",
        ),
    ):
        return FFmpegFailureKind.PERMISSION_DENIED
    if _contains_any(
        text,
        (
            "unknown encoder",
            "encoder (codec",
            "encoder not found",
            "not found for output stream",
            "error selecting an encoder",
        ),
    ):
        return FFmpegFailureKind.ENCODER_UNAVAILABLE
    if _contains_any(
        text,
        (
            "matches no streams",
            "does not contain any stream",
            "audio stream not found",
            "no audio stream",
        ),
    ):
        return FFmpegFailureKind.NO_AUDIO_STREAM
    if _contains_any(
        text,
        (
            "no such file or directory",
            "invalid filename",
            "error opening output",
            "could not open output file",
            "unable to open output file",
            "filename too long",
            "file name too long",
        ),
    ):
        return FFmpegFailureKind.OUTPUT_PATH
    if _contains_any(
        text,
        (
            "invalid data found when processing input",
            "moov atom not found",
            "error opening input",
            "could not find codec parameters",
            "invalid argument",
        ),
    ):
        return FFmpegFailureKind.INVALID_INPUT
    if _contains_any(
        text,
        (
            "broken pipe",
            "input/output error",
            "error writing trailer",
            "error closing file",
            "conversion failed",
        ),
    ):
        return FFmpegFailureKind.INTERRUPTED_WRITE
    return FFmpegFailureKind.UNKNOWN


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _is_systemic_ytdlp_failure(kind: YtdlpFailureKind) -> bool:
    return kind in {
        YtdlpFailureKind.HTTP_401,
        YtdlpFailureKind.RATE_LIMIT,
        YtdlpFailureKind.BOT_CHECK,
        YtdlpFailureKind.COOKIE_SESSION,
        YtdlpFailureKind.LOGIN_REQUIRED,
        YtdlpFailureKind.PO_TOKEN_OR_VISITOR_DATA,
        YtdlpFailureKind.HTTP_403,
    }


def _uses_ytdlp_failure_kind_friendly_error(kind: YtdlpFailureKind) -> bool:
    return kind in {
        YtdlpFailureKind.HTTP_401,
        YtdlpFailureKind.RATE_LIMIT,
        YtdlpFailureKind.BOT_CHECK,
        YtdlpFailureKind.COOKIE_SESSION,
        YtdlpFailureKind.LOGIN_REQUIRED,
        YtdlpFailureKind.PO_TOKEN_OR_VISITOR_DATA,
        YtdlpFailureKind.HTTP_403,
        YtdlpFailureKind.FORMAT_UNAVAILABLE,
        YtdlpFailureKind.NETWORK_TIMEOUT,
        YtdlpFailureKind.NETWORK,
        YtdlpFailureKind.OUTPUT_PATH,
    }


def _resolve_systemic_ytdlp_failure(
    exc: YtdlpExecutionError,
    failure_kind: YtdlpFailureKind,
    options: DownloadOptions,
    command: list[str],
    log,
    cancel_controller: DownloadController | None,
    attempt_state: _YtdlpAttemptState,
    attempt_info: _PreparedCookieAttempt | None,
) -> bool:
    failed_snapshot = attempt_info.canonical_snapshot if attempt_info else None
    cookie_path = attempt_info.canonical_path if attempt_info else ""
    current_snapshot = _current_cookie_snapshot(cookie_path)
    if _cookie_retry_allowed(options, attempt_state, failed_snapshot, cookie_path) and _cookie_snapshot_changed(
        failed_snapshot,
        current_snapshot,
    ):
        attempt_state.verified_retry_used = True
        log("[INFO] Cookie source changed after failed yt-dlp attempt. Retrying current part once.")
        return True

    callback = getattr(cancel_controller, "systemic_block_callback", None) if cancel_controller is not None else None
    if callback is None:
        log(
            "[ERROR] Systemic YouTube/session failure requires a user decision, "
            "but no pause callback is available. Stopping batch."
        )
        if cancel_controller is not None:
            cancel_controller.request_cancel()
        raise DownloadCancelled("download cancelled/interrupted")

    note = ""
    while True:
        _raise_if_cancelled(cancel_controller)
        retry_allowed = _cookie_retry_allowed(options, attempt_state, failed_snapshot, cookie_path)
        current_snapshot = _current_cookie_snapshot(cookie_path)
        cookie_changed = _cookie_snapshot_changed(failed_snapshot, current_snapshot)
        if retry_allowed and cookie_changed:
            attempt_state.verified_retry_used = True
            log("[INFO] Cookie source changed. Retrying current part once with a fresh isolated copy.")
            return True

        context = _systemic_block_context(
            exc,
            failure_kind,
            options,
            command,
            retry_allowed=retry_allowed,
            cookie_changed=cookie_changed,
            refreshed_retry_used=attempt_state.verified_retry_used,
            note=note,
        )
        decision = cancel_controller.wait_for_systemic_decision(context)
        if decision == BatchDecision.STOP_BATCH:
            cancel_controller.request_cancel()
            raise DownloadCancelled("download cancelled/interrupted")
        if decision == BatchDecision.SKIP_CURRENT:
            raise SkipCurrentVideo("skipped by user after systemic yt-dlp failure")
        if decision != BatchDecision.RETRY_CURRENT:
            note = "Unknown decision; choose Skip or Stop."
            continue
        if not retry_allowed:
            note = "A verified cookie retry was already used for this part."
            continue

        current_snapshot = _current_cookie_snapshot(cookie_path)
        if _cookie_snapshot_changed(failed_snapshot, current_snapshot):
            attempt_state.verified_retry_used = True
            log("[INFO] Cookie source changed. Retrying current part once with a fresh isolated copy.")
            return True

        note = "No changed cookie export was detected. Export/replace cookies before retrying."
        log("[WARNING] Retry requested, but the canonical cookie source has not changed since the failed attempt.")


def _cookie_retry_allowed(
    options: DownloadOptions,
    attempt_state: _YtdlpAttemptState,
    failed_snapshot: _CookieFileSnapshot | None,
    cookie_path: str,
) -> bool:
    return bool(options.cookies_enabled and cookie_path and failed_snapshot and not attempt_state.verified_retry_used)


def _current_cookie_snapshot(cookie_path: str) -> _CookieFileSnapshot | None:
    if not cookie_path:
        return None
    return _cookie_file_snapshot(Path(cookie_path))


_SYSTEMIC_BLOCK_LOCK = threading.Lock()
_SYSTEMIC_BLOCK_COUNTER = 0


def _next_systemic_block_id() -> str:
    global _SYSTEMIC_BLOCK_COUNTER
    with _SYSTEMIC_BLOCK_LOCK:
        _SYSTEMIC_BLOCK_COUNTER += 1
        return f"systemic-{int(time.time() * 1000)}-{_SYSTEMIC_BLOCK_COUNTER}"


def _systemic_block_context(
    exc: YtdlpExecutionError,
    failure_kind: YtdlpFailureKind,
    options: DownloadOptions,
    command: list[str],
    retry_allowed: bool,
    cookie_changed: bool,
    refreshed_retry_used: bool,
    note: str = "",
) -> SystemicBlockContext:
    progress_context = _current_progress_context()
    video = progress_context.video if progress_context else None
    title = getattr(video, "sanitized_filename_base", "") or getattr(video, "title", "") or ""
    video_id = getattr(video, "video_id", "") or _command_video_id(command)
    part = _part_from_progress_phase(progress_context.phase if progress_context else "")
    cookie_path = _effective_cookie_path_or_empty(options)
    friendly = friendly_ytdlp_failure_kind_error(
        failure_kind.value,
        refreshed_rejected=refreshed_retry_used and failure_kind in {
            YtdlpFailureKind.HTTP_401,
            YtdlpFailureKind.BOT_CHECK,
            YtdlpFailureKind.COOKIE_SESSION,
            YtdlpFailureKind.LOGIN_REQUIRED,
            YtdlpFailureKind.PO_TOKEN_OR_VISITOR_DATA,
            YtdlpFailureKind.HTTP_403,
        },
    )
    reason = f"{friendly.title}: {friendly.reason}"
    if note:
        reason = f"{reason}\n{note}"
    return SystemicBlockContext(
        block_id=_next_systemic_block_id(),
        failure_kind=failure_kind,
        retry_allowed=retry_allowed,
        reason=reason,
        video_id=video_id,
        title=title,
        part=part,
        cookie_source=_cookie_source_text(options),
        cookie_path=cookie_path,
        cookie_changed=cookie_changed,
        refreshed_retry_used=refreshed_retry_used,
        output_lines=tuple(_technical_lines_for_ytdlp(exc)[:8]),
        stage=exc.stage,
        exit_code=exc.exit_code,
    )


def _effective_cookie_path_or_empty(options: DownloadOptions) -> str:
    try:
        return effective_cookies_path(options)
    except DownloadError:
        return ""


def _cookie_source_text(options: DownloadOptions) -> str:
    if not options.cookies_enabled:
        return "disabled"
    if options.cookie_source == COOKIE_SOURCE_BRIDGE:
        return "Local Cookie Bridge"
    return "File cookies.txt"


def _part_from_progress_phase(phase: str) -> str:
    lower = (phase or "").strip().lower()
    if lower in {"video", "validating mp4"}:
        return PART_VIDEO
    if lower in {"mp3", "audio"}:
        return PART_AUDIO
    if lower in {"thumbnail", "thumb"}:
        return PART_THUMB
    return YTDLP_PART_UNKNOWN


def _normalize_ytdlp_part(part: str) -> str:
    normalized = (part or "").strip().lower()
    if normalized in {PART_VIDEO, PART_AUDIO, PART_THUMB}:
        return normalized
    return YTDLP_PART_UNKNOWN


def _normalize_ytdlp_stage(stage: str) -> str:
    normalized = (stage or "").strip().lower()
    if normalized in {YTDLP_STAGE_EXTRACT, YTDLP_STAGE_DOWNLOAD, YTDLP_STAGE_POSTPROCESS}:
        return normalized
    return YTDLP_STAGE_UNKNOWN


def _current_ytdlp_part() -> str:
    progress_context = _current_progress_context()
    if progress_context is None:
        return YTDLP_PART_UNKNOWN
    return _normalize_ytdlp_part(_part_from_progress_phase(progress_context.phase))


def _attach_ytdlp_attempt_context(exc: YtdlpExecutionError, part: str) -> None:
    if exc.part == YTDLP_PART_UNKNOWN:
        exc.part = _normalize_ytdlp_part(part)
    exc.stage = _normalize_ytdlp_stage(exc.stage)


def _ytdlp_start_log_line(command: list[str], options: DownloadOptions, attempt: int, part: str) -> str:
    _ = options
    cookies = "enabled" if _command_uses_cookies(command) else "disabled"
    ipv4 = "forced" if "--force-ipv4" in command else "default"
    fragments = _command_option_value(command, "-N") or "default"
    return (
        "[YT-DLP START] "
        f"part={_normalize_ytdlp_part(part)} "
        f"stage={YTDLP_STAGE_EXTRACT} "
        f"attempt={max(1, int(attempt))} "
        f"cookies={cookies} "
        f"ipv4={ipv4} "
        f"fragments={fragments}"
    )


def _command_uses_cookies(command: list[str]) -> bool:
    return any(
        value == YTDLP_COOKIES_OPTION or str(value).startswith(f"{YTDLP_COOKIES_OPTION}=")
        for value in command
    )

def _command_option_value(command: list[str], option: str) -> str:
    for index, value in enumerate(command):
        if value == option and index + 1 < len(command):
            return str(command[index + 1])
        prefix = f"{option}="
        if str(value).startswith(prefix):
            return str(value)[len(prefix) :]
    return ""


def _log_ytdlp_attempt_failure(
    log,
    exc: YtdlpExecutionError,
    failure_kind: YtdlpFailureKind,
    attempt: int,
) -> None:
    log(
        "[YT-DLP FAILED] "
        f"part={exc.part} stage={exc.stage} exit_code={exc.exit_code} attempt={max(1, int(attempt or 1))}"
    )
    fatal_lines = list(exc.fatal_lines) or _extract_ytdlp_fatal_lines(exc.output_lines)
    for line in fatal_lines[:YTDLP_FATAL_LINE_LIMIT]:
        log(f"[YT-DLP FATAL] {line}")
    if not fatal_lines:
        log("[YT-DLP FATAL] <no fatal yt-dlp output captured>")
    log(f"[YT-DLP CLASS] {failure_kind.value}")


def _ytdlp_stage_after_line(current_stage: str, line: str) -> str:
    current_stage = _normalize_ytdlp_stage(current_stage)
    lower = (line or "").strip().lower()
    if not lower:
        return current_stage
    if _is_ytdlp_postprocess_line(lower):
        return YTDLP_STAGE_POSTPROCESS
    if current_stage != YTDLP_STAGE_POSTPROCESS and _is_ytdlp_download_line(lower):
        return YTDLP_STAGE_DOWNLOAD
    return current_stage if current_stage != YTDLP_STAGE_UNKNOWN else YTDLP_STAGE_EXTRACT


def _is_ytdlp_download_line(lower_line: str) -> bool:
    return bool(
        lower_line.startswith("[download]")
        or lower_line.startswith("[hlsnative]")
        or lower_line.startswith("[dashsegments]")
        or "unable to download video data" in lower_line
        or "failed to download video data" in lower_line
        or re.search(r"^\[info\].*downloading \d+ format\(s\)", lower_line)
    )


def _is_ytdlp_postprocess_line(lower_line: str) -> bool:
    return (
        lower_line.startswith("[merger]")
        or lower_line.startswith("[extractaudio]")
        or lower_line.startswith("[fixupm3u8]")
        or lower_line.startswith("[videoconvertor]")
        or lower_line.startswith("[metadata]")
        or "post-processing" in lower_line
    )


def _run_ytdlp(command: list[str], cancel_controller: DownloadController | None = None) -> str:
    creationflags = _subprocess_creationflags()
    process = None
    output_tail: list[str] = []
    meaningful_lines: list[str] = []
    stream_interrupted = False
    stage = YTDLP_STAGE_EXTRACT
    return_code = 0
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        if cancel_controller is not None:
            cancel_controller.set_current_process(process)
        if _cancel_requested(cancel_controller):
            _terminate_process_tree(process)
            raise DownloadCancelled("download cancelled/interrupted")
        if process.stdout is not None:
            for line in process.stdout:
                if _cancel_requested(cancel_controller):
                    _terminate_process_tree(process)
                    raise DownloadCancelled("download cancelled/interrupted")
                line = line.rstrip("\r\n")
                sanitized = _sanitize_ytdlp_output_line(line)
                if sanitized:
                    _append_limited(output_tail, sanitized, YTDLP_OUTPUT_TAIL_LIMIT)
                if _is_meaningful_ytdlp_line(sanitized):
                    _append_limited(meaningful_lines, sanitized, 50)
                stage = _ytdlp_stage_after_line(stage, sanitized)
                _emit_ytdlp_progress_from_line(sanitized)
                if _cancel_requested(cancel_controller):
                    _terminate_process_tree(process)
                    raise DownloadCancelled("download cancelled/interrupted")
        return_code = _wait_for_process_exit(process, 1.0)
        if return_code is None:
            if _cancel_requested(cancel_controller):
                _terminate_process_tree(process)
                raise DownloadCancelled("download cancelled/interrupted")
            _append_limited(
                output_tail,
                "yt-dlp process did not exit promptly after output ended",
                YTDLP_OUTPUT_TAIL_LIMIT,
            )
            stream_interrupted = True
            _terminate_process_tree(process)
            return_code = process.poll()
            if return_code is None:
                return_code = -1
    except FileNotFoundError:
        raise DownloadError("yt-dlp.exe missing")
    except KeyboardInterrupt:
        _terminate_process_tree(process)
        raise DownloadCancelled("download cancelled/interrupted")
    finally:
        if process is not None and cancel_controller is not None:
            cancel_controller.clear_current_process(process)

    output = "\n".join(output_tail)
    if _cancel_requested(cancel_controller):
        _terminate_process_tree(process)
        raise DownloadCancelled("download cancelled/interrupted")
    if return_code == 0:
        return output

    if return_code != 0:
        diagnostic_lines = meaningful_lines or _last_meaningful_output_lines("", output, limit=50)
        fatal_lines = _extract_ytdlp_fatal_lines(diagnostic_lines or output_tail)
        fatal_text = "\n".join(fatal_lines)
        failure_kind = _classify_ytdlp_failure_text(fatal_text or output)
        http_status = _http_status_from_text(fatal_text)
        stream_interrupted = stream_interrupted or (
            failure_kind in {YtdlpFailureKind.NETWORK, YtdlpFailureKind.NETWORK_TIMEOUT}
            and _contains_stream_interrupted_output(fatal_text or output)
        )
        raise YtdlpExecutionError(
            return_code,
            _ytdlp_failure_message(failure_kind, fatal_text or output),
            output_tail,
            failure_kind == YtdlpFailureKind.BOT_CHECK,
            failure_kind == YtdlpFailureKind.HTTP_403,
            failure_kind == YtdlpFailureKind.TOOL_CONFIGURATION,
            output,
            stream_interrupted,
            failure_kind=failure_kind,
            fatal_lines=fatal_lines,
            http_status=http_status,
            stage=stage,
            part=_current_ytdlp_part(),
            command=command,
        )

    return output


def _append_limited(items: list[str], value: str, limit: int) -> None:
    items.append(value)
    if len(items) > limit:
        del items[: len(items) - limit]


def _sanitize_ytdlp_output_lines(lines: list[str] | tuple[str, ...]) -> list[str]:
    sanitized: list[str] = []
    for line in lines or []:
        safe_line = _sanitize_ytdlp_output_line(line)
        if safe_line:
            sanitized.append(safe_line)
    return sanitized


def _bounded_sanitized_ytdlp_output(text: str, limit: int = FFMPEG_COMBINED_OUTPUT_LIMIT) -> str:
    lines = _sanitize_ytdlp_output_lines(str(text or "").splitlines())
    return _join_bounded_tail(lines[-YTDLP_OUTPUT_TAIL_LIMIT:], max(1, int(limit)))


def _extract_ytdlp_fatal_lines(output_lines: list[str] | tuple[str, ...]) -> list[str]:
    lines = [
        line
        for line in _sanitize_ytdlp_output_lines(output_lines)[-YTDLP_OUTPUT_TAIL_LIMIT:]
        if _is_meaningful_ytdlp_line(line)
    ]
    if not lines:
        return []

    marker_indexes = [index for index, line in enumerate(lines) if _is_ytdlp_fatal_marker_line(line)]
    if marker_indexes:
        start = marker_indexes[-1]
        selected = lines[start : start + YTDLP_FATAL_LINE_LIMIT]
        return selected[-YTDLP_FATAL_LINE_LIMIT:]

    return lines[-YTDLP_FATAL_LINE_LIMIT:]


def _is_ytdlp_fatal_marker_line(line: str) -> bool:
    text = (line or "").strip()
    lower = text.lower()
    return bool(
        re.search(r"(^|\s)error:", text, flags=re.IGNORECASE)
        or lower.startswith("traceback")
        or "traceback (most recent call last)" in lower
    )


def _emit_ytdlp_progress_from_line(line: str) -> None:
    context = _current_progress_context()
    if context is None:
        return
    parsed = parse_ytdlp_progress_line(line)
    if not parsed:
        return

    now = time.monotonic()
    message = parsed.get("message") or ""
    percent = parsed.get("percent")
    force = percent == "100%" or message in {
        "Already downloaded",
        "Extracting audio",
        "Merging formats",
        "Post-processing",
        "Preparing download",
    }
    if not force and now - context.last_emit < 0.3:
        return
    context.last_emit = now
    _emit_progress(
        context.callback,
        context.phase,
        context.video,
        context.video_index,
        context.video_total,
        message=message,
        percent=percent,
        speed=parsed.get("speed"),
        eta=parsed.get("eta"),
        fragment=parsed.get("fragment"),
    )


def _coerce_ytdlp_failure_kind(kind: YtdlpFailureKind | str | None) -> YtdlpFailureKind | None:
    if kind is None:
        return None
    if isinstance(kind, YtdlpFailureKind):
        return kind
    normalized = str(kind or "").strip().lower()
    if normalized == "rate_limit":
        normalized = YtdlpFailureKind.RATE_LIMIT.value
    for candidate in YtdlpFailureKind:
        if candidate.value == normalized or candidate.name.lower() == normalized:
            return candidate
    return YtdlpFailureKind.UNKNOWN


def _classify_ytdlp_failure_text(text: str) -> YtdlpFailureKind:
    if _contains_http_401_error(text):
        return YtdlpFailureKind.HTTP_401
    if _contains_http_403_error(text):
        return YtdlpFailureKind.HTTP_403
    if _contains_rate_limit_error(text):
        return YtdlpFailureKind.RATE_LIMIT
    if _contains_bot_check_error(text):
        return YtdlpFailureKind.BOT_CHECK
    if _contains_po_token_or_visitor_data_error(text):
        return YtdlpFailureKind.PO_TOKEN_OR_VISITOR_DATA
    if _contains_login_required_error(text):
        return YtdlpFailureKind.LOGIN_REQUIRED
    if _contains_format_unavailable_error(text):
        return YtdlpFailureKind.FORMAT_UNAVAILABLE
    if _contains_permanent_video_error(text):
        return YtdlpFailureKind.PERMANENT_VIDEO
    if is_cookie_session_error(text):
        return YtdlpFailureKind.COOKIE_SESSION
    if _contains_missing_js_runtime_error(text):
        return YtdlpFailureKind.TOOL_CONFIGURATION
    if _contains_network_timeout_error(text):
        return YtdlpFailureKind.NETWORK_TIMEOUT
    if _contains_network_error(text) or _contains_stream_interrupted_output(text):
        return YtdlpFailureKind.NETWORK
    if _contains_output_path_error(text):
        return YtdlpFailureKind.OUTPUT_PATH
    return YtdlpFailureKind.UNKNOWN


def _ytdlp_failure_message(kind: YtdlpFailureKind, text: str) -> str:
    if kind == YtdlpFailureKind.BOT_CHECK:
        return "YouTube requires sign-in/bot verification; enable Cookies and select a valid cookies.txt"
    if kind == YtdlpFailureKind.TOOL_CONFIGURATION:
        return "yt-dlp needs a supported JavaScript runtime for this YouTube extraction"
    if kind == YtdlpFailureKind.FORMAT_UNAVAILABLE:
        return "no Premiere-safe MP4 H.264/AAC format available"
    if kind == YtdlpFailureKind.HTTP_401:
        return "yt-dlp failed with HTTP 401"
    if kind == YtdlpFailureKind.HTTP_403:
        return "yt-dlp failed with HTTP 403"
    if kind == YtdlpFailureKind.RATE_LIMIT:
        return "yt-dlp failed with HTTP 429 rate limiting"
    if kind == YtdlpFailureKind.LOGIN_REQUIRED:
        return "YouTube sign-in is required"
    if kind == YtdlpFailureKind.PO_TOKEN_OR_VISITOR_DATA:
        return "yt-dlp requires PO token or Visitor Data"
    if kind == YtdlpFailureKind.PERMANENT_VIDEO:
        return _classify_ytdlp_error(text)
    if kind == YtdlpFailureKind.NETWORK_TIMEOUT:
        return "yt-dlp network timeout"
    if kind == YtdlpFailureKind.NETWORK:
        return "yt-dlp network error"
    if kind == YtdlpFailureKind.OUTPUT_PATH:
        return "yt-dlp output path error"
    return "nonzero yt-dlp exit code"


def _http_status_from_text(text: str) -> int | None:
    match = re.search(r"(?i)\b(?:http\s+error\s+)?(401|403|429)\b", text or "")
    if match is None:
        return None
    return int(match.group(1))


def _classify_ytdlp_error_from_flags(
    output: str,
    bot_check: bool = False,
    missing_js_runtime: bool = False,
    premiere_safe_format_error: bool = False,
) -> str:
    if bot_check:
        return "YouTube requires sign-in/bot verification; enable Cookies and select a valid cookies.txt"
    if missing_js_runtime:
        return "yt-dlp needs a supported JavaScript runtime for this YouTube extraction"
    if premiere_safe_format_error:
        return "no Premiere-safe MP4 H.264/AAC format available"
    return _classify_ytdlp_error(output)


def _classify_ytdlp_error(output: str) -> str:
    lower = (output or "").lower()
    if _contains_bot_check_error(output):
        return "YouTube requires sign-in/bot verification; enable Cookies and select a valid cookies.txt"
    if "no supported javascript runtime" in lower:
        return "yt-dlp needs a supported JavaScript runtime for this YouTube extraction"
    if "private video" in lower:
        return "private video"
    if _contains_premiere_safe_format_error(output):
        return "no Premiere-safe MP4 H.264/AAC format available"
    if "video unavailable" in lower or "unavailable" in lower:
        return "video unavailable"
    if "permission denied" in lower or "access is denied" in lower or "winerror 5" in lower:
        return "file permission denied"
    if "interrupted" in lower or "cancelled" in lower or "canceled" in lower:
        return "download cancelled/interrupted"
    if "timed out" in lower or "network" in lower or "http error" in lower:
        return "network error"
    return "nonzero yt-dlp exit code"


def _contains_bot_check_error(stderr: str) -> bool:
    lower = (stderr or "").lower()
    return (
        "sign in to confirm you're not a bot" in lower
        or "sign in to confirm you are not a bot" in lower
        or "not a bot" in lower
        or "confirm you're not a bot" in lower
        or "confirm you are not a bot" in lower
        or "this helps protect our community" in lower
        or "verify that you're human" in lower
        or "verify you're human" in lower
        or "verify that you are human" in lower
        or "verify you are human" in lower
        or "unusual traffic" in lower
        or "automated requests" in lower
        or "automated request" in lower
        or "detected automated traffic" in lower
    )


def is_cookie_session_error(text: str) -> bool:
    lower = (text or "").lower()
    if not lower:
        return False
    session_markers = (
        "cookies are expired",
        "cookies expired",
        "cookies are invalid",
        "cookie invalid",
        "cookies invalid",
        "cookie is invalid",
        "cookies are no longer valid",
        "cookie is no longer valid",
        "no longer valid cookie",
        "cookie/session rejected",
        "cookie session rejected",
        "session cookie rejected",
        "youtube rejected the supplied session",
        "youtube rejected the current session",
        "supplied browser session has expired",
        "browser session has expired",
        "login session expired",
        "session has expired",
        "current account is not authenticated",
        "account is not authenticated",
        "account authentication failed",
        "not authenticated",
        "authentication required",
        "authentication is required",
        "authentication is required to view this video",
        "login required",
        "please log in",
        "sign in to continue",
        "please sign in to continue",
        "please sign in to view this video",
        "you must be signed in to view this video",
        "sign in to confirm your age",
        "sign in to confirm your identity",
        "sign in to confirm your account",
        "failed to load cookies",
        "could not load cookies",
        "unable to load cookies",
        "failed to parse cookies",
        "could not parse cookies",
        "unable to parse cookies",
        "cookie parsing failed",
    )
    if any(marker in lower for marker in session_markers):
        return True

    if "age-restricted" in lower or "age restricted" in lower or "age-restricted video" in lower:
        return any(
            marker in lower
            for marker in (
                "authenticate",
                "authentication",
                "sign in",
                "signed in",
                "login",
                "cookie",
                "cookies",
            )
        )

    has_cookie_marker = "cookie" in lower or "cookies" in lower or "browser session" in lower
    explicit_problem_markers = (
        "expired",
        "invalid",
        "not valid",
        "no longer valid",
        "rejected",
        "failed",
        "unable to",
        "could not",
        "authentication",
        "authenticate",
    )
    if has_cookie_marker and any(marker in lower for marker in explicit_problem_markers):
        return True

    has_http_marker = (
        "http error 403" in lower
        or "403: forbidden" in lower
        or "http error 429" in lower
        or "429: too many requests" in lower
    )
    http_context_markers = (
        "login required",
        "please log in",
        "authentication required",
        "cookies are expired",
        "cookies are invalid",
        "cookies are no longer valid",
        "browser session has expired",
        "session rejected",
    )
    return has_http_marker and any(marker in lower for marker in http_context_markers)


def cookie_session_error_message(options: DownloadOptions) -> str:
    source = (
        options.cookie_source
        if options.cookie_source in {COOKIE_SOURCE_FILE, COOKIE_SOURCE_BRIDGE}
        else COOKIE_SOURCE_FILE
    )
    if source == COOKIE_SOURCE_BRIDGE:
        return BRIDGE_COOKIE_SESSION_ERROR_MESSAGE
    return FILE_COOKIE_SESSION_ERROR_MESSAGE


def _contains_http_401_error(text: str) -> bool:
    lower = (text or "").lower()
    return bool(
        "http error 401" in lower
        or "401: unauthorized" in lower
        or re.search(r"\b401\b[^\n\r]*(unauthorized|authorization required)", lower)
    )


def _contains_http_403_error(text: str) -> bool:
    lower = (text or "").lower()
    return bool(
        "http error 403" in lower
        or "403: forbidden" in lower
        or re.search(r"\b403\b[^\n\r]*\bforbidden\b", lower)
        or re.search(r"\bforbidden\b[^\n\r]*\b403\b", lower)
    )


def _contains_rate_limit_error(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "http error 429" in lower
        or "429: too many requests" in lower
        or "too many requests" in lower
        or "rate limit" in lower
        or "rate-limit" in lower
        or "temporarily blocked" in lower
    )


def _contains_login_required_error(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "login_required" in lower
        or "login required" in lower
        or "sign in to continue" in lower
        or "please sign in to continue" in lower
        or "authentication required" in lower
        or "authentication is required" in lower
    )


def _contains_po_token_or_visitor_data_error(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "po token" in lower
        or "potoken" in lower
        or "visitor data" in lower
        or "visitor_data" in lower
        or "missing required visitor data" in lower
    )


def _contains_format_unavailable_error(text: str) -> bool:
    return _contains_premiere_safe_format_error(text)


def _contains_permanent_video_error(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "private video" in lower
        or "video unavailable" in lower
        or "this video is unavailable" in lower
        or "deleted video" in lower
        or "this video has been deleted" in lower
        or "this video has been removed" in lower
        or "removed by the uploader" in lower
        or "has been removed" in lower
        or "members-only" in lower
        or "members only" in lower
        or "join this channel" in lower
        or "not available in your region" in lower
        or "copyright claim" in lower
        or "not available in your country" in lower
        or "uploader account has been terminated" in lower
        or "account associated with this video has been terminated" in lower
        or "livestream recording is unavailable" in lower
        or "live stream recording is unavailable" in lower
        or "recording of this live stream is unavailable" in lower
        or "no longer available" in lower
        or "unsupported media" in lower
        or "no longer supported" in lower
        or _contains_premiere_safe_format_error(text)
    )


def _contains_network_timeout_error(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "timed out" in lower
        or "timeout" in lower
        or "read timed out" in lower
        or "connection timed out" in lower
        or "operation timed out" in lower
    )


def _contains_network_error(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "network error" in lower
        or "temporary failure" in lower
        or "connection reset" in lower
        or "connection aborted" in lower
        or "unable to download webpage" in lower
    )


def _contains_missing_js_runtime_error(stderr: str) -> bool:
    lower = (stderr or "").lower()
    return "no supported javascript runtime" in lower or "javascript runtime" in lower or "ejs" in lower


def _contains_premiere_safe_format_error(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "premiere_safe_mp4_validation_failed" in lower
        or "premiere-safe validation failed" in lower
        or "requested format is not available" in lower
        or "requested format not available" in lower
        or "no video formats found" in lower
        or "no suitable formats" in lower
    )


def _contains_output_path_error(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "output path" in lower
        or "filename too long" in lower
        or "file name too long" in lower
        or "path too long" in lower
        or "invalid argument" in lower and "output" in lower
        or "permission denied" in lower
        or "access is denied" in lower
        or "winerror 5" in lower
        or "no space left on device" in lower
    )


def _contains_stream_interrupted_output(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "bytes read" in lower
        or "more expected" in lower
        or "read timed out" in lower
        or "fragment" in lower
        or "got error" in lower
        or "giving up after" in lower
        or "connection reset" in lower
    )


def _remove_command_flags(command: list[str], flags: set[str]) -> list[str]:
    return [value for value in command if value not in flags]


def _remove_command_options(command: list[str], options: set[str]) -> list[str]:
    prepared: list[str] = []
    index = 0
    while index < len(command):
        value = str(command[index])
        if value in options:
            index += 2
            continue
        if any(value.startswith(f"{option}=") for option in options):
            index += 1
            continue
        prepared.append(command[index])
        index += 1
    return prepared


def _strip_media_downloader_options(command: list[str]) -> list[str]:
    return _remove_command_options(
        command,
        {
            "-N",
            "--concurrent-fragments",
            "--downloader",
            "--external-downloader",
            "--downloader-args",
            "--external-downloader-args",
        },
    )


def _strip_url_arguments(command: list[str]) -> list[str]:
    return [
        value
        for value in command
        if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", str(value or ""))
    ]
def _replace_option(command: list[str], option: str, value: str) -> list[str]:
    replaced = []
    index = 0
    while index < len(command):
        if command[index] == option:
            index += 2
            continue
        replaced.append(command[index])
        index += 1
    insert_at = _option_insert_index(replaced)
    return [*replaced[:insert_at], option, value, *replaced[insert_at:]]


def _ensure_flag(command: list[str], flag: str) -> list[str]:
    if flag in command:
        return list(command)
    insert_at = _option_insert_index(command)
    return [*command[:insert_at], flag, *command[insert_at:]]


def _option_insert_index(command: list[str]) -> int:
    if command and re.match(r"https?://", command[-1], flags=re.IGNORECASE):
        return len(command) - 1
    return len(command)


def _validate_output_paths(paths, parts: tuple[str, ...]) -> None:
    final_paths = []
    if PART_VIDEO in parts:
        final_paths.append(paths.video_path)
    if PART_AUDIO in parts:
        final_paths.append(paths.audio_path)
    if PART_THUMB in parts:
        final_paths.append(paths.thumb_path)

    for final_path in final_paths:
        if len(str(final_path.resolve(strict=False))) > MAX_FINAL_PATH_LENGTH:
            raise DownloadError(OUTPUT_PATH_TOO_LONG_MESSAGE)


def _remember_run_part(parts: list[str] | None, part: str | None) -> None:
    if parts is None:
        return
    if part and part not in parts:
        parts.append(part)


def _mark_part_error(options: DownloadOptions, video, paths, part: str | None) -> None:
    if part not in (PART_VIDEO, PART_AUDIO, PART_THUMB):
        return
    try:
        update_video_part_state(
            options.channel_id,
            options.channel_name,
            options.base_folder,
            video,
            paths,
            part,
            STATUS_ERROR,
            options.download_mode,
        )
    except OSError:
        pass


def _success_file_list(stem: str, download_mode: str) -> str:
    names = []
    for part in required_parts(download_mode):
        if part == PART_VIDEO:
            names.append(f"{stem}.mp4")
        elif part == PART_AUDIO:
            names.append(f"{stem}.mp3")
        elif part == PART_THUMB:
            names.append(f"{stem}.jpg")
    if len(names) <= 1:
        return names[0] if names else stem
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _safe_temp_stem(video_id: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", video_id or "video")
    return stem[:64].strip(".") or "video"


def _last_meaningful_output_lines(stdout: str, stderr: str, limit: int = 50) -> list[str]:
    combined = []
    for text in (stderr or "", stdout or ""):
        for line in text.splitlines():
            sanitized = _sanitize_ytdlp_output_line(line)
            if _is_meaningful_ytdlp_line(sanitized):
                combined.append(sanitized)
    return combined[-limit:]


def _collect_meaningful_ffmpeg_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = _sanitize_subprocess_output_line(raw_line)
        if not line:
            continue
        line = _bound_subprocess_output_line(line)
        if not _is_meaningful_ffmpeg_line(line):
            continue
        if lines and lines[-1] == line:
            continue
        lines.append(line)
    return lines


def _ffmpeg_output_lines(stdout: str, stderr: str, *, limit: int = FFMPEG_OUTPUT_LINE_LIMIT) -> list[str]:
    stderr_lines = _collect_meaningful_ffmpeg_lines(stderr)
    stdout_lines = _collect_meaningful_ffmpeg_lines(stdout)
    preferred_lines = stderr_lines if stderr_lines else stdout_lines
    return preferred_lines[-max(1, int(limit)) :]


def _bounded_sanitized_subprocess_output(
    stdout: str,
    stderr: str,
    *,
    limit: int = FFMPEG_COMBINED_OUTPUT_LIMIT,
) -> str:
    stderr_lines = _collect_meaningful_ffmpeg_lines(stderr)
    stdout_lines = _collect_meaningful_ffmpeg_lines(stdout)
    preferred_lines = stderr_lines if stderr_lines else stdout_lines
    return _join_bounded_tail(preferred_lines, max(1, int(limit)))


def _sanitize_subprocess_output_line(line: str) -> str:
    try:
        text = str(line or "").strip()
    except Exception:
        return ""
    if not text:
        return ""

    try:
        youtube_api_key_prefix = "AI" "za"
        text = re.sub(
            re.escape(youtube_api_key_prefix) + r"[0-9A-Za-z_-]{20,}",
            "<redacted-api-key>",
            text,
        )
        text = re.sub(
            r"(?i)--cookies(?:[=\s]+(?:\"[^\"]*\"|'[^']*'|\S+))?",
            "<cookies-arg-redacted>",
            text,
        )
        text = re.sub(
            r"(?i)\b(?:set-cookie|cookies?|cookie)\s*[:=]\s*[^\r\n]*",
            "<cookie-redacted>",
            text,
        )
        text = _sanitize_subprocess_paths(text)
    except Exception:
        return "<sanitized-output-unavailable>"
    return text.strip()


def _sanitize_subprocess_paths(text: str) -> str:
    text, protected_urls = _protect_urls_for_path_sanitization(text)
    text = _redact_sensitive_assignments_outside_urls(text)
    quoted_path = r"(?P<quote>['\"])(?P<path>(?:[A-Za-z]:[\\/]|\\\\|/)(?:(?!(?P=quote)).)*)(?P=quote)"

    def replace_quoted(match: re.Match) -> str:
        path_text = match.group("path")
        if not _is_absolute_subprocess_path(path_text):
            return match.group(0)
        quote = match.group("quote")
        return f"{quote}{_path_placeholder(path_text)}{quote}"

    text = re.sub(quoted_path, replace_quoted, text)

    unquoted_patterns = (
        r"(?<![\w/])([A-Za-z]:[\\/][^\r\n\"'<>|?*]*?)(?=(?::\s|[,;)\]\r\n]|$))",
        r"(?<![\w/])(\\\\[^\r\n\"'<>|?*]*?)(?=(?::\s|[,;)\]\r\n]|$))",
        r"(?<![\w:])(/[^\s\r\n\"'<>?][^\r\n\"'<>?]*?)(?=(?::\s|[,;)\]\r\n]|$))",
    )
    for pattern in unquoted_patterns:
        text = re.sub(pattern, lambda match: _path_placeholder(match.group(1)), text)
    return _restore_protected_urls(text, protected_urls)


def _protect_urls_for_path_sanitization(text: str) -> tuple[str, dict[str, str]]:
    if not text:
        return text, {}

    marker = "__S9H_PROTECTED_URL_"
    while marker in text:
        marker = f"_{marker}_"

    protected_urls: dict[str, str] = {}

    def replace_url(match: re.Match) -> str:
        index = len(protected_urls)
        placeholder = f"{marker}{index}__"
        url, suffix = _split_url_trailing_punctuation(match.group("url"))
        protected_urls[placeholder] = _redact_sensitive_url_query_values(url)
        return f"{placeholder}{suffix}"

    protected_text = re.sub(
        r"(?P<url>[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+)",
        replace_url,
        text,
    )
    return protected_text, protected_urls


def _redact_sensitive_url_query_values(url: str) -> str:
    try:
        text = str(url or "")
    except Exception:
        return ""
    lower = text.lower()
    if "googlevideo.com" in lower or "/videoplayback" in lower:
        return "<signed-media-url-redacted>"
    if not text or "?" not in text:
        return text

    if "#" in text:
        before_fragment, fragment = text.split("#", 1)
        fragment = f"#{fragment}"
    else:
        before_fragment = text
        fragment = ""

    prefix, query = before_fragment.split("?", 1)
    query = re.sub(
        r"(?i)(?P<prefix>^|[&;])"
        r"(?P<name>key|api_key|token|access_token|sig|signature|lsig)"
        r"(?P<equals>=)"
        r"(?P<value>[^&;#]*)",
        lambda match: f"{match.group('prefix')}{match.group('name')}{match.group('equals')}***",
        query,
    )
    return f"{prefix}?{query}{fragment}"


def _redact_sensitive_assignments_outside_urls(text: str) -> str:
    try:
        source = str(text or "")
    except Exception:
        return ""
    if not source:
        return ""

    pieces: list[str] = []
    cursor = 0
    search_from = 0
    while True:
        match = _STANDALONE_SECRET_ASSIGNMENT_PATTERN.search(source, search_from)
        if match is None:
            pieces.append(source[cursor:])
            break

        value_start = match.end()
        value_end = _standalone_secret_value_end(source, value_start)
        pieces.append(source[cursor:match.start()])
        pieces.append(source[match.start() : value_start])
        pieces.append("***")
        cursor = value_end
        search_from = max(value_end, value_start)

    return "".join(pieces)


def _standalone_secret_value_end(text: str, start: int) -> int:
    index = start
    bracket_depth = 0
    while index < len(text):
        char = text[index]
        if char in "&#,;)}":
            break
        if char == "]" and bracket_depth <= 0:
            break
        if char.isspace():
            break
        if char == ":" and index + 1 < len(text) and text[index + 1].isspace():
            break
        if char == "[":
            bracket_depth += 1
        elif char == "]" and bracket_depth > 0:
            bracket_depth -= 1
        index += 1
    return index


def _restore_protected_urls(text: str, protected_urls: dict[str, str]) -> str:
    restored = text
    for placeholder, url in protected_urls.items():
        if placeholder in restored:
            restored = restored.replace(placeholder, url, 1)
        if placeholder in restored:
            restored = restored.replace(placeholder, "<url>")
    return restored


def _split_url_trailing_punctuation(url: str) -> tuple[str, str]:
    candidate = str(url or "")
    suffix = ""
    while candidate:
        last = candidate[-1]
        if last in ",;":
            suffix = last + suffix
            candidate = candidate[:-1]
            continue
        if last == ":":
            suffix = last + suffix
            candidate = candidate[:-1]
            continue
        if last == ")" and candidate.count(")") > candidate.count("("):
            suffix = last + suffix
            candidate = candidate[:-1]
            continue
        if last == "]" and candidate.count("]") > candidate.count("["):
            suffix = last + suffix
            candidate = candidate[:-1]
            continue
        break
    return candidate or "<url>", suffix


def _is_absolute_subprocess_path(path_text: str) -> bool:
    return bool(
        re.match(r"^[A-Za-z]:[\\/]", path_text or "")
        or str(path_text or "").startswith("\\\\")
        or str(path_text or "").startswith("/")
    )


def _path_placeholder(path_text: str) -> str:
    stripped = str(path_text or "").strip().rstrip("\\/")
    if not stripped:
        return "<path>"
    is_posix = stripped.startswith("/") and not stripped.startswith("\\\\")
    path_cls = PurePosixPath if is_posix else PureWindowsPath
    basename = path_cls(stripped).name
    if not _safe_path_basename(basename):
        return "<path>"
    separator = "/" if is_posix or ("/" in stripped and "\\" not in stripped) else "\\"
    return f"<path>{separator}{basename[:120]}"


def _safe_path_basename(basename: str) -> bool:
    if not basename:
        return False
    if any(separator in basename for separator in ("\\", "/")):
        return False
    if any(ord(char) < 32 for char in basename):
        return False
    if any(char in basename for char in ("?", "&", "=")):
        return False
    return True


def _bound_subprocess_output_line(line: str, limit: int = FFMPEG_OUTPUT_LINE_CHAR_LIMIT) -> str:
    text = " ".join(str(line or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _join_bounded_tail(lines: list[str], limit: int) -> str:
    selected: list[str] = []
    total = 0
    for line in reversed(lines):
        addition = len(line) + (1 if selected else 0)
        if selected and total + addition > limit:
            break
        if not selected and addition > limit:
            selected.append(line[-limit:])
            break
        selected.append(line)
        total += addition
    return "\n".join(reversed(selected))


def _is_meaningful_ffmpeg_line(line: str) -> bool:
    if not line:
        return False
    lower = line.lower().strip()
    if lower.startswith("ffmpeg version "):
        return False
    if lower.startswith("built with "):
        return False
    if lower.startswith("configuration:"):
        return False
    if lower.startswith("press [q] to stop"):
        return False
    if re.match(r"^(libavutil|libavcodec|libavformat|libavdevice|libavfilter|libswscale|libswresample|libpostproc)\s+", lower):
        return False
    if _is_ffmpeg_progress_line(lower):
        return False
    return True


def _is_ffmpeg_progress_line(lower_line: str) -> bool:
    if lower_line.startswith("frame=") and "time=" in lower_line:
        return True
    if lower_line.startswith("size=") and "time=" in lower_line and "bitrate=" in lower_line:
        return True
    return False


def _sanitize_ytdlp_output_line(line: str) -> str:
    return _sanitize_subprocess_output_line(line)


def _is_meaningful_ytdlp_line(line: str) -> bool:
    if not line:
        return False
    if line.strip().upper() == "ERROR:":
        return False
    lower = line.lower()
    if lower.startswith("[download]") and "%" in lower and "eta" in lower:
        return False
    if lower.startswith("[download]") and "of" in lower and "at" in lower:
        return False
    return True


def _log_friendly_ytdlp_error(log, exc: YtdlpExecutionError, options: DownloadOptions) -> None:
    text = "\n".join(exc.fatal_lines or exc.output_lines) or exc.combined_output
    failure_kind = classify_ytdlp_failure_kind(exc, options)
    if _uses_ytdlp_failure_kind_friendly_error(failure_kind):
        friendly = friendly_ytdlp_failure_kind_error(failure_kind.value)
    else:
        friendly = classify_ytdlp_error(
            text,
            cookies_enabled=options.cookies_enabled,
            bot_check=exc.bot_check,
            http_403=exc.http_403,
            missing_js_runtime=exc.missing_js_runtime,
        )
    log(format_friendly_error(friendly, _technical_lines_for_ytdlp(exc)))
    if options.cookies_enabled and failure_kind == YtdlpFailureKind.COOKIE_SESSION:
        log(cookie_session_error_message(options))


def _log_friendly_ffmpeg_error(log, exc: FFmpegExecutionError) -> None:
    failure_kind = classify_ffmpeg_failure_kind(exc)
    friendly = friendly_ffmpeg_failure_kind_error(failure_kind.value, exc.combined_output)
    log(format_friendly_error(friendly, _ffmpeg_technical_lines_for_log(exc)))


def _log_missing_js_runtime_warning(log, output_lines: list[str]) -> None:
    log(format_friendly_error(missing_js_runtime_warning(), output_lines[:1]))


def _log_friendly_general_error(log, message: str, technical_lines: list[str] | None = None) -> None:
    log(format_friendly_error(classify_general_error(message), technical_lines or [message]))


def _technical_lines_for_ytdlp(exc: YtdlpExecutionError) -> list[str]:
    lines = [line for line in (exc.fatal_lines or exc.output_lines) if line.strip()]
    kind = exc.failure_kind.value if exc.failure_kind is not None else YtdlpFailureKind.UNKNOWN.value
    if not lines:
        return [f"yt-dlp exit code {exc.exit_code}: {exc}"]
    return [
        *lines[-YTDLP_FATAL_LINE_LIMIT:],
        f"yt-dlp exit code {exc.exit_code}",
        f"yt-dlp part {exc.part}",
        f"yt-dlp stage {exc.stage}",
        f"yt-dlp failure kind {kind}",
    ]


def _technical_lines_for_ffmpeg(exc: FFmpegExecutionError) -> list[str]:
    lines = [line for line in exc.output_lines if line.strip()]
    if not lines and str(exc).strip():
        lines = [_sanitize_subprocess_output_line(str(exc))]
    return [*lines[-FFMPEG_OUTPUT_LINE_LIMIT:], f"ffmpeg exit code {exc.exit_code}", f"operation: {exc.operation}"]


def _ffmpeg_technical_lines_for_log(exc: FFmpegExecutionError) -> list[str]:
    lines = _technical_lines_for_ffmpeg(exc)
    evidence = [
        line
        for line in lines
        if not line.startswith("ffmpeg exit code ") and not line.startswith("operation: ")
    ]
    summary = _bound_subprocess_output_line(
        " | ".join([f"ffmpeg exit code {exc.exit_code}", f"operation: {exc.operation}", *evidence[:3]])
    )
    return [summary, *lines]


@contextmanager
def _media_staging_directory(channel_dir: Path, video_id: str, log=None):
    channel_dir.mkdir(parents=True, exist_ok=True)
    _hide_existing_staging_directories(channel_dir, log)
    safe_id = _safe_temp_stem(video_id)[:32]
    staging_path = Path(tempfile.mkdtemp(prefix=f".s9h-stage-{safe_id}-", dir=str(channel_dir)))
    _mark_staging_directory_hidden(staging_path, log)
    try:
        yield staging_path
    finally:
        _cleanup_media_staging_directory(staging_path, channel_dir, log)


def _hide_existing_staging_directories(channel_dir: Path, log=None) -> None:
    """Hide staging directories left visible by older application versions."""
    try:
        candidates = list(channel_dir.glob(".s9h-stage-*"))
    except OSError:
        return

    for candidate in candidates:
        try:
            if candidate.is_symlink() or not candidate.is_dir():
                continue
        except OSError:
            continue
        _mark_staging_directory_hidden(candidate, log)


def _mark_staging_directory_hidden(staging_path: Path, log=None) -> None:
    """Hide the transient staging directory in Windows Explorer.

    A leading dot is not a hidden-file marker on Windows, so the native
    FILE_ATTRIBUTE_HIDDEN flag must be applied explicitly. The directory
    remains accessible to yt-dlp, FFmpeg and cleanup code.
    """
    if os.name != "nt":
        return

    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_attributes = kernel32.GetFileAttributesW
        get_attributes.argtypes = [ctypes.c_wchar_p]
        get_attributes.restype = ctypes.c_uint32
        set_attributes = kernel32.SetFileAttributesW
        set_attributes.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        set_attributes.restype = ctypes.c_int

        file_attribute_hidden = 0x00000002
        invalid_file_attributes = 0xFFFFFFFF
        path_text = str(staging_path)
        attributes = get_attributes(path_text)
        if attributes == invalid_file_attributes:
            raise OSError(ctypes.get_last_error(), "GetFileAttributesW failed")
        if attributes & file_attribute_hidden:
            return
        if not set_attributes(path_text, attributes | file_attribute_hidden):
            raise OSError(ctypes.get_last_error(), "SetFileAttributesW failed")
    except (AttributeError, OSError, ValueError):
        if log:
            log("[WARNING] Could not hide temporary staging directory.")


def _cleanup_media_staging_directory(staging_path: Path, channel_dir: Path, log=None) -> None:
    try:
        if not _is_path_relative_to(staging_path.resolve(strict=False), channel_dir.resolve(strict=False)):
            if log:
                log("[WARNING] Could not clean staging directory safely.")
            return
        if not staging_path.exists():
            return
        if staging_path.is_symlink() or not staging_path.is_dir():
            if log:
                log("[WARNING] Could not clean staging directory safely.")
            return
        shutil.rmtree(staging_path)
    except OSError:
        if log:
            log("[WARNING] Could not remove staging directory; a file may still be locked.")


def _is_path_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _move_single_file(
    temp_dir: Path,
    pattern: str,
    final_path: Path,
    log=None,
    replace_existing: bool = False,
    cancel_controller: DownloadController | None = None,
) -> None:
    if not replace_existing and _final_file_ready(final_path):
        return
    staged_path = _select_staged_file(temp_dir, pattern, final_path.suffix)
    _atomic_promote_with_retry(
        staged_path,
        final_path,
        log,
        replace_existing=replace_existing,
        cancel_controller=cancel_controller,
    )


def _select_staged_file(
    staging_dir: Path,
    pattern: str,
    expected_suffix: str,
) -> Path:
    suffix = (expected_suffix or "").lower()
    candidates = []
    for path in staging_dir.rglob(pattern):
        if not path.is_file():
            continue
        if suffix and path.suffix.lower() != suffix:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 0:
            candidates.append((size, path))
    if not candidates:
        raise DownloadError(f"expected {expected_suffix} file was not created")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _download_thumbnail_from_url(
    thumbnail_url: str,
    temp_path: Path,
    final_path: Path,
    log=None,
    cancel_controller: DownloadController | None = None,
) -> None:
    request = urllib.request.Request(thumbnail_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
    except (urllib.error.URLError, TimeoutError):
        raise DownloadError("thumbnail download failed")

    if not _is_jpeg_download(content_type, data):
        raise DownloadError("thumbnail download failed")

    temp_path.write_bytes(data)
    if not _final_file_ready(temp_path):
        raise DownloadError("thumbnail download failed")
    _atomic_promote_with_retry(temp_path, final_path, log, cancel_controller=cancel_controller)


def _is_jpeg_download(content_type: str, data: bytes) -> bool:
    if not data or len(data) < 3:
        return False
    if not data.startswith(b"\xff\xd8\xff"):
        return False
    lower_type = (content_type or "").lower()
    if lower_type and any(kind in lower_type for kind in ("webp", "png", "gif", "avif")):
        return False
    return True


def _move_with_retry(
    source_path: Path,
    final_path: Path,
    log=None,
    replace_existing: bool = False,
    cancel_controller: DownloadController | None = None,
) -> None:
    _atomic_promote_with_retry(
        source_path,
        final_path,
        log,
        replace_existing=replace_existing,
        cancel_controller=cancel_controller,
    )


def _atomic_promote_with_retry(
    source_path: Path,
    final_path: Path,
    log=None,
    replace_existing: bool = False,
    cancel_controller: DownloadController | None = None,
) -> None:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if _final_file_ready(final_path) and not replace_existing:
        return

    try:
        if not source_path.exists() or not source_path.is_file() or source_path.stat().st_size <= 0:
            raise OSError("source file is missing or empty")
    except OSError as exc:
        raise FileOperationError("promote", source_path, final_path, exc) from exc

    if not _paths_share_filesystem(source_path, final_path.parent):
        exc = OSError("source and destination are not on the same filesystem")
        raise FileOperationError("promote", source_path, final_path, exc) from exc

    promoted = False
    last_replace_error: OSError | None = None
    successful_attempt = 0
    for attempt, delay in enumerate((0, 1, 3, 5)):
        _raise_if_cancelled(cancel_controller)
        if delay:
            _sleep_with_cancel(delay, cancel_controller)
        try:
            os.replace(source_path, final_path)
            promoted = True
            successful_attempt = attempt
            break
        except OSError as exc:
            last_replace_error = exc

    if not promoted:
        if last_replace_error is None:
            last_replace_error = OSError("promotion did not complete")
        raise FileOperationError("promote", source_path, final_path, last_replace_error)

    _verify_promoted_file_with_retry(source_path, final_path, cancel_controller)
    if successful_attempt > 0 and log:
        log("[WARNING] File was temporarily locked, retry succeeded.")


def _verify_promoted_file_with_retry(
    source_path: Path,
    final_path: Path,
    cancel_controller: DownloadController | None = None,
) -> None:
    last_error: OSError | None = None
    for delay in (0, 0.05, 0.1, 0.2):
        _raise_if_cancelled(cancel_controller)
        if delay:
            _sleep_with_cancel(delay, cancel_controller)
        try:
            _verify_promoted_file_once(source_path, final_path)
            return
        except OSError as exc:
            last_error = exc
    if last_error is None:
        last_error = OSError("promoted file verification failed")
    raise FileOperationError("verify_promoted_file", source_path, final_path, last_error)


def _verify_promoted_file_once(source_path: Path, final_path: Path) -> None:
    if not final_path.exists():
        raise OSError("promoted file does not exist")
    if not final_path.is_file():
        raise OSError("promoted path is not a file")
    if final_path.stat().st_size <= 0:
        raise OSError("promoted file is empty")
    if source_path.exists():
        raise OSError("source still exists after promotion")


def _paths_share_filesystem(source_path: Path, destination_parent: Path) -> bool:
    try:
        if not source_path.exists() or not source_path.is_file():
            return False
        source_parent = source_path.parent
        if not source_parent.exists() or not source_parent.is_dir():
            return False
        if not destination_parent.exists() or not destination_parent.is_dir():
            return False
        source_resolved = source_path.resolve(strict=False)
        destination_resolved = destination_parent.resolve(strict=False)
        if os.name == "nt" and source_resolved.anchor.casefold() != destination_resolved.anchor.casefold():
            return False
        source_parent_device = source_path.parent.stat().st_dev
        destination_parent_device = destination_parent.stat().st_dev
        return source_parent_device == destination_parent_device
    except OSError:
        return False


def _final_file_ready(path: Path) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False
