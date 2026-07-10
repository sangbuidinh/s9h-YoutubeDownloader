import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.progress_status import (
    TRANSFER_SOURCE_ARIA2,
    TRANSFER_SOURCE_YTDLP,
    ParsedTransferProgress,
    ProgressEvent,
    format_progress_event_lines,
    parse_aria2_progress,
)
from ui.main_window import YouTubeDownloaderWindow


def main() -> int:
    _test_parser_variants()
    _test_display_source_and_hidden_diagnostics()
    _test_command_source_detection()
    _test_real_reader_bridges_fake_aria2_output()
    _test_throttling_and_forced_completion()
    _test_preparing_does_not_overwrite_transfer()
    _test_stale_attempt_and_video_guards()
    _test_failure_evidence_filtering()
    print("aria2 progress bridge smoke passed")
    return 0


def _test_parser_variants() -> None:
    plain = _parsed("[#3fd53a 35MiB/36MiB(97%) CN:1 DL:36MiB]")
    _assert(plain.percent == 97.0, "plain aria2 percent was wrong")
    _assert(plain.speed_text == "36MiB/s", "plain aria2 speed was wrong")
    _assert(plain.connection_count == 1, "connection count was not retained")
    _assert(plain.eta_text is None, "missing ETA did not remain absent")

    ansi = _parsed("\x1b[32m[#abc 10MiB/20MiB(50%) CN:4 DL:5MiB]\x1b[0m\r")
    _assert(ansi.percent == 50.0 and ansi.speed_text == "5MiB/s", "ANSI progress did not parse")

    repeated = _parsed(
        "[#abc 10MiB/100MiB(10%) CN:4 DL:2MiB]\r"
        "[#abc 20MiB/100MiB(20%) CN:4 DL:4MiB]\r"
        "[#abc 30MiB/100MiB(30%) CN:4 DL:6MiB]"
    )
    _assert(repeated.percent == 30.0, "final carriage-return snapshot was not selected")
    _assert(repeated.speed_text == "6MiB/s", "final carriage-return speed was wrong")

    decimal = _parsed("[#ABC123 1.5GiB/2GiB(75.5%) CN:16 DL:42.2MiB ETA:12s]")
    _assert(decimal.percent == 75.5, "decimal percentage was wrong")
    _assert(decimal.eta_text == "12s", "diagnostic ETA was not retained")
    _assert(decimal.connection_count == 16, "diagnostic connection count was wrong")

    no_speed = _parsed("[#abc 1MiB/2MiB(50%) CN:2]")
    _assert(no_speed.speed_text is None, "missing speed was invented")
    no_connections = _parsed("[#abc 1MiB/2MiB(50%) DL:4.2MiB/s]")
    _assert(no_connections.connection_count is None, "missing connection count was invented")
    _assert(no_connections.speed_text == "4.2MiB/s", "existing /s suffix was duplicated")
    clamped = _parsed("[#abc 2MiB/2MiB(120%) DL:0]")
    _assert(clamped.percent == 100.0, "percentage was not clamped")
    _assert(clamped.speed_text is None, "zero speed was displayed")

    for text in (
        "ERROR: aria2c exited with code 22",
        "[download] 50% of 100MiB",
        "random text containing 22",
    ):
        _assert(parse_aria2_progress(text) is None, f"unrelated text parsed as aria2: {text}")


def _test_display_source_and_hidden_diagnostics() -> None:
    _current, stable = format_progress_event_lines(
        ProgressEvent(
            phase="Video",
            percent="3.6%",
            speed="47.72MiB/s",
            source=TRANSFER_SOURCE_YTDLP,
        )
    )
    _assert(stable == "Processing: 3.6% | yt-dlp 47.72MiB/s", f"Stable display changed: {stable}")

    _current, fast = format_progress_event_lines(
        ProgressEvent(
            phase="Video",
            percent="97.0%",
            speed="36MiB/s",
            source=TRANSFER_SOURCE_ARIA2,
            eta="21s",
        )
    )
    _assert(fast == "Processing: 97.0% | aria2c 36MiB/s", f"Fast display was wrong: {fast}")
    _assert("ETA" not in fast and "21s" not in fast and "CN:" not in fast, "diagnostics leaked into UI")

    _current, no_speed = format_progress_event_lines(
        ProgressEvent(phase="Video", percent="97.0%", source=TRANSFER_SOURCE_ARIA2)
    )
    _assert(no_speed == "Processing: 97.0% | aria2c", "missing-speed display was wrong")
    _current, speed_only = format_progress_event_lines(
        ProgressEvent(phase="Video", speed="3.9MiB/s", source=TRANSFER_SOURCE_ARIA2)
    )
    _assert(speed_only == "Processing: aria2c 3.9MiB/s", "speed-only display was wrong")


