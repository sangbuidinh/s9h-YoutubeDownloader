import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

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
    batch_blocked_warning,
    classify_general_error,
    classify_ytdlp_error,
    format_friendly_error,
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
OUTPUT_PATH_TOO_LONG_MESSAGE = (
    "Output path too long. Please choose a shorter save folder or shorten filename limit."
)
COOKIE_SOURCE_FILE = "file"
COOKIE_SOURCE_BRIDGE = "bridge"
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
COOKIE_BRIDGE_REFRESH_TIMEOUT_SECONDS = 45
COOKIE_BRIDGE_REFRESH_POLL_SECONDS = 0.5


class DownloadError(Exception):
    pass


class DownloadCancelled(DownloadError):
    pass


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
    def __init__(self):
        self._cancel_requested = threading.Event()
        self._process_lock = threading.Lock()
        self.current_process: subprocess.Popen | None = None

    def request_cancel(self) -> None:
        self._cancel_requested.set()
        with self._process_lock:
            process = self.current_process
        if process is not None:
            threading.Thread(target=self._terminate_process, args=(process,), daemon=True).start()

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def set_current_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            self.current_process = process
        if self.is_cancel_requested():
            self._terminate_process(process)

    def clear_current_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            if self.current_process is process:
                self.current_process = None

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
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.output_lines = output_lines
        self.bot_check = bot_check
        self.http_403 = http_403
        self.missing_js_runtime = missing_js_runtime
        self.combined_output = combined_output
        self.stream_interrupted = stream_interrupted


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


@dataclass
class _CookieFileMetadata:
    exists: bool
    size: int
    mtime_ns: int | None


@dataclass
class _CookieRefreshRetryState:
    fresh_retry_used: bool = False
    refreshed_rejected_logged: bool = False


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
    text = exc.combined_output or "\n".join([str(exc), *exc.output_lines])
    friendly = classify_ytdlp_error(
        text,
        cookies_enabled=cookies_enabled,
        bot_check=exc.bot_check,
        http_403=exc.http_403,
        missing_js_runtime=exc.missing_js_runtime,
    )
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


