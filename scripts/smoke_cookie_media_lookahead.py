import sys
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.downloader import DownloadOptions


def main() -> int:
    _test_prefetched_metadata_skips_duplicate_extraction_and_wait()
    _test_prefetched_metadata_waits_only_remaining_age()
    _test_lookahead_worker_prepares_and_hands_off_metadata()
    _test_fallback_starts_next_lookahead_before_current_media_transfer()
    _test_aria2_code_22_preserves_age_retry_and_lookahead_reuse()
    _test_download_controller_tracks_parallel_processes()
    print("cookie media lookahead smoke passed")
    return 0


def _test_prefetched_metadata_skips_duplicate_extraction_and_wait() -> None:
    with TemporaryDirectory(prefix="lookahead_reuse_") as temp_dir:
        root = Path(temp_dir)
        options = _options(root)
        prefetch = _prefetch(root, age_seconds=15.0)
        batch_state = downloader._YtdlpBatchState(
            cookie_bootstrap_media_mode=True,
            media_settle_delay_seconds=10,
        )
        lookahead_calls = []
        state = downloader._YtdlpAttemptState(
            batch_state=batch_state,
            prefetched_media=prefetch,
            lookahead_callback=lambda: lookahead_calls.append("started"),
        )
        calls = []
        delays = []
        logs = []
        old_run = downloader._run_ytdlp
        old_sleep = downloader._sleep_with_cancel
        try:
            downloader._run_ytdlp = lambda command, _controller=None: calls.append(list(command)) or ""
            downloader._sleep_with_cancel = lambda seconds, _controller=None: delays.append(int(seconds))
            with _progress_phase("Video"):
                downloader._run_ytdlp_with_retries(
                    _video_command(root, "reuse"),
                    options,
                    logs.append,
                    cookie_retry_state=state,
                )
        finally:
            downloader._run_ytdlp = old_run
            downloader._sleep_with_cancel = old_sleep

        _assert(len(calls) == 1, f"lookahead reuse made unexpected yt-dlp calls: {len(calls)}")
        _assert("--load-info-json" in calls[0], "prefetched info JSON was not reused")
        _assert(not downloader._command_uses_cookies(calls[0]), "media transfer still used cookies")
        _assert(delays == [], f"old metadata still caused a fixed wait: {delays}")
        _assert(lookahead_calls == ["started"], f"next lookahead callback count was wrong: {lookahead_calls}")
        joined = "\n".join(logs)
        _assert("Using one-video lookahead metadata" in joined, "lookahead reuse log missing")
        _assert("starting media transfer immediately" in joined, "immediate media start log missing")


def _test_prefetched_metadata_waits_only_remaining_age() -> None:
    with TemporaryDirectory(prefix="lookahead_remaining_") as temp_dir:
        root = Path(temp_dir)
        options = _options(root)
        prefetch = _prefetch(root, age_seconds=7.1)
        batch_state = downloader._YtdlpBatchState(
            cookie_bootstrap_media_mode=True,
            media_settle_delay_seconds=10,
        )
        state = downloader._YtdlpAttemptState(batch_state=batch_state, prefetched_media=prefetch)
        delays = []
        old_run = downloader._run_ytdlp
        old_sleep = downloader._sleep_with_cancel
        try:
            downloader._run_ytdlp = lambda _command, _controller=None: ""
            downloader._sleep_with_cancel = lambda seconds, _controller=None: delays.append(int(seconds))
            with _progress_phase("Video"):
                downloader._run_ytdlp_with_retries(
                    _video_command(root, "remaining"),
                    options,
                    lambda _message: None,
                    cookie_retry_state=state,
                )
        finally:
            downloader._run_ytdlp = old_run
            downloader._sleep_with_cancel = old_sleep

        _assert(delays == [3], f"lookahead did not wait only the remaining metadata age: {delays}")


