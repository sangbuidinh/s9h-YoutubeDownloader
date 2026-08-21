"""FFmpeg execution, live-progress parsing, and bounded diagnostic handling."""

import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from core.download_contracts import (
    DownloadCancelled,
    DownloadError,
    FFmpegFailureKind,
)
from core.download_process import (
    DownloadController,
    _cancel_requested,
    _subprocess_creationflags,
    _terminate_process_tree,
    _wait_for_process_exit,
)


FFMPEG_OUTPUT_LINE_LIMIT = 20
FFMPEG_OUTPUT_LINE_CHAR_LIMIT = 500
FFMPEG_COMBINED_OUTPUT_LIMIT = 8192
FFMPEG_PROGRESS_EMIT_INTERVAL_SECONDS = 0.3
FFMPEG_PROGRESS_QUEUE_POLL_SECONDS = 0.1
FFMPEG_PROGRESS_SPEED_UNKNOWN = "--"
_PROCESS_OUTPUT_TAIL_LIMIT = 200
_STANDALONE_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?P<name>key|api_key|token|access_token)="
)


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
class _FfmpegProgressState:
    duration_seconds: float | None
    out_time_seconds: float = 0.0
    speed: str = FFMPEG_PROGRESS_SPEED_UNKNOWN
    percent_value: int = 0
    last_emit_monotonic: float = 0.0


def _parse_media_timestamp_seconds(value: object) -> float | None:
    text = str(value or "").strip()
    if not text or text.upper() == "N/A":
        return None
    try:
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            seconds = float(text)
            return seconds if seconds >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None

    match = re.fullmatch(
        r"(?P<hours>\d+):(?P<minutes>\d{1,2}):(?P<seconds>\d{1,2}(?:\.\d+)?)",
        text,
    )
    if match is None:
        return None
    try:
        hours = int(match.group("hours"))
        minutes = int(match.group("minutes"))
        seconds = float(match.group("seconds"))
    except (TypeError, ValueError, OverflowError):
        return None
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _parse_ffmpeg_progress_line(line: str) -> tuple[str, str] | None:
    text = str(line or "").strip()
    if not text or "=" not in text:
        return None
    key, value = text.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return key, value.strip()


def _ffmpeg_out_time_seconds(key: str, value: str) -> float | None:
    normalized_key = str(key or "").strip().lower()
    if normalized_key in {"out_time_us", "out_time_ms"}:
        try:
            seconds = float(str(value or "").strip()) / 1_000_000.0
        except (TypeError, ValueError, OverflowError):
            return None
        return seconds if seconds >= 0 else None
    if normalized_key == "out_time":
        return _parse_media_timestamp_seconds(value)
    return None


def _normalize_ffmpeg_speed(value: str | None) -> str:
    text = _sanitize_subprocess_output_line(value or "")
    if not text or text.upper() == "N/A":
        return FFMPEG_PROGRESS_SPEED_UNKNOWN
    return _bound_subprocess_output_line(text)


def _ffmpeg_progress_percent(
    out_time_seconds: float,
    duration_seconds: float | None,
    completed: bool = False,
) -> int:
    if completed:
        return 100
    try:
        duration = float(duration_seconds) if duration_seconds is not None else 0.0
        out_time = max(0.0, float(out_time_seconds))
    except (TypeError, ValueError, OverflowError):
        return 0
    if duration <= 0:
        return 0
    return max(0, min(99, int((out_time / duration) * 100)))


def _emit_ffmpeg_conversion_progress(
    state: _FfmpegProgressState,
    completed: bool = False,
    force: bool = False,
    progress_emitter=None,
) -> None:
    now = time.monotonic()
    if not force and now - state.last_emit_monotonic < FFMPEG_PROGRESS_EMIT_INTERVAL_SECONDS:
        return

    percent_value = _ffmpeg_progress_percent(
        state.out_time_seconds,
        state.duration_seconds,
        completed=completed,
    )
    if not completed:
        percent_value = max(state.percent_value, percent_value)
    state.percent_value = percent_value
    state.last_emit_monotonic = now
    if progress_emitter is not None:
        progress_emitter(
            "FFmpeg",
            kind="ffmpeg_progress",
            message="",
            percent=f"{state.percent_value}%",
            speed=state.speed,
            eta=None,
            fragment=None,
        )