def effective_cookies_path(options: DownloadOptions) -> str:
    if not options.cookies_enabled:
        return ""
    source = options.cookie_source if options.cookie_source in {COOKIE_SOURCE_FILE, COOKIE_SOURCE_BRIDGE} else COOKIE_SOURCE_FILE
    if source == COOKIE_SOURCE_BRIDGE:
        path_text = (options.bridge_cookie_path or "").strip()
        error_message = BRIDGE_COOKIE_FILE_MISSING_MESSAGE
    else:
        path_text = (options.cookies_path or "").strip()
        error_message = "Cookies file missing"
    if not path_text:
        raise DownloadError(error_message)
    path = Path(path_text)
    try:
        if not path.exists() or not path.is_file():
            raise OSError
        with path.open("rb"):
            pass
    except OSError as exc:
        raise DownloadError(error_message) from exc
    return str(path)


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
    _log_runtime_tool_summary(log)
    downloaded_count = 0
    failed_count = 0
    skipped_count = 0
    blocking_failure_count = 0
    consecutive_blocking_failures = 0
    mode_parts = required_parts(options.download_mode)
    video_total = len(videos)
    cancelled = False
    stopped_early = False

    if options.cookies_enabled:
        log("[INFO] Cookies enabled: yes")
        log("[INFO] Passing cookies.txt to yt-dlp.")
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
        cookie_retry_state = _CookieRefreshRetryState()

        log(f"[INFO] Starting download: {index}/{len(videos)}")
        log(f"[INFO] Mode: {options.download_mode}")
        try:
            _validate_output_paths(paths, mode_parts)

            entry = get_video_entry(options.channel_id, video.video_id)
            if is_mode_complete(entry, options.download_mode):
                log(f"[SKIP] {stem} marked as downloaded in SQLite state")
                _emit_progress(progress_callback, "Skipped", video, index, video_total)
                skipped_count += 1
                consecutive_blocking_failures = 0
                video.status = get_effective_status(entry, options.download_mode)
                status_callback(video)
                continue

            missing_parts = missing_parts_for_mode(entry, options.download_mode)
            if not missing_parts:
                _emit_progress(progress_callback, "Skipped", video, index, video_total)
                skipped_count += 1
                consecutive_blocking_failures = 0
                video.status = get_effective_status(entry, options.download_mode)
                status_callback(video)
                continue

            with tempfile.TemporaryDirectory(prefix="youtube_downloader_") as temp_dir:
                temp_path = Path(temp_dir)
                for part in missing_parts:
                    _raise_if_cancelled(cancel_controller)
                    current_part = part
                    _remember_run_part(run_parts_current_run, part)
                    if part == PART_VIDEO:
                        log(f"[INFO] Downloading {stem}.mp4")
                        log("[INFO] Premiere-safe mode: MP4 H.264/AAC only, max 1080p.")
                        _emit_progress(progress_callback, "Video", video, index, video_total, message="Downloading...")
                        previous_progress = _set_progress_context(
                            progress_callback, video, index, video_total, "Video"
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
                                cookie_retry_state,
                            )
                        finally:
                            _restore_progress_context(previous_progress)
                    elif part == PART_AUDIO:
                        log(f"[INFO] Downloading audio {stem}.mp3")
                        if options.download_mode == MODE_VIDEO_AUDIO_THUMB:
                            _emit_progress(progress_callback, "Validating MP4", video, index, video_total)
                            if not _premiere_safe_mp4_ready(paths.video_path):
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
                                try:
                                    _download_video(
                                        video.video_id,
                                        stem,
                                        temp_path,
                                        paths.video_path,
                                        options,
                                        log,
                                        cancel_controller,
                                        cookie_retry_state,
                                    )
                                finally:
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
                                    cookie_retry_state,
                                )
                            finally:
                                _restore_progress_context(previous_progress)
                    elif part == PART_THUMB:
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
                                cookie_retry_state,
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
                consecutive_blocking_failures = 0
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
            if current_part == PART_VIDEO:
                _cleanup_companion_outputs_after_video_failure(
                    options,
                    video,
                    paths,
                    log,
                    run_parts_current_run,
                )
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
                consecutive_blocking_failures = 0
                log(f"[SUCCESS] Downloaded {_success_file_list(stem, options.download_mode)}")
                continue
            failed_count += 1
            consecutive_blocking_failures = 0
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
        except YtdlpExecutionError as exc:
            _remember_run_part(run_parts_current_run, current_part)
            _mark_part_error(options, video, paths, current_part)
            if current_part == PART_VIDEO:
                _cleanup_companion_outputs_after_video_failure(
                    options,
                    video,
                    paths,
                    log,
                    run_parts_current_run,
                )
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
                consecutive_blocking_failures = 0
                log(f"[SUCCESS] Downloaded {_success_file_list(stem, options.download_mode)}")
                continue
            failed_count += 1
            _emit_ytdlp_error_progress(progress_callback, video, index, video_total, exc, options.cookies_enabled)
            _log_friendly_ytdlp_error(log, exc, options)
            if exc.missing_js_runtime and not _deno_runtime_path().exists() and (exc.bot_check or exc.http_403):
                _log_missing_js_runtime_warning(log, exc.output_lines)
            if exc.bot_check or exc.http_403:
                blocking_failure_count += 1
                consecutive_blocking_failures += 1
                if consecutive_blocking_failures >= 3:
                    log(format_friendly_error(batch_blocked_warning()))
                    stopped_early = True
                    break
            else:
                consecutive_blocking_failures = 0
        except DownloadError as exc:
            _remember_run_part(run_parts_current_run, current_part)
            _mark_part_error(options, video, paths, current_part)
            if current_part == PART_VIDEO:
                _cleanup_companion_outputs_after_video_failure(
                    options,
                    video,
                    paths,
                    log,
                    run_parts_current_run,
                )
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
                consecutive_blocking_failures = 0
                log(f"[SUCCESS] Downloaded {_success_file_list(stem, options.download_mode)}")
                continue
            failed_count += 1
            consecutive_blocking_failures = 0
            if str(exc) == OUTPUT_PATH_TOO_LONG_MESSAGE:
                _emit_general_error_progress(progress_callback, video, index, video_total, "Path too long")
                _log_friendly_general_error(log, "Path too long", [str(exc)])
            else:
                _emit_general_error_progress(progress_callback, video, index, video_total, str(exc))
                _log_friendly_general_error(log, str(exc), [str(exc)])
        except Exception as exc:
            _remember_run_part(run_parts_current_run, current_part)
            _mark_part_error(options, video, paths, current_part)
            if current_part == PART_VIDEO:
                _cleanup_companion_outputs_after_video_failure(
                    options,
                    video,
                    paths,
                    log,
                    run_parts_current_run,
                )
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
                consecutive_blocking_failures = 0
                log(f"[SUCCESS] Downloaded {_success_file_list(stem, options.download_mode)}")
                continue
            failed_count += 1
            consecutive_blocking_failures = 0
            technical = f"{type(exc).__name__}: {exc}"
            _emit_general_error_progress(progress_callback, video, index, video_total, technical)
            _log_friendly_general_error(log, technical, [technical])

    log(f"[SUCCESS] Downloaded: {downloaded_count}")
    if failed_count > 0:
        log(f"[ERROR] Failed: {failed_count}")
    if skipped_count > 0:
        log(f"[SKIP] Skipped: {skipped_count}")
    if blocking_failure_count > 0:
        log(f"[WARNING] Bot-check/403 failures: {blocking_failure_count}")
    if cancelled:
        _emit_progress_event(progress_callback, ProgressEvent(kind="stop_requested"))
    elif not stopped_early:
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
    cookie_retry_state: _CookieRefreshRetryState | None = None,
) -> None:
    url = f"https://www.youtube.com/watch?v={video_id}"
    if final_path.exists() and _premiere_safe_mp4_ready(final_path):
        return
    output_template = str(temp_dir / f"{_safe_temp_stem(video_id)}.%(ext)s")
    command = _base_ytdlp_command(options) + [
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
    _run_ytdlp_with_retries(command, options, log, cancel_controller, cookie_retry_state)
    _move_single_file(temp_dir, "*.mp4", final_path, log, replace_existing=True)
    _emit_current_progress("Validating MP4")
    _validate_premiere_safe_mp4(final_path, log, delete_invalid=True)


def _download_audio(
    video_id: str,
    stem: str,
    temp_dir: Path,
    final_path: Path,
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
    cookie_retry_state: _CookieRefreshRetryState | None = None,
) -> None:
    url = f"https://www.youtube.com/watch?v={video_id}"
    output_template = str(temp_dir / f"{_safe_temp_stem(video_id)}.%(ext)s")
    command = _base_ytdlp_command(options) + [
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
    _run_ytdlp_with_retries(command, options, log, cancel_controller, cookie_retry_state)
    _move_single_file(temp_dir, "*.mp3", final_path, log)


def _extract_mp3_from_video(
    source_video_path: Path,
    temp_dir: Path,
    final_audio_path: Path,
    log=None,
    cancel_controller: DownloadController | None = None,
) -> None:
    _emit_current_progress("Validating MP4")
    _validate_premiere_safe_mp4(source_video_path, log, delete_invalid=False)
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
    _move_with_retry(temp_mp3_path, final_audio_path, log)
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
    except KeyboardInterrupt:
        _terminate_process_tree(process)
        raise DownloadCancelled("download cancelled/interrupted")
    finally:
        if process is not None and cancel_controller is not None:
            cancel_controller.clear_current_process(process)

    if _cancel_requested(cancel_controller):
        raise DownloadCancelled("download cancelled/interrupted")
    if process.returncode == 0:
        return f"{stdout}\n{stderr}"
    raise DownloadError("audio extraction failed")


def _download_thumbnail(
    video,
    stem: str,
    temp_dir: Path,
    final_path: Path,
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
    cookie_retry_state: _CookieRefreshRetryState | None = None,
) -> None:
    url = f"https://www.youtube.com/watch?v={video.video_id}"
    output_template = str(temp_dir / f"{_safe_temp_stem(video.video_id)}.%(ext)s")
    if video.thumbnail_url:
        try:
            log("[INFO] Downloading thumbnail from API URL first.")
            _raise_if_cancelled(cancel_controller)
            _download_thumbnail_from_url(video.thumbnail_url, temp_dir / f"{_safe_temp_stem(video.video_id)}.jpg", final_path, log)
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
        _move_single_file(temp_dir, "*.jpg", final_path, log)
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
        "-N",
        "4",
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
    cookies_path = effective_cookies_path(options)
    if cookies_path:
        command.extend(["--cookies", cookies_path])
    if options.speed_limit:
        command.extend(["--limit-rate", options.speed_limit])
    return command


def _premiere_safe_mp4_ready(path: Path) -> bool:
    try:
        _validate_premiere_safe_mp4(path, delete_invalid=False)
    except DownloadError:
        return False
    return True


def _validate_premiere_safe_mp4(path: Path, log=None, delete_invalid: bool = True) -> None:
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
        output = _probe_media_with_ffmpeg(path)
    except DownloadError as exc:
        message = str(exc)
        if message == "ffmpeg.exe missing":
            raise
        reason = message.removeprefix("premiere_safe_mp4_validation_failed: ").strip() or "unable to probe media"
        _fail_premiere_safe_validation(path, reason, log, delete_invalid=delete_invalid)
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


def _probe_media_with_ffmpeg(path: Path) -> str:
    ffmpeg_path = runtime_file("ffmpeg.exe")
    ffprobe_path = ffmpeg_path.with_name("ffprobe.exe")
    if ffprobe_path.exists():
        output = _run_probe_command(
            [
                str(ffprobe_path),
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,codec_tag_string,width,height",
                "-of",
                "compact=p=0:nk=0",
                str(path),
            ]
        )
        if output.strip():
            return output

    output = _run_probe_command([str(ffmpeg_path), "-hide_banner", "-i", str(path)])
    if output.strip():
        return output
    raise DownloadError("premiere_safe_mp4_validation_failed: unable to probe media")


def _run_probe_command(command: list[str]) -> str:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=creationflags,
        )
    except FileNotFoundError:
        raise DownloadError("ffmpeg.exe missing")
    except subprocess.TimeoutExpired:
        raise DownloadError("premiere_safe_mp4_validation_failed: media probe timed out")
    return f"{result.stdout}\n{result.stderr}"


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


def _log_runtime_tool_summary(log) -> None:
    ytdlp_path = runtime_file("yt-dlp.exe")
    ffmpeg_path = runtime_file("ffmpeg.exe")
    deno_path = _deno_runtime_path()

    ytdlp_version = _get_command_version(ytdlp_path, ["--version"])
    if ytdlp_version:
        log(f"[INFO] yt-dlp version: {ytdlp_version}")
    else:
        log("[INFO] yt-dlp version: unavailable")

    ffmpeg_version = _get_command_version(ffmpeg_path, ["-version"]) if ffmpeg_path.exists() else ""
    if ffmpeg_path.exists() and ffmpeg_version:
        log(f"[INFO] ffmpeg found: yes ({ffmpeg_version})")
    elif ffmpeg_path.exists():
        log("[INFO] ffmpeg found: yes (version unavailable)")
    else:
        log("[INFO] ffmpeg found: no")

    deno_version = _get_command_version(deno_path, ["--version"]) if deno_path.exists() else ""
    if deno_path.exists() and deno_version:
        log(f"[INFO] deno found: yes ({deno_version})")
    elif deno_path.exists():
        log("[INFO] deno found: yes (version unavailable)")
    else:
        log("[INFO] deno found: no")


def _get_command_version(path: Path, args: list[str]) -> str:
    if not path.exists():
        return ""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [str(path), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=creationflags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    output = result.stdout or result.stderr or ""
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


def _cookie_file_metadata(path: Path) -> _CookieFileMetadata:
    try:
        stat = path.stat()
        if not path.is_file():
            return _CookieFileMetadata(False, 0, None)
        return _CookieFileMetadata(True, stat.st_size, stat.st_mtime_ns)
    except OSError:
        return _CookieFileMetadata(False, 0, None)


def _metadata_mtime_text(metadata: _CookieFileMetadata) -> str:
    if metadata.mtime_ns is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(metadata.mtime_ns / 1_000_000_000))


def _metadata_log_text(metadata: _CookieFileMetadata) -> str:
    return f"exists={metadata.exists}, size={metadata.size}, mtime={_metadata_mtime_text(metadata)}"


def _is_fresh_cookie_file(
    current: _CookieFileMetadata,
    old: _CookieFileMetadata,
    error_detected_ns: int,
) -> bool:
    if not current.exists or current.size <= 0 or current.mtime_ns is None:
        return False
    old_mtime_ns = old.mtime_ns or 0
    return current.mtime_ns > old_mtime_ns and current.mtime_ns >= error_detected_ns


def _wait_for_fresh_cookie_file(
    path: Path,
    old_metadata: _CookieFileMetadata,
    error_detected_ns: int,
    timeout_seconds: int | float,
    poll_seconds: int | float,
    cancel_controller: DownloadController | None,
) -> _CookieFileMetadata | None:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        _raise_if_cancelled(cancel_controller)
        current = _cookie_file_metadata(path)
        if _is_fresh_cookie_file(current, old_metadata, error_detected_ns):
            return current
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        _sleep_with_cancel(min(float(poll_seconds), remaining), cancel_controller)


def _is_cookie_refresh_error(exc: YtdlpExecutionError) -> bool:
    text = exc.combined_output or "\n".join([str(exc), *exc.output_lines])
    return exc.bot_check or is_cookie_session_error(text)


def _maybe_retry_after_bridge_cookie_refresh(
    exc: YtdlpExecutionError,
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None,
    retry_state: _CookieRefreshRetryState,
) -> bool:
    if not options.cookies_enabled or not _is_cookie_refresh_error(exc):
        return False
    if options.cookie_source != COOKIE_SOURCE_BRIDGE:
        return False
    if retry_state.fresh_retry_used:
        if not retry_state.refreshed_rejected_logged:
            log(
                "[ERROR] Cookie file was refreshed, but YouTube still rejected the browser session. "
                "Open YouTube in the same browser profile, sign in again, then let the bridge export cookies again."
            )
            retry_state.refreshed_rejected_logged = True
        return False

    log("[INFO] Cookie/session error detected while using Local Cookie Bridge.")
    error_detected_ns = time.time_ns()
    try:
        cookie_path = Path(effective_cookies_path(options))
    except DownloadError:
        log("[WARNING] Cookie Bridge file was not available for refresh check. Retry skipped.")
        return False

    old_metadata = _cookie_file_metadata(cookie_path)
    log(f"[INFO] Cookie Bridge path: {cookie_path}")
    log(f"[INFO] Cookie Bridge old metadata: {_metadata_log_text(old_metadata)}")
    log("[INFO] Waiting for Cookie Bridge to refresh the cookie file...")
    new_metadata = _wait_for_fresh_cookie_file(
        cookie_path,
        old_metadata,
        error_detected_ns,
        COOKIE_BRIDGE_REFRESH_TIMEOUT_SECONDS,
        COOKIE_BRIDGE_REFRESH_POLL_SECONDS,
        cancel_controller,
    )
    if new_metadata is None:
        current_metadata = _cookie_file_metadata(cookie_path)
        log(f"[WARNING] Cookie Bridge timeout metadata: {_metadata_log_text(current_metadata)}")
        log("[WARNING] Cookie Bridge file was not updated before timeout. Retry skipped.")
        log("[ERROR] Cookie Bridge did not provide a refreshed cookie file in time.")
        return False

    retry_state.fresh_retry_used = True
    log(f"[INFO] Cookie Bridge new metadata: {_metadata_log_text(new_metadata)}")
    log("[INFO] Cookie Bridge file updated. Retrying once with refreshed cookies.")
    return True


def _run_ytdlp_with_retries(
    command: list[str],
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
    cookie_retry_state: _CookieRefreshRetryState | None = None,
) -> None:
    http_403_delays = [10, 30]
    http_403_retries = 0
    cookie_retry_state = cookie_retry_state or _CookieRefreshRetryState()
    stream_interrupted_retried = False
    current_command = list(command)

    while True:
        _raise_if_cancelled(cancel_controller)
        try:
            stderr = _run_ytdlp(current_command, cancel_controller)
            if (
                SHOW_TECHNICAL_WARNINGS
                and stderr
                and _contains_missing_js_runtime_error(stderr)
                and not _deno_runtime_path().exists()
            ):
                _log_missing_js_runtime_warning(log, [stderr])
            return
        except YtdlpExecutionError as exc:
            if _maybe_retry_after_bridge_cookie_refresh(
                exc,
                options,
                log,
                cancel_controller,
                cookie_retry_state,
            ):
                current_command = list(command)
                continue

            if exc.bot_check or (options.cookies_enabled and _is_cookie_refresh_error(exc)):
                raise

            if exc.http_403 and http_403_retries < len(http_403_delays):
                delay = http_403_delays[http_403_retries]
                http_403_retries += 1
                log(
                    "[WARNING] HTTP 403 / Forbidden from yt-dlp. "
                    f"Retrying in {delay} seconds (retry {http_403_retries}/2)."
                )
                _sleep_with_cancel(delay, cancel_controller)
                continue

            if (
                exc.stream_interrupted or _contains_stream_interrupted_output(exc.combined_output)
            ) and not stream_interrupted_retried:
                stream_interrupted_retried = True
                current_command = _ensure_flag(
                    _replace_option(command, "--http-chunk-size", "512K"),
                    "--no-continue",
                )
                log("[WARNING] Stream interrupted. Retrying once with safer chunk settings.")
                continue

            raise


def _run_ytdlp(command: list[str], cancel_controller: DownloadController | None = None) -> str:
    creationflags = _subprocess_creationflags()
    process = None
    output_tail: list[str] = []
    meaningful_lines: list[str] = []
    bot_check = False
    http_403 = False
    missing_js_runtime = False
    premiere_safe_format_error = False
    stream_interrupted = False
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
                _append_limited(output_tail, line, 200)
                sanitized = _sanitize_ytdlp_output_line(line)
                if _is_meaningful_ytdlp_line(sanitized):
                    _append_limited(meaningful_lines, sanitized, 50)
                bot_check = bot_check or _contains_bot_check_error(line)
                http_403 = http_403 or _contains_http_403_error(line)
                missing_js_runtime = missing_js_runtime or _contains_missing_js_runtime_error(line)
                premiere_safe_format_error = premiere_safe_format_error or _contains_premiere_safe_format_error(line)
                stream_interrupted = stream_interrupted or _contains_stream_interrupted_output(line)
                _emit_ytdlp_progress_from_line(sanitized)
                if _cancel_requested(cancel_controller):
                    _terminate_process_tree(process)
                    raise DownloadCancelled("download cancelled/interrupted")
        return_code = _wait_for_process_exit(process, 1.0)
        if return_code is None:
            if _cancel_requested(cancel_controller):
                _terminate_process_tree(process)
                raise DownloadCancelled("download cancelled/interrupted")
            _append_limited(output_tail, "yt-dlp process did not exit promptly after output ended", 200)
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
        raise YtdlpExecutionError(
            return_code,
            _classify_ytdlp_error_from_flags(
                output,
                bot_check=bot_check,
                missing_js_runtime=missing_js_runtime,
                premiere_safe_format_error=premiere_safe_format_error,
            ),
            meaningful_lines or _last_meaningful_output_lines("", output, limit=50),
            bot_check,
            http_403,
            missing_js_runtime,
            output,
            stream_interrupted,
        )

    return output


def _append_limited(items: list[str], value: str, limit: int) -> None:
    items.append(value)
    if len(items) > limit:
        del items[: len(items) - limit]


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
        "sign in to confirm" in lower
        or "not a bot" in lower
        or "confirm you're not a bot" in lower
        or "this helps protect our community" in lower
        or "use --cookies" in lower
        or "use --cookies-from-browser" in lower
    )


