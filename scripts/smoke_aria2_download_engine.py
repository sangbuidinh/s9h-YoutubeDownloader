import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.downloader import (
    ARIA2_FAST_DOWNLOADER_ARGS,
    DEFAULT_DOWNLOAD_ENGINE,
    DOWNLOAD_ENGINE_ARIA2_FAST,
    DOWNLOAD_ENGINE_STABLE,
    DownloadError,
    DownloadOptions,
    YTDLP_STAGE_DOWNLOAD,
    YtdlpExecutionError,
    YtdlpFailureKind,
)
from ui import main_window


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"


def main() -> int:
    _test_stable_default_media_command()
    _test_explicit_stable_media_command()
    _test_unknown_engine_normalizes_to_stable()
    _test_fast_with_valid_aria2_media_command()
    _test_fast_missing_aria2_fails_before_media()
    _test_fast_invalid_aria2_fails_before_media()
    _test_thumbnail_isolation()
    _test_authenticated_metadata_extraction_isolation()
    _test_lookahead_metadata_isolation()
    _test_saved_infojson_media_transfer_retains_fast()
    _test_direct_audio_uses_aria2()
    _test_ffmpeg_extraction_excludes_aria2()
    _test_fast_retry_remains_fast()
    _test_fast_failure_does_not_change_later_items()
    _test_stable_retries_remain_stable()
    _test_ui_mapping_remains_fixed()
    _test_logs_contain_no_fallback_wording()
    _test_aria2_logs_do_not_expose_secrets()
    print("aria2 download engine smoke passed")
    return 0


