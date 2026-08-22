import inspect
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import download_process, downloader
from core.download_process import DownloadController
from core.progress_status import ParsedTransferProgress, TRANSFER_SOURCE_ARIA2


VIDEO_ID = "video-id"
VIDEO_FORMAT_ID = "137"
AUDIO_FORMAT_ID = "140"
COMBINED_FORMAT_ID = "22"


def main() -> int:
    _test_measured_watchdog_rule()
    _test_split_fallback_reuses_snapshot_and_preserves_pipeline()
    _test_combined_fallback_reuses_snapshot_without_audio_or_merge()
    _test_speed_limit_disables_watchdog()
    _test_cancellation_before_and_after_fallback()
    _test_native_failure_propagates_normally()
    _test_exact_process_tree_isolation()
    _test_unrelated_transports_remain_outside_watchdog()
    print("fast performance fallback smoke passed")
    return 0


def _test_measured_watchdog_rule() -> None:
    threshold = downloader.FAST_VIDEO_SLOW_THRESHOLD_BPS

    healthy_clock = _FakeClock()
    healthy = downloader._FastVideoPerformanceWatchdog(clock=healthy_clock)
    healthy.start_process()
    _assert(healthy.observe(_progress(1_000_000, 1.0)) is None, "first sample switched")
    healthy_clock.advance(3.0)
    healthy_bytes = 1_000_000 + int(threshold * 3.0) + 1
    _assert(healthy.observe(_progress(healthy_bytes, 80.0)) is None, "healthy x16 switched")

    slow_clock = _FakeClock()
    slow = downloader._FastVideoPerformanceWatchdog(clock=slow_clock)
    slow.start_process()
    _assert(slow.observe(_progress(1_000_000, 1.0)) is None, "slow first sample switched")
    slow_clock.advance(1.0)
    _assert(
        slow.observe(_progress(2_000_000, 2.0)) is None,
        "transient single slow sample switched",
    )
    slow_clock.advance(1.999)
    _assert(
        slow.observe(_progress(3_000_000, 3.0)) is None,
        "watchdog switched before the measured window",
    )
    slow_clock.advance(0.001)
    decision = slow.observe(_progress(4_000_000, 4.0))
    _assert(
        isinstance(decision, downloader._FastVideoPerformanceFallback),
        "sustained slow x16 did not switch",
    )

    boundary_clock = _FakeClock()
    boundary = downloader._FastVideoPerformanceWatchdog(clock=boundary_clock)
    boundary.start_process()
    _assert(boundary.observe(_progress(1, 0.1)) is None, "boundary baseline switched")
    boundary_clock.advance(downloader.FAST_VIDEO_SLOW_OBSERVATION_SECONDS)
    boundary_delta = int(
        downloader.FAST_VIDEO_SLOW_THRESHOLD_BPS
        * downloader.FAST_VIDEO_SLOW_OBSERVATION_SECONDS
    )
    _assert(
        boundary.observe(_progress(1 + boundary_delta, 99.0)) is None,
        "exact threshold boundary switched",
    )

    early_clock = _FakeClock()
    early = downloader._FastVideoPerformanceWatchdog(clock=early_clock)
    early.start_process()
    _assert(early.observe(_progress(1, 1.0)) is None, "small-object baseline switched")
    early_clock.advance(2.99)
    _assert(
        early.observe(_progress(2, 100.0)) is None,
        "object completing before the observation window switched",
    )
    early_clock.advance(0.01)
    _assert(
        early.observe(_progress(3, 100.0)) is None,
        "completed object switched at the observation boundary",
    )


