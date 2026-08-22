import json
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
    ARIA2_FAST_DOWNLOADER_ARGS,
    DOWNLOAD_ENGINE_ARIA2_FAST,
    DOWNLOAD_ENGINE_STABLE,
    DownloadError,
    DownloadOptions,
    YTDLP_STAGE_DOWNLOAD,
    YtdlpExecutionError,
    YtdlpFailureKind,
)


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"
VIDEO_ID = "video-id"


def main() -> int:
    _test_stable_video_command_unchanged()
    _test_default_explicit_stable_and_malformed_engine_are_stable()
    _test_fast_video_command_matches_stable_except_downloader()
    _test_fast_audio_command_matches_stable_except_downloader()
    _test_fast_has_no_extractor_override()
    _test_fast_has_no_direct_cookie_option()
    _test_fast_has_no_ignore_errors()
    _test_fast_uses_premiere_safe_selector()
    _test_fast_uses_aria2_profile()
    _test_command_uses_aria2_detects_only_fast()
    _test_aria2_code_22_helper_is_video_only()
    _test_authenticated_extract_strips_aria2()
    _test_saved_media_transfer_retains_aria2()
    _test_fast_uses_retry_pipeline()
    _test_fast_uses_isolated_cookie_copy()
    _test_fast_uses_lookahead()
    _test_fast_has_no_full_transcode()
    _test_strict_format_failure_uses_existing_flow()
    _test_runtime_logs_describe_engine_only_difference()
    _test_stable_runtime_does_not_require_aria2()
    print("aria2 download engine smoke passed")
    return 0


def _test_stable_video_command_unchanged() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        options = _options(root, DOWNLOAD_ENGINE_STABLE)
        with _patched_runtime(paths):
            command = downloader._build_stable_video_ytdlp_command(VIDEO_ID, root, options)

        expected = [
            str(paths.ytdlp),
            "--no-playlist",
            "--newline",
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
            "--ffmpeg-location",
            str(paths.ffmpeg.parent),
            "-N",
            "1",
            "-f",
            downloader.PREMIERE_SAFE_VIDEO_FORMAT,
            "--merge-output-format",
            "mp4",
            "--no-write-info-json",
            "--no-write-description",
            "--no-write-thumbnail",
            "-o",
            str(root / f"{VIDEO_ID}.%(ext)s"),
            f"https://www.youtube.com/watch?v={VIDEO_ID}",
        ]
        _assert(command == expected, f"Stable video command changed: {command}")


def _test_default_explicit_stable_and_malformed_engine_are_stable() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        with _patched_runtime(paths):
            default_options = _options(root, DOWNLOAD_ENGINE_STABLE)
            explicit_options = _options(root, DOWNLOAD_ENGINE_STABLE)
            malformed_options = _options(root, "malformed")
            default_command = downloader._build_video_ytdlp_command(VIDEO_ID, root, default_options)
            explicit_command = downloader._build_video_ytdlp_command(VIDEO_ID, root, explicit_options)
            malformed_command = downloader._build_video_ytdlp_command(VIDEO_ID, root, malformed_options)

        _assert(default_command == explicit_command, "Default engine did not match explicit stable")
        _assert(malformed_command == explicit_command, "Malformed engine did not normalize to stable")
        for command in (default_command, explicit_command, malformed_command):
            _assert("-N" in command and _option_value(command, "-N") == "1", "Stable command missed -N 1")
            _assert("--downloader" not in command, "Stable command unexpectedly used aria2")
            _assert("--downloader-args" not in command, "Stable command unexpectedly used aria2 args")


