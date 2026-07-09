import os
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.download_modes import MODE_VIDEO_THUMB, PART_AUDIO, PART_VIDEO
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
    DownloadCancelled,
    DownloadError,
    DownloadOptions,
    FFmpegExecutionError,
    YTDLP_STAGE_DOWNLOAD,
    YtdlpExecutionError,
    YtdlpFailureKind,
)


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"
BAT_CONTINUATION_WARNING = (
    "[WARNING] Fast yt-dlp reported an error, but a staged MP4 exists. "
    "The source will be queued for BAT-compatible conversion."
)


def main() -> int:
    _test_exact_fast_constants()
    _test_exact_fast_video_command()
    _test_fast_base_default_is_strict()
    _test_fast_base_explicit_ignore_errors()
    _test_direct_fast_cookie_path()
    _test_stable_commands_exclude_ignore_errors()
    _test_fast_bypasses_stable_helpers()
    _test_stable_still_uses_retry_pipeline()
    _test_exact_fast_ffmpeg_command()
    _test_converted_output_is_promoted()
    _test_fast_ytdlp_failure_with_usable_mp4_continues()
    _test_fast_ytdlp_failure_without_mp4_reraises()
    _test_fast_ytdlp_failure_with_zero_byte_mp4_reraises()
    _test_fixed_mp4_is_not_selected_as_source()
    _test_zero_byte_exact_mp4_is_not_selected()
    _test_exact_merged_mp4_beats_newer_fragment()
    _test_non_fragment_fallback_beats_newer_fragment()
    _test_fragment_is_last_fallback()
    _test_ytdlp_error_prefers_exact_merged_mp4_over_fragment()
    _test_ffmpeg_failure_is_not_promoted_and_marks_failed()
    _test_corrupt_fast_mp4_is_not_promoted()
    _test_fast_cancellation_before_continuation()
    _test_stable_command_unchanged()
    _test_fast_audio_command()
    _test_fast_audio_remains_strict()
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
    _assert(command.count("--ignore-errors") == 1, "Fast command did not contain exactly one --ignore-errors")
    _assert(command.count("--no-warnings") == 1, "Fast command did not contain exactly one --no-warnings")
    _assert(command.count("-f") == 1, "Fast command did not contain exactly one -f")
    _assert(_option_value(command, "-f") == ARIA2_FAST_VIDEO_FORMAT, "Fast format mismatch")
    _assert(_option_value(command, "--merge-output-format") == "mp4", "Fast merge format mismatch")
    _assert("-N" not in command, "Fast command contained Stable -N")
    _assert(downloader.PREMIERE_SAFE_VIDEO_FORMAT not in command, "Fast command used Premiere-safe selector")
    _assert("--http-chunk-size" not in command, "Fast command used Stable chunk setting")
    _assert("--load-info-json" not in command, "Fast command used saved info-json")


