import sys
import queue
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.progress_status import (
    ProgressEvent,
    format_progress_event_lines,
    parse_ytdlp_progress_line,
    put_latest_progress_event,
)
from ui.main_window import YouTubeDownloaderWindow


def main() -> int:
    _test_percent_speed_eta()
    _test_fragment_progress()
    _test_fragment_display_omits_eta()
    _test_unknown_line_is_ignored()
    _test_secret_text_is_not_displayed_raw()
    _test_speed_is_labeled_as_ytdlp_speed()
    _test_ffmpeg_progress_uses_speed_label()
    _test_ffmpeg_progress_localizes_to_requested_line()
    _test_video_event_formats_as_two_lines()
    _test_thumbnail_event_formats_as_two_lines()
    _test_mp3_event_formats_as_two_lines()
    _test_batch_completed_formats_as_two_lines()
    _test_stop_requested_formats_as_two_lines()
    _test_sticky_full_event_displays_full_detail()
    _test_sticky_partial_event_keeps_same_item_speed()
    _test_sticky_new_video_resets_speed()
    _test_sticky_new_phase_resets_speed()
    _test_sticky_terminal_events_reset_speed()
    _test_progress_queue_put_latest_does_not_block()
    print("progress status smoke tests passed")
    return 0


def _test_percent_speed_eta() -> None:
    parsed = parse_ytdlp_progress_line("[download]  45.2% of 123.45MiB at 3.80MiB/s ETA 00:01:24")
    _assert(parsed is not None, "percent progress did not parse")
    _assert(parsed.get("percent") == "45.2%", "percent was wrong")
    _assert(parsed.get("speed") == "3.80MiB/s", "speed was wrong")
    _assert(parsed.get("eta") == "00:01:24", "eta was wrong")


def _test_fragment_progress() -> None:
    parsed = parse_ytdlp_progress_line("[download] Downloading fragment 12 of 200")
    _assert(parsed is not None, "fragment progress did not parse")
    _assert(parsed.get("fragment") == "12/200", "fragment count was wrong")


def _test_fragment_display_omits_eta() -> None:
    _current_line, detail_line = format_progress_event_lines(
        ProgressEvent(phase="Video", fragment="12/200", speed="1.91MiB/s", eta="00:40")
    )
    _assert("Fragment 12/200" in detail_line, "fragment display missing")
    _assert("yt-dlp 1.91MiB/s" in detail_line, "fragment speed missing")
    _assert("ETA" not in detail_line, "fragment display included ETA label")
    _assert("00:40" not in detail_line, "fragment display included ETA value")


def _test_unknown_line_is_ignored() -> None:
    parsed = parse_ytdlp_progress_line("unexpected yt-dlp output that should not crash")
    _assert(parsed is None, "unknown line should not produce progress")


def _test_secret_text_is_not_displayed_raw() -> None:
    api_key = "AIza12345678901234567890123456789012345"
    current_line, detail_line = format_progress_event_lines(
        ProgressEvent(
            phase="Error",
            message=(
                f"Cookie: SID=secret-value Authorization: Bearer abc123 key={api_key} "
                "SAPISID=sapisid-secret __Secure-1PSID=secure-secret LOGIN_INFO=login-secret"
            ),
            video_index=1,
            video_total=2,
            title=f"title {api_key} VISITOR_INFO1_LIVE=visitor-secret",
        )
    )
    text = f"{current_line}\n{detail_line}"
    for secret in (
        api_key,
        "secret-value",
        "abc123",
        "sapisid-secret",
        "secure-secret",
        "login-secret",
        "visitor-secret",
    ):
        _assert(secret not in text, f"secret was displayed raw: {secret}")


def _test_speed_is_labeled_as_ytdlp_speed() -> None:
    _current_line, detail_line = format_progress_event_lines(
        ProgressEvent(phase="Video", speed="10.4MiB/s", percent="45.2%")
    )
    _assert("yt-dlp 10.4MiB/s" in detail_line, "yt-dlp speed label was missing")