def _test_fast_video_command_matches_stable_except_downloader() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        aria2_validation = _aria2_validation(paths)
        stable_options = _options(root, DOWNLOAD_ENGINE_STABLE)
        fast_options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        with _patched_runtime(paths):
            stable_command = downloader._build_stable_video_ytdlp_command(VIDEO_ID, root, stable_options)
            fast_command = downloader._build_fast_video_ytdlp_command(
                VIDEO_ID,
                root,
                fast_options,
                aria2_validation,
            )

        _assert(
            _remove_aria2_options(fast_command) == stable_command,
            "Fast video command differs from Stable beyond aria2 options",
        )


def _test_fast_audio_command_matches_stable_except_downloader() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        aria2_validation = _aria2_validation(paths)
        stable_options = _options(root, DOWNLOAD_ENGINE_STABLE)
        fast_options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        with _patched_runtime(paths):
            stable_command = downloader._build_stable_audio_ytdlp_command(VIDEO_ID, root, stable_options)
            fast_command = downloader._build_fast_audio_ytdlp_command(
                VIDEO_ID,
                root,
                fast_options,
                aria2_validation,
            )

        _assert(
            _remove_aria2_options(fast_command) == stable_command,
            "Fast audio command differs from Stable beyond aria2 options",
        )


def _test_fast_has_no_extractor_override() -> None:
    command = _fast_video_command()
    _assert("--extractor-args" not in command, "Fast still overrides extractor args")
    _assert("ios,web" not in _joined(command), "Fast still forces ios,web")


def _test_fast_has_no_direct_cookie_option() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cookie_path = root / "canonical-cookies.txt"
        cookie_path.write_text("cookie-data", encoding="utf-8")
        paths = _runtime_paths(root)
        options = _options(
            root,
            DOWNLOAD_ENGINE_ARIA2_FAST,
            cookies_enabled=True,
            cookies_path=str(cookie_path),
        )
        with _patched_runtime(paths):
            command = downloader._build_fast_video_ytdlp_command(
                VIDEO_ID,
                root,
                options,
                _aria2_validation(paths),
            )

    _assert("--cookies" not in command, "Fast base command directly contains cookies")
    _assert(str(cookie_path) not in command, "Fast command directly contains canonical cookie path")


def _test_fast_has_no_ignore_errors() -> None:
    command = _fast_video_command()
    _assert("--ignore-errors" not in command, "Fast still uses ignore-errors")
    _assert("--no-warnings" not in command, "Fast still suppresses warnings")


def _test_fast_uses_premiere_safe_selector() -> None:
    command = _fast_video_command()
    _assert(
        _option_value(command, "-f") == downloader.PREMIERE_SAFE_VIDEO_FORMAT,
        "Fast did not use Stable Premiere-safe selector",
    )
    text = _joined(command)
    for forbidden in ("bestvideo", "bestaudio", " best", "vp9", "av01"):
        _assert(forbidden not in text, f"Fast command contains unrestricted selector value: {forbidden}")


def _test_fast_uses_aria2_profile() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        command = _fast_video_command(root, paths)
    _assert(_option_value(command, "--downloader") == str(paths.aria2), "Fast aria2 path mismatch")
    _assert(
        _option_value(command, "--downloader-args") == ARIA2_FAST_DOWNLOADER_ARGS,
        "Fast aria2 profile mismatch",
    )


def _test_command_uses_aria2_detects_only_fast() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        fast_command = _fast_video_command(root, paths)
        stable_options = _options(root, DOWNLOAD_ENGINE_STABLE)
        with _patched_runtime(paths):
            stable_command = downloader._build_stable_video_ytdlp_command(
                VIDEO_ID,
                root,
                stable_options,
            )

    _assert(downloader._command_uses_aria2(fast_command), "Fast command was not recognized as aria2")
    _assert(not downloader._command_uses_aria2(stable_command), "Stable command was recognized as aria2")
    _assert(downloader._command_uses_aria2(["yt-dlp", "--downloader", "aria2c"]), "aria2c name missed")
    _assert(
        downloader._command_uses_aria2(["yt-dlp", "--downloader", "aria2c.exe"]),
        "aria2c.exe name missed",
    )