def _test_fast_base_default_is_strict() -> None:
    with TemporaryDirectory(prefix="fast_base_default_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        validation = _aria2_validation(paths)
        with _patched_runtime(paths):
            command = downloader._base_fast_ytdlp_command(
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                validation,
            )

    _assert("--ignore-errors" not in command, "Fast base default included --ignore-errors")
    _assert(_option_value(command, "--extractor-args") == ARIA2_FAST_EXTRACTOR_ARGS, "Fast base missed player client")
    _assert(_option_value(command, "--downloader-args") == ARIA2_FAST_DOWNLOADER_ARGS, "Fast base missed aria2 profile")


def _test_fast_base_explicit_ignore_errors() -> None:
    with TemporaryDirectory(prefix="fast_base_ignore_errors_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        validation = _aria2_validation(paths)
        with _patched_runtime(paths):
            command = downloader._base_fast_ytdlp_command(
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                validation,
                ignore_errors=True,
            )

    _assert(command.count("--ignore-errors") == 1, "Fast base explicit ignore_errors did not add exactly one flag")


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


def _test_stable_commands_exclude_ignore_errors() -> None:
    with TemporaryDirectory(prefix="stable_no_ignore_errors_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        options = _options(root, DOWNLOAD_ENGINE_STABLE)
        captured_thumbnail: list[list[str]] = []
        old_run = downloader._run_ytdlp_with_retries
        try:
            def fake_thumbnail(command, *_args, **_kwargs):
                captured_thumbnail.append(list(command))
                _output_path(command, "jpg").write_bytes(b"\xff\xd8\xff")

            downloader._run_ytdlp_with_retries = fake_thumbnail
            with _patched_runtime(paths):
                stable_video = downloader._build_stable_video_ytdlp_command("stable-video", root, options)
                stable_audio = downloader._build_stable_audio_ytdlp_command("stable-audio", root, options)
                metadata_extract = downloader._build_authenticated_infojson_extract_command(
                    stable_video,
                    str(root / "authenticated.%(ext)s"),
                )
                metadata_media = downloader._build_infojson_media_download_command(
                    stable_video,
                    root / "authenticated.info.json",
                )
                downloader._download_thumbnail(
                    SimpleNamespace(video_id="thumb-video", thumbnail_url=""),
                    "thumb-video",
                    root,
                    root / "thumb.jpg",
                    options,
                    lambda _message: None,
                )
        finally:
            downloader._run_ytdlp_with_retries = old_run

    _assert("--ignore-errors" not in stable_video, "Stable video command contained --ignore-errors")
    _assert("--ignore-errors" not in stable_audio, "Stable audio command contained --ignore-errors")
    _assert("--ignore-errors" not in metadata_extract, "Metadata extract command contained --ignore-errors")
    _assert("--ignore-errors" not in metadata_media, "Info-json media command contained --ignore-errors")
    _assert(captured_thumbnail, "Thumbnail command was not captured")
    _assert("--ignore-errors" not in captured_thumbnail[0], "Thumbnail command contained --ignore-errors")
    _assert(not _contains_aria2(captured_thumbnail[0]), "Thumbnail command contained aria2")
    _assert("--extractor-args" not in captured_thumbnail[0], "Thumbnail command contained Fast extractor args")


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


def _test_fast_ytdlp_failure_with_usable_mp4_continues() -> None:
    with TemporaryDirectory(prefix="fast_error_with_mp4_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        final_path = root / "final.mp4"
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        validation = _aria2_validation(paths)
        logs: list[str] = []
        promoted: list[Path] = []
        ffmpeg_calls: list[list[str]] = []
        source_paths: list[Path] = []
        final_ok = False

        old_run_fast = downloader._run_fast_ytdlp_command
        old_ffmpeg = downloader._run_ffmpeg_command
        old_validate = downloader._validate_premiere_safe_mp4_for_download
        old_promote = downloader._atomic_promote_with_retry
        try:
            def fail_after_mp4(command, *_args, **_kwargs):
                source = _output_path(command, "mp4")
                source.write_bytes(b"source")
                source_paths.append(source)
                raise _failure(command, YtdlpFailureKind.NETWORK, "aria2c exited with code 22")

            def fake_ffmpeg(command, *, operation, cancel_controller=None):
                _assert(operation == "fast_video_transcode", "Fast conversion used wrong operation after yt-dlp error")
                ffmpeg_calls.append(list(command))
                Path(command[-1]).write_bytes(b"fixed")
                return ""

            def fake_promote(source, target, *_args, **_kwargs):
                promoted.append(Path(source))
                Path(target).write_bytes(Path(source).read_bytes())

            downloader._run_fast_ytdlp_command = fail_after_mp4
            downloader._run_ffmpeg_command = fake_ffmpeg
            downloader._validate_premiere_safe_mp4_for_download = lambda *_args, **_kwargs: None
            downloader._atomic_promote_with_retry = fake_promote
            with _patched_runtime(paths):
                downloader._download_video_fast_bat_compatible(
                    "fast-error-with-mp4",
                    root,
                    final_path,
                    options,
                    logs.append,
                    None,
                    validation,
                )
                final_ok = final_path.exists() and final_path.read_bytes() == b"fixed"
        finally:
            downloader._run_fast_ytdlp_command = old_run_fast
            downloader._run_ffmpeg_command = old_ffmpeg
            downloader._validate_premiere_safe_mp4_for_download = old_validate
            downloader._atomic_promote_with_retry = old_promote

    _assert(logs.count(BAT_CONTINUATION_WARNING) == 1, "BAT continuation warning count was wrong")
    _assert(ffmpeg_calls, "FFmpeg was not called after yt-dlp error with MP4")
    _assert(promoted and promoted[0].name.endswith("_FIXED.mp4"), "Converted output was not promoted")
    _assert(source_paths and promoted[0] != source_paths[0], "Source MP4 was promoted directly")
    _assert(final_ok, "Final output was not created from converted MP4")


def _test_fast_ytdlp_failure_without_mp4_reraises() -> None:
    with TemporaryDirectory(prefix="fast_error_without_mp4_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        final_path = root / "final.mp4"
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        validation = _aria2_validation(paths)
        logs: list[str] = []
        original_error = _failure([], YtdlpFailureKind.NETWORK, "aria2c exited with code 22")
        ffmpeg_calls: list[list[str]] = []
        final_exists = False

        old_run_fast = downloader._run_fast_ytdlp_command
        old_ffmpeg = downloader._run_ffmpeg_command
        try:
            def fail_without_mp4(*_args, **_kwargs):
                raise original_error

            def fake_ffmpeg(command, **_kwargs):
                ffmpeg_calls.append(list(command))
                return ""

            downloader._run_fast_ytdlp_command = fail_without_mp4
            downloader._run_ffmpeg_command = fake_ffmpeg
            with _patched_runtime(paths):
                try:
                    downloader._download_video_fast_bat_compatible(
                        "fast-error-no-mp4",
                        root,
                        final_path,
                        options,
                        logs.append,
                        None,
                        validation,
                    )
                except YtdlpExecutionError as exc:
                    _assert(exc is original_error, "Original yt-dlp error was not re-raised")
                else:
                    raise AssertionError("Fast yt-dlp error without MP4 did not fail")
                final_exists = final_path.exists()
        finally:
            downloader._run_fast_ytdlp_command = old_run_fast
            downloader._run_ffmpeg_command = old_ffmpeg

    _assert(not ffmpeg_calls, "FFmpeg ran without usable MP4")
    _assert(not final_exists, "Final file was created without MP4")
    _assert(BAT_CONTINUATION_WARNING not in logs, "Continuation warning was logged without MP4")


def _test_fast_ytdlp_failure_with_zero_byte_mp4_reraises() -> None:
    with TemporaryDirectory(prefix="fast_error_zero_mp4_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        final_path = root / "final.mp4"
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        validation = _aria2_validation(paths)
        logs: list[str] = []
        original_error = _failure([], YtdlpFailureKind.NETWORK, "aria2c exited with code 22")
        ffmpeg_calls: list[list[str]] = []

        old_run_fast = downloader._run_fast_ytdlp_command
        old_ffmpeg = downloader._run_ffmpeg_command
        try:
            def fail_after_zero_mp4(command, *_args, **_kwargs):
                _output_path(command, "mp4").write_bytes(b"")
                raise original_error

            def fake_ffmpeg(command, **_kwargs):
                ffmpeg_calls.append(list(command))
                return ""

            downloader._run_fast_ytdlp_command = fail_after_zero_mp4
            downloader._run_ffmpeg_command = fake_ffmpeg
            with _patched_runtime(paths):
                try:
                    downloader._download_video_fast_bat_compatible(
                        "fast-error-zero-mp4",
                        root,
                        final_path,
                        options,
                        logs.append,
                        None,
                        validation,
                    )
                except YtdlpExecutionError as exc:
                    _assert(exc is original_error, "Original yt-dlp error was not re-raised for zero-byte MP4")
                else:
                    raise AssertionError("Fast yt-dlp error with zero-byte MP4 did not fail")
        finally:
            downloader._run_fast_ytdlp_command = old_run_fast
            downloader._run_ffmpeg_command = old_ffmpeg

    _assert(not ffmpeg_calls, "FFmpeg ran for zero-byte MP4")
    _assert(not final_path.exists(), "Final file was created for zero-byte MP4")
    _assert(BAT_CONTINUATION_WARNING not in logs, "Continuation warning was logged for zero-byte MP4")


def _test_fixed_mp4_is_not_selected_as_source() -> None:
    with TemporaryDirectory(prefix="fast_fixed_selector_") as temp_dir:
        root = Path(temp_dir)
        video_id = "video"
        fixed = root / "video_FIXED.mp4"
        fixed.write_bytes(b"fixed")
        selected = downloader._select_fast_source_mp4(root, video_id)
    _assert(selected is None, "_FIXED.mp4 was selected as Fast source")


def _test_zero_byte_exact_mp4_is_not_selected() -> None:
    with TemporaryDirectory(prefix="fast_zero_exact_selector_") as temp_dir:
        root = Path(temp_dir)
        video_id = "selector-video"
        exact = root / f"{video_id}.mp4"
        exact.write_bytes(b"")
        selected = downloader._select_fast_source_mp4(root, video_id)
    _assert(selected is None, "Zero-byte exact MP4 was selected as Fast source")


def _test_exact_merged_mp4_beats_newer_fragment() -> None:
    with TemporaryDirectory(prefix="fast_exact_selector_") as temp_dir:
        root = Path(temp_dir)
        video_id = "selector-video"
        exact = root / f"{video_id}.mp4"
        fragment = root / f"{video_id}.f137.mp4"
        exact.write_bytes(b"exact")
        fragment.write_bytes(b"fragment-larger")
        _set_mtime_ns(exact, 1_000_000_000)
        _set_mtime_ns(fragment, 2_000_000_000)
        selected = downloader._select_fast_source_mp4(root, video_id)
    _assert(selected == exact, "Exact merged MP4 did not beat newer fragment")


def _test_non_fragment_fallback_beats_newer_fragment() -> None:
    with TemporaryDirectory(prefix="fast_non_fragment_selector_") as temp_dir:
        root = Path(temp_dir)
        video_id = "selector-video"
        alternate = root / "alternate.mp4"
        fragment = root / f"{video_id}.f137.mp4"
        alternate.write_bytes(b"alternate")
        fragment.write_bytes(b"fragment-larger")
        _set_mtime_ns(alternate, 1_000_000_000)
        _set_mtime_ns(fragment, 2_000_000_000)
        selected = downloader._select_fast_source_mp4(root, video_id)
    _assert(selected == alternate, "Non-fragment fallback did not beat newer fragment")


def _test_fragment_is_last_fallback() -> None:
    with TemporaryDirectory(prefix="fast_fragment_selector_") as temp_dir:
        root = Path(temp_dir)
        video_id = "selector-video"
        fragment = root / f"{video_id}.f137.mp4"
        fragment.write_bytes(b"fragment")
        selected = downloader._select_fast_source_mp4(root, video_id)
    _assert(selected == fragment, "Fragment was not selected as last fallback")


def _test_ytdlp_error_prefers_exact_merged_mp4_over_fragment() -> None:
    with TemporaryDirectory(prefix="fast_error_exact_over_fragment_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        video_id = "fast-exact-over-fragment"
        final_path = root / "final.mp4"
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        validation = _aria2_validation(paths)
        logs: list[str] = []
        ffmpeg_inputs: list[Path] = []
        promoted: list[Path] = []
        final_created = False
        old_run_fast = downloader._run_fast_ytdlp_command
        old_ffmpeg = downloader._run_ffmpeg_command
        old_validate = downloader._validate_premiere_safe_mp4_for_download
        old_promote = downloader._atomic_promote_with_retry
        old_build_stable = downloader._build_stable_video_ytdlp_command
        try:
            def fail_after_exact_and_fragment(command, *_args, **_kwargs):
                exact = _output_path(command, "mp4")
                fragment = exact.with_name(f"{exact.stem}.f137.mp4")
                exact.write_bytes(b"exact")
                fragment.write_bytes(b"fragment-larger")
                _set_mtime_ns(exact, 1_000_000_000)
                _set_mtime_ns(fragment, 2_000_000_000)
                raise _failure(command, YtdlpFailureKind.NETWORK, "aria2c exited with code 22")

            def fake_ffmpeg(command, *, operation, cancel_controller=None):
                ffmpeg_inputs.append(Path(command[3]))
                Path(command[-1]).write_bytes(b"fixed")
                return ""

            def fake_promote(source, target, *_args, **_kwargs):
                promoted.append(Path(source))
                Path(target).write_bytes(Path(source).read_bytes())

            def forbidden_stable(*_args, **_kwargs):
                raise AssertionError("Fast generated a Stable fallback command")

            downloader._run_fast_ytdlp_command = fail_after_exact_and_fragment
            downloader._run_ffmpeg_command = fake_ffmpeg
            downloader._validate_premiere_safe_mp4_for_download = lambda *_args, **_kwargs: None
            downloader._atomic_promote_with_retry = fake_promote
            downloader._build_stable_video_ytdlp_command = forbidden_stable
            with _patched_runtime(paths):
                downloader._download_video_fast_bat_compatible(
                    video_id,
                    root,
                    final_path,
                    options,
                    logs.append,
                    None,
                    validation,
                )
                final_created = final_path.exists()
        finally:
            downloader._run_fast_ytdlp_command = old_run_fast
            downloader._run_ffmpeg_command = old_ffmpeg
            downloader._validate_premiere_safe_mp4_for_download = old_validate
            downloader._atomic_promote_with_retry = old_promote
            downloader._build_stable_video_ytdlp_command = old_build_stable

    expected = root / f"{video_id}.mp4"
    fragment = root / f"{video_id}.f137.mp4"
    _assert(ffmpeg_inputs == [expected], f"FFmpeg did not receive exact merged MP4: {ffmpeg_inputs}")
    _assert(fragment not in ffmpeg_inputs, "FFmpeg received fragment instead of exact merged MP4")
    _assert(promoted and promoted[0].name.endswith("_FIXED.mp4"), "Converted MP4 was not promoted")
    _assert(final_created, "Final MP4 was not created")


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
                raise _failure(command, YtdlpFailureKind.NETWORK, "aria2c exited with code 22")

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
    old_download_source = downloader._download_fast_video_source
    old_convert = downloader._convert_and_promote_fast_video
    old_mark = downloader._mark_part_error
    old_reconcile = downloader._reconcile_current_item
    old_get_entry = downloader.get_video_entry
    old_is_complete = downloader.is_mode_complete
    old_missing = downloader.missing_parts_for_mode
    old_missing_current = downloader._missing_parts_for_current_paths
    old_validate_env = downloader.validate_download_environment
    old_ensure_dirs = downloader.ensure_output_dirs
    old_summary = downloader._call_runtime_tool_summary
    old_prepare_runtime = downloader._prepare_media_downloader_runtime
    try:
        def fake_source(video_id, staging_dir, *_args, **_kwargs):
            source = Path(staging_dir) / f"{video_id}.mp4"
            source.write_bytes(b"source")
            return source

        downloader._download_fast_video_source = fake_source
        downloader._convert_and_promote_fast_video = lambda *_args, **_kwargs: (_ for _ in ()).throw(
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
        downloader._missing_parts_for_current_paths = lambda *_args, **_kwargs: (PART_VIDEO,)
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
        downloader._download_fast_video_source = old_download_source
        downloader._convert_and_promote_fast_video = old_convert
        downloader._mark_part_error = old_mark
        downloader._reconcile_current_item = old_reconcile
        downloader.get_video_entry = old_get_entry
        downloader.is_mode_complete = old_is_complete
        downloader.missing_parts_for_mode = old_missing
        downloader._missing_parts_for_current_paths = old_missing_current
        downloader.validate_download_environment = old_validate_env
        downloader.ensure_output_dirs = old_ensure_dirs
        downloader._call_runtime_tool_summary = old_summary
        downloader._prepare_media_downloader_runtime = old_prepare_runtime
    _assert(marked_parts == [PART_VIDEO], f"Outer state handling did not mark video failed: {marked_parts}")
    _assert(statuses and statuses[-1] == downloader.STATUS_ERROR, "Outer state handling did not publish error status")


def _test_corrupt_fast_mp4_is_not_promoted() -> None:
    with TemporaryDirectory(prefix="fast_corrupt_mp4_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        final_path = root / "final.mp4"
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        validation = _aria2_validation(paths)
        promoted = False

        old_run_fast = downloader._run_fast_ytdlp_command
        old_ffmpeg = downloader._run_ffmpeg_command
        old_validate = downloader._validate_premiere_safe_mp4_for_download
        old_promote = downloader._atomic_promote_with_retry
        try:
            def fail_after_mp4(command, *_args, **_kwargs):
                _output_path(command, "mp4").write_bytes(b"corrupt")
                raise _failure(command, YtdlpFailureKind.NETWORK, "aria2c exited with code 22")

            def fake_ffmpeg(command, *, operation, cancel_controller=None):
                Path(command[-1]).write_bytes(b"fixed-but-invalid")
                return ""

            def fail_validation(*_args, **_kwargs):
                raise DownloadError("premiere_safe_mp4_validation_failed: corrupt")

            def fake_promote(*_args, **_kwargs):
                nonlocal promoted
                promoted = True
                raise AssertionError("Fast promoted corrupt MP4")

            downloader._run_fast_ytdlp_command = fail_after_mp4
            downloader._run_ffmpeg_command = fake_ffmpeg
            downloader._validate_premiere_safe_mp4_for_download = fail_validation
            downloader._atomic_promote_with_retry = fake_promote
            with _patched_runtime(paths):
                try:
                    downloader._download_video_fast_bat_compatible(
                        "fast-corrupt",
                        root,
                        final_path,
                        options,
                        lambda _message: None,
                        None,
                        validation,
                    )
                except DownloadError as exc:
                    _assert("corrupt" in str(exc), "Validation failure did not propagate")
                else:
                    raise AssertionError("Corrupt Fast MP4 was accepted")
        finally:
            downloader._run_fast_ytdlp_command = old_run_fast
            downloader._run_ffmpeg_command = old_ffmpeg
            downloader._validate_premiere_safe_mp4_for_download = old_validate
            downloader._atomic_promote_with_retry = old_promote

    _assert(not promoted, "Corrupt MP4 was promoted")
    _assert(not final_path.exists(), "Final file was created for corrupt MP4")


def _test_fast_cancellation_before_continuation() -> None:
    with TemporaryDirectory(prefix="fast_cancel_before_continue_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        final_path = root / "final.mp4"
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        validation = _aria2_validation(paths)
        controller = downloader.DownloadController()
        ffmpeg_calls: list[list[str]] = []
        promoted = False

        old_run_fast = downloader._run_fast_ytdlp_command
        old_ffmpeg = downloader._run_ffmpeg_command
        old_promote = downloader._atomic_promote_with_retry
        try:
            def fail_after_cancel(command, *_args, **_kwargs):
                _output_path(command, "mp4").write_bytes(b"source")
                controller.request_cancel()
                raise _failure(command, YtdlpFailureKind.NETWORK, "aria2c exited with code 22")

            def fake_ffmpeg(command, **_kwargs):
                ffmpeg_calls.append(list(command))
                raise AssertionError("FFmpeg started after cancellation")

            def fake_promote(*_args, **_kwargs):
                nonlocal promoted
                promoted = True
                raise AssertionError("Promotion ran after cancellation")

            downloader._run_fast_ytdlp_command = fail_after_cancel
            downloader._run_ffmpeg_command = fake_ffmpeg
            downloader._atomic_promote_with_retry = fake_promote
            with _patched_runtime(paths):
                try:
                    downloader._download_video_fast_bat_compatible(
                        "fast-cancel",
                        root,
                        final_path,
                        options,
                        lambda _message: None,
                        controller,
                        validation,
                    )
                except DownloadCancelled:
                    pass
                else:
                    raise AssertionError("Cancellation did not stop Fast continuation")
        finally:
            downloader._run_fast_ytdlp_command = old_run_fast
            downloader._run_ffmpeg_command = old_ffmpeg
            downloader._atomic_promote_with_retry = old_promote

    _assert(not ffmpeg_calls, "FFmpeg started after cancellation")
    _assert(not promoted, "Promotion ran after cancellation")
    _assert(not final_path.exists(), "Final file was created after cancellation")


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
    _assert("--ignore-errors" not in command, "Fast audio command contained --ignore-errors")
    _assert("-N" not in command, "Fast audio contained Stable -N")


def _test_fast_audio_remains_strict() -> None:
    with TemporaryDirectory(prefix="fast_audio_strict_") as temp_dir:
        root = Path(temp_dir)
        paths = _runtime_paths(root, aria2=True)
        final_path = root / "final.mp3"
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        validation = _aria2_validation(paths)
        moved = False
        original_error: YtdlpExecutionError | None = None

        old_run_fast = downloader._run_fast_ytdlp_command
        old_move = downloader._move_single_file
        try:
            def fail_fast_audio(command, *_args, **_kwargs):
                nonlocal original_error
                _output_path(command, "mp3").write_bytes(b"partial")
                original_error = _failure(
                    command,
                    YtdlpFailureKind.NETWORK,
                    "aria2c audio transfer failed",
                    part=PART_AUDIO,
                )
                raise original_error

            def fake_move(*_args, **_kwargs):
                nonlocal moved
                moved = True
                raise AssertionError("Fast audio moved partial file after yt-dlp failure")

            downloader._run_fast_ytdlp_command = fail_fast_audio
            downloader._move_single_file = fake_move
            with _patched_runtime(paths):
                try:
                    downloader._download_audio(
                        "fast-audio-strict",
                        "fast-audio-strict",
                        root,
                        final_path,
                        options,
                        lambda _message: None,
                        aria2_validation=validation,
                    )
                except YtdlpExecutionError as exc:
                    _assert(original_error is not None and exc is original_error, "Fast audio did not re-raise yt-dlp error")
                else:
                    raise AssertionError("Fast audio failure did not propagate")
        finally:
            downloader._run_fast_ytdlp_command = old_run_fast
            downloader._move_single_file = old_move

    _assert(not moved, "Fast audio attempted to move a partial file")
    _assert(not final_path.exists(), "Fast audio created final output after yt-dlp failure")


def _test_fast_has_no_automatic_fallback() -> None:
    calls: list[list[str]] = []
    old_run_fast = downloader._run_fast_ytdlp_command
    old_ffmpeg = downloader._run_ffmpeg_command
    old_validate = downloader._validate_premiere_safe_mp4_for_download
    old_promote = downloader._atomic_promote_with_retry
    old_build_stable = downloader._build_stable_video_ytdlp_command
    try:
        def fake_ffmpeg(command, *, operation, cancel_controller=None):
            calls.append(list(command))
            Path(command[-1]).write_bytes(b"fixed")
            return ""

        def fake_promote(source, target, *_args, **_kwargs):
            Path(target).write_bytes(Path(source).read_bytes())

        def forbidden_stable(*_args, **_kwargs):
            raise AssertionError("Fast generated a Stable fallback command")

        downloader._run_ffmpeg_command = fake_ffmpeg
        downloader._validate_premiere_safe_mp4_for_download = lambda *_args, **_kwargs: None
        downloader._atomic_promote_with_retry = fake_promote
        downloader._build_stable_video_ytdlp_command = forbidden_stable

        with TemporaryDirectory(prefix="fast_no_fallback_with_mp4_") as temp_dir:
            root = Path(temp_dir)
            paths = _runtime_paths(root, aria2=True)
            validation = _aria2_validation(paths)
            options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)

            def fail_with_mp4(command, *_args, **_kwargs):
                calls.append(list(command))
                _output_path(command, "mp4").write_bytes(b"source")
                raise _failure(command, YtdlpFailureKind.NETWORK, "connection reset by peer")

            downloader._run_fast_ytdlp_command = fail_with_mp4
            with _patched_runtime(paths):
                downloader._download_video_fast_bat_compatible(
                    "fast-failure-with-mp4",
                    root,
                    root / "final.mp4",
                    options,
                    lambda _message: None,
                    None,
                    validation,
                )

        with TemporaryDirectory(prefix="fast_no_fallback_without_mp4_") as temp_dir:
            root = Path(temp_dir)
            paths = _runtime_paths(root, aria2=True)
            validation = _aria2_validation(paths)
            options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)

            def fail_without_mp4(command, *_args, **_kwargs):
                calls.append(list(command))
                raise _failure(command, YtdlpFailureKind.NETWORK, "connection reset by peer")

            downloader._run_fast_ytdlp_command = fail_without_mp4
            with _patched_runtime(paths):
                try:
                    downloader._download_video_fast_bat_compatible(
                        "fast-failure-without-mp4",
                        root,
                        root / "final.mp4",
                        options,
                        lambda _message: None,
                        None,
                        validation,
                    )
                except YtdlpExecutionError:
                    pass
                else:
                    raise AssertionError("Fast failure without MP4 did not propagate")
    finally:
        downloader._run_fast_ytdlp_command = old_run_fast
        downloader._run_ffmpeg_command = old_ffmpeg
        downloader._validate_premiere_safe_mp4_for_download = old_validate
        downloader._atomic_promote_with_retry = old_promote
        downloader._build_stable_video_ytdlp_command = old_build_stable

    _assert(len(calls) == 3, f"Fast failure made unexpected commands: {len(calls)}")
    _assert(any(_contains_aria2(command) for command in calls), "Fast failure did not run aria2 command")
    for command in calls:
        _assert(_option_value(command, "-N") != "1", "Fast failure generated or executed Stable -N 1")


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

        validation = _aria2_validation(paths)
        old_run_fast = downloader._run_fast_ytdlp_command
        old_ffmpeg = downloader._run_ffmpeg_command
        old_validate = downloader._validate_premiere_safe_mp4_for_download
        old_promote = downloader._atomic_promote_with_retry
        try:
            api_key_name = "api" + "_key"
            signature_key = "signature" + "="
            auth_header_name = "author" + "ization"
            token_secret = "token" + "=secret"

            def fail_after_mp4(command, *_args, **_kwargs):
                _output_path(command, "mp4").write_bytes(b"source")
                raise _failure(
                    command,
                    YtdlpFailureKind.NETWORK,
                    (
                        "signed=https://media.example/video.mp4?"
                        f"{signature_key}secret&{api_key_name}=hidden "
                        f"{auth_header_name}: bearer token"
                    ),
                )

            def fake_ffmpeg(command, *, operation, cancel_controller=None):
                Path(command[-1]).write_bytes(b"fixed")
                return ""

            def fake_promote(source, target, *_args, **_kwargs):
                Path(target).write_bytes(Path(source).read_bytes())

            downloader._run_fast_ytdlp_command = fail_after_mp4
            downloader._run_ffmpeg_command = fake_ffmpeg
            downloader._validate_premiere_safe_mp4_for_download = lambda *_args, **_kwargs: None
            downloader._atomic_promote_with_retry = fake_promote
            with _patched_runtime(paths):
                downloader._download_video_fast_bat_compatible(
                    "fast-secret-log",
                    root,
                    root / "final.mp4",
                    options,
                    logs.append,
                    None,
                    validation,
                )
        finally:
            downloader._run_fast_ytdlp_command = old_run_fast
            downloader._run_ffmpeg_command = old_ffmpeg
            downloader._validate_premiere_safe_mp4_for_download = old_validate
            downloader._atomic_promote_with_retry = old_promote
    joined = "\n".join(logs)
    _assert(logs.count(BAT_CONTINUATION_WARNING) == 1, "Secret-safety test did not log exact continuation warning")
    for forbidden in (
        str(cookie_path),
        "secret-cookie-file",
        "cookie_secret_value",
        api_key_name,
        signature_key,
        auth_header_name,
        token_secret,
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
    _assert("--ignore-errors" in text, "Sanitized probe missed --ignore-errors")
    _assert("--cookies" not in text, "Sanitized probe exposed cookies")


def _failure(
    command: list[str],
    kind: YtdlpFailureKind,
    text: str,
    *,
    part: str = PART_VIDEO,
) -> YtdlpExecutionError:
    return YtdlpExecutionError(
        1,
        text,
        [text],
        combined_output=text,
        stream_interrupted=kind in {YtdlpFailureKind.NETWORK, YtdlpFailureKind.NETWORK_TIMEOUT},
        failure_kind=kind,
        fatal_lines=[text],
        stage=YTDLP_STAGE_DOWNLOAD,
        part=part,
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
        file_start_number=1,
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


def _set_mtime_ns(path: Path, modified_ns: int) -> None:
    os.utime(path, ns=(modified_ns, modified_ns))


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