def _test_split_fallback_reuses_snapshot_and_preserves_pipeline() -> None:
    result = _run_fast_case("split")
    _assert(result.error is None, f"split fallback failed: {result.error}")
    _assert(result.extraction_count == 1, "split fallback performed a second extraction")
    _assert(result.aria_video_count == 1, "split fallback did not start aria2 exactly once")
    _assert(result.native_video_count == 1, "split fallback did not start native exactly once")
    _assert(result.audio_count == 1, "split fallback changed companion audio")
    _assert(result.ffmpeg_count == 1, "split fallback changed merge behavior")
    _assert(result.promotions == ["merged.mp4"], f"split promotion changed: {result.promotions}")
    _assert(result.final_bytes == b"merged", "split fallback final output was wrong")
    _assert(result.same_snapshot, "split fallback did not reuse the info JSON snapshot")
    _assert(result.exact_format, "split fallback changed the selected video format")
    _assert(result.partial_clean_before_native, "aria2 partial staging was not cleaned")
    _assert(result.metadata_survived_until_native, "snapshot was deleted before native fallback")
    _assert(result.native_has_no_external_downloader, "native fallback retained aria2 options")
    _assert(result.audio_watchdog_absent, "companion audio inherited the video watchdog")
    _assert(result.progress_values, "fallback emitted no numeric progress")
    _assert(result.progress_values == sorted(result.progress_values), "fallback progress moved backwards")
    _assert(result.switch_stage_count == 1, "neutral transport-switch stage was missing")
    _assert(result.fallback_log_count == 1, "performance fallback log was missing or duplicated")
    _assert("performance_fallback=true" in result.perf_line, "PERF fallback flag was missing")
    _assert(
        "aria2_before_fallback_seconds=6.25" in result.perf_line,
        "PERF fallback timing was missing",
    )


def _test_combined_fallback_reuses_snapshot_without_audio_or_merge() -> None:
    result = _run_fast_case("combined")
    _assert(result.error is None, f"combined fallback failed: {result.error}")
    _assert(result.extraction_count == 1, "combined fallback performed a second extraction")
    _assert(result.aria_video_count == 1 and result.native_video_count == 1, "combined transports were wrong")
    _assert(result.audio_count == 0, "combined fallback started companion audio")
    _assert(result.ffmpeg_count == 0, "combined fallback started a merge")
    _assert(result.promotions == ["video.mp4"], f"combined promotion changed: {result.promotions}")
    _assert(result.final_bytes == b"native-video", "combined native output was not promoted")
    _assert(result.same_snapshot and result.exact_format, "combined snapshot or format changed")
    _assert(result.progress_values, "combined fallback emitted no numeric progress")
    _assert(result.progress_values == sorted(result.progress_values), "combined progress moved backwards")


def _test_speed_limit_disables_watchdog() -> None:
    result = _run_fast_case("split", speed_limit="2M")
    _assert(result.error is None, f"speed-limited Fast path failed: {result.error}")
    _assert(result.aria_video_count == 1, "speed-limited Fast path skipped aria2")
    _assert(result.native_video_count == 0, "speed-limited Fast path switched to native")
    _assert(result.fallback_log_count == 0, "speed-limited Fast path logged fallback")
    _assert("performance_fallback=false" in result.perf_line, "speed-limited PERF flag was wrong")


def _test_cancellation_before_and_after_fallback() -> None:
    before = _run_fast_case("split", cancel_before_video=True)
    _assert(isinstance(before.error, downloader.DownloadCancelled), "pre-fallback cancellation changed")
    _assert(before.aria_video_count == 0 and before.native_video_count == 0, "pre-fallback cancellation started video")
    _assert(not before.promotions, "pre-fallback cancellation promoted output")

    after = _run_fast_case("split", cancel_after_cleanup=True)
    _assert(isinstance(after.error, downloader.DownloadCancelled), "post-fallback cancellation changed")
    _assert(after.aria_video_count == 1 and after.native_video_count == 0, "post-fallback cancellation started native")
    _assert(after.partial_clean_before_native, "post-fallback cancellation left partial staging")
    _assert(not after.promotions, "post-fallback cancellation promoted output")


def _test_native_failure_propagates_normally() -> None:
    result = _run_fast_case("split", native_failure=True)
    _assert(isinstance(result.error, downloader.DownloadError), "native fallback failure did not propagate")
    _assert(str(result.error) == "native fallback failed", "native fallback error was rewritten")
    _assert(result.fallback_log_count == 1, "native failure repeated performance fallback")
    _assert(result.aria_video_count == 1 and result.native_video_count == 1, "native failure looped transports")
    _assert(not result.promotions, "failed native fallback promoted output")