def _test_aria2_code_22_helper_is_video_only() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        fast_command = _fast_video_command(root, paths)
        stable_options = _options(root, DOWNLOAD_ENGINE_STABLE)
        with _patched_runtime(paths):
            stable_command = downloader._build_stable_video_ytdlp_command(
                VIDEO_ID,
                root,
                stable_options,
            )

    line = "ERROR: aria2c exited with code 22"
    video_error = YtdlpExecutionError(
        1,
        "nonzero yt-dlp exit code",
        [line],
        combined_output=line,
        failure_kind=YtdlpFailureKind.UNKNOWN,
        fatal_lines=[line],
        stage=YTDLP_STAGE_DOWNLOAD,
        part=PART_VIDEO,
        command=fast_command,
    )
    audio_error = YtdlpExecutionError(
        1,
        "nonzero yt-dlp exit code",
        [line],
        combined_output=line,
        failure_kind=YtdlpFailureKind.UNKNOWN,
        fatal_lines=[line],
        stage=YTDLP_STAGE_DOWNLOAD,
        part=downloader.PART_AUDIO,
        command=fast_command,
    )
    stable_error = YtdlpExecutionError(
        1,
        "nonzero yt-dlp exit code",
        [line],
        combined_output=line,
        failure_kind=YtdlpFailureKind.UNKNOWN,
        fatal_lines=[line],
        stage=YTDLP_STAGE_DOWNLOAD,
        part=PART_VIDEO,
        command=stable_command,
    )
    _assert(downloader._is_aria2_http_response_media_failure(video_error), "Fast video code 22 was missed")
    _assert(not downloader._is_aria2_http_response_media_failure(audio_error), "Fast audio code 22 matched")
    _assert(not downloader._is_aria2_http_response_media_failure(stable_error), "Stable code-22 text matched")


def _test_authenticated_extract_strips_aria2() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        command = _fast_video_command(root, paths)
        extract_command = downloader._build_authenticated_infojson_extract_command(
            command,
            str(root / "bootstrap.%(ext)s"),
        )

    _assert("--downloader" not in extract_command, "Metadata extraction retained aria2 downloader")
    _assert("--downloader-args" not in extract_command, "Metadata extraction retained aria2 args")
    _assert(not downloader._command_uses_aria2(extract_command), "Metadata extraction was aria2-backed")
    _assert("--skip-download" in extract_command, "Metadata extraction missed skip-download")
    _assert("--write-info-json" in extract_command, "Metadata extraction missed write-info-json")


def _test_saved_media_transfer_retains_aria2() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        command = _fast_video_command(root, paths)
        info_json_path = root / "media.info.json"
        media_command = downloader._build_infojson_media_download_command(command, info_json_path)

    _assert(_option_value(media_command, "--downloader") == str(paths.aria2), "Saved-media transfer lost aria2")
    _assert(downloader._command_uses_aria2(media_command), "Saved-media transfer was not aria2-backed")
    _assert(
        _option_value(media_command, "--downloader-args") == ARIA2_FAST_DOWNLOADER_ARGS,
        "Saved-media transfer lost aria2 profile",
    )
    _assert(
        _option_value(media_command, "--load-info-json") == str(info_json_path),
        "Saved-media transfer missed load-info-json",
    )
    _assert("--cookies" not in media_command, "Saved-media transfer still contains cookies")
    _assert(
        not any(str(value).startswith("https://www.youtube.com/") for value in media_command),
        "Saved-media transfer still contains the watch URL",
    )


