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
    DOWNLOAD_ENGINE_ARIA2_FAST,
    DOWNLOAD_ENGINE_STABLE,
    DownloadCancelled,
    DownloadController,
    DownloadOptions,
    YTDLP_STAGE_DOWNLOAD,
    YtdlpExecutionError,
    YtdlpFailureKind,
)


CHANNEL_ID = "channel"
CHANNEL_NAME = "Channel"


def main() -> int:
    _test_stable_default_explicit_and_unknown()
    _test_aria2_command_when_available()
    _test_missing_aria2_falls_back_without_required_validation()
    _test_thumbnail_metadata_and_lookahead_isolation()
    _test_direct_audio_and_ffmpeg_extraction_scope()
    _test_aria2_failure_rebuilds_stable_retry_once()
    _test_ineligible_failures_do_not_fallback()
    _test_cancellation_before_stable_fallback()
    _test_new_aria2_logs_do_not_expose_secrets()
    print("aria2 download engine smoke passed")
    return 0


def _test_stable_default_explicit_and_unknown() -> None:
    for engine in (None, DOWNLOAD_ENGINE_STABLE, "bad-engine"):
        with TemporaryDirectory(prefix="stable_engine_") as temp_dir:
            root = Path(temp_dir)
            runtime_paths = _runtime_paths(root, aria2=False)
            with _patched_runtime(runtime_paths):
                options = _options(root)
                if engine is not None:
                    options.download_engine = engine
                command = downloader._build_video_ytdlp_command("stable-video", root, options)
            _assert(downloader._normalize_download_engine(options.download_engine) == DOWNLOAD_ENGINE_STABLE, "engine did not normalize to stable")
            _assert(_option_value(command, "-N") == "1", "stable media command did not use -N 1")
            _assert("--downloader" not in command, "stable command unexpectedly used external downloader")
            _assert("--downloader-args" not in command, "stable command unexpectedly used downloader args")
            _assert(not _contains_aria2(command), "stable command contained aria2")


