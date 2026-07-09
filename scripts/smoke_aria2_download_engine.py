import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.download_modes import MODE_VIDEO_THUMB, PART_VIDEO
from core.downloader import (
    ARIA2_FAST_AUDIO_FORMAT,
    ARIA2_FAST_DOWNLOADER_ARGS,
    ARIA2_FAST_EXTRACTOR_ARGS,
    ARIA2_FAST_TRANSCODE_AUDIO_CODEC,
    ARIA2_FAST_TRANSCODE_CRF,
    ARIA2_FAST_TRANSCODE_PRESET,
    ARIA2_FAST_TRANSCODE_VIDEO_CODEC,
    ARIA2_FAST_VIDEO_FORMAT,
    DOWNLOAD_ENGINE_ARIA2_FAST,
    DOWNLOAD_ENGINE_STABLE,
    DownloadError,
    DownloadOptions,
    FFmpegExecutionError,
    YTDLP_STAGE_DOWNLOAD,
    YtdlpExecutionError,
    YtdlpFailureKind,
)


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"


def main() -> int:
    _test_exact_fast_constants()
    _test_exact_fast_video_command()
    _test_direct_fast_cookie_path()
    _test_fast_bypasses_stable_helpers()
    _test_stable_still_uses_retry_pipeline()
    _test_exact_fast_ffmpeg_command()
    _test_converted_output_is_promoted()
    _test_ffmpeg_failure_is_not_promoted_and_marks_failed()
    _test_stable_command_unchanged()
    _test_fast_audio_command()
    _test_fast_has_no_automatic_fallback()
    _test_fast_does_not_start_lookahead()
    _test_stable_still_starts_eligible_lookahead()
    _test_runtime_logs()
    _test_fast_log_secret_safety()
    _test_sanitized_fast_command_probe()
    print("aria2 download engine smoke passed")
    return 0


def _test_exact_fast_constants() -> None:
    _assert(ARIA2_FAST_EXTRACTOR_ARGS == "youtube:player_client=ios,web", "Fast extractor args changed")
    _assert(
        ARIA2_FAST_VIDEO_FORMAT == "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "Fast video format changed",
    )
    _assert(ARIA2_FAST_AUDIO_FORMAT == "bestaudio/best", "Fast audio format changed")
    _assert(ARIA2_FAST_DOWNLOADER_ARGS == "aria2c:-x 16 -s 16 -j 16 -k 1M", "Fast aria2 args changed")
    _assert(ARIA2_FAST_TRANSCODE_VIDEO_CODEC == "libx264", "Fast video codec changed")
    _assert(ARIA2_FAST_TRANSCODE_PRESET == "slow", "Fast preset changed")
    _assert(ARIA2_FAST_TRANSCODE_CRF == "18", "Fast CRF changed")
    _assert(ARIA2_FAST_TRANSCODE_AUDIO_CODEC == "aac", "Fast audio codec changed")


