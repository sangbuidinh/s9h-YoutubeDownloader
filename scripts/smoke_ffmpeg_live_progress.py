import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(REPO_ROOT),
    )


from core import downloader
from core.downloader import (
    DownloadCancelled,
    DownloadController,
    FFmpegExecutionError,
)


def main() -> int:
    _test_progress_parsers()
    _test_streamed_progress_events()
    _test_completion_reaches_100_percent()
    _test_stderr_is_preserved_on_failure()
    _test_streamed_runner_cancels_quickly()

    print(
        "ffmpeg live progress smoke tests passed"
    )

    return 0


def _test_progress_parsers() -> None:
    _assert(
        downloader._parse_ffmpeg_progress_line(
            "out_time_us=43000000"
        )
        == (
            "out_time_us",
            "43000000",
        ),
        "out_time_us did not parse",
    )

    _assert(
        downloader._ffmpeg_out_time_seconds(
            "out_time_us",
            "43000000",
        )
        == 43.0,
        "out_time_us conversion was wrong",
    )

    _assert(
        downloader._ffmpeg_out_time_seconds(
            "out_time_ms",
            "43000000",
        )
        == 43.0,
        "out_time_ms conversion was wrong",
    )

    _assert(
        downloader._ffmpeg_out_time_seconds(
            "out_time",
            "00:00:43.000000",
        )
        == 43.0,
        "out_time timestamp conversion was wrong",
    )

    _assert(
        downloader._normalize_ffmpeg_speed(
            "1.18x"
        )
        == "1.18x",
        "FFmpeg speed normalization was wrong",
    )

    _assert(
        downloader._normalize_ffmpeg_speed(
            "N/A"
        )
        == "--",
        "unknown speed was not normalized",
    )

    _assert(
        downloader._ffmpeg_progress_percent(
            43.0,
            100.0,
        )
        == 43,
        "FFmpeg percent calculation was wrong",
    )

    _assert(
        downloader._ffmpeg_progress_percent(
            100.0,
            100.0,
        )
        == 99,
        (
            "running FFmpeg progress "
            "was allowed to reach 100"
        ),
    )

    _assert(
        downloader._ffmpeg_progress_percent(
            100.0,
            100.0,
            completed=True,
        )
        == 100,
        (
            "completed FFmpeg progress "
            "did not reach 100"
        ),
    )


def _test_streamed_progress_events() -> None:
    events = []

    video = SimpleNamespace(
        title="Title",
        sanitized_filename_base=(
            "001 Title"
        ),
    )

    previous = (
        downloader._set_progress_context(
            events.append,
            video,
            1,
            2,
            "FFmpeg",
        )
    )

    command = [
        sys.executable,
        "-u",
        "-c",
        (
            "import sys, time\n"
            "print("
            "'out_time_us=43000000', "
            "flush=True"
            ")\n"
            "print("
            "'speed=1.18x', "
            "flush=True"
            ")\n"
            "print("
            "'progress=continue', "
            "flush=True"
            ")\n"
            "time.sleep(0.05)\n"
            "print("
            "'out_time_us=99000000', "
            "flush=True"
            ")\n"
            "print("
            "'speed=1.20x', "
            "flush=True"
            ")\n"
            "print("
            "'progress=end', "
            "flush=True"
            ")\n"
        ),
    ]

    try:
        downloader._run_ffmpeg_command(
            command,
            operation="generic_ffmpeg_progress",
            progress_duration_seconds=100.0,
        )
    finally:
        downloader._restore_progress_context(
            previous
        )

    ffmpeg_events = [
        event
        for event in events
        if event.kind
        == "ffmpeg_progress"
    ]

    _assert(
        ffmpeg_events,
        "no FFmpeg progress event was emitted",
    )

    _assert(
        any(
            event.percent == "43%"
            and event.speed == "1.18x"
            for event in ffmpeg_events
        ),
        (
            "43 percent / 1.18x "
            "event was not emitted"
        ),
    )