def _test_fast_uses_retry_pipeline() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        stable_options = _options(root, DOWNLOAD_ENGINE_STABLE)
        fast_options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        calls: list[tuple[str, list[str]]] = []
        direct_calls: list[list[str]] = []

        def fake_retry(command, options, log, cancel_controller=None, cookie_retry_state=None):
            calls.append((options.download_engine, list(command)))
            _write_hybrid_fixture(command)

        def fake_direct(command, cancel_controller=None):
            direct_calls.append(list(command))
            raise AssertionError("Fast used direct yt-dlp runner")

        def fake_select(staging_dir, pattern, suffix):
            return root / "staged.mp4"

        def fake_validate(path, log, delete_invalid, cancel_controller):
            return None

        def fake_promote(source, final, log, replace_existing=False, cancel_controller=None):
            final.parent.mkdir(parents=True, exist_ok=True)
            final.write_bytes(b"mp4")

        def fake_ffmpeg(command, **kwargs):
            Path(command[-1]).write_bytes(b"merged")
            return ""

        with _patched_runtime(paths), _patched_attr(downloader, "_run_ytdlp_with_retries", fake_retry), _patched_attr(
            downloader, "_run_ytdlp", fake_direct
        ), _patched_attr(downloader, "_select_staged_file", fake_select), _patched_attr(
            downloader, "_validate_premiere_safe_mp4_for_download", fake_validate
        ), _patched_attr(
            downloader, "_atomic_promote_with_retry", fake_promote
        ), _patched_attr(
            downloader, "_run_ffmpeg_command", fake_ffmpeg
        ):
            downloader._download_video(
                VIDEO_ID,
                "001 Title",
                root,
                root / "stable.mp4",
                stable_options,
                _noop_log,
                aria2_validation=_aria2_validation(paths),
            )
            downloader._download_video(
                VIDEO_ID,
                "001 Title",
                root,
                root / "fast.mp4",
                fast_options,
                _noop_log,
                aria2_validation=_aria2_validation(paths),
            )

    _assert(len(calls) == 4, f"Retry runner did not receive Stable plus three Fast stages: {calls}")
    _assert(not direct_calls, "Direct yt-dlp runner was used")
    _assert(not hasattr(downloader, "_run_fast_ytdlp_command"), "Fast-specific yt-dlp runner still exists")
    _assert("--downloader" not in calls[0][1], "Stable retry command unexpectedly had aria2")
    _assert("--write-info-json" in calls[1][1], "Fast metadata extraction was not routed through retries")
    _assert("--downloader" not in calls[1][1], "Fast metadata extraction used aria2")
    _assert("--downloader" in calls[2][1], "Fast video transport missed aria2")
    _assert("--downloader" not in calls[3][1], "Fast companion audio transport used aria2")
    _assert(_option_value(calls[2][1], "--load-info-json") == _option_value(calls[3][1], "--load-info-json"), "Fast transports used different snapshots")


def _test_fast_uses_isolated_cookie_copy() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        canonical = root / "canonical-cookies.txt"
        cookie_bytes = (
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tsecret-cookie\n"
        ).encode("utf-8")
        canonical.write_bytes(cookie_bytes)
        options = _options(
            root,
            DOWNLOAD_ENGINE_ARIA2_FAST,
            cookies_enabled=True,
            cookies_path=str(canonical),
        )
        command = _fast_video_command(root, paths, options)

        with downloader._prepared_cookie_attempt(command, options, _noop_log, use_cookies=True) as prepared:
            temp_cookie_path = Path(prepared.temp_cookie_path)
            _assert(_option_count(prepared.command, "--cookies") == 1, "Prepared command did not contain one cookies option")
            _assert(temp_cookie_path != canonical, "Temporary cookie path matched canonical path")
            _assert(temp_cookie_path.name == "cookies.txt", "Temporary cookie filename was not cookies.txt")
            _assert("s9h_cookie_attempt" in temp_cookie_path.parent.name, "Temporary cookie directory name changed")
            _assert(temp_cookie_path.exists(), "Temporary cookie copy did not exist inside context")
            _assert(temp_cookie_path.read_bytes() == cookie_bytes, "Temporary cookie copy content differed")
            _assert(canonical.read_bytes() == cookie_bytes, "Canonical cookie file was modified")
            _assert("--downloader" in prepared.command, "Prepared Fast command lost aria2")
            remembered_temp_path = temp_cookie_path

        _assert(not remembered_temp_path.exists(), "Temporary cookie copy was not deleted after context")
        _assert(canonical.read_bytes() == cookie_bytes, "Canonical cookie file changed after context")