def _consume_ffmpeg_progress_line(
    line: str,
    state: _FfmpegProgressState,
    progress_emitter=None,
) -> bool:
    parsed = _parse_ffmpeg_progress_line(line)
    if parsed is None:
        return False

    key, value = parsed
    normalized_key = key.strip().lower()
    out_time_seconds = _ffmpeg_out_time_seconds(normalized_key, value)
    if out_time_seconds is not None:
        state.out_time_seconds = out_time_seconds
        return True

    if normalized_key == "speed":
        state.speed = _normalize_ffmpeg_speed(value)
        return True

    if normalized_key == "progress":
        normalized_value = value.strip().lower()
        if normalized_value == "end":
            _emit_ffmpeg_conversion_progress(
                state,
                completed=True,
                force=True,
                progress_emitter=progress_emitter,
            )
        elif normalized_value == "continue":
            _emit_ffmpeg_conversion_progress(state, progress_emitter=progress_emitter)
        return True

    return True


def _read_process_stream(stream, stream_name: str, output_queue: queue.Queue) -> None:
    try:
        if stream is not None:
            for raw_line in stream:
                output_queue.put((stream_name, str(raw_line).rstrip("\r\n")))
    finally:
        output_queue.put((stream_name, None))


def run_ffmpeg_command(
    command: list[str],
    *,
    operation: str,
    cancel_controller: DownloadController | None = None,
    progress_duration_seconds: float | None = None,
    progress_emitter=None,
    terminate_process_tree=_terminate_process_tree,
) -> str:
    creationflags = _subprocess_creationflags()
    process = None
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    return_code = -1
    progress_state = (
        _FfmpegProgressState(progress_duration_seconds)
        if progress_duration_seconds is not None
        else None
    )
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

        output_queue: queue.Queue = queue.Queue()
        stream_count = 0
        for stream_name, stream in (
            ("stdout", getattr(process, "stdout", None)),
            ("stderr", getattr(process, "stderr", None)),
        ):
            if stream is None:
                continue
            stream_count += 1
            threading.Thread(
                target=_read_process_stream,
                args=(stream, stream_name, output_queue),
                daemon=True,
            ).start()

        while stream_count > 0:
            if _cancel_requested(cancel_controller):
                terminate_process_tree(process)
                raise DownloadCancelled("download cancelled/interrupted")
            try:
                stream_name, line = output_queue.get(timeout=FFMPEG_PROGRESS_QUEUE_POLL_SECONDS)
            except queue.Empty:
                continue
            if line is None:
                stream_count -= 1
                continue
            if stream_name == "stdout":
                if progress_state is not None and _consume_ffmpeg_progress_line(
                    line,
                    progress_state,
                    progress_emitter,
                ):
                    continue
                _append_limited(stdout_lines, line, _PROCESS_OUTPUT_TAIL_LIMIT)
            else:
                _append_limited(stderr_lines, line, _PROCESS_OUTPUT_TAIL_LIMIT)

        while True:
            if _cancel_requested(cancel_controller):
                terminate_process_tree(process)
                raise DownloadCancelled("download cancelled/interrupted")
            return_code = _wait_for_process_exit(process, FFMPEG_PROGRESS_QUEUE_POLL_SECONDS)
            if return_code is not None:
                break
    except FileNotFoundError:
        raise DownloadError("ffmpeg.exe missing")
    except OSError as exc:
        reason = _sanitize_subprocess_output_line(str(exc)) or type(exc).__name__
        raise DownloadError(
            f"ffmpeg process creation failed during {operation}: {type(exc).__name__}: {reason}"
        ) from exc
    except KeyboardInterrupt:
        terminate_process_tree(process)
        raise DownloadCancelled("download cancelled/interrupted")
    finally:
        if process is not None and cancel_controller is not None:
            cancel_controller.clear_current_process(process)

    if _cancel_requested(cancel_controller):
        raise DownloadCancelled("download cancelled/interrupted")
    stdout = "\n".join(stdout_lines)
    stderr = "\n".join(stderr_lines)
    if return_code == 0:
        if progress_state is not None:
            _emit_ffmpeg_conversion_progress(
                progress_state,
                completed=True,
                force=True,
                progress_emitter=progress_emitter,
            )
        return _bounded_sanitized_subprocess_output(stdout, stderr)

    output_lines = _ffmpeg_output_lines(stdout, stderr)
    combined_output = _bounded_sanitized_subprocess_output(stdout, stderr)
    initial = FFmpegExecutionError(
        operation=operation,
        exit_code=return_code,
        message=f"ffmpeg {operation} failed",
        output_lines=output_lines,
        combined_output=combined_output,
    )
    failure_kind = classify_ffmpeg_failure_kind(initial)
    raise FFmpegExecutionError(
        operation=operation,
        exit_code=return_code,
        message=f"ffmpeg {operation} failed: {failure_kind.value}",
        output_lines=output_lines,
        combined_output=combined_output,
    )