def _test_exact_process_tree_isolation() -> None:
    clock = _SequenceClock((0.0, 0.0, 3.0))
    watchdog = downloader._FastVideoPerformanceWatchdog(clock=clock)
    active = _FakeProcess(
        41001,
        (
            "[#abc 1MiB/500MiB(1%) CN:16 DL:1MiB]\n",
            "[#abc 4MiB/500MiB(2%) CN:16 DL:1MiB]\n",
        ),
    )
    other = _FakeProcess(41002, ())
    terminated: list[_FakeProcess] = []
    controller = DownloadController()

    def fake_terminate(process):
        terminated.append(process)
        process.terminated = True

    with (
        _patched_attr(downloader.subprocess, "Popen", lambda *_args, **_kwargs: active),
        _patched_attr(downloader, "_terminate_process_tree", fake_terminate),
        downloader._fast_video_performance_watchdog_scope(watchdog),
    ):
        try:
            downloader._run_ytdlp(
                ["yt-dlp", "--downloader", "aria2c", "--load-info-json", "snapshot.info.json"],
                controller,
            )
        except downloader._FastVideoPerformanceFallback:
            pass
        else:
            raise AssertionError("real reader did not raise the performance fallback")

    _assert(terminated == [active], f"fallback terminated the wrong process set: {terminated}")
    _assert(active.terminated, "active media process was not terminated")
    _assert(not other.terminated, "another simulated download process was modified")
    _assert(controller.current_process is None, "active process ownership was not cleared")

    source = inspect.getsource(download_process._terminate_process_tree)
    _assert('"taskkill"' in source and '"/PID"' in source, "exact-PID Windows termination was lost")
    _assert('"/IM"' not in source, "global process-name termination was introduced")


def _test_unrelated_transports_remain_outside_watchdog() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        stable_options = downloader.DownloadOptions(
            str(root), "channel", "Channel", download_engine=downloader.DOWNLOAD_ENGINE_STABLE
        )
        fast_options = downloader.DownloadOptions(
            str(root), "channel", "Channel", download_engine=downloader.DOWNLOAD_ENGINE_ARIA2_FAST
        )
        with _patched_runtime(paths):
            stable = downloader._build_stable_video_ytdlp_command(VIDEO_ID, root, stable_options)
            mp3 = downloader._build_fast_audio_ytdlp_command(
                VIDEO_ID,
                root,
                fast_options,
                downloader._Aria2RuntimeValidation(True, True, paths.aria2),
            )
    _assert("--downloader" not in stable, "Stable transport changed")
    _assert("--load-info-json" not in stable, "Stable was routed through the watchdog snapshot")
    _assert("-x" in mp3 and "--load-info-json" not in mp3, "separate MP3 mode changed")
    _assert(downloader._current_fast_video_performance_watchdog() is None, "watchdog leaked outside Fast video")