def _test_fast_uses_lookahead() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST, cookies_enabled=True, cookies_path=str(root / "cookies.txt"))
        batch_state = downloader._YtdlpBatchState(cookie_bootstrap_media_mode=True)
        current = SimpleNamespace(video_id="A", title="A", sanitized_filename_base="001 A")
        nxt = SimpleNamespace(video_id="B", title="B", sanitized_filename_base="002 B")
        started: list[tuple[str, str]] = []

        def fake_find(videos, start_index, callback_options):
            _assert(callback_options.download_engine == DOWNLOAD_ENGINE_ARIA2_FAST, "Lookahead lost Fast options")
            return nxt, "002 B", root

        def fake_start(batch, video_id, title, channel_dir, callback_options, log, cancel_controller):
            started.append((callback_options.download_engine, video_id))

        with _patched_attr(downloader, "_find_cookie_media_lookahead_candidate", fake_find), _patched_attr(
            downloader, "_start_cookie_media_lookahead", fake_start
        ):
            state, prefetched = downloader._video_attempt_state_for_batch(
                [current, nxt],
                1,
                "A",
                options,
                batch_state,
                _noop_log,
                None,
            )
            _assert(prefetched is None, "Unexpected prefetched media before current video")
            _assert(state.lookahead_callback is not None, "Fast did not create a lookahead callback")
            downloader._start_attempt_lookahead(state, _noop_log)

    _assert(started == [(DOWNLOAD_ENGINE_ARIA2_FAST, "B")], f"Fast lookahead did not start: {started}")


def _test_fast_has_no_full_transcode() -> None:
    command = _fast_video_command()
    text = _joined(command)
    for forbidden in ("libx264", "-crf", "-preset", "-c:v", "-c:a", "-progress", "pipe:1", "_FIXED"):
        _assert(forbidden not in text, f"Fast video command contains transcode option: {forbidden}")
    _assert(not hasattr(downloader, "_transcode_fast_video_like_bat"), "Fast transcode helper still exists")
    _assert(not hasattr(downloader, "_run_ffmpeg_for_fast_video"), "Fast FFmpeg helper still exists")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        ffmpeg_calls: list[list[str]] = []

        def fake_retry(command, options, log, cancel_controller=None, cookie_retry_state=None):
            _write_hybrid_fixture(command)

        def fake_select(staging_dir, pattern, suffix):
            return root / "staged.mp4"

        def fake_validate(path, log, delete_invalid, cancel_controller):
            return None

        def fake_promote(source, final, log, replace_existing=False, cancel_controller=None):
            final.parent.mkdir(parents=True, exist_ok=True)
            final.write_bytes(b"mp4")

        def fake_ffmpeg(command, **kwargs):
            ffmpeg_calls.append(list(command))
            Path(command[-1]).write_bytes(b"merged")
            return ""

        with _patched_runtime(paths), _patched_attr(downloader, "_run_ytdlp_with_retries", fake_retry), _patched_attr(
            downloader, "_select_staged_file", fake_select
        ), _patched_attr(downloader, "_validate_premiere_safe_mp4_for_download", fake_validate), _patched_attr(
            downloader, "_atomic_promote_with_retry", fake_promote
        ), _patched_attr(
            downloader, "_run_ffmpeg_command", fake_ffmpeg
        ):
            downloader._download_video(
                VIDEO_ID,
                "001 Title",
                root,
                root / "fast.mp4",
                options,
                _noop_log,
                aria2_validation=_aria2_validation(paths),
            )

    _assert(len(ffmpeg_calls) == 1, "Fast production path did not perform exactly one merge")
    merge_text = _joined(ffmpeg_calls[0])
    _assert("-c copy" in merge_text, "Fast merge was not stream copy")
    for forbidden in ("libx264", "-crf", "-preset"):
        _assert(forbidden not in merge_text, f"Fast merge unexpectedly transcoded: {forbidden}")