def _test_ffmpeg_progress_uses_speed_label() -> None:
    _current_line, detail_line = format_progress_event_lines(
        ProgressEvent(
            kind="ffmpeg_progress",
            phase="FFmpeg",
            title="001 Title",
            percent="43%",
            speed="1.18x",
        )
    )
    _assert(
        detail_line == "Processing: 001 Title.mp4 | 43% | speed 1.18x",
        f"FFmpeg progress detail line was wrong: {detail_line}",
    )
    _assert("yt-dlp" not in detail_line, "FFmpeg progress used yt-dlp speed label")


def _test_ffmpeg_progress_localizes_to_requested_line() -> None:
    window = _progress_window()
    _current_line, detail_line = window._localized_progress_lines(
        ProgressEvent(
            kind="ffmpeg_progress",
            phase="FFmpeg",
            title="001 Title",
            percent="43%",
            speed="1.18x",
        )
    )
    _assert(
        detail_line == "Đang xử lý: 001 Title.mp4 | 43% | speed 1.18x",
        f"localized FFmpeg progress line was wrong: {detail_line}",
    )


def _test_video_event_formats_as_two_lines() -> None:
    current_line, detail_line = format_progress_event_lines(
        ProgressEvent(phase="Video", title="abc", percent="45.2%", speed="10.4MiB/s", eta="00:01:24")
    )
    _assert("Downloading: Video" in current_line, "video current line was wrong")
    _assert(".mp4" in current_line, "video filename extension missing")
    _assert("45.2%" in detail_line, "video percent missing")
    _assert("yt-dlp 10.4MiB/s" in detail_line, "video speed missing")
    _assert("ETA" not in detail_line, "video ETA label should not be displayed")
    _assert("00:01:24" not in detail_line, "video ETA value should not be displayed")


def _test_thumbnail_event_formats_as_two_lines() -> None:
    current_line, detail_line = format_progress_event_lines(
        ProgressEvent(phase="Thumbnail", title="abc", message="Downloading image")
    )
    _assert("Downloading: Thumbnail" in current_line, "thumbnail current line was wrong")
    _assert(".jpg" in current_line, "thumbnail filename extension missing")
    _assert("Downloading image" in detail_line, "thumbnail detail missing")


def _test_mp3_event_formats_as_two_lines() -> None:
    current_line, detail_line = format_progress_event_lines(
        ProgressEvent(phase="MP3", title="abc", message="Extracting audio from MP4")
    )
    _assert("Downloading: MP3" in current_line, "MP3 current line was wrong")
    _assert(".mp3" in current_line, "MP3 filename extension missing")
    _assert("Extracting audio" in detail_line, "MP3 detail missing")


def _test_batch_completed_formats_as_two_lines() -> None:
    current_line, detail_line = format_progress_event_lines(ProgressEvent(kind="batch_complete"))
    _assert(current_line == "Downloading: Batch completed", "batch completed current line was wrong")
    _assert(detail_line == "Processing: -", "batch completed detail line was wrong")


def _test_stop_requested_formats_as_two_lines() -> None:
    current_line, detail_line = format_progress_event_lines(ProgressEvent(kind="stop_requested"))
    _assert(current_line == "Downloading: Stop requested", "stop requested current line was wrong")
    _assert(detail_line == "Processing: Cancelling current process...", "stop requested detail line was wrong")


def _test_sticky_full_event_displays_full_detail() -> None:
    window = _progress_window()
    _current_line, detail_line = _sticky_lines(
        window,
        ProgressEvent(phase="Video", percent="7.4%", speed="1.91MiB/s", eta="00:40"),
    )
    _assert("7.4%" in detail_line, "sticky full event percent missing")
    _assert("yt-dlp 1.91MiB/s" in detail_line, "sticky full event speed missing")
    _assert("ETA" not in detail_line, "sticky full event displayed ETA label")
    _assert("00:40" not in detail_line, "sticky full event displayed ETA value")