def _run_fast_case(
    kind: str,
    *,
    speed_limit: str | None = None,
    cancel_before_video: bool = False,
    cancel_after_cleanup: bool = False,
    native_failure: bool = False,
):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        options = downloader.DownloadOptions(
            base_folder=str(root / "downloads"),
            channel_id="channel",
            channel_name="Channel",
            download_engine=downloader.DOWNLOAD_ENGINE_ARIA2_FAST,
            speed_limit=speed_limit,
            file_start_number=1,
        )
        controller = DownloadController()
        logs: list[str] = []
        events = []
        commands: list[list[str]] = []
        promotions: list[str] = []
        ffmpeg_commands: list[list[str]] = []
        checks = {
            "partial_clean": False,
            "metadata_survived": False,
            "audio_watchdog_absent": True,
        }
        final_path = root / "final.mp4"

        def fake_retry(command, current_options, log, cancel_controller=None, cookie_retry_state=None):
            captured = list(command)
            commands.append(captured)
            output_template = _option_value(captured, "-o")
            if "--write-info-json" in captured:
                info_path = Path(output_template.replace("%(ext)s", "info.json"))
                info_path.parent.mkdir(parents=True, exist_ok=True)
                payload = _split_info() if kind == "split" else _combined_info()
                info_path.write_text(json.dumps(payload), encoding="utf-8")
                if cancel_before_video:
                    controller.request_cancel()
                return

            format_id = _option_value(captured, "-f")
            output_path = Path(output_template.replace("%(ext)s", "mp4"))
            is_video = format_id in {VIDEO_FORMAT_ID, COMBINED_FORMAT_ID}
            uses_aria2 = "--downloader" in captured
            watchdog = downloader._current_fast_video_performance_watchdog()
            if is_video and uses_aria2:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                downloader._start_progress_attempt(TRANSFER_SOURCE_ARIA2)
                if watchdog is None:
                    downloader._emit_aria2_progress(_progress(100_000_000, 100.0))
                    output_path.write_bytes(b"speed-limited-aria-video")
                    return
                downloader._emit_aria2_progress(_progress(20_000_000, 20.0))
                output_path.write_bytes(b"partial")
                Path(f"{output_path}.part").write_bytes(b"part")
                Path(f"{output_path}.aria2").write_bytes(b"aria2")
                raise downloader._FastVideoPerformanceFallback(6.25, 20.0)

            if is_video:
                info_path = Path(_option_value(captured, "--load-info-json"))
                checks["partial_clean"] = not any(
                    path.name.lower().startswith("video.")
                    for path in output_path.parent.iterdir()
                )
                checks["metadata_survived"] = info_path.exists()
                if native_failure:
                    raise downloader.DownloadError("native fallback failed")
                downloader._start_progress_attempt(downloader.TRANSFER_SOURCE_YTDLP)
                downloader._emit_ytdlp_progress_from_line(
                    "[download]   0.0% of 100MiB at 40MiB/s ETA 00:02",
                    downloader.TRANSFER_SOURCE_YTDLP,
                )
                downloader._emit_ytdlp_progress_from_line(
                    "[download] 100.0% of 100MiB at 40MiB/s ETA 00:00",
                    downloader.TRANSFER_SOURCE_YTDLP,
                )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"native-video")
                return

            checks["audio_watchdog_absent"] = watchdog is None
            downloader._start_progress_attempt(downloader.TRANSFER_SOURCE_YTDLP)
            downloader._emit_ytdlp_progress_from_line(
                "[download] 100.0% of 10MiB at 10MiB/s ETA 00:00",
                downloader.TRANSFER_SOURCE_YTDLP,
            )
            audio_path = Path(output_template.replace("%(ext)s", "m4a"))
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(b"native-audio")

        def fake_ffmpeg(command, *, operation, cancel_controller=None, progress_duration_seconds=None):
            ffmpeg_commands.append(list(command))
            Path(command[-1]).write_bytes(b"merged")
            return ""

        def fake_promote(source, final, log, replace_existing=False, cancel_controller=None):
            promotions.append(Path(source).name)
            Path(final).write_bytes(Path(source).read_bytes())

        original_cleanup = downloader._clean_fast_video_transport_staging

        def cleanup_then_maybe_cancel(hybrid_dir, cancel_controller):
            original_cleanup(hybrid_dir, cancel_controller)
            checks["partial_clean"] = not any(
                path.name.lower().startswith("video.")
                for path in hybrid_dir.iterdir()
            )
            if cancel_after_cleanup:
                controller.request_cancel()

        video = SimpleNamespace(video_id=VIDEO_ID, title="Title", sanitized_filename_base="001 Title")
        previous_progress = downloader._set_progress_context(events.append, video, 1, 1, "Video")
        error = None
        try:
            with (
                _patched_runtime(paths),
                _patched_attr(downloader, "_run_ytdlp_with_retries", fake_retry),
                _patched_attr(downloader, "_run_ffmpeg_command", fake_ffmpeg),
                _patched_attr(downloader, "_clean_fast_video_transport_staging", cleanup_then_maybe_cancel),
                _patched_attr(downloader, "_validate_premiere_safe_mp4_for_download", lambda *args, **kwargs: None),
                _patched_attr(downloader, "_atomic_promote_with_retry", fake_promote),
            ):
                downloader._download_video(
                    VIDEO_ID,
                    "001 Title",
                    root,
                    final_path,
                    options,
                    logs.append,
                    controller,
                    aria2_validation=downloader._Aria2RuntimeValidation(True, True, paths.aria2),
                )
        except (downloader.DownloadCancelled, downloader.DownloadError) as exc:
            error = exc
        finally:
            downloader._restore_progress_context(previous_progress)

        extraction = [command for command in commands if "--write-info-json" in command]
        media = [command for command in commands if "--load-info-json" in command]
        aria_video = [
            command
            for command in media
            if _option_value(command, "-f") in {VIDEO_FORMAT_ID, COMBINED_FORMAT_ID}
            and "--downloader" in command
        ]
        native_video = [
            command
            for command in media
            if _option_value(command, "-f") in {VIDEO_FORMAT_ID, COMBINED_FORMAT_ID}
            and "--downloader" not in command
        ]
        audio = [command for command in media if _option_value(command, "-f") == AUDIO_FORMAT_ID]
        snapshots = {
            _option_value(command, "--load-info-json")
            for command in [*aria_video, *native_video]
        }
        expected_format = VIDEO_FORMAT_ID if kind == "split" else COMBINED_FORMAT_ID
        progress_values = [
            float(event.percent.removesuffix("%"))
            for event in events
            if event.percent is not None
        ]
        perf_lines = [line for line in logs if line.startswith("[PERF]")]
        return SimpleNamespace(
            error=error,
            extraction_count=len(extraction),
            aria_video_count=len(aria_video),
            native_video_count=len(native_video),
            audio_count=len(audio),
            ffmpeg_count=len(ffmpeg_commands),
            promotions=promotions,
            final_bytes=final_path.read_bytes() if final_path.exists() else None,
            same_snapshot=len(snapshots) == 1 and bool(snapshots),
            exact_format=all(_option_value(command, "-f") == expected_format for command in [*aria_video, *native_video]),
            partial_clean_before_native=checks["partial_clean"],
            metadata_survived_until_native=checks["metadata_survived"],
            native_has_no_external_downloader=all(
                "--downloader" not in command and "--downloader-args" not in command
                for command in native_video
            ),
            audio_watchdog_absent=checks["audio_watchdog_absent"],
            progress_values=progress_values,
            fallback_log_count=sum("Fast video transfer remained below" in line for line in logs),
            switch_stage_count=sum(
                event.message == "Fast transfer is slow; switching transport..."
                for event in events
            ),
            perf_line=perf_lines[0] if len(perf_lines) == 1 else "",
        )