def _test_strict_format_failure_uses_existing_flow() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        retry_calls: list[list[str]] = []
        select_calls: list[object] = []

        def fake_retry(command, options, log, cancel_controller=None, cookie_retry_state=None):
            retry_calls.append(list(command))
            raise YtdlpExecutionError(
                1,
                "Requested format is not available",
                ["ERROR: Requested format is not available"],
                failure_kind=YtdlpFailureKind.FORMAT_UNAVAILABLE,
                stage=YTDLP_STAGE_DOWNLOAD,
                part=PART_VIDEO,
                command=command,
            )

        def fake_select(*args):
            select_calls.append(args)
            raise AssertionError("Strict format failure should not select staged files")

        with _patched_runtime(paths), _patched_attr(downloader, "_run_ytdlp_with_retries", fake_retry), _patched_attr(
            downloader, "_select_staged_file", fake_select
        ):
            try:
                downloader._download_video(
                    VIDEO_ID,
                    "001 Title",
                    root,
                    root / "fast.mp4",
                    options,
                    _noop_log,
                    aria2_validation=_aria2_validation(paths),
                )
            except YtdlpExecutionError as exc:
                _assert(
                    exc.failure_kind == YtdlpFailureKind.FORMAT_UNAVAILABLE,
                    "Format-unavailable classification was not preserved",
                )
            else:
                raise AssertionError("Format-unavailable error did not propagate")

    _assert(len(retry_calls) == 1, f"Unexpected format retry count: {len(retry_calls)}")
    command_text = _joined(retry_calls[0])
    _assert(downloader.PREMIERE_SAFE_VIDEO_FORMAT in command_text, "Strict selector was not used")
    _assert("bestvideo" not in command_text, "Strict failure retried with unrestricted bestvideo")
    _assert("bestaudio" not in command_text, "Strict failure retried with unrestricted bestaudio")
    _assert("libx264" not in command_text, "Strict failure triggered transcode fallback")
    _assert(not select_calls, "Strict failure attempted staged file selection")


def _test_runtime_logs_describe_engine_only_difference() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root)
        logs: list[str] = []
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        with _patched_runtime(paths), _patched_attr(
            downloader,
            "_get_command_version",
            lambda *args, **kwargs: "aria2 version 1.37.0",
        ):
            validation = downloader._prepare_media_downloader_runtime(options, logs.append)

    _assert(validation.available, "Fast runtime validation did not succeed")
    text = "\n".join(logs)
    for expected in (
        "Download engine: aria2c accelerated video transfer with native companion audio",
        "Fast pipeline: one metadata snapshot, split video/audio transfer, stream-copy merge, validation and promotion.",
        "Fast format: MP4 H.264/AAC only, max 1080p.",
        "aria2c profile: connections=16 splits=16 jobs=16 piece=1M",
        "Fast post-processing: merge/remux only; no full video transcode.",
    ):
        _assert(expected in text, f"Fast runtime log missing: {expected}")
    for forbidden in (
        "BAT-compatible",
        "ios,web",
        "bestvideo+bestaudio",
        "libx264",
        "crf=18",
        "Fast batch phase 1/2",
        "Fast batch phase 2/2",
        "cookies.txt",
        "Authorization:",
        "X-Goog",
        "signature=",
    ):
        _assert(forbidden not in text, f"Fast runtime log exposed forbidden text: {forbidden}")