def _test_exact_fast_video_command() -> None:
    with TemporaryDirectory(prefix="fast_video_command_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        validation = _aria2_validation(paths)
        with _patched_runtime(paths):
            command = downloader._build_fast_video_ytdlp_command(
                "fast-video",
                root,
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                validation,
            )

    _assert(command.count("--no-playlist") == 1, "Fast command missed exactly one --no-playlist")
    _assert(command.count("--extractor-args") == 1, "Fast command did not contain exactly one --extractor-args")
    _assert(_option_value(command, "--extractor-args") == ARIA2_FAST_EXTRACTOR_ARGS, "Fast extractor args mismatch")
    _assert(command.count("--ffmpeg-location") == 1, "Fast command did not contain exactly one --ffmpeg-location")
    _assert(_option_value(command, "--ffmpeg-location") == str(paths["ffmpeg.exe"].parent), "Fast ffmpeg location mismatch")
    _assert(command.count("--downloader") == 1, "Fast command did not contain exactly one --downloader")
    _assert(_option_value(command, "--downloader") == str(paths["aria2c.exe"]), "Fast downloader path mismatch")
    _assert(command.count("--downloader-args") == 1, "Fast command did not contain exactly one --downloader-args")
    _assert(_option_value(command, "--downloader-args") == ARIA2_FAST_DOWNLOADER_ARGS, "Fast downloader args mismatch")
    _assert(command.count("-f") == 1, "Fast command did not contain exactly one -f")
    _assert(_option_value(command, "-f") == ARIA2_FAST_VIDEO_FORMAT, "Fast format mismatch")
    _assert(_option_value(command, "--merge-output-format") == "mp4", "Fast merge format mismatch")
    _assert("-N" not in command, "Fast command contained Stable -N")
    _assert(downloader.PREMIERE_SAFE_VIDEO_FORMAT not in command, "Fast command used Premiere-safe selector")
    _assert("--http-chunk-size" not in command, "Fast command used Stable chunk setting")
    _assert("--load-info-json" not in command, "Fast command used saved info-json")


def _test_direct_fast_cookie_path() -> None:
    with TemporaryDirectory(prefix="fast_direct_cookies_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        validation = _aria2_validation(paths)
        file_cookie = root / "file-cookies.txt"
        bridge_cookie = root / "bridge-cookies.txt"
        file_cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        bridge_cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        with _patched_runtime(paths):
            file_options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
            file_options.cookies_enabled = True
            file_options.cookies_path = str(file_cookie)
            file_command = downloader._build_fast_video_ytdlp_command("file-cookie", root, file_options, validation)

            bridge_options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
            bridge_options.cookies_enabled = True
            bridge_options.cookie_source = downloader.COOKIE_SOURCE_BRIDGE
            bridge_options.bridge_cookie_path = str(bridge_cookie)
            bridge_command = downloader._build_fast_video_ytdlp_command("bridge-cookie", root, bridge_options, validation)

    _assert(file_command.count("--cookies") == 1, "File cookie command did not contain exactly one --cookies")
    _assert(_option_value(file_command, "--cookies") == str(file_cookie), "File cookie path was not direct canonical path")
    _assert(bridge_command.count("--cookies") == 1, "Bridge cookie command did not contain exactly one --cookies")
    _assert(_option_value(bridge_command, "--cookies") == str(bridge_cookie), "Bridge cookie path was not direct canonical path")
    _assert(not list(root.glob(".s9h-*")), "Fast command construction created a temporary cookie/staging path")


def _test_fast_bypasses_stable_helpers() -> None:
    with TemporaryDirectory(prefix="fast_bypass_helpers_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        final_path = root / "final.mp4"
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        validation = _aria2_validation(paths)
        promoted: list[Path] = []
        final_created = False

        old_prepared = downloader._prepared_cookie_attempt
        old_prepare_info = downloader._prepare_authenticated_infojson_download_command
        old_extract_info = downloader._extract_authenticated_infojson_path
        old_build_info = downloader._build_infojson_media_download_command
        old_lookahead = downloader._start_cookie_media_lookahead
        old_retries = downloader._run_ytdlp_with_retries
        old_run_ytdlp = downloader._run_ytdlp
        old_ffmpeg = downloader._run_ffmpeg_command
        old_validate = downloader._validate_premiere_safe_mp4_for_download
        old_promote = downloader._atomic_promote_with_retry
        try:
            def forbidden(*_args, **_kwargs):
                raise AssertionError("Fast path called a Stable-only helper")

            def fake_ytdlp(command, _controller=None):
                _output_path(command, "mp4").write_bytes(b"fast source")
                return ""

            def fake_ffmpeg(command, *, operation, cancel_controller=None):
                _assert(operation == "fast_video_transcode", "Fast conversion used wrong operation")
                Path(command[-1]).write_bytes(b"fixed")
                return ""

            def fake_promote(source, target, *_args, **_kwargs):
                promoted.append(Path(source))
                Path(target).write_bytes(Path(source).read_bytes())

            downloader._prepared_cookie_attempt = forbidden
            downloader._prepare_authenticated_infojson_download_command = forbidden
            downloader._extract_authenticated_infojson_path = forbidden
            downloader._build_infojson_media_download_command = forbidden
            downloader._start_cookie_media_lookahead = forbidden
            downloader._run_ytdlp_with_retries = forbidden
            downloader._run_ytdlp = fake_ytdlp
            downloader._run_ffmpeg_command = fake_ffmpeg
            downloader._validate_premiere_safe_mp4_for_download = lambda *_args, **_kwargs: None
            downloader._atomic_promote_with_retry = fake_promote
            with _patched_runtime(paths):
                downloader._download_video_fast_bat_compatible(
                    "fast-bypass",
                    root,
                    final_path,
                    options,
                    lambda _message: None,
                    None,
                    validation,
                )
                final_created = final_path.exists()
        finally:
            downloader._prepared_cookie_attempt = old_prepared
            downloader._prepare_authenticated_infojson_download_command = old_prepare_info
            downloader._extract_authenticated_infojson_path = old_extract_info
            downloader._build_infojson_media_download_command = old_build_info
            downloader._start_cookie_media_lookahead = old_lookahead
            downloader._run_ytdlp_with_retries = old_retries
            downloader._run_ytdlp = old_run_ytdlp
            downloader._run_ffmpeg_command = old_ffmpeg
            downloader._validate_premiere_safe_mp4_for_download = old_validate
            downloader._atomic_promote_with_retry = old_promote

    _assert(final_created, "Fast bypass test did not create final output")
    _assert(promoted and promoted[0].name.endswith("_FIXED.mp4"), "Fast bypass test did not promote converted output")


def _test_stable_still_uses_retry_pipeline() -> None:
    with TemporaryDirectory(prefix="stable_retry_pipeline_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        final_path = root / "final.mp4"
        options = _options(root, DOWNLOAD_ENGINE_STABLE)
        calls: list[list[str]] = []

        old_retries = downloader._run_ytdlp_with_retries
        old_validate = downloader._validate_premiere_safe_mp4_for_download
        old_promote = downloader._atomic_promote_with_retry
        try:
            def fake_retries(command, *_args, **_kwargs):
                calls.append(list(command))
                _output_path(command, "mp4").write_bytes(b"stable source")

            def fake_promote(source, target, *_args, **_kwargs):
                Path(target).write_bytes(Path(source).read_bytes())

            downloader._run_ytdlp_with_retries = fake_retries
            downloader._validate_premiere_safe_mp4_for_download = lambda *_args, **_kwargs: None
            downloader._atomic_promote_with_retry = fake_promote
            with _patched_runtime(paths):
                downloader._download_video_stable(
                    "stable-video",
                    root,
                    final_path,
                    options,
                    lambda _message: None,
                    None,
                    downloader._YtdlpAttemptState(),
                )
        finally:
            downloader._run_ytdlp_with_retries = old_retries
            downloader._validate_premiere_safe_mp4_for_download = old_validate
            downloader._atomic_promote_with_retry = old_promote

    _assert(calls, "Stable video did not use _run_ytdlp_with_retries")
    _assert_stable_video_command(calls[0])


def _test_exact_fast_ffmpeg_command() -> None:
    with TemporaryDirectory(prefix="fast_ffmpeg_command_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        source = root / "source.mp4"
        source.write_bytes(b"mp4")
        captured: list[list[str]] = []
        old_run = downloader._run_ffmpeg_command
        try:
            def fake_run(command, *, operation, cancel_controller=None):
                captured.append(list(command))
                Path(command[-1]).write_bytes(b"fixed")
                return ""

            downloader._run_ffmpeg_command = fake_run
            with _patched_runtime(paths):
                fixed_path = downloader._transcode_fast_video_like_bat(source, root, lambda _message: None)
        finally:
            downloader._run_ffmpeg_command = old_run

    expected = [
        str(paths["ffmpeg.exe"]),
        "-y",
        "-i",
        str(source),
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(fixed_path),
    ]
    _assert(captured == [expected], f"Fast FFmpeg command mismatch: {captured}")


def _test_converted_output_is_promoted() -> None:
    with TemporaryDirectory(prefix="fast_promote_fixed_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        final_path = root / "final.mp4"
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        validation = _aria2_validation(paths)
        promoted: list[Path] = []
        source_paths: list[Path] = []
        source_removed = False
        final_was_fixed = False

        old_run_fast = downloader._run_fast_ytdlp_command
        old_ffmpeg = downloader._run_ffmpeg_command
        old_validate = downloader._validate_premiere_safe_mp4_for_download
        old_promote = downloader._atomic_promote_with_retry
        try:
            def fake_fast(command, *_args, **_kwargs):
                source = _output_path(command, "mp4")
                source.write_bytes(b"source")
                source_paths.append(source)

            def fake_ffmpeg(command, *, operation, cancel_controller=None):
                Path(command[-1]).write_bytes(b"fixed")
                return ""

            def fake_promote(source, target, *_args, **_kwargs):
                promoted.append(Path(source))
                Path(target).write_bytes(Path(source).read_bytes())

            downloader._run_fast_ytdlp_command = fake_fast
            downloader._run_ffmpeg_command = fake_ffmpeg
            downloader._validate_premiere_safe_mp4_for_download = lambda *_args, **_kwargs: None
            downloader._atomic_promote_with_retry = fake_promote
            with _patched_runtime(paths):
                downloader._download_video_fast_bat_compatible(
                    "fast-promote",
                    root,
                    final_path,
                    options,
                    lambda _message: None,
                    None,
                    validation,
                )
                source_removed = bool(source_paths and not source_paths[0].exists())
                final_was_fixed = final_path.exists() and final_path.read_bytes() == b"fixed"
        finally:
            downloader._run_fast_ytdlp_command = old_run_fast
            downloader._run_ffmpeg_command = old_ffmpeg
            downloader._validate_premiere_safe_mp4_for_download = old_validate
            downloader._atomic_promote_with_retry = old_promote

    _assert(promoted and promoted[0].name.endswith("_FIXED.mp4"), "Fast did not promote converted MP4")
    _assert(source_removed, "Fast original staging MP4 was not removed after promotion")
    _assert(final_was_fixed, "Fast final output was not converted data")


def _test_ffmpeg_failure_is_not_promoted_and_marks_failed() -> None:
    with TemporaryDirectory(prefix="fast_ffmpeg_failure_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        final_path = root / "final.mp4"
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        validation = _aria2_validation(paths)
        final_created_after_failure = False
        old_run_fast = downloader._run_fast_ytdlp_command
        old_ffmpeg = downloader._run_ffmpeg_command
        old_promote = downloader._atomic_promote_with_retry
        try:
            def fake_fast(command, *_args, **_kwargs):
                _output_path(command, "mp4").write_bytes(b"source")

            def fail_ffmpeg(*_args, **_kwargs):
                raise FFmpegExecutionError(
                    operation="fast_video_transcode",
                    exit_code=1,
                    message="ffmpeg fast_video_transcode failed: invalid_input",
                    output_lines=("error",),
                    combined_output="error",
                )

            downloader._run_fast_ytdlp_command = fake_fast
            downloader._run_ffmpeg_command = fail_ffmpeg
            downloader._atomic_promote_with_retry = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("Fast promoted after FFmpeg failure")
            )
            with _patched_runtime(paths):
                try:
                    downloader._download_video_fast_bat_compatible(
                        "fast-fail",
                        root,
                        final_path,
                        options,
                        lambda _message: None,
                        None,
                        validation,
                    )
                except FFmpegExecutionError:
                    pass
                else:
                    raise AssertionError("Fast FFmpeg failure did not propagate")
                final_created_after_failure = final_path.exists()
        finally:
            downloader._run_fast_ytdlp_command = old_run_fast
            downloader._run_ffmpeg_command = old_ffmpeg
            downloader._atomic_promote_with_retry = old_promote
    _assert(not final_created_after_failure, "Final output was created after FFmpeg failure")

    marked_parts: list[str] = []
    old_download_fast = downloader._download_video_fast_bat_compatible
    old_mark = downloader._mark_part_error
    old_reconcile = downloader._reconcile_current_item
    old_get_entry = downloader.get_video_entry
    old_is_complete = downloader.is_mode_complete
    old_missing = downloader.missing_parts_for_mode
    old_validate_env = downloader.validate_download_environment
    old_ensure_dirs = downloader.ensure_output_dirs
    old_summary = downloader._call_runtime_tool_summary
    old_prepare_runtime = downloader._prepare_media_downloader_runtime
    try:
        downloader._download_video_fast_bat_compatible = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FFmpegExecutionError(
                operation="fast_video_transcode",
                exit_code=1,
                message="ffmpeg fast_video_transcode failed: invalid_input",
                output_lines=("error",),
                combined_output="error",
            )
        )
        downloader._mark_part_error = lambda _options, _video, _paths, part: marked_parts.append(part)
        downloader._reconcile_current_item = lambda _options, video, _paths, _log, status_callback, **_kwargs: (
            setattr(video, "status", downloader.STATUS_ERROR),
            status_callback(video),
            downloader.STATUS_ERROR,
        )[-1]
        downloader.get_video_entry = lambda *_args, **_kwargs: {}
        downloader.is_mode_complete = lambda *_args, **_kwargs: False
        downloader.missing_parts_for_mode = lambda *_args, **_kwargs: [PART_VIDEO]
        downloader.validate_download_environment = lambda _options: None
        downloader.ensure_output_dirs = lambda *_args, **_kwargs: None
        downloader._call_runtime_tool_summary = lambda *_args, **_kwargs: None
        downloader._prepare_media_downloader_runtime = lambda *_args, **_kwargs: validation
        with _patched_runtime(paths):
            video = _video("fast-outer-fail")
            statuses = []
            downloader.download_items(
                [video],
                options,
                lambda _message: None,
                lambda updated: statuses.append(updated.status),
            )
    finally:
        downloader._download_video_fast_bat_compatible = old_download_fast
        downloader._mark_part_error = old_mark
        downloader._reconcile_current_item = old_reconcile
        downloader.get_video_entry = old_get_entry
        downloader.is_mode_complete = old_is_complete
        downloader.missing_parts_for_mode = old_missing
        downloader.validate_download_environment = old_validate_env
        downloader.ensure_output_dirs = old_ensure_dirs
        downloader._call_runtime_tool_summary = old_summary
        downloader._prepare_media_downloader_runtime = old_prepare_runtime
    _assert(marked_parts == [PART_VIDEO], f"Outer state handling did not mark video failed: {marked_parts}")
    _assert(statuses and statuses[-1] == downloader.STATUS_ERROR, "Outer state handling did not publish error status")


def _test_stable_command_unchanged() -> None:
    with TemporaryDirectory(prefix="stable_command_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        with _patched_runtime(paths):
            command = downloader._build_stable_video_ytdlp_command(
                "stable-command",
                root,
                _options(root, DOWNLOAD_ENGINE_STABLE),
            )
    _assert_stable_video_command(command)
    _assert(_option_value(command, "-f") == downloader.PREMIERE_SAFE_VIDEO_FORMAT, "Stable selector changed")
    _assert("--extractor-args" not in command, "Stable command added Fast extractor args")
    _assert(not _contains_aria2(command), "Stable command added aria2")


def _test_fast_audio_command() -> None:
    with TemporaryDirectory(prefix="fast_audio_command_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        validation = _aria2_validation(paths)
        with _patched_runtime(paths):
            command = downloader._build_fast_audio_ytdlp_command(
                "fast-audio",
                root,
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                validation,
            )
    _assert(_option_value(command, "--extractor-args") == ARIA2_FAST_EXTRACTOR_ARGS, "Fast audio missed player client")
    _assert(_option_value(command, "--downloader-args") == ARIA2_FAST_DOWNLOADER_ARGS, "Fast audio missed aria2 profile")
    _assert(_option_value(command, "-f") == ARIA2_FAST_AUDIO_FORMAT, "Fast audio format mismatch")
    _assert("-x" in command, "Fast audio did not request extraction")
    _assert("-N" not in command, "Fast audio contained Stable -N")


def _test_fast_has_no_automatic_fallback() -> None:
    with TemporaryDirectory(prefix="fast_no_fallback_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        validation = _aria2_validation(paths)
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        calls: list[list[str]] = []
        old_run = downloader._run_ytdlp
        try:
            def fail_once(command, _controller=None):
                calls.append(list(command))
                raise _failure(command, YtdlpFailureKind.NETWORK, "connection reset by peer")

            downloader._run_ytdlp = fail_once
            with _patched_runtime(paths):
                command = downloader._build_fast_video_ytdlp_command("fast-failure", root, options, validation)
                try:
                    downloader._run_fast_ytdlp_command(command, options, lambda _message: None, part=PART_VIDEO)
                except YtdlpExecutionError:
                    pass
                else:
                    raise AssertionError("Fast failure did not propagate")
        finally:
            downloader._run_ytdlp = old_run
    _assert(len(calls) == 1, f"Fast failure made unexpected attempts: {len(calls)}")
    _assert(_contains_aria2(calls[0]), "Fast failure did not run aria2 command")
    _assert("-N" not in calls[0], "Fast failure ran a Stable command")


def _test_fast_does_not_start_lookahead() -> None:
    with TemporaryDirectory(prefix="fast_no_lookahead_") as temp_dir:
        root = Path(temp_dir)
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        options.cookies_enabled = True
        state = downloader._YtdlpBatchState(cookie_bootstrap_media_mode=True)
        attempt_state, prefetched = downloader._video_attempt_state_for_batch(
            [_video("next")],
            1,
            "current",
            options,
            state,
            lambda _message: None,
            None,
        )
        downloader._start_attempt_lookahead(attempt_state, lambda _message: None)
    _assert(prefetched is None, "Fast consumed lookahead metadata")
    _assert(attempt_state.lookahead_callback is None, "Fast created a lookahead callback")
    _assert(state.prefetch is None, "Fast created a lookahead prefetch")


def _test_stable_still_starts_eligible_lookahead() -> None:
    with TemporaryDirectory(prefix="stable_lookahead_") as temp_dir:
        root = Path(temp_dir)
        options = _options(root, DOWNLOAD_ENGINE_STABLE)
        options.cookies_enabled = True
        state = downloader._YtdlpBatchState(cookie_bootstrap_media_mode=True)
        started: list[str] = []
        old_find = downloader._find_cookie_media_lookahead_candidate
        old_start = downloader._start_cookie_media_lookahead
        try:
            downloader._find_cookie_media_lookahead_candidate = lambda *_args, **_kwargs: (
                _video("lookahead-next"),
                "Lookahead Next",
                root,
            )
            downloader._start_cookie_media_lookahead = lambda *_args, **_kwargs: started.append("started")
            attempt_state, _prefetched = downloader._video_attempt_state_for_batch(
                [_video("lookahead-next")],
                1,
                "current",
                options,
                state,
                lambda _message: None,
                None,
            )
            downloader._start_attempt_lookahead(attempt_state, lambda _message: None)
        finally:
            downloader._find_cookie_media_lookahead_candidate = old_find
            downloader._start_cookie_media_lookahead = old_start
    _assert(started == ["started"], "Stable lookahead did not start when eligible")


def _test_runtime_logs() -> None:
    with TemporaryDirectory(prefix="fast_runtime_logs_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        logs: list[str] = []
        with _patched_runtime(paths), _patched_version("aria2 version 1.37.0"):
            downloader._prepare_media_downloader_runtime(
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                logs.append,
                None,
            )
    joined = "\n".join(logs)
    for required in (
        "aria2c BAT-compatible fast mode",
        "ios,web",
        "connections=16",
        "splits=16",
        "jobs=16",
        "piece=1M",
        "libx264",
        "preset=slow",
        "crf=18",
        "audio=aac",
    ):
        _assert(required in joined, f"Fast runtime logs missed {required}")
    for forbidden in (
        "connections=" + "8",
        "fallback to " + "stable",
        "retrying with " + "stable",
    ):
        _assert(forbidden not in joined.lower(), f"Fast runtime logs retained {forbidden}")


def _test_fast_log_secret_safety() -> None:
    with TemporaryDirectory(prefix="fast_secret_logs_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        cookie_path = root / "secret-cookie-file.txt"
        cookie_path.write_text("cookie_secret_value\n", encoding="utf-8")
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        options.cookies_enabled = True
        options.cookies_path = str(cookie_path)
        options.speed_limit = "1M"
        logs: list[str] = []
        with _patched_runtime(paths), _patched_version("aria2 version 1.37.0"):
            downloader._prepare_media_downloader_runtime(options, logs.append, None)
    joined = "\n".join(logs)
    for forbidden in (
        str(cookie_path),
        "secret-cookie-file",
        "cookie_secret_value",
        "api_key",
        "signature=",
        "authorization",
        "token=secret",
    ):
        _assert(forbidden not in joined, f"Fast logs exposed {forbidden}")


def _test_sanitized_fast_command_probe() -> None:
    with TemporaryDirectory(prefix="fast_command_probe_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        validation = _aria2_validation(paths)
        with _patched_runtime(paths):
            command = downloader._build_fast_video_ytdlp_command(
                "probe-video",
                root,
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                validation,
            )
    sanitized = _sanitize_command_for_probe(command)
    text = " ".join(sanitized)
    print(f"[COMMAND PROBE] {text}")
    _assert("--extractor-args youtube:player_client=ios,web" in text, "Sanitized probe missed extractor args")
    _assert(f"-f {ARIA2_FAST_VIDEO_FORMAT}" in text, "Sanitized probe missed video format")
    _assert("--merge-output-format mp4" in text, "Sanitized probe missed merge format")
    _assert("--downloader <aria2c.exe>" in text, "Sanitized probe missed aria2 placeholder")
    _assert(f"--downloader-args {ARIA2_FAST_DOWNLOADER_ARGS}" in text, "Sanitized probe missed aria2 args")
    _assert("--cookies" not in text, "Sanitized probe exposed cookies")


def _failure(command: list[str], kind: YtdlpFailureKind, text: str) -> YtdlpExecutionError:
    return YtdlpExecutionError(
        1,
        text,
        [text],
        combined_output=text,
        stream_interrupted=kind in {YtdlpFailureKind.NETWORK, YtdlpFailureKind.NETWORK_TIMEOUT},
        failure_kind=kind,
        fatal_lines=[text],
        stage=YTDLP_STAGE_DOWNLOAD,
        part=PART_VIDEO,
        command=command,
    )


def _video(video_id: str):
    return SimpleNamespace(
        video_id=video_id,
        title=video_id,
        sanitized_filename_base=video_id,
        thumbnail_url="",
        status="",
    )


def _options(root: Path, engine: str) -> DownloadOptions:
    return DownloadOptions(
        base_folder=str(root),
        channel_id=CHANNEL_ID,
        channel_name=CHANNEL_NAME,
        download_engine=engine,
        download_mode=MODE_VIDEO_THUMB,
    )


def _runtime_paths(root: Path, *, aria2: bool) -> dict[str, Path]:
    paths = {
        "yt-dlp.exe": root / "yt-dlp.exe",
        "ffmpeg.exe": root / "ffmpeg.exe",
        "deno.exe": root / "deno.exe",
        "aria2c.exe": root / "aria2c.exe",
    }
    paths["yt-dlp.exe"].write_bytes(b"")
    paths["ffmpeg.exe"].write_bytes(b"")
    paths["deno.exe"].write_bytes(b"")
    if aria2:
        paths["aria2c.exe"].write_bytes(b"")
    return paths


def _aria2_validation(paths: dict[str, Path]) -> downloader._Aria2RuntimeValidation:
    return downloader._Aria2RuntimeValidation(True, True, paths["aria2c.exe"])


@contextmanager
def _patched_runtime(paths: dict[str, Path]):
    old_runtime_file = downloader.runtime_file
    try:
        downloader.runtime_file = lambda filename: paths.get(filename, Path(filename))
        yield
    finally:
        downloader.runtime_file = old_runtime_file


@contextmanager
def _patched_version(version: str):
    old_get_version = downloader._get_command_version
    try:
        downloader._get_command_version = lambda *_args, **_kwargs: version
        yield
    finally:
        downloader._get_command_version = old_get_version


def _output_path(command: list[str], extension: str) -> Path:
    output_template = _option_value(command, "-o")
    _assert(output_template, "command did not contain output template")
    path = Path(output_template.replace("%(ext)s", extension))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _option_value(command: list[str], option: str) -> str:
    try:
        index = command.index(option)
    except ValueError:
        return ""
    if index + 1 >= len(command):
        return ""
    return command[index + 1]


def _contains_aria2(command: list[str]) -> bool:
    return any("aria2" in str(value).lower() for value in command)


def _assert_stable_video_command(command: list[str]) -> None:
    _assert(_option_value(command, "-N") == "1", "Stable command missed -N 1")
    _assert(_option_value(command, "-f") == downloader.PREMIERE_SAFE_VIDEO_FORMAT, "Stable command missed Premiere-safe selector")
    _assert("--http-chunk-size" in command, "Stable command lost chunk setting")
    _assert("--downloader" not in command, "Stable command used external downloader")
    _assert("--downloader-args" not in command, "Stable command used external downloader args")
    _assert("--extractor-args" not in command, "Stable command used Fast extractor args")
    _assert(not _contains_aria2(command), "Stable command contained aria2")


def _sanitize_command_for_probe(command: list[str]) -> list[str]:
    sanitized: list[str] = []
    skip_next = False
    for index, value in enumerate(command):
        if skip_next:
            skip_next = False
            continue
        if value == "--cookies":
            sanitized.extend(["--cookies", "<cookies>"])
            skip_next = True
            continue
        if value == "--downloader":
            sanitized.extend(["--downloader", "<aria2c.exe>"])
            skip_next = True
            continue
        if value == "--ffmpeg-location":
            sanitized.extend(["--ffmpeg-location", "<ffmpeg-bin>"])
            skip_next = True
            continue
        if index == 0 and str(value).lower().endswith("yt-dlp.exe"):
            sanitized.append("<yt-dlp.exe>")
            continue
        sanitized.append(str(value))
    return sanitized


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