def _test_sticky_partial_event_keeps_same_item_speed() -> None:
    window = _progress_window()
    _sticky_lines(
        window,
        ProgressEvent(
            video_index=1,
            phase="Video",
            title="A",
            percent="7.4%",
            speed="1.91MiB/s",
            eta="00:40",
        ),
    )
    _current_line, detail_line = _sticky_lines(
        window,
        ProgressEvent(video_index=1, phase="Video", title="A", percent="49.4%"),
    )
    _assert("49.4%" in detail_line, "sticky partial event did not update percent")
    _assert("yt-dlp 1.91MiB/s" in detail_line, "sticky partial event dropped speed")
    _assert("ETA" not in detail_line, "sticky partial event displayed ETA label")
    _assert("00:40" not in detail_line, "sticky partial event displayed ETA value")


def _test_sticky_new_video_resets_speed() -> None:
    window = _progress_window()
    _sticky_lines(
        window,
        ProgressEvent(
            video_index=1,
            phase="Video",
            title="A",
            percent="7.4%",
            speed="1.91MiB/s",
            eta="00:40",
        ),
    )
    _current_line, detail_line = _sticky_lines(
        window,
        ProgressEvent(video_index=2, phase="Video", title="B", percent="1.0%"),
    )
    _assert("1.0%" in detail_line, "new video percent missing")
    _assert("1.91MiB/s" not in detail_line, "new video reused old speed")
    _assert("ETA" not in detail_line, "new video displayed ETA label")
    _assert("00:40" not in detail_line, "new video displayed old ETA value")


def _test_sticky_new_phase_resets_speed() -> None:
    window = _progress_window()
    _sticky_lines(
        window,
        ProgressEvent(
            video_index=1,
            phase="Video",
            title="A",
            percent="7.4%",
            speed="1.91MiB/s",
            eta="00:40",
        ),
    )
    _current_line, detail_line = _sticky_lines(
        window,
        ProgressEvent(video_index=1, phase="Thumbnail", title="A", message="Downloading image"),
    )
    _assert("Downloading image" in detail_line, "new phase message missing")
    _assert("1.91MiB/s" not in detail_line, "new phase reused old speed")
    _assert("ETA" not in detail_line, "new phase displayed ETA label")
    _assert("00:40" not in detail_line, "new phase displayed old ETA value")


def _test_sticky_terminal_events_reset_speed() -> None:
    for event in (
        ProgressEvent(kind="error", phase="Error", title="A", message="Bot-check, cookies required"),
        ProgressEvent(kind="stop_requested"),
        ProgressEvent(kind="batch_complete"),
    ):
        window = _progress_window()
        _sticky_lines(
            window,
            ProgressEvent(
                video_index=1,
                phase="Video",
                title="A",
                percent="7.4%",
                speed="1.91MiB/s",
                eta="00:40",
            ),
        )
        _current_line, detail_line = _sticky_lines(window, event)
        _assert("1.91MiB/s" not in detail_line, f"{event.kind} reused old speed")
        _assert("ETA" not in detail_line, f"{event.kind} displayed ETA label")
        _assert("00:40" not in detail_line, f"{event.kind} displayed old ETA value")


def _test_progress_queue_put_latest_does_not_block() -> None:
    progress_queue = queue.Queue(maxsize=1)
    put_latest_progress_event(progress_queue, ProgressEvent(phase="Video", message="old"))
    put_latest_progress_event(progress_queue, ProgressEvent(phase="Video", message="latest"))
    event = progress_queue.get_nowait()
    _assert(event.message == "latest", "progress queue did not keep latest event")


def _progress_window():
    window = YouTubeDownloaderWindow.__new__(YouTubeDownloaderWindow)
    window._reset_progress_sticky()
    return window


def _sticky_lines(window, event: ProgressEvent) -> tuple[str, str]:
    merged = window._merge_progress_event_for_display(event)
    return format_progress_event_lines(merged)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