def _test_stable_runtime_does_not_require_aria2() -> None:
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _runtime_paths(root, aria2=False)
        logs: list[str] = []
        options = _options(root, DOWNLOAD_ENGINE_STABLE)
        with _patched_runtime(paths):
            validation = downloader._prepare_media_downloader_runtime(options, logs.append)

    _assert(not validation.available, "Stable runtime unexpectedly required aria2")
    _assert(any("stable yt-dlp internal" in line for line in logs), "Stable runtime log missing")


def _fast_video_command(
    root: Path | None = None,
    paths=None,
    options: DownloadOptions | None = None,
) -> list[str]:
    if root is None:
        with TemporaryDirectory() as tmp:
            return _fast_video_command(Path(tmp))
    if paths is None:
        paths = _runtime_paths(root)
    if options is None:
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
    with _patched_runtime(paths):
        return downloader._build_fast_video_ytdlp_command(
            VIDEO_ID,
            root,
            options,
            _aria2_validation(paths),
        )


def _write_hybrid_fixture(command: list[str]) -> None:
    if "--write-info-json" not in command:
        return
    output_template = _option_value(command, "-o")
    info_path = Path(output_template.replace("%(ext)s", "info.json"))
    info_path.parent.mkdir(parents=True, exist_ok=True)
    info_path.write_text(
        json.dumps(
            {
                "id": VIDEO_ID,
                "duration": 60.0,
                "requested_formats": [
                    {
                        "format_id": "137",
                        "ext": "mp4",
                        "vcodec": "avc1.640028",
                        "acodec": "none",
                        "height": 1080,
                        "filesize": 9_000_000,
                    },
                    {
                        "format_id": "140",
                        "ext": "m4a",
                        "vcodec": "none",
                        "acodec": "mp4a.40.2",
                        "filesize": 1_000_000,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def _runtime_paths(root: Path, *, aria2: bool = True):
    bin_dir = root / "data" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    ytdlp = bin_dir / "yt-dlp.exe"
    ffmpeg = bin_dir / "ffmpeg.exe"
    ytdlp.write_text("yt-dlp", encoding="utf-8")
    ffmpeg.write_text("ffmpeg", encoding="utf-8")
    aria2_path = bin_dir / "aria2c.exe"
    if aria2:
        aria2_path.write_text("aria2", encoding="utf-8")
    return SimpleNamespace(
        root=root,
        bin=bin_dir,
        ytdlp=ytdlp,
        ffmpeg=ffmpeg,
        aria2=aria2_path,
    )


def _aria2_validation(paths):
    return downloader._Aria2RuntimeValidation(True, paths.aria2.exists(), paths.aria2)


def _options(
    root: Path,
    engine: str,
    *,
    cookies_enabled: bool = False,
    cookies_path: str = "",
) -> DownloadOptions:
    return DownloadOptions(
        base_folder=str(root / "downloads"),
        channel_id=CHANNEL_ID,
        channel_name=CHANNEL_NAME,
        cookies_enabled=cookies_enabled,
        cookies_path=cookies_path,
        download_mode=MODE_VIDEO_THUMB,
        download_engine=engine,
        file_start_number=1,
    )


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


@contextmanager
def _patched_attr(target, name: str, value):
    missing = object()
    old_value = getattr(target, name, missing)
    setattr(target, name, value)
    try:
        yield
    finally:
        if old_value is missing:
            delattr(target, name)
        else:
            setattr(target, name, old_value)


def _remove_aria2_options(command: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(command):
        value = command[index]
        if value in {"--downloader", "--downloader-args"}:
            index += 2
            continue
        result.append(value)
        index += 1
    return result


def _option_value(command: list[str], option: str) -> str:
    for index, value in enumerate(command):
        if value == option and index + 1 < len(command):
            return str(command[index + 1])
    return ""


def _option_count(command: list[str], option: str) -> int:
    return sum(1 for value in command if value == option)


def _joined(command: list[str]) -> str:
    return " ".join(str(value) for value in command)


def _noop_log(_message: str) -> None:
    return None


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
