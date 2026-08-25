import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.progress_status import (
    STAGE_PREPARING,
    STAGE_PROMOTING,
    STAGE_RETRY_WAIT,
    STAGE_VALIDATING,
    TRANSFER_SOURCE_YTDLP,
)


def main() -> int:
    _test_stable_stage_calculation()
    _test_fast_failed_attempt_retry_wait_and_success()
    _test_no_merge_and_no_progress_attempts()
    _test_cancelled_summary()
    _test_download_video_emits_one_sanitized_summary()
    _test_download_video_failure_emits_one_summary()
    print("download stage timing smoke passed")
    return 0


def _test_stable_stage_calculation() -> None:
    clock = _FakeClock()
    timing = downloader._VideoDownloadTiming(
        engine=TRANSFER_SOURCE_YTDLP,
        logical_started_at=clock(),
        clock=clock,
    )
    attempt = timing.start_attempt(1, TRANSFER_SOURCE_YTDLP)
    clock.set(2)
    attempt.mark_first_transfer()
    clock.set(12)
    attempt.mark_merge_started()
    clock.set(14)
    attempt.finish(True)
    validation_started = clock()
    clock.set(15)
    timing.add_validation(validation_started)
    promotion_started = clock()
    clock.set(15.5)
    timing.add_promotion(promotion_started)
    timing.finish_logical_download()

    _assert(timing.stage_totals() == (2.0, 10.0, 2.0), f"Stable stage totals were wrong: {timing.stage_totals()}")
    summary = timing.format_summary("success")
    _assert("engine=yt-dlp" in summary and "attempts=1" in summary, "Stable summary identity was wrong")
    _assert("prepare=2.00s transfer=10.00s merge=2.00s" in summary, "Stable transfer stages were wrong")
    _assert("validate=1.00s promote=0.50s" in summary, "Stable final stages were wrong")
    _assert("retry_wait=0.00s total=15.50s result=success" in summary, "Stable total was wrong")


def _test_fast_failed_attempt_retry_wait_and_success() -> None:
    command = ["yt-dlp", "--downloader", "aria2c"]
    error = _code_22_error(command)
    options = downloader.DownloadOptions(
        "",
        "",
        "",
        cookies_enabled=True,
        download_engine=downloader.DOWNLOAD_ENGINE_ARIA2_FAST,
    )
    kind = downloader.classify_ytdlp_failure_kind(error, options)
    _assert(kind == downloader.YtdlpFailureKind.HTTP_403, "code-22 compatibility class changed")
    _assert(
        downloader._should_use_authenticated_infojson_fallback(
            error,
            kind,
            options,
            downloader._PreparedCookieAttempt(command=command, cookies_used=True),
            downloader._YtdlpAttemptState(),
        ),
        "code-22 fallback eligibility changed",
    )

    clock = _FakeClock()
    timing = downloader._VideoDownloadTiming(
        engine=TRANSFER_SOURCE_YTDLP,
        logical_started_at=clock(),
        clock=clock,
    )
    failed = timing.start_attempt(1, TRANSFER_SOURCE_YTDLP)
    clock.set(1)
    failed.mark_first_transfer()
    clock.set(3)
    failed.finish(False)

    events = []
    previous = downloader._set_progress_context(
        events.append,
        SimpleNamespace(title="Timing", sanitized_filename_base="012 Timing"),
        1,
        1,
        "Video",
    )
    try:
        with downloader._video_download_timing_scope(timing), _patched_attr(
            downloader,
            "_sleep_with_cancel",
            lambda seconds, _controller=None: clock.advance(seconds),
        ):
            downloader._wait_for_ytdlp_retry(18, None)
    finally:
        downloader._restore_progress_context(previous)
    _assert(timing.retry_wait_seconds == 18.0, "retry wait was not counted once")
    _assert(clock() == 21.0, "fake retry wait changed duration")
    _assert(any(event.message == STAGE_RETRY_WAIT for event in events), "retry wait stage was not emitted")

    successful = timing.start_attempt(2, TRANSFER_SOURCE_YTDLP)
    clock.set(23)
    successful.mark_first_transfer()
    clock.set(30)
    successful.mark_merge_started()
    clock.set(31)
    successful.finish(True)
    validation_started = clock()
    clock.set(31.2)
    timing.add_validation(validation_started)
    promotion_started = clock()
    clock.set(31.3)
    timing.add_promotion(promotion_started)
    timing.finish_logical_download()

    summary = timing.format_summary("success")
    _assert("engine=yt-dlp part=video attempts=2" in summary, "Fast attempt count was wrong")
    _assert("prepare=3.00s transfer=9.00s merge=1.00s" in summary, "failed-attempt time was lost")
    _assert("retry_wait=18.00s total=31.30s result=success" in summary, "Fast retry total was wrong")
    for forbidden in ("cookies.txt", "SID=", "http://", "https://", "Timing", "\\"):
        _assert(forbidden not in summary, f"PERF summary leaked {forbidden!r}")