def is_cookie_session_error(text: str) -> bool:
    lower = (text or "").lower()
    if not lower:
        return False
    direct_markers = (
        "sign in",
        "sign in to confirm",
        "confirm you're not a bot",
        "not a bot",
        "this helps protect our community",
        "use --cookies",
        "use --cookies-from-browser",
        "login required",
        "please log in",
        "authentication required",
        "account authentication",
    )
    if any(marker in lower for marker in direct_markers):
        return True

    has_cookie_marker = "cookie" in lower or "cookies" in lower
    cookie_problem_markers = (
        "expired",
        "invalid",
        "required",
        "not valid",
        "no longer valid",
        "requires",
        "require",
    )
    if has_cookie_marker and any(marker in lower for marker in cookie_problem_markers):
        return True

    has_http_marker = (
        "http error 403" in lower
        or "403: forbidden" in lower
        or "http error 429" in lower
        or "429: too many requests" in lower
    )
    http_context_markers = (
        "sign in",
        "login required",
        "please log in",
        "authentication required",
        "bot",
        "not a bot",
        "cookie",
        "cookies",
        "use --cookies",
        "use --cookies-from-browser",
        "protect our community",
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


def _contains_http_403_error(stderr: str) -> bool:
    lower = (stderr or "").lower()
    return "http error 403" in lower or "forbidden" in lower


def _contains_missing_js_runtime_error(stderr: str) -> bool:
    lower = (stderr or "").lower()
    return "no supported javascript runtime" in lower or "javascript runtime" in lower or "ejs" in lower


def _contains_premiere_safe_format_error(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "premiere_safe_mp4_validation_failed" in lower
        or "requested format is not available" in lower
        or "requested format not available" in lower
        or "no video formats found" in lower
        or "no suitable formats" in lower
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


def _cleanup_companion_outputs_after_video_failure(
    options: DownloadOptions,
    video,
    paths,
    log,
    run_parts_current_run: list[str] | None = None,
) -> None:
    for part, path in _companion_outputs_for_failed_video(paths, options.download_mode):
        _remember_run_part(run_parts_current_run, part)
        _delete_companion_output_after_video_failure(path, part, log)
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
            if log:
                log(f"[WARNING] Could not update companion state after video failure: {part}")


def _companion_outputs_for_failed_video(paths, download_mode: str) -> tuple[tuple[str, Path], ...]:
    companions: list[tuple[str, Path]] = []
    mode_parts = required_parts(download_mode)
    if PART_THUMB in mode_parts:
        companions.append((PART_THUMB, paths.thumb_path))
    if PART_AUDIO in mode_parts:
        companions.append((PART_AUDIO, paths.audio_path))
    return tuple(companions)


def _delete_companion_output_after_video_failure(path: Path, part: str, log=None) -> None:
    try:
        if path.exists() and path.is_file():
            path.unlink()
            if log:
                log(f"[WARNING] Removed {part} output after video failure: {path.name}")
    except OSError:
        if log:
            log(f"[WARNING] Could not remove {part} output after video failure: {path.name}")


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


def _sanitize_ytdlp_output_line(line: str) -> str:
    text = (line or "").strip()
    youtube_api_key_prefix = "AI" "za"
    text = re.sub(r"(?i)(key=)[^&\s]+", r"\1***", text)
    text = re.sub(
        re.escape(youtube_api_key_prefix) + r"[0-9A-Za-z_-]{20,}",
        youtube_api_key_prefix + "...****",
        text,
    )
    text = re.sub(r"(?i)(cookie(?:s)?\s*[:=]).*", r"\1 ***", text)
    return text


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
    text = exc.combined_output or "\n".join([str(exc), *exc.output_lines])
    friendly = classify_ytdlp_error(
        text,
        cookies_enabled=options.cookies_enabled,
        bot_check=exc.bot_check,
        http_403=exc.http_403,
        missing_js_runtime=exc.missing_js_runtime,
    )
    log(format_friendly_error(friendly, _technical_lines_for_ytdlp(exc)))
    if options.cookies_enabled and (exc.bot_check or is_cookie_session_error(text)):
        log(cookie_session_error_message(options))


def _log_missing_js_runtime_warning(log, output_lines: list[str]) -> None:
    log(format_friendly_error(missing_js_runtime_warning(), output_lines[:1]))


def _log_friendly_general_error(log, message: str, technical_lines: list[str] | None = None) -> None:
    log(format_friendly_error(classify_general_error(message), technical_lines or [message]))


def _technical_lines_for_ytdlp(exc: YtdlpExecutionError) -> list[str]:
    lines = [line for line in exc.output_lines if line.strip()]
    if not lines:
        return [f"yt-dlp exit code {exc.exit_code}: {exc}"]
    return [*lines, f"yt-dlp exit code {exc.exit_code}"]


def _move_single_file(
    temp_dir: Path,
    pattern: str,
    final_path: Path,
    log=None,
    replace_existing: bool = False,
) -> None:
    if not replace_existing and _final_file_ready(final_path):
        return

    candidates = [path for path in temp_dir.rglob(pattern) if path.is_file()]
    if not candidates:
        raise DownloadError(f"expected {final_path.suffix} file was not created")

    candidates.sort(key=lambda path: path.stat().st_size, reverse=True)
    _move_with_retry(candidates[0], final_path, log, replace_existing=replace_existing)


def _download_thumbnail_from_url(thumbnail_url: str, temp_path: Path, final_path: Path, log=None) -> None:
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
    if _final_file_ready(final_path):
        return
    _move_with_retry(temp_path, final_path, log)


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
) -> None:
    last_error: BaseException | None = None
    final_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt, delay in enumerate((0, 1, 3, 5)):
        if delay:
            time.sleep(delay)
        try:
            if _final_file_ready(final_path) and not replace_existing:
                return
            if final_path.exists():
                final_path.unlink()
            shutil.move(str(source_path), str(final_path))
            if attempt > 0 and log:
                log("[WARNING] File was temporarily locked, retry succeeded.")
            return
        except (OSError, shutil.Error) as exc:
            last_error = exc

    if last_error is not None:
        raise FileOperationError("move", source_path, final_path, last_error)


def _final_file_ready(path: Path) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False