def classify_ffmpeg_failure_kind(exc: FFmpegExecutionError) -> FFmpegFailureKind:
    text = "\n".join([str(exc), exc.combined_output, *exc.output_lines]).lower()
    markers = (
        (
            FFmpegFailureKind.DISK_FULL,
            (
                "no space left on device",
                "disk full",
                "not enough space",
                "there is not enough space on the disk",
            ),
        ),
        (
            FFmpegFailureKind.PERMISSION_DENIED,
            ("permission denied", "access is denied", "operation not permitted"),
        ),
        (
            FFmpegFailureKind.ENCODER_UNAVAILABLE,
            (
                "unknown encoder",
                "encoder (codec",
                "encoder not found",
                "not found for output stream",
                "error selecting an encoder",
            ),
        ),
        (
            FFmpegFailureKind.NO_AUDIO_STREAM,
            (
                "matches no streams",
                "does not contain any stream",
                "audio stream not found",
                "no audio stream",
            ),
        ),
        (
            FFmpegFailureKind.OUTPUT_PATH,
            (
                "no such file or directory",
                "invalid filename",
                "error opening output",
                "could not open output file",
                "unable to open output file",
                "filename too long",
                "file name too long",
            ),
        ),
        (
            FFmpegFailureKind.INVALID_INPUT,
            (
                "invalid data found when processing input",
                "moov atom not found",
                "error opening input",
                "could not find codec parameters",
                "invalid argument",
            ),
        ),
        (
            FFmpegFailureKind.INTERRUPTED_WRITE,
            (
                "broken pipe",
                "input/output error",
                "error writing trailer",
                "error closing file",
                "conversion failed",
            ),
        ),
    )
    for kind, candidates in markers:
        if any(marker in text for marker in candidates):
            return kind
    return FFmpegFailureKind.UNKNOWN


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


def _ffmpeg_output_lines(
    stdout: str,
    stderr: str,
    *,
    limit: int = FFMPEG_OUTPUT_LINE_LIMIT,
) -> list[str]:
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
        r"(?i)(?P<prefix>^|[&;])(?P<name>key|api_key|token|access_token|sig|signature|lsig)(?P<equals>=)(?P<value>[^&;#]*)",
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
        if last in ",;" or last == ":":
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


def _bound_subprocess_output_line(
    line: str,
    limit: int = FFMPEG_OUTPUT_LINE_CHAR_LIMIT,
) -> str:
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
    if lower.startswith("ffmpeg version ") or lower.startswith("built with "):
        return False
    if lower.startswith("configuration:") or lower.startswith("press [q] to stop"):
        return False
    if re.match(
        r"^(libavutil|libavcodec|libavformat|libavdevice|libavfilter|libswscale|libswresample|libpostproc)\s+",
        lower,
    ):
        return False
    if _is_ffmpeg_progress_line(lower):
        return False
    return True


def _is_ffmpeg_progress_line(lower_line: str) -> bool:
    if lower_line.startswith("frame=") and "time=" in lower_line:
        return True
    return bool(
        lower_line.startswith("size=")
        and "time=" in lower_line
        and "bitrate=" in lower_line
    )


def _append_limited(lines: list[str], line: str, limit: int) -> None:
    lines.append(line)
    overflow = len(lines) - limit
    if overflow > 0:
        del lines[:overflow]