def _test_no_merge_and_no_progress_attempts() -> None:
    clock = _FakeClock()
    timing = downloader._VideoDownloadTiming(TRANSFER_SOURCE_YTDLP, clock(), clock=clock)
    no_merge = timing.start_attempt(1, TRANSFER_SOURCE_YTDLP)
    clock.set(1)
    no_merge.mark_first_transfer()
    clock.set(5)
    no_merge.finish(True)
    _assert(no_merge.stage_seconds() == (1.0, 4.0, 0.0), "combined-format timing invented merge time")

    no_progress = timing.start_attempt(2, TRANSFER_SOURCE_YTDLP)
    clock.set(7)
    no_progress.mark_merge_started()
    clock.set(8)
    no_progress.finish(False)
    _assert(no_progress.stage_seconds() == (3.0, 0.0, 1.0), "no-progress attempt timing was wrong")


def _test_cancelled_summary() -> None:
    clock = _FakeClock()
    timing = downloader._VideoDownloadTiming(TRANSFER_SOURCE_YTDLP, clock(), clock=clock)
    attempt = timing.start_attempt(1, TRANSFER_SOURCE_YTDLP)
    clock.set(2)
    attempt.mark_first_transfer()
    clock.set(4)
    attempt.finish(False)
    timing.finish_logical_download()
    summary = timing.format_summary("cancelled")
    _assert("attempts=1" in summary and "result=cancelled" in summary, "cancellation summary was wrong")
    _assert("total=4.00s" in summary and "retry_wait=0.00s" in summary, "cancellation timing was wrong")


def _test_download_video_emits_one_sanitized_summary() -> None:
    logs: list[str] = []
    events = []
    with TemporaryDirectory(prefix="timing_download_") as temp_dir:
        root = Path(temp_dir)
        staged = root / "video.mp4"
        final = root / "final.mp4"
        options = downloader.DownloadOptions(str(root), "channel", "Channel")

        def fake_retry(_command, _options, _log, _controller=None, _state=None):
            timing = downloader._current_video_download_timing()
            _assert(timing is not None, "logical timing context was unavailable")
            attempt = timing.start_attempt(1, TRANSFER_SOURCE_YTDLP)
            attempt.mark_first_transfer()
            attempt.mark_merge_started()
            staged.write_bytes(b"mp4")
            attempt.finish(True)

        previous = downloader._set_progress_context(
            events.append,
            SimpleNamespace(title="Secret title", sanitized_filename_base="012 Secret title"),
            1,
            1,
            "Video",
        )
        try:
            with _patched_attr(
                downloader,
                "_build_video_ytdlp_command",
                lambda *_args, **_kwargs: ["yt-dlp", "video-id"],
            ), _patched_attr(
                downloader,
                "_run_ytdlp_with_retries",
                fake_retry,
            ), _patched_attr(
                downloader,
                "_validate_premiere_safe_mp4_for_download",
                lambda *_args, **_kwargs: None,
            ):
                downloader._download_video(
                    "video-id",
                    "012 Secret title",
                    root,
                    final,
                    options,
                    logs.append,
                )
        finally:
            downloader._restore_progress_context(previous)

    perf_lines = [line for line in logs if line.startswith("[PERF]")]
    _assert(len(perf_lines) == 1, f"logical video emitted {len(perf_lines)} PERF lines")
    _assert("result=success" in perf_lines[0], "success PERF result was wrong")
    _assert("Secret title" not in perf_lines[0] and temp_dir not in perf_lines[0], "PERF line exposed identity/path")
    messages = [event.message for event in events]
    for expected in (STAGE_PREPARING, STAGE_VALIDATING, STAGE_PROMOTING):
        _assert(expected in messages, f"stage event was missing: {expected}")


def _test_download_video_failure_emits_one_summary() -> None:
    logs: list[str] = []
    with TemporaryDirectory(prefix="timing_failure_") as temp_dir:
        root = Path(temp_dir)
        options = downloader.DownloadOptions(str(root), "channel", "Channel")

        def fail_retry(_command, _options, _log, _controller=None, _state=None):
            timing = downloader._current_video_download_timing()
            _assert(timing is not None, "failure timing context was unavailable")
            attempt = timing.start_attempt(1, TRANSFER_SOURCE_YTDLP)
            attempt.mark_first_transfer()
            attempt.finish(False)
            raise downloader.DownloadError("offline failure")

        with _patched_attr(
            downloader,
            "_build_video_ytdlp_command",
            lambda *_args, **_kwargs: ["yt-dlp", "video-id"],
        ), _patched_attr(
            downloader,
            "_run_ytdlp_with_retries",
            fail_retry,
        ):
            try:
                downloader._download_video(
                    "video-id",
                    "012 Failure",
                    root,
                    root / "final.mp4",
                    options,
                    logs.append,
                )
            except downloader.DownloadError as exc:
                _assert(str(exc) == "offline failure", "failure was changed")
            else:
                raise AssertionError("offline failure did not propagate")

    perf_lines = [line for line in logs if line.startswith("[PERF]")]
    _assert(len(perf_lines) == 1, f"failure emitted {len(perf_lines)} PERF lines")
    _assert("attempts=1" in perf_lines[0] and "result=failed" in perf_lines[0], "failure PERF line was wrong")
    _assert("validate=0.00s promote=0.00s" in perf_lines[0], "failure invented final-stage time")


def _code_22_error(command: list[str]) -> downloader.YtdlpExecutionError:
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


class _FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def set(self, value: float) -> None:
        self.value = float(value)

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