def _test_command_source_detection() -> None:
    fast = ["yt-dlp", "--downloader", "aria2c", "video-id"]
    stable = ["yt-dlp", "-N", "1", "video-id"]
    _assert(
        downloader._transfer_source_for_command(fast) == TRANSFER_SOURCE_ARIA2,
        "Fast command did not select aria2 source",
    )
    _assert(
        downloader._transfer_source_for_command(stable) == TRANSFER_SOURCE_YTDLP,
        "Stable command did not select yt-dlp source",
    )


def _test_real_reader_bridges_fake_aria2_output() -> None:
    events: list[ProgressEvent] = []
    video = SimpleNamespace(title="Fake video", sanitized_filename_base="012 Fake video")
    code = (
        "print('[#abc 3MiB/100MiB(3%) CN:4 DL:8MiB]', flush=True)\n"
        "print('[#abc 98MiB/100MiB(98%) CN:4 DL:3.9MiB ETA:1s]', flush=True)\n"
        "print('[download] 100.0% of 100MiB in 00:00:10 at 3.5MiB/s', flush=True)\n"
    )
    previous = downloader._set_progress_context(events.append, video, 1, 1, "Video")
    try:
        downloader._run_ytdlp(
            [sys.executable, "-u", "-c", code, "--downloader", "aria2c"],
        )
    finally:
        downloader._restore_progress_context(previous)

    transfer_events = [event for event in events if event.percent]
    _assert(any(event.percent == "3.0%" for event in transfer_events), "3% aria2 event was not bridged")
    _assert(any(event.percent == "98.0%" for event in transfer_events), "98% aria2 event was not bridged")
    _assert(transfer_events[-1].percent == "100.0%", "wrapper 100% was not emitted")
    _assert(
        all(event.source == TRANSFER_SOURCE_ARIA2 for event in transfer_events),
        "Fast wrapper progress was relabeled as yt-dlp",
    )


def _test_throttling_and_forced_completion() -> None:
    events: list[ProgressEvent] = []
    video = SimpleNamespace(title="Throttle", sanitized_filename_base="012 Throttle")
    clock = _FakeClock()
    previous = downloader._set_progress_context(events.append, video, 1, 1, "Video")
    with _patched_attr(downloader.time, "monotonic", clock):
        try:
            downloader._start_progress_attempt(TRANSFER_SOURCE_ARIA2)
            events.clear()
            downloader._emit_aria2_progress(_progress(10.0, "2MiB/s"))
            clock.advance(0.1)
            downloader._emit_aria2_progress(_progress(10.0, "2MiB/s"))
            clock.advance(0.1)
            downloader._emit_aria2_progress(_progress(10.0, "3MiB/s"))
            clock.advance(0.1)
            downloader._emit_aria2_progress(_progress(10.0, "3MiB/s"))
            downloader._emit_aria2_progress(_progress(10.1, "3MiB/s"))
            downloader._emit_aria2_progress(_progress(100.0, "3MiB/s"))
        finally:
            downloader._restore_progress_context(previous)

    _assert([event.percent for event in events] == ["10.0%", "10.0%", "10.1%", "100.0%"], "throttle decisions were wrong")


def _test_preparing_does_not_overwrite_transfer() -> None:
    events: list[ProgressEvent] = []
    previous = downloader._set_progress_context(
        events.append,
        SimpleNamespace(title="Guard", sanitized_filename_base="012 Guard"),
        1,
        1,
        "Video",
    )
    try:
        downloader._start_progress_attempt(TRANSFER_SOURCE_ARIA2)
        downloader._emit_aria2_progress(_progress(37.5, "14.2MiB/s"))
        count_after_transfer = len(events)
        downloader._emit_ytdlp_progress_from_line(
            "[download] Destination: hidden-output.mp4",
            TRANSFER_SOURCE_ARIA2,
        )
    finally:
        downloader._restore_progress_context(previous)
    _assert(len(events) == count_after_transfer, "delayed preparation overwrote live aria2 progress")
    _assert(events[-1].percent == "37.5%", "live aria2 progress was not retained")