def _test_lookahead_worker_prepares_and_hands_off_metadata() -> None:
    with TemporaryDirectory(prefix="lookahead_worker_") as temp_dir:
        root = Path(temp_dir)
        options = _options(root)
        batch_state = downloader._YtdlpBatchState(cookie_bootstrap_media_mode=True)
        logs = []
        old_extract = downloader._extract_authenticated_infojson_path
        try:
            def fake_extract(command, _options, _log, _controller, attempt_number, part, *, log_start):
                _assert(attempt_number == 0, "background extraction used a foreground attempt number")
                _assert(part == downloader.PART_VIDEO, "background extraction used the wrong part")
                _assert(not log_start, "background extraction emitted a foreground yt-dlp start line")
                output_template = Path(downloader._command_option_value(command, "-o"))
                info_dir = output_template.parent / ".s9h-auth-info-test"
                info_dir.mkdir(parents=True, exist_ok=True)
                info_path = info_dir / "authenticated.info.json"
                info_path.write_text("{}", encoding="utf-8")
                return info_path

            downloader._extract_authenticated_infojson_path = fake_extract
            downloader._start_cookie_media_lookahead(
                batch_state,
                "next-video",
                "Next Video",
                root,
                options,
                logs.append,
                None,
            )
            with batch_state.prefetch_lock:
                active = batch_state.prefetch
            _assert(active is not None, "lookahead worker was not registered")
            _assert(active.done.wait(timeout=3), "lookahead worker did not finish")

            taken = downloader._take_cookie_media_lookahead(
                batch_state,
                "next-video",
                options,
                logs.append,
                None,
            )
            _assert(taken is active, "prepared metadata was not handed to the matching video")
            _assert(taken.info_json_path is not None and taken.info_json_path.exists(), "info JSON is missing")
            downloader._cleanup_cookie_media_prefetch(taken, logs.append)
            _assert(not active.staging_dir.exists(), "lookahead staging directory was not cleaned")
        finally:
            downloader._extract_authenticated_infojson_path = old_extract

        joined = "\n".join(logs)
        _assert("Preparing authenticated metadata for next video" in joined, "lookahead start log missing")
        _assert("Authenticated metadata is ready for next video" in joined, "lookahead completion log missing")


def _test_fallback_starts_next_lookahead_before_current_media_transfer() -> None:
    with TemporaryDirectory(prefix="lookahead_callback_") as temp_dir:
        root = Path(temp_dir)
        options = _options(root)
        batch_state = downloader._YtdlpBatchState()
        events = []
        state = downloader._YtdlpAttemptState(
            batch_state=batch_state,
            lookahead_callback=lambda: events.append("lookahead-started"),
        )
        old_run = downloader._run_ytdlp
        try:
            def sequence(command, _controller=None):
                call_number = sum(1 for event in events if event.startswith("yt-dlp-")) + 1
                events.append(f"yt-dlp-{call_number}")
                if call_number == 1:
                    raise _media_403_error()
                if call_number == 2:
                    output_template = Path(downloader._command_option_value(command, "-o"))
                    output_template.parent.mkdir(parents=True, exist_ok=True)
                    info_path = output_template.parent / "authenticated.info.json"
                    info_path.write_text("{}", encoding="utf-8")
                    return ""
                if call_number == 3:
                    return ""
                raise AssertionError(f"unexpected yt-dlp call {call_number}")

            downloader._run_ytdlp = sequence
            with _progress_phase("Video"):
                downloader._run_ytdlp_with_retries(
                    _video_command(root, "callback"),
                    options,
                    lambda _message: None,
                    cookie_retry_state=state,
                )
        finally:
            downloader._run_ytdlp = old_run

        _assert(events.count("lookahead-started") == 1, f"lookahead callback count was wrong: {events}")
        _assert(
            events.index("lookahead-started") < events.index("yt-dlp-3"),
            f"next metadata did not start before current media transfer: {events}",
        )


def _media_403_error() -> downloader.YtdlpExecutionError:
    line = "ERROR: unable to download video data: HTTP Error 403: Forbidden"
    return downloader.YtdlpExecutionError(
        1,
        "yt-dlp failed",
        [line],
        False,
        True,
        False,
        line,
        failure_kind=downloader.YtdlpFailureKind.HTTP_403,
        fatal_lines=[line],
        http_status=403,
        stage=downloader.YTDLP_STAGE_DOWNLOAD,
        part=downloader.PART_VIDEO,
    )