def _split_info() -> dict:
    return {
        "id": VIDEO_ID,
        "duration": 120.0,
        "requested_formats": [
            {
                "format_id": VIDEO_FORMAT_ID,
                "ext": "mp4",
                "vcodec": "avc1.640028",
                "acodec": "none",
                "height": 1080,
                "filesize": 90_000_000,
            },
            {
                "format_id": AUDIO_FORMAT_ID,
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "filesize": 10_000_000,
            },
        ],
    }


def _combined_info() -> dict:
    return {
        "id": VIDEO_ID,
        "format_id": COMBINED_FORMAT_ID,
        "ext": "mp4",
        "vcodec": "avc1.64001F",
        "acodec": "mp4a.40.2",
        "height": 720,
        "filesize": 25_000_000,
        "duration": 120.0,
    }


def _progress(downloaded_bytes: int, percent: float) -> ParsedTransferProgress:
    return ParsedTransferProgress(
        source=TRANSFER_SOURCE_ARIA2,
        percent=percent,
        speed_text=None,
        downloaded_text=f"{downloaded_bytes}B",
        connection_count=16,
    )


def _runtime_paths(root: Path):
    bin_dir = root / "data" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    ytdlp = bin_dir / "yt-dlp.exe"
    ffmpeg = bin_dir / "ffmpeg.exe"
    aria2 = bin_dir / "aria2c.exe"
    for path in (ytdlp, ffmpeg, aria2):
        path.write_text(path.stem, encoding="utf-8")
    return SimpleNamespace(root=root, bin=bin_dir, ytdlp=ytdlp, ffmpeg=ffmpeg, aria2=aria2)


@contextmanager
def _patched_runtime(paths):
    def fake_runtime_file(filename: str) -> Path:
        if filename == "yt-dlp.exe":
            return paths.ytdlp
        if filename == "ffmpeg.exe":
            return paths.ffmpeg
        if filename == "aria2c.exe":
            return paths.aria2
        if filename == "deno.exe":
            return paths.bin / "deno.exe"
        return paths.root / filename

    with _patched_attr(downloader, "runtime_file", fake_runtime_file):
        yield


class _FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


class _SequenceClock:
    def __init__(self, values):
        self.values = list(values)
        self.last = self.values[-1]

    def __call__(self) -> float:
        if self.values:
            self.last = float(self.values.pop(0))
        return self.last


class _FakeProcess:
    def __init__(self, pid: int, lines):
        self.pid = pid
        self.stdout = iter(lines)
        self.terminated = False

    def poll(self):
        return -1 if self.terminated else None


@contextmanager
def _patched_attr(target, name: str, value):
    previous = getattr(target, name)
    setattr(target, name, value)
    try:
        yield
    finally:
        setattr(target, name, previous)


def _option_value(command: list[str], option: str) -> str:
    for index, value in enumerate(command):
        if value == option and index + 1 < len(command):
            return str(command[index + 1])
    return ""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