def _test_stable_default_media_command() -> None:
    with TemporaryDirectory(prefix="stable_default_engine_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=False)
        with _patched_runtime(runtime_paths):
            command = downloader._build_video_ytdlp_command("stable-default", root, _options(root))
    _assert(downloader._normalize_download_engine(DEFAULT_DOWNLOAD_ENGINE) == DOWNLOAD_ENGINE_STABLE, "default engine was not stable")
    _assert_stable_media_command(command)


def _test_explicit_stable_media_command() -> None:
    with TemporaryDirectory(prefix="stable_explicit_engine_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=False)
        with _patched_runtime(runtime_paths):
            command = downloader._build_video_ytdlp_command(
                "stable-explicit",
                root,
                _options(root, DOWNLOAD_ENGINE_STABLE),
            )
    _assert_stable_media_command(command)


def _test_unknown_engine_normalizes_to_stable() -> None:
    with TemporaryDirectory(prefix="stable_unknown_engine_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=False)
        options = _options(root, "bad-engine")
        with _patched_runtime(runtime_paths):
            command = downloader._build_video_ytdlp_command("stable-unknown", root, options)
    _assert(downloader._normalize_download_engine(options.download_engine) == DOWNLOAD_ENGINE_STABLE, "unknown engine did not normalize to stable")
    _assert_stable_media_command(command)


def _test_fast_with_valid_aria2_media_command() -> None:
    with TemporaryDirectory(prefix="fast_valid_engine_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        with _patched_runtime(runtime_paths):
            command = downloader._build_video_ytdlp_command(
                "fast-valid",
                root,
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                aria2_validation=validation,
            )
    _assert_fast_media_command(command, runtime_paths["aria2c.exe"])


def _test_fast_missing_aria2_fails_before_media() -> None:
    with TemporaryDirectory(prefix="fast_missing_engine_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=False)
        logs: list[str] = []
        media_calls: list[list[str]] = []
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        old_run = downloader._run_ytdlp
        try:
            downloader._run_ytdlp = lambda command, _controller=None: media_calls.append(list(command))
            with _patched_runtime(runtime_paths), _patched_version("tool version"):
                downloader.validate_download_environment(_options(root, DOWNLOAD_ENGINE_STABLE))
                try:
                    downloader.download_items([_video("fast-missing")], options, logs.append, lambda _video: None)
                except DownloadError as exc:
                    _assert("Fast download cannot start" in str(exc), "missing aria2 error was unclear")
                else:
                    raise AssertionError("missing aria2 did not fail Fast batch startup")
        finally:
            downloader._run_ytdlp = old_run
    _assert(not media_calls, "Fast missing aria2 started a media subprocess")
    _assert(not any(_is_stable_media_command(command) for command in media_calls), "missing aria2 created a stable media command")
    _assert(any("[ERROR] aria2c.exe is unavailable. Fast download cannot start." in message for message in logs), "missing aria2 was not logged as an error")


def _test_fast_invalid_aria2_fails_before_media() -> None:
    with TemporaryDirectory(prefix="fast_invalid_engine_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        logs: list[str] = []
        media_calls: list[list[str]] = []
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        old_run = downloader._run_ytdlp
        try:
            downloader._run_ytdlp = lambda command, _controller=None: media_calls.append(list(command))
            with _patched_runtime(runtime_paths), _patched_version_for_aria2_failure():
                try:
                    downloader.download_items([_video("fast-invalid")], options, logs.append, lambda _video: None)
                except DownloadError as exc:
                    _assert("Fast download cannot start" in str(exc), "invalid aria2 error was unclear")
                else:
                    raise AssertionError("invalid aria2 did not fail Fast batch startup")
        finally:
            downloader._run_ytdlp = old_run
    _assert(not media_calls, "Fast invalid aria2 started a media subprocess")
    _assert(any("[ERROR] aria2c.exe is unavailable. Fast download cannot start." in message for message in logs), "invalid aria2 was not logged as an error")


def _test_thumbnail_isolation() -> None:
    with TemporaryDirectory(prefix="thumbnail_isolation_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        captured: list[list[str]] = []
        old_run = downloader._run_ytdlp_with_retries
        try:
            def fake_run(command, *_args, **_kwargs):
                captured.append(list(command))
                output_template = Path(downloader._command_option_value(command, "-o"))
                output_template.with_name(output_template.name.replace("%(ext)s", "jpg")).write_bytes(b"\xff\xd8\xff")

            downloader._run_ytdlp_with_retries = fake_run
            with _patched_runtime(runtime_paths):
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
    _assert(captured, "thumbnail yt-dlp command was not captured")
    _assert_no_media_downloader_args(captured[0], "thumbnail command")


def _test_authenticated_metadata_extraction_isolation() -> None:
    with TemporaryDirectory(prefix="metadata_isolation_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        with _patched_runtime(runtime_paths):
            media_command = downloader._build_video_ytdlp_command(
                "metadata-video",
                root,
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                aria2_validation=validation,
            )
            extract_command = downloader._build_authenticated_infojson_extract_command(
                media_command,
                str(root / "auth.%(ext)s"),
            )
    _assert(_contains_aria2(media_command), "fast media command did not contain aria2 before metadata extraction")
    _assert_no_media_downloader_args(extract_command, "metadata extraction command")


def _test_lookahead_metadata_isolation() -> None:
    with TemporaryDirectory(prefix="lookahead_isolation_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        options.cookies_enabled = True
        cookie_path = root / "cookies.txt"
        cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        options.cookies_path = str(cookie_path)
        captured: list[list[str]] = []
        old_extract = downloader._extract_authenticated_infojson_path
        try:
            def fake_extract(command, *_args, **_kwargs):
                captured.append(list(command))
                info_path = root / "lookahead.info.json"
                info_path.write_text("{}", encoding="utf-8")
                return info_path

            downloader._extract_authenticated_infojson_path = fake_extract
            with _patched_runtime(runtime_paths):
                batch_state = downloader._YtdlpBatchState(cookie_bootstrap_media_mode=True)
                downloader._start_cookie_media_lookahead(
                    batch_state,
                    "lookahead-video",
                    "Lookahead Video",
                    root,
                    options,
                    lambda _message: None,
                    None,
                )
                prefetch = batch_state.prefetch
                _assert(prefetch is not None, "lookahead prefetch was not created")
                prefetch.done.wait(5)
        finally:
            downloader._extract_authenticated_infojson_path = old_extract
    _assert(captured, "lookahead metadata command was not captured")
    _assert_no_media_downloader_args(captured[0], "lookahead metadata command")


def _test_saved_infojson_media_transfer_retains_fast() -> None:
    with TemporaryDirectory(prefix="saved_infojson_fast_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        info_json = root / "saved.info.json"
        info_json.write_text("{}", encoding="utf-8")
        with _patched_runtime(runtime_paths):
            media_command = downloader._build_video_ytdlp_command(
                "saved-infojson",
                root,
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                aria2_validation=validation,
            )
            extract_command = downloader._build_authenticated_infojson_extract_command(
                media_command,
                str(root / "auth.%(ext)s"),
            )
            saved_media_command = downloader._build_infojson_media_download_command(media_command, info_json)
    _assert_no_media_downloader_args(extract_command, "saved-infojson metadata extraction command")
    _assert_fast_media_command(saved_media_command, runtime_paths["aria2c.exe"])
    _assert("--load-info-json" in saved_media_command, "saved-media command did not load info JSON")


def _test_direct_audio_uses_aria2() -> None:
    with TemporaryDirectory(prefix="direct_audio_fast_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        with _patched_runtime(runtime_paths):
            command = downloader._build_audio_ytdlp_command(
                "audio-video",
                root,
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                aria2_validation=validation,
            )
    _assert_fast_media_command(command, runtime_paths["aria2c.exe"])


def _test_ffmpeg_extraction_excludes_aria2() -> None:
    with TemporaryDirectory(prefix="ffmpeg_extract_") as temp_dir:
        root = Path(temp_dir)
        calls: list[list[str]] = []
        source = root / "source.mp4"
        source.write_bytes(b"mp4")
        old_validate = downloader._validate_premiere_safe_mp4_for_download
        old_run = downloader._run_ffmpeg_for_audio
        try:
            downloader._validate_premiere_safe_mp4_for_download = lambda *_args, **_kwargs: None

            def fake_ffmpeg(command, _controller=None):
                calls.append(list(command))
                Path(command[-1]).write_bytes(b"mp3")
                return ""

            downloader._run_ffmpeg_for_audio = fake_ffmpeg
            downloader._extract_mp3_from_video(source, root, root / "audio.mp3", lambda _message: None)
        finally:
            downloader._validate_premiere_safe_mp4_for_download = old_validate
            downloader._run_ffmpeg_for_audio = old_run
    _assert(calls, "FFmpeg extraction command was not captured")
    _assert(not _contains_aria2(calls[0]), "FFmpeg extraction command contained aria2")


def _test_fast_retry_remains_fast() -> None:
    with TemporaryDirectory(prefix="fast_retry_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        calls: list[list[str]] = []
        delays: list[int] = []
        old_run = downloader._run_ytdlp
        old_sleep = downloader._sleep_with_cancel
        try:
            def fake_run(command, _controller=None):
                calls.append(list(command))
                if len(calls) == 1:
                    raise _failure([], YtdlpFailureKind.HTTP_403, "HTTP Error 403: Forbidden", http_status=403)
                return ""

            downloader._run_ytdlp = fake_run
            downloader._sleep_with_cancel = lambda seconds, _controller=None: delays.append(int(seconds))
            with _patched_runtime(runtime_paths):
                command = downloader._build_video_ytdlp_command(
                    "fast-retry",
                    root,
                    options,
                    aria2_validation=validation,
                )
                downloader._run_ytdlp_with_retries(command, options, lambda _message: None)
        finally:
            downloader._run_ytdlp = old_run
            downloader._sleep_with_cancel = old_sleep
    _assert(delays == [10], f"Fast retry did not use the expected first HTTP 403 delay: {delays}")
    _assert(calls and all(_contains_aria2(command) for command in calls), "Fast media retry removed aria2")
    _assert(not any(_is_stable_media_command(command) for command in calls), "Fast media retry generated a Stable command")


def _test_fast_failure_does_not_change_later_items() -> None:
    with TemporaryDirectory(prefix="fast_later_items_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        commands: list[list[str]] = []
        with _patched_runtime(runtime_paths):
            for video_id in ("first-failed", "second-after-skip"):
                command = downloader._build_video_ytdlp_command(
                    video_id,
                    root,
                    options,
                    aria2_validation=validation,
                )
                commands.append(command)
                if video_id == "first-failed":
                    try:
                        raise _failure(command, YtdlpFailureKind.NETWORK, "connection reset by peer")
                    except YtdlpExecutionError:
                        pass
    _assert(len(commands) == 2, "later item command was not built")
    _assert(all(_contains_aria2(command) for command in commands), "Fast engine did not remain fixed for later items")
    _assert(not any(_is_stable_media_command(command) for command in commands), "later Fast item became Stable")


def _test_stable_retries_remain_stable() -> None:
    with TemporaryDirectory(prefix="stable_retry_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        options = _options(root, DOWNLOAD_ENGINE_STABLE)
        calls: list[list[str]] = []
        delays: list[int] = []
        old_run = downloader._run_ytdlp
        old_sleep = downloader._sleep_with_cancel
        try:
            def fake_run(command, _controller=None):
                calls.append(list(command))
                if len(calls) == 1:
                    raise _failure([], YtdlpFailureKind.HTTP_403, "HTTP Error 403: Forbidden", http_status=403)
                return ""

            downloader._run_ytdlp = fake_run
            downloader._sleep_with_cancel = lambda seconds, _controller=None: delays.append(int(seconds))
            with _patched_runtime(runtime_paths):
                command = downloader._build_video_ytdlp_command("stable-retry", root, options)
                downloader._run_ytdlp_with_retries(command, options, lambda _message: None)
        finally:
            downloader._run_ytdlp = old_run
            downloader._sleep_with_cancel = old_sleep
    _assert(delays == [10], f"Stable retry did not use the expected first HTTP 403 delay: {delays}")
    _assert(calls and all(_is_stable_media_command(command) for command in calls), "Stable retry did not remain Stable")
    _assert(not any(_contains_aria2(command) for command in calls), "Stable retry used aria2")


def _test_ui_mapping_remains_fixed() -> None:
    _assert(main_window.DOWNLOAD_ENGINE_LABELS[DOWNLOAD_ENGINE_STABLE] == "Stable - yt-dlp internal", "Stable label changed")
    _assert(main_window.DOWNLOAD_ENGINE_LABELS[DOWNLOAD_ENGINE_ARIA2_FAST] == "Fast - aria2c experimental", "Fast label changed")
    _assert(
        main_window.DOWNLOAD_ENGINE_VALUES_BY_LABEL["Stable - yt-dlp internal"] == DOWNLOAD_ENGINE_STABLE,
        "Stable label did not map to stable",
    )
    _assert(
        main_window.DOWNLOAD_ENGINE_VALUES_BY_LABEL["Fast - aria2c experimental"] == DOWNLOAD_ENGINE_ARIA2_FAST,
        "Fast label did not map to aria2_fast",
    )
    _assert(
        main_window.DOWNLOAD_ENGINE_VALUES_BY_LABEL.get("bad label", DOWNLOAD_ENGINE_STABLE) == DOWNLOAD_ENGINE_STABLE,
        "unknown UI label did not resolve to stable",
    )
    source = (REPO_ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    _assert("download_engine_var.set(" not in source, "UI code mutates the engine selector after initialization")


def _test_logs_contain_no_fallback_wording() -> None:
    with TemporaryDirectory(prefix="log_wording_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        logs: list[str] = []
        with _patched_runtime(runtime_paths), _patched_version("aria2 version 1.37.0"):
            downloader._prepare_media_downloader_runtime(_options(root, DOWNLOAD_ENGINE_STABLE), logs.append, None)
            downloader._prepare_media_downloader_runtime(_options(root, DOWNLOAD_ENGINE_ARIA2_FAST), logs.append, None)
    joined = "\n".join(logs).lower()
    for forbidden in (
        "fallback to " + "stable",
        "retrying with " + "stable",
        "stable downloader " + "fallback",
    ):
        _assert(forbidden not in joined, f"engine log contained obsolete fallback wording: {forbidden}")
    source = (REPO_ROOT / "core" / "downloader.py").read_text(encoding="utf-8").lower()
    for forbidden in ("aria2c media transfer " + "failed", "stable downloader " + "fallback"):
        _assert(forbidden not in source, f"source retained obsolete fallback wording: {forbidden}")


def _test_aria2_logs_do_not_expose_secrets() -> None:
    with TemporaryDirectory(prefix="log_secret_safety_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        logs: list[str] = []
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        options.cookies_enabled = True
        options.cookies_path = str(root / "isolated-cookie-secret.txt")
        options.speed_limit = "token=super-secret"
        with _patched_runtime(runtime_paths), _patched_version("aria2 version 1.37.0"):
            downloader._prepare_media_downloader_runtime(options, logs.append, None)
    joined = "\n".join(logs)
    for forbidden in (
        "isolated-cookie-secret",
        "super-secret",
        "signature=",
        "authorization",
        "Cookie:",
    ):
        _assert(forbidden not in joined, f"aria2 log exposed {forbidden}")


def _failure(
    command: list[str],
    kind: YtdlpFailureKind,
    text: str,
    *,
    http_status: int | None = None,
) -> YtdlpExecutionError:
    return YtdlpExecutionError(
        1,
        text,
        [text],
        http_403=kind == YtdlpFailureKind.HTTP_403,
        combined_output=text,
        stream_interrupted=kind in {YtdlpFailureKind.NETWORK, YtdlpFailureKind.NETWORK_TIMEOUT},
        failure_kind=kind,
        fatal_lines=[text],
        http_status=http_status,
        stage=YTDLP_STAGE_DOWNLOAD,
        part=downloader.PART_VIDEO,
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


def _options(root: Path, engine: str | None = None) -> DownloadOptions:
    return DownloadOptions(
        base_folder=str(root),
        channel_id=CHANNEL_ID,
        channel_name=CHANNEL_NAME,
        download_engine=engine if engine is not None else DEFAULT_DOWNLOAD_ENGINE,
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


@contextmanager
def _patched_version_for_aria2_failure():
    old_get_version = downloader._get_command_version
    try:
        def fake_get_version(command, *_args, **_kwargs):
            if command and "aria2c" in Path(str(command[0])).name.lower():
                return ""
            return "tool version"

        downloader._get_command_version = fake_get_version
        yield
    finally:
        downloader._get_command_version = old_get_version


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


def _is_stable_media_command(command: list[str]) -> bool:
    return _option_value(command, "-N") == "1" and not _contains_aria2(command)


def _assert_stable_media_command(command: list[str]) -> None:
    _assert(_option_value(command, "-N") == "1", "Stable media command missed -N 1")
    _assert("--downloader" not in command, "Stable media command used --downloader")
    _assert("--downloader-args" not in command, "Stable media command used --downloader-args")
    _assert("--external-downloader" not in command, "Stable media command used --external-downloader")
    _assert("--external-downloader-args" not in command, "Stable media command used --external-downloader-args")
    _assert(not _contains_aria2(command), "Stable media command contained aria2")


def _assert_fast_media_command(command: list[str], aria2_path: Path) -> None:
    _assert(_option_value(command, "--downloader") == str(aria2_path), "Fast media command did not use resolved aria2c.exe")
    _assert(_option_value(command, "--downloader-args") == ARIA2_FAST_DOWNLOADER_ARGS, "Fast media command used the wrong aria2 profile")
    _assert(_option_value(command, "-N") == "", "Fast media command kept Stable -N 1")
    _assert("-x 8 -s 8 -j 4 -k 1M" in _option_value(command, "--downloader-args"), "Fast media command missed approved aria2 profile")


def _assert_no_media_downloader_args(command: list[str], label: str) -> None:
    forbidden = {
        "-N",
        "--concurrent-fragments",
        "--downloader",
        "--external-downloader",
        "--downloader-args",
        "--external-downloader-args",
    }
    for option in forbidden:
        _assert(option not in command, f"{label} contained {option}")
    _assert(not _contains_aria2(command), f"{label} contained aria2")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
