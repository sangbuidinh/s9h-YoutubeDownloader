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


class DownloadError(Exception):
    pass


class DownloadCancelled(DownloadError):
    pass


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
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    if process.poll() is None:
                        process.kill()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
        except OSError:
            pass


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
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.output_lines = output_lines
        self.bot_check = bot_check
        self.http_403 = http_403
        self.missing_js_runtime = missing_js_runtime
        self.combined_output = combined_output


@dataclass
class DownloadOptions:
    base_folder: str
    channel_id: str
    channel_name: str
    cookies_enabled: bool = False
    cookies_path: str = ""
    speed_limit: str | None = None
    download_mode: str = MODE_VIDEO_THUMB


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
    if options.cookies_enabled:
        cookies_path = Path(options.cookies_path)
        if not options.cookies_path or not cookies_path.exists() or not cookies_path.is_file():
            raise DownloadError("Cookies file missing")


def download_items(
    videos: list,
    options: DownloadOptions,
    log,
    status_callback,
    cancel_controller: DownloadController | None = None,
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

    if options.cookies_enabled:
        log("[INFO] Cookies enabled: yes")
        log("[INFO] Passing cookies.txt to yt-dlp.")
    if _deno_runtime_path().exists():
        log("[INFO] Deno runtime found. JavaScript challenge solving enabled.")

    for index, video in enumerate(videos, start=1):
        if _cancel_requested(cancel_controller):
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
                skipped_count += 1
                consecutive_blocking_failures = 0
                video.status = get_effective_status(entry, options.download_mode)
                status_callback(video)
                continue

            missing_parts = missing_parts_for_mode(entry, options.download_mode)
            if not missing_parts:
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
                        _download_video(
                            video.video_id,
                            stem,
                            temp_path,
                            paths.video_path,
                            options,
                            log,
                            cancel_controller,
                        )
                    elif part == PART_AUDIO:
                        log(f"[INFO] Downloading audio {stem}.mp3")
                        if options.download_mode == MODE_VIDEO_AUDIO_THUMB:
                            if not _premiere_safe_mp4_ready(paths.video_path):
                                current_part = PART_VIDEO
                                _remember_run_part(run_parts_current_run, PART_VIDEO)
                                log(f"[INFO] Local MP4 missing or invalid; downloading {stem}.mp4 for MP3 extraction.")
                                log("[INFO] Premiere-safe mode: MP4 H.264/AAC only, max 1080p.")
                                _download_video(
                                    video.video_id,
                                    stem,
                                    temp_path,
                                    paths.video_path,
                                    options,
                                    log,
                                    cancel_controller,
                                )
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
                            _extract_mp3_from_video(
                                paths.video_path,
                                temp_path,
                                paths.audio_path,
                                log,
                                cancel_controller,
                            )
                        else:
                            _download_audio(
                                video.video_id,
                                stem,
                                temp_path,
                                paths.audio_path,
                                options,
                                log,
                                cancel_controller,
                            )
                    elif part == PART_THUMB:
                        log(f"[INFO] Downloading thumbnail {stem}.jpg")
                        _download_thumbnail(video, stem, temp_path, paths.thumb_path, options, log, cancel_controller)
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
            _log_friendly_general_error(log, f"{type(exc).__name__}: {exc}", [f"{type(exc).__name__}: {exc}"])
        except DownloadCancelled:
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
            _log_friendly_ytdlp_error(log, exc, options.cookies_enabled)
            if exc.missing_js_runtime and not _deno_runtime_path().exists() and (exc.bot_check or exc.http_403):
                _log_missing_js_runtime_warning(log, exc.output_lines)
            if exc.bot_check or exc.http_403:
                blocking_failure_count += 1
                consecutive_blocking_failures += 1
                if consecutive_blocking_failures >= 3:
                    log(format_friendly_error(batch_blocked_warning()))
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
                _log_friendly_general_error(log, "Path too long", [str(exc)])
            else:
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
            _log_friendly_general_error(log, technical, [technical])

    log(f"[SUCCESS] Downloaded: {downloaded_count}")
    if failed_count > 0:
        log(f"[ERROR] Failed: {failed_count}")
    if skipped_count > 0:
        log(f"[SKIP] Skipped: {skipped_count}")
    if blocking_failure_count > 0:
        log(f"[WARNING] Bot-check/403 failures: {blocking_failure_count}")


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


def _download_video(
    video_id: str,
    stem: str,
    temp_dir: Path,
    final_path: Path,
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
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
    _run_ytdlp_with_retries(command, options, log, cancel_controller)
    _move_single_file(temp_dir, "*.mp4", final_path, log, replace_existing=True)
    _validate_premiere_safe_mp4(final_path, log, delete_invalid=True)


def _download_audio(
    video_id: str,
    stem: str,
    temp_dir: Path,
    final_path: Path,
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
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
    _run_ytdlp_with_retries(command, options, log, cancel_controller)
    _move_single_file(temp_dir, "*.mp3", final_path, log)


def _extract_mp3_from_video(
    source_video_path: Path,
    temp_dir: Path,
    final_audio_path: Path,
    log=None,
    cancel_controller: DownloadController | None = None,
) -> None:
    _validate_premiere_safe_mp4(source_video_path, log, delete_invalid=False)
    if _final_file_ready(final_audio_path):
        return

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
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = None
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
        stdout, stderr = process.communicate()
    except FileNotFoundError:
        raise DownloadError("ffmpeg.exe missing")
    except KeyboardInterrupt:
        raise DownloadCancelled("download cancelled/interrupted")
    finally:
        if process is not None and cancel_controller is not None:
            cancel_controller.clear_current_process(process)

    if process.returncode == 0:
        return f"{stdout}\n{stderr}"
    if _cancel_requested(cancel_controller):
        raise DownloadCancelled("download cancelled/interrupted")
    raise DownloadError("audio extraction failed")


def _download_thumbnail(
    video,
    stem: str,
    temp_dir: Path,
    final_path: Path,
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
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
        _run_ytdlp_with_retries(command, options, log, cancel_controller)
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
    if options.cookies_enabled:
        command.extend(["--cookies", options.cookies_path])
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


def _run_ytdlp_with_retries(
    command: list[str],
    options: DownloadOptions,
    log,
    cancel_controller: DownloadController | None = None,
) -> None:
    http_403_delays = [10, 30]
    http_403_retries = 0
    bot_check_retries = 0
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
            if exc.bot_check:
                if options.cookies_enabled and bot_check_retries < 1:
                    bot_check_retries += 1
                    delay = 10
                    log(
                        "[WARNING] YouTube bot-check from yt-dlp. "
                        f"Retrying in {delay} seconds (retry {bot_check_retries}/1)."
                    )
                    _sleep_with_cancel(delay, cancel_controller)
                    continue
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

            if _contains_stream_interrupted_output(exc.combined_output) and not stream_interrupted_retried:
                stream_interrupted_retried = True
                current_command = _ensure_flag(
                    _replace_option(command, "--http-chunk-size", "512K"),
                    "--no-continue",
                )
                log("[WARNING] Stream interrupted. Retrying once with safer chunk settings.")
                continue

            raise


def _run_ytdlp(command: list[str], cancel_controller: DownloadController | None = None) -> str:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = None
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
        stdout, stderr = process.communicate()
    except FileNotFoundError:
        raise DownloadError("yt-dlp.exe missing")
    except KeyboardInterrupt:
        raise DownloadError("download cancelled/interrupted")
    finally:
        if process is not None and cancel_controller is not None:
            cancel_controller.clear_current_process(process)

    if process.returncode == 0:
        return stderr or ""

    if _cancel_requested(cancel_controller):
        raise DownloadCancelled("download cancelled/interrupted")

    if process.returncode != 0:
        output = f"{stdout}\n{stderr}"
        raise YtdlpExecutionError(
            process.returncode,
            _classify_ytdlp_error(output),
            _last_meaningful_output_lines(stdout, stderr, limit=50),
            _contains_bot_check_error(output),
            _contains_http_403_error(output),
            _contains_missing_js_runtime_error(output),
            output,
        )

    return stderr or ""


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
        or "use --cookies" in lower
        or "use --cookies-from-browser" in lower
    )


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


def _log_friendly_ytdlp_error(log, exc: YtdlpExecutionError, cookies_enabled: bool) -> None:
    text = exc.combined_output or "\n".join([str(exc), *exc.output_lines])
    friendly = classify_ytdlp_error(
        text,
        cookies_enabled=cookies_enabled,
        bot_check=exc.bot_check,
        http_403=exc.http_403,
        missing_js_runtime=exc.missing_js_runtime,
    )
    log(format_friendly_error(friendly, _technical_lines_for_ytdlp(exc)))


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