def _test_stale_attempt_and_video_guards() -> None:
    window = YouTubeDownloaderWindow.__new__(YouTubeDownloaderWindow)
    window._reset_progress_sticky(reset_order=True)
    current = window._merge_progress_event_for_display(
        ProgressEvent(video_index=1, video_total=2, phase="Video", title="A", percent="60.0%", generation=2)
    )
    stale_attempt = window._merge_progress_event_for_display(
        ProgressEvent(video_index=1, video_total=2, phase="Video", title="A", percent="20.0%", generation=1)
    )
    _assert(stale_attempt == current, "stale attempt overwrote newer progress")

    next_video = window._merge_progress_event_for_display(
        ProgressEvent(video_index=2, video_total=2, phase="Video", title="B", percent="3.0%", generation=1)
    )
    stale_video = window._merge_progress_event_for_display(
        ProgressEvent(video_index=1, video_total=2, phase="Video", title="A", percent="100.0%", generation=2)
    )
    _assert(stale_video == next_video, "stale video overwrote the next video")


def _test_failure_evidence_filtering() -> None:
    lines = [
        "ERROR: aria2c exited with code 22",
        "[#3fd53a 35MiB/36MiB(97%) CN:1 DL:36MiB]",
        "[#3fd53a 35MiB/36MiB(98%) CN:1 DL:32KiB ETA:21s]",
        "[download] 100% of 36.25MiB in 00:00:10 at 3.50MiB/s",
    ]
    command = ["yt-dlp", "--downloader", "aria2c"]
    error = downloader.YtdlpExecutionError(
        1,
        "nonzero yt-dlp exit code",
        lines,
        combined_output="\n".join(lines),
        failure_kind=downloader.YtdlpFailureKind.UNKNOWN,
        fatal_lines=lines,
        stage=downloader.YTDLP_STAGE_DOWNLOAD,
        part=downloader.PART_VIDEO,
        command=command,
    )
    _assert(lines[0] in error.fatal_lines, "code-22 fatal evidence was lost")
    _assert(
        all("[#3fd53a" not in line for line in error.fatal_lines),
        f"aria2 snapshots polluted fatal evidence: {error.fatal_lines}",
    )
    _assert("[#3fd53a" in error.combined_output, "private combined output lost progress snapshots")

    options = downloader.DownloadOptions("", "", "", download_engine=downloader.DOWNLOAD_ENGINE_ARIA2_FAST)
    kind = downloader.classify_ytdlp_failure_kind(error, options)
    _assert(kind == downloader.YtdlpFailureKind.HTTP_403, "code-22 compatibility class changed")
    _assert(downloader._is_aria2_http_response_media_failure(error), "code-22 detail detection changed")
    _assert(
        downloader._should_use_authenticated_infojson_fallback(
            error,
            kind,
            downloader.DownloadOptions("", "", "", cookies_enabled=True),
            downloader._PreparedCookieAttempt(command=command, cookies_used=True),
            downloader._YtdlpAttemptState(),
        ),
        "code-22 fallback eligibility changed",
    )
    logs: list[str] = []
    downloader._log_ytdlp_attempt_failure(logs.append, error, kind, 1)
    if downloader._is_aria2_http_response_media_failure(error):
        logs.append("[YT-DLP CLASS DETAIL] aria2_http_response_exit_22")
    joined = "\n".join(logs)
    _assert("aria2_http_response_exit_22" in joined, "code-22 detail log was lost")
    _assert("[#3fd53a" not in joined, "aria2 progress was logged as fatal evidence")

    mixed_fragment = f"{lines[0]}\r{lines[1]}"
    _assert(
        not downloader._is_aria2_progress_line(mixed_fragment),
        "mixed error/progress fragment was incorrectly treated as pure progress",
    )
    mixed_error = downloader.YtdlpExecutionError(
        1,
        "nonzero yt-dlp exit code",
        [mixed_fragment],
        combined_output=mixed_fragment,
        fatal_lines=[mixed_fragment],
        stage=downloader.YTDLP_STAGE_DOWNLOAD,
        part=downloader.PART_VIDEO,
        command=command,
    )
    _assert("code 22" in mixed_error.fatal_lines[0], "mixed fragment lost code-22 evidence")


def _parsed(text: str) -> ParsedTransferProgress:
    parsed = parse_aria2_progress(text)
    if parsed is None:
        raise AssertionError(f"aria2 progress did not parse: {text!r}")
    return parsed


def _progress(percent: float, speed: str | None) -> ParsedTransferProgress:
    return ParsedTransferProgress(
        source=TRANSFER_SOURCE_ARIA2,
        percent=percent,
        speed_text=speed,
    )


class _FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


@contextmanager
def _patched_attr(target, name: str, value):
    previous = getattr(target, name)
    setattr(target, name, value)
    try:
        yield
    finally:
        setattr(target, name, previous)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