def _test_aria2_command_when_available() -> None:
    with TemporaryDirectory(prefix="fast_engine_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        with _patched_runtime(runtime_paths):
            command = downloader._build_video_ytdlp_command(
                "fast-video",
                root,
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                aria2_validation=validation,
            )
    _assert("--downloader" in command, "aria2 command missed --downloader")
    _assert(_option_value(command, "--downloader") == str(runtime_paths["aria2c.exe"]), "aria2 path was not resolved through runtime_file")
    _assert(_option_value(command, "--downloader-args") == downloader.ARIA2_FAST_DOWNLOADER_ARGS, "aria2 args did not match the approved profile")
    _assert(_option_value(command, "-N") == "", "aria2 media command should not keep stable -N 1")


def _test_missing_aria2_falls_back_without_required_validation() -> None:
    with TemporaryDirectory(prefix="missing_engine_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=False)
        logs: list[str] = []
        with _patched_runtime(runtime_paths):
            stable_options = _options(root, DOWNLOAD_ENGINE_STABLE)
            downloader.validate_download_environment(stable_options)
            validation = downloader._prepare_media_downloader_runtime(
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                logs.append,
                None,
            )
            command = downloader._build_video_ytdlp_command(
                "missing-aria2",
                root,
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                aria2_validation=validation,
            )
    _assert(not validation.available, "missing aria2 validated as available")
    _assert(sum("aria2c.exe is missing or unavailable" in message for message in logs) == 1, "missing aria2 warning was not emitted once")
    _assert(_option_value(command, "-N") == "1", "missing aria2 did not fall back to stable command")
    _assert("--downloader" not in command and "--downloader-args" not in command, "missing aria2 command kept external downloader options")


def _test_thumbnail_metadata_and_lookahead_isolation() -> None:
    with TemporaryDirectory(prefix="isolation_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        options.cookies_enabled = True
        captured_thumbnail: list[list[str]] = []
        captured_lookahead: list[list[str]] = []
        with _patched_runtime(runtime_paths):
            video_command = downloader._build_video_ytdlp_command(
                "metadata-video",
                root,
                options,
                aria2_validation=validation,
            )
            extract_command = downloader._build_authenticated_infojson_extract_command(
                video_command,
                str(root / "auth.%(ext)s"),
            )

            old_run_with_retries = downloader._run_ytdlp_with_retries
            old_extract = downloader._extract_authenticated_infojson_path
            try:
                def fake_run(command, _options, _log, _controller=None, _cookie_retry_state=None):
                    captured_thumbnail.append(list(command))
                    output_template = Path(downloader._command_option_value(command, "-o"))
                    output_template.with_name(output_template.name.replace("%(ext)s", "jpg")).write_bytes(b"\xff\xd8\xff")

                def fake_extract(command, *_args, **_kwargs):
                    captured_lookahead.append(list(command))
                    info_path = root / "lookahead.info.json"
                    info_path.write_text("{}", encoding="utf-8")
                    return info_path

                downloader._run_ytdlp_with_retries = fake_run
                downloader._extract_authenticated_infojson_path = fake_extract
                downloader._download_thumbnail(
                    SimpleNamespace(video_id="thumb-video", thumbnail_url=""),
                    "thumb-video",
                    root,
                    root / "thumb.jpg",
                    options,
                    lambda _message: None,
                )
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
                downloader._run_ytdlp_with_retries = old_run_with_retries
                downloader._extract_authenticated_infojson_path = old_extract

    _assert(not _contains_aria2(extract_command), "metadata extraction command contained aria2")
    _assert(_option_value(extract_command, "-N") == "", "metadata extraction command kept media fragment option")
    _assert(captured_thumbnail and not _contains_aria2(captured_thumbnail[0]), "thumbnail command contained aria2")
    _assert(captured_lookahead and not _contains_aria2(captured_lookahead[0]), "lookahead command contained aria2")


def _test_direct_audio_and_ffmpeg_extraction_scope() -> None:
    with TemporaryDirectory(prefix="audio_scope_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        with _patched_runtime(runtime_paths):
            audio_command = downloader._build_audio_ytdlp_command(
                "audio-video",
                root,
                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                aria2_validation=validation,
            )
            calls: list[list[str]] = []
            old_validate = downloader._validate_premiere_safe_mp4_for_download
            old_run_ffmpeg = downloader._run_ffmpeg_for_audio
            try:
                downloader._validate_premiere_safe_mp4_for_download = lambda *_args, **_kwargs: None

                def fake_ffmpeg(command, _controller=None):
                    calls.append(list(command))
                    Path(command[-1]).write_bytes(b"mp3")
                    return ""

                downloader._run_ffmpeg_for_audio = fake_ffmpeg
                source = root / "source.mp4"
                source.write_bytes(b"mp4")
                downloader._extract_mp3_from_video(source, root, root / "audio.mp3", lambda _message: None)
            finally:
                downloader._validate_premiere_safe_mp4_for_download = old_validate
                downloader._run_ffmpeg_for_audio = old_run_ffmpeg

    _assert(_contains_aria2(audio_command), "direct audio command did not use aria2")
    _assert(calls and not _contains_aria2(calls[0]), "FFmpeg extraction command contained aria2")


def _test_aria2_failure_rebuilds_stable_retry_once() -> None:
    with TemporaryDirectory(prefix="fallback_once_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        calls: list[list[str]] = []
        logs: list[str] = []
        remaining_partials: list[Path] = []
        old_run = downloader._run_ytdlp_with_retries
        try:
            def fake_run(command, *_args):
                calls.append(list(command))
                if len(calls) == 1:
                    output_template = Path(downloader._command_option_value(command, "-o"))
                    output_template.with_name(output_template.name.replace("%(ext)s", "mp4.part")).write_bytes(b"partial")
                    raise _failure(command, YtdlpFailureKind.NETWORK, "connection reset by peer")
                return None

            downloader._run_ytdlp_with_retries = fake_run
            with _patched_runtime(runtime_paths):
                downloader._run_media_ytdlp_with_engine_fallback(
                    lambda *, force_stable: downloader._build_video_ytdlp_command(
                        "fallback-video",
                        root,
                        _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                        force_stable_downloader=force_stable,
                        aria2_validation=validation,
                    ),
                    root,
                    _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                    logs.append,
                )
                remaining_partials = list(root.glob("fallback-video*.part"))
        finally:
            downloader._run_ytdlp_with_retries = old_run

    _assert(len(calls) == 2, f"fallback call count was wrong: {len(calls)}")
    _assert(_contains_aria2(calls[0]), "first attempt was not aria2")
    _assert_stable_fallback_command(calls[1])
    _assert(not remaining_partials, "aria2 partial file was not cleaned")
    _assert(any("Stable downloader fallback succeeded" in message for message in logs), "stable fallback success was not logged")


def _test_ineligible_failures_do_not_fallback() -> None:
    ineligible = (
        YtdlpFailureKind.HTTP_401,
        YtdlpFailureKind.BOT_CHECK,
        YtdlpFailureKind.COOKIE_SESSION,
        YtdlpFailureKind.LOGIN_REQUIRED,
        YtdlpFailureKind.PO_TOKEN_OR_VISITOR_DATA,
        YtdlpFailureKind.RATE_LIMIT,
        YtdlpFailureKind.FORMAT_UNAVAILABLE,
        YtdlpFailureKind.PERMANENT_VIDEO,
        YtdlpFailureKind.OUTPUT_PATH,
        YtdlpFailureKind.TOOL_CONFIGURATION,
    )
    for kind in ineligible:
        with TemporaryDirectory(prefix="ineligible_") as temp_dir:
            root = Path(temp_dir)
            runtime_paths = _runtime_paths(root, aria2=True)
            validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
            calls: list[list[str]] = []
            old_run = downloader._run_ytdlp_with_retries
            try:
                def fake_run(command, *_args, failure_kind=kind):
                    calls.append(list(command))
                    raise _failure(command, failure_kind, failure_kind.value)

                downloader._run_ytdlp_with_retries = fake_run
                with _patched_runtime(runtime_paths):
                    try:
                        downloader._run_media_ytdlp_with_engine_fallback(
                            lambda *, force_stable: downloader._build_video_ytdlp_command(
                                "ineligible-video",
                                root,
                                _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                                force_stable_downloader=force_stable,
                                aria2_validation=validation,
                            ),
                            root,
                            _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                            lambda _message: None,
                        )
                    except YtdlpExecutionError:
                        pass
                    else:
                        raise AssertionError(f"{kind.value} unexpectedly succeeded")
            finally:
                downloader._run_ytdlp_with_retries = old_run
            _assert(len(calls) == 1, f"{kind.value} triggered fallback")


def _test_cancellation_before_stable_fallback() -> None:
    with TemporaryDirectory(prefix="cancel_fallback_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        controller = DownloadController()
        calls: list[list[str]] = []
        old_run = downloader._run_ytdlp_with_retries
        old_cleanup = downloader._cleanup_failed_media_attempt_partials
        try:
            def fake_run(command, *_args):
                calls.append(list(command))
                raise _failure(command, YtdlpFailureKind.NETWORK_TIMEOUT, "timed out")

            def cancel_after_cleanup(*_args, **_kwargs):
                controller.request_cancel()

            downloader._run_ytdlp_with_retries = fake_run
            downloader._cleanup_failed_media_attempt_partials = cancel_after_cleanup
            with _patched_runtime(runtime_paths):
                try:
                    downloader._run_media_ytdlp_with_engine_fallback(
                        lambda *, force_stable: downloader._build_video_ytdlp_command(
                            "cancel-video",
                            root,
                            _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                            force_stable_downloader=force_stable,
                            aria2_validation=validation,
                        ),
                        root,
                        _options(root, DOWNLOAD_ENGINE_ARIA2_FAST),
                        lambda _message: None,
                        controller,
                    )
                except DownloadCancelled:
                    pass
                else:
                    raise AssertionError("cancellation did not stop stable fallback")
        finally:
            downloader._run_ytdlp_with_retries = old_run
            downloader._cleanup_failed_media_attempt_partials = old_cleanup
    _assert(len(calls) == 1, "stable fallback started after cancellation")


def _test_new_aria2_logs_do_not_expose_secrets() -> None:
    with TemporaryDirectory(prefix="log_safety_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        logs: list[str] = []
        old_version = downloader._get_command_version
        try:
            downloader._get_command_version = lambda *_args, **_kwargs: "aria2 version 1.37.0"
            with _patched_runtime(runtime_paths):
                options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
                options.cookies_enabled = True
                options.cookies_path = str(root / "secret-cookie.txt")
                downloader._prepare_media_downloader_runtime(options, logs.append, None)
        finally:
            downloader._get_command_version = old_version
    joined = "\n".join(logs).lower()
    for forbidden in ("secret-cookie", "api_key", "signature=", "authorization:", "--cookies"):
        _assert(forbidden not in joined, f"aria2 log exposed {forbidden}")


def _failure(command: list[str], kind: YtdlpFailureKind, text: str) -> YtdlpExecutionError:
    return YtdlpExecutionError(
        1,
        text,
        [text],
        combined_output=text,
        stream_interrupted=kind in {YtdlpFailureKind.NETWORK, YtdlpFailureKind.NETWORK_TIMEOUT},
        failure_kind=kind,
        fatal_lines=[text],
        http_status=403 if kind == YtdlpFailureKind.HTTP_403 else None,
        stage=YTDLP_STAGE_DOWNLOAD,
        part=downloader.PART_VIDEO,
        command=command,
    )


def _options(root: Path, engine: str = DOWNLOAD_ENGINE_STABLE) -> DownloadOptions:
    return DownloadOptions(
        base_folder=str(root),
        channel_id=CHANNEL_ID,
        channel_name=CHANNEL_NAME,
        download_engine=engine,
    )


def _runtime_paths(root: Path, *, aria2: bool) -> dict[str, Path]:
    paths = {
        "yt-dlp.exe": root / "yt-dlp.exe",
        "ffmpeg.exe": root / "ffmpeg.exe",
        "deno.exe": root / "deno.exe",
        "aria2c.exe": root / "aria2c.exe",
    }
    for name in ("yt-dlp.exe", "ffmpeg.exe", "deno.exe"):
        paths[name].write_bytes(b"")
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


def _option_value(command: list[str], option: str) -> str:
    return downloader._command_option_value(command, option)


def _contains_aria2(command: list[str]) -> bool:
    return any("aria2" in str(value).lower() for value in command)


def _assert_stable_fallback_command(command: list[str]) -> None:
    _assert(_option_value(command, "-N") == "1", "stable fallback command missed -N 1")
    _assert("--downloader" not in command, "stable fallback retained --downloader")
    _assert("--downloader-args" not in command, "stable fallback retained --downloader-args")
    _assert(not _contains_aria2(command), "stable fallback retained aria2 value")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