def _test_completion_reaches_100_percent() -> None:
    events = []

    video = SimpleNamespace(
        title="Title",
        sanitized_filename_base=(
            "001 Title"
        ),
    )

    previous = (
        downloader._set_progress_context(
            events.append,
            video,
            1,
            1,
            "FFmpeg",
        )
    )

    command = [
        sys.executable,
        "-u",
        "-c",
        (
            "print("
            "'out_time_us=50000000', "
            "flush=True"
            ")\n"
            "print("
            "'speed=1.18x', "
            "flush=True"
            ")\n"
            "print("
            "'progress=end', "
            "flush=True"
            ")\n"
        ),
    ]

    try:
        downloader._run_ffmpeg_command(
            command,
            operation="generic_ffmpeg_progress",
            progress_duration_seconds=100.0,
        )
    finally:
        downloader._restore_progress_context(
            previous
        )

    ffmpeg_events = [
        event
        for event in events
        if event.kind
        == "ffmpeg_progress"
    ]

    _assert(
        ffmpeg_events[-1].percent == "100%",
        "final FFmpeg percent was not 100%",
    )

    _assert(
        ffmpeg_events[-1].speed == "1.18x",
        (
            "final FFmpeg speed "
            "was not preserved"
        ),
    )


def _test_stderr_is_preserved_on_failure() -> None:
    command = [
        sys.executable,
        "-u",
        "-c",
        (
            "import sys\n"
            "print("
            "'out_time_us=1000000', "
            "flush=True"
            ")\n"
            "print("
            "'progress=continue', "
            "flush=True"
            ")\n"
            "print("
            "'encoder failed', "
            "file=sys.stderr, "
            "flush=True"
            ")\n"
            "raise SystemExit(7)\n"
        ),
    ]

    try:
        downloader._run_ffmpeg_command(
            command,
            operation="generic_ffmpeg_progress",
            progress_duration_seconds=100.0,
        )
    except FFmpegExecutionError as exc:
        _assert(
            "encoder failed"
            in exc.combined_output,
            (
                "streamed stderr was not "
                "preserved in FFmpeg error"
            ),
        )

        _assert(
            "out_time_us="
            not in exc.combined_output,
            (
                "FFmpeg progress protocol "
                "leaked into error output"
            ),
        )
    else:
        raise AssertionError(
            "failing process did not raise "
            "FFmpegExecutionError"
        )


def _test_streamed_runner_cancels_quickly() -> None:
    controller = DownloadController()

    result = []

    command = [
        sys.executable,
        "-u",
        "-c",
        (
            "import time\n"
            "print("
            "'out_time_us=1000000', "
            "flush=True"
            ")\n"
            "print("
            "'speed=1.00x', "
            "flush=True"
            ")\n"
            "print("
            "'progress=continue', "
            "flush=True"
            ")\n"
            "time.sleep(30)\n"
        ),
    ]

    def run() -> None:
        try:
            downloader._run_ffmpeg_command(
                command,
                operation=(
                    "generic_ffmpeg_progress"
                ),
                cancel_controller=controller,
                progress_duration_seconds=100.0,
            )
        except DownloadCancelled:
            result.append(
                "cancelled"
            )
        except BaseException as exc:
            result.append(
                f"{type(exc).__name__}: {exc}"
            )
        else:
            result.append(
                "completed"
            )

    worker = threading.Thread(
        target=run,
        daemon=True,
    )

    worker.start()

    deadline = time.monotonic() + 5

    while time.monotonic() < deadline:
        if (
            controller.current_process
            is not None
        ):
            break

        time.sleep(0.05)
    else:
        raise AssertionError(
            "streamed FFmpeg runner did not "
            "register its process"
        )

    started = time.monotonic()

    controller.request_cancel()

    worker.join(timeout=5)

    elapsed = (
        time.monotonic()
        - started
    )

    _assert(
        not worker.is_alive(),
        (
            "streamed FFmpeg runner did not "
            "stop after cancellation"
        ),
    )

    _assert(
        elapsed < 5,
        (
            "streamed FFmpeg cancellation "
            f"was too slow: {elapsed:.2f}s"
        ),
    )

    _assert(
        result == ["cancelled"],
        (
            "streamed FFmpeg cancellation "
            f"result was wrong: {result}"
        ),
    )

    _assert(
        controller.current_process is None,
        (
            "streamed FFmpeg process was "
            "not cleared"
        ),
    )


def _assert(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise AssertionError(
            message
        )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