def _test_aria2_code_22_preserves_age_retry_and_lookahead_reuse() -> None:
    with TemporaryDirectory(prefix="lookahead_aria2_code22_") as temp_dir:
        root = Path(temp_dir)
        options = _options(root)
        batch_state = downloader._YtdlpBatchState()
        lookahead_calls: list[str] = []
        first_state = downloader._YtdlpAttemptState(
            batch_state=batch_state,
            lookahead_callback=lambda: lookahead_calls.append("started"),
        )
        calls: list[list[str]] = []
        delays: list[int] = []
        logs: list[str] = []

        def sequence(command, _controller=None):
            calls.append(list(command))
            if len(calls) == 1:
                raise _aria2_code_22_error(command)
            if len(calls) == 2:
                output_template = Path(downloader._command_option_value(command, "-o"))
                output_template.parent.mkdir(parents=True, exist_ok=True)
                info_path = output_template.parent / "authenticated.info.json"
                info_path.write_text("{}", encoding="utf-8")
                return ""
            if len(calls) == 3:
                raise _aria2_code_22_error(command)
            if len(calls) in {4, 5}:
                return ""
            raise AssertionError(f"unexpected aria2 lookahead call {len(calls)}")

        old_run = downloader._run_ytdlp
        old_sleep = downloader._sleep_with_cancel
        try:
            downloader._run_ytdlp = sequence
            downloader._sleep_with_cancel = lambda seconds, _controller=None: delays.append(int(seconds))
            with _progress_phase("Video"):
                downloader._run_ytdlp_with_retries(
                    _aria2_video_command(root, "first-code22"),
                    options,
                    logs.append,
                    cookie_retry_state=first_state,
                )

            expected_target = downloader.COOKIE_MEDIA_RETRY_TARGET_SECONDS[0]
            prefetched = _prefetch(root, age_seconds=float(expected_target) + 0.2)
            following_state = downloader._YtdlpAttemptState(
                batch_state=batch_state,
                prefetched_media=prefetched,
            )
            with _progress_phase("Video"):
                downloader._run_ytdlp_with_retries(
                    _aria2_video_command(root, "following-video"),
                    options,
                    logs.append,
                    cookie_retry_state=following_state,
                )
        finally:
            downloader._run_ytdlp = old_run
            downloader._sleep_with_cancel = old_sleep

        expected_target = downloader.COOKIE_MEDIA_RETRY_TARGET_SECONDS[0]
        _assert(delays == [expected_target], f"aria2 metadata-age sequence changed: {delays}")
        _assert(lookahead_calls == ["started"], f"aria2 fallback lost lookahead: {lookahead_calls}")
        _assert(batch_state.cookie_bootstrap_media_mode, "aria2 fallback did not enable batch mode")
        _assert(len(calls) == 5, f"aria2 lookahead sequence call count was wrong: {len(calls)}")
        _assert("--load-info-json" in calls[4], "following video did not reuse prefetched metadata")
        _assert(downloader._command_uses_aria2(calls[4]), "prefetched Fast transfer lost aria2")
        _assert(not downloader._command_uses_cookies(calls[4]), "prefetched transfer restored cookies")
        _assert(
            "aria2 HTTP response failure during cookieless media transfer" in "\n".join(logs),
            "aria2 metadata-age retry log missing",
        )


def _aria2_code_22_error(command: list[str]) -> downloader.YtdlpExecutionError:
    line = "ERROR: aria2c exited with code 22"
    return downloader.YtdlpExecutionError(
        1,
        "nonzero yt-dlp exit code",
        [line],
        combined_output=line,
        failure_kind=downloader.YtdlpFailureKind.UNKNOWN,
        fatal_lines=[line],
        stage=downloader.YTDLP_STAGE_DOWNLOAD,
        part=downloader.PART_VIDEO,
        command=command,
    )


def _test_download_controller_tracks_parallel_processes() -> None:
    class FakeProcess:
        def __init__(self):
            self.running = True

        def poll(self):
            return None if self.running else 0

    controller = downloader.DownloadController()
    first = FakeProcess()
    second = FakeProcess()
    controller.set_current_process(first)
    controller.set_current_process(second)
    _assert(controller.has_active_process(), "parallel processes were not tracked")
    controller.clear_current_process(second)
    _assert(controller.has_active_process(), "clearing one process lost the other active process")
    first.running = False
    controller.clear_current_process(first)
    _assert(not controller.has_active_process(), "controller remained active after both processes ended")


def _options(root: Path) -> DownloadOptions:
    cookie_path = root / "cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    return DownloadOptions(
        str(root),
        "channel",
        "Channel",
        cookies_enabled=True,
        cookies_path=str(cookie_path),
    )


def _prefetch(root: Path, age_seconds: float) -> downloader._CookieMediaPrefetch:
    staging = root / "prefetch"
    staging.mkdir(exist_ok=True)
    info_path = staging / "authenticated.info.json"
    info_path.write_text("{}", encoding="utf-8")
    item = downloader._CookieMediaPrefetch("video", "Video", staging)
    item.info_json_path = info_path
    item.ready_monotonic = time.monotonic() - age_seconds
    item.done.set()
    return item


def _video_command(root: Path, video_id: str) -> list[str]:
    return [
        "yt-dlp",
        "-N",
        "1",
        "--no-write-info-json",
        "-o",
        str(root / "output" / "video.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ]


def _aria2_video_command(root: Path, video_id: str) -> list[str]:
    command = _video_command(root, video_id)
    command[1:1] = [
        "--downloader",
        str(root / "data" / "bin" / "aria2c.exe"),
        "--downloader-args",
        downloader.ARIA2_FAST_DOWNLOADER_ARGS,
    ]
    return command


@contextmanager
def _progress_phase(phase: str):
    previous = getattr(downloader._PROGRESS_CONTEXT, "current", None)
    downloader._PROGRESS_CONTEXT.current = SimpleNamespace(phase=phase)
    try:
        yield
    finally:
        downloader._PROGRESS_CONTEXT.current = previous


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
