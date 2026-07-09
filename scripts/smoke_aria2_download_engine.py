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
    BatchDecision,
    DOWNLOAD_ENGINE_ARIA2_FAST,
    DOWNLOAD_ENGINE_STABLE,
    DownloadCancelled,
    DownloadController,
    DownloadOptions,
    SkipCurrentVideo,
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
    _test_runner_plain_aria2_http403_defers_to_stable()
    _test_runner_aria2_network_interruption_defers_without_aria2_retry()
    _test_runner_aria2_network_timeout_defers_to_stable()
    _test_runner_ineligible_systemic_failures_do_not_defer()
    _test_runner_stable_failure_reaches_systemic_handling()
    _test_runner_cancellation_prevents_stable_fallback()
    _test_runner_cookie_media_preparation_before_defer()
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
            def fake_run(command, *_args, **_kwargs):
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


def _test_runner_plain_aria2_http403_defers_to_stable() -> None:
    calls, contexts = _run_real_runner_transport_fallback(
        YtdlpFailureKind.HTTP_403,
        "ERROR: unable to download video data: HTTP Error 403: Forbidden",
        http_status=403,
    )
    _assert(len(calls) == 2, f"plain HTTP 403 did not switch directly to stable: {len(calls)}")
    _assert(_contains_aria2(calls[0]), "plain HTTP 403 first command was not aria2")
    _assert_stable_fallback_command(calls[1])
    _assert(contexts == [], "systemic callback ran before stable fallback")


def _test_runner_aria2_network_interruption_defers_without_aria2_retry() -> None:
    calls, _contexts = _run_real_runner_transport_fallback(
        YtdlpFailureKind.NETWORK,
        "ERROR: fragment download failed: connection reset by peer",
    )
    _assert(len(calls) == 2, f"network interruption made unexpected attempts: {len(calls)}")
    _assert(_contains_aria2(calls[0]), "network interruption first command was not aria2")
    _assert_stable_fallback_command(calls[1])
    aria2_retry_commands = [
        command
        for command in calls
        if _contains_aria2(command) and ("--no-continue" in command or _option_value(command, "--http-chunk-size") == "512K")
    ]
    _assert(not aria2_retry_commands, "network interruption retried aria2 with safer chunk settings")


def _test_runner_aria2_network_timeout_defers_to_stable() -> None:
    calls, _contexts = _run_real_runner_transport_fallback(
        YtdlpFailureKind.NETWORK_TIMEOUT,
        "ERROR: download failed: operation timed out",
    )
    aria2_count = sum(1 for command in calls if _contains_aria2(command))
    stable_count = sum(1 for command in calls if not _contains_aria2(command))
    _assert(aria2_count == 1, f"network timeout aria2 engine count was {aria2_count}")
    _assert(stable_count == 1, f"network timeout stable engine count was {stable_count}")
    _assert_stable_fallback_command(calls[-1])


def _test_runner_ineligible_systemic_failures_do_not_defer() -> None:
    cases = (
        (YtdlpFailureKind.BOT_CHECK, "Sign in to confirm you're not a bot"),
        (YtdlpFailureKind.COOKIE_SESSION, "cookies are expired or invalid"),
        (YtdlpFailureKind.LOGIN_REQUIRED, "This video is only available to logged in users"),
        (YtdlpFailureKind.PO_TOKEN_OR_VISITOR_DATA, "This request requires a PO Token"),
        (YtdlpFailureKind.RATE_LIMIT, "HTTP Error 429: Too Many Requests"),
    )
    for kind, text in cases:
        calls = _run_real_runner_ineligible_failure(kind, text)
        _assert(len(calls) == 1, f"{kind.value} made unexpected subprocess attempts: {len(calls)}")
        _assert(_contains_aria2(calls[0]), f"{kind.value} did not fail on the aria2 command")


def _test_runner_stable_failure_reaches_systemic_handling() -> None:
    with TemporaryDirectory(prefix="stable_systemic_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        calls: list[list[str]] = []
        contexts = []
        controller = DownloadController()

        def callback(context):
            contexts.append(context)
            controller.submit_systemic_decision(context.block_id, BatchDecision.SKIP_CURRENT.value)

        controller.systemic_block_callback = callback
        old_run = downloader._run_ytdlp
        try:
            def fake_run(command, _controller=None):
                calls.append(list(command))
                if _contains_aria2(command):
                    raise _failure([], YtdlpFailureKind.NETWORK, "connection reset by peer")
                raise _failure([], YtdlpFailureKind.COOKIE_SESSION, "cookies are expired or invalid")

            downloader._run_ytdlp = fake_run
            with _patched_runtime(runtime_paths):
                try:
                    downloader._run_media_ytdlp_with_engine_fallback(
                        lambda *, force_stable: downloader._build_video_ytdlp_command(
                            "stable-systemic",
                            root,
                            options,
                            force_stable_downloader=force_stable,
                            aria2_validation=validation,
                        ),
                        root,
                        options,
                        lambda _message: None,
                        controller,
                    )
                except SkipCurrentVideo:
                    pass
                else:
                    raise AssertionError("stable systemic failure did not reach user-decision flow")
        finally:
            downloader._run_ytdlp = old_run

    _assert(len(calls) == 2, f"stable systemic flow made unexpected attempts: {len(calls)}")
    _assert(_contains_aria2(calls[0]), "first stable-systemic attempt was not aria2")
    _assert_stable_fallback_command(calls[1])
    _assert(len(contexts) == 1, f"systemic callback count was {len(contexts)}")
    _assert(contexts[0].failure_kind == YtdlpFailureKind.COOKIE_SESSION, "stable systemic kind was not preserved")


def _test_runner_cancellation_prevents_stable_fallback() -> None:
    with TemporaryDirectory(prefix="runner_cancel_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        controller = DownloadController()
        calls: list[list[str]] = []
        old_run = downloader._run_ytdlp
        old_cleanup = downloader._cleanup_failed_media_attempt_partials
        try:
            def fake_run(command, _controller=None):
                calls.append(list(command))
                raise _failure([], YtdlpFailureKind.NETWORK_TIMEOUT, "operation timed out")

            def cancel_after_cleanup(*_args, **_kwargs):
                controller.request_cancel()

            downloader._run_ytdlp = fake_run
            downloader._cleanup_failed_media_attempt_partials = cancel_after_cleanup
            with _patched_runtime(runtime_paths):
                try:
                    downloader._run_media_ytdlp_with_engine_fallback(
                        lambda *, force_stable: downloader._build_video_ytdlp_command(
                            "runner-cancel",
                            root,
                            options,
                            force_stable_downloader=force_stable,
                            aria2_validation=validation,
                        ),
                        root,
                        options,
                        lambda _message: None,
                        controller,
                    )
                except DownloadCancelled:
                    pass
                else:
                    raise AssertionError("runner cancellation did not stop stable fallback")
        finally:
            downloader._run_ytdlp = old_run
            downloader._cleanup_failed_media_attempt_partials = old_cleanup
    _assert(len(calls) == 1, "stable fallback started after runner cancellation")


def _test_runner_cookie_media_preparation_before_defer() -> None:
    with TemporaryDirectory(prefix="cookie_media_defer_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        cookie_path = root / "cookies.txt"
        cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        options.cookies_enabled = True
        options.cookies_path = str(cookie_path)
        calls: list[list[str]] = []
        delays: list[int] = []
        old_run = downloader._run_ytdlp
        old_sleep = downloader._sleep_with_cancel
        old_targets = downloader.COOKIE_MEDIA_RETRY_TARGET_SECONDS
        try:
            downloader.COOKIE_MEDIA_RETRY_TARGET_SECONDS = (10, 30)

            def fake_run(command, _controller=None):
                calls.append(list(command))
                if "--write-info-json" in command:
                    _write_fake_infojson(command)
                    return ""
                if _contains_aria2(command):
                    raise _failure(
                        [],
                        YtdlpFailureKind.HTTP_403,
                        "ERROR: unable to download video data: HTTP Error 403: Forbidden",
                        http_status=403,
                    )
                _assert_stable_fallback_command(command)
                return ""

            downloader._run_ytdlp = fake_run
            downloader._sleep_with_cancel = lambda seconds, _controller=None: delays.append(int(seconds))
            with _patched_runtime(runtime_paths):
                downloader._run_media_ytdlp_with_engine_fallback(
                    lambda *, force_stable: downloader._build_video_ytdlp_command(
                        "cookie-media",
                        root,
                        options,
                        force_stable_downloader=force_stable,
                        aria2_validation=validation,
                    ),
                    root,
                    options,
                    lambda _message: None,
                )
        finally:
            downloader._run_ytdlp = old_run
            downloader._sleep_with_cancel = old_sleep
            downloader.COOKIE_MEDIA_RETRY_TARGET_SECONDS = old_targets

    _assert(len(calls) == 6, f"cookie-media defer call count was {len(calls)}")
    initial, extract, saved_one, saved_two, saved_three, stable = calls
    _assert(_contains_aria2(initial) and downloader._command_uses_cookies(initial), "initial aria2 media attempt did not use isolated cookies")
    _assert("--write-info-json" in extract and not _contains_aria2(extract), "authenticated metadata extraction used aria2 or did not write info JSON")
    for saved in (saved_one, saved_two, saved_three):
        _assert(_contains_aria2(saved), "saved media retry did not keep aria2 before defer")
        _assert("--load-info-json" in saved, "saved media retry did not load authenticated info JSON")
        _assert(not downloader._command_uses_cookies(saved), "saved media retry re-enabled cookies")
    _assert(delays == [10, 20], f"cookie-media 10/30 delays were not preserved: {delays}")
    _assert_stable_fallback_command(stable)


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
                def fake_run(command, *_args, failure_kind=kind, **_kwargs):
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
            def fake_run(command, *_args, **_kwargs):
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


def _run_real_runner_transport_fallback(
    kind: YtdlpFailureKind,
    text: str,
    *,
    http_status: int | None = None,
) -> tuple[list[list[str]], list]:
    with TemporaryDirectory(prefix="real_runner_fallback_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        calls: list[list[str]] = []
        contexts = []
        controller = DownloadController()

        def callback(context):
            contexts.append(context)
            controller.submit_systemic_decision(context.block_id, BatchDecision.SKIP_CURRENT.value)

        controller.systemic_block_callback = callback
        old_run = downloader._run_ytdlp
        try:
            def fake_run(command, _controller=None):
                calls.append(list(command))
                if len(calls) == 1:
                    raise _failure([], kind, text, http_status=http_status)
                _assert_stable_fallback_command(list(command))
                return ""

            downloader._run_ytdlp = fake_run
            with _patched_runtime(runtime_paths):
                downloader._run_media_ytdlp_with_engine_fallback(
                    lambda *, force_stable: downloader._build_video_ytdlp_command(
                        "real-runner",
                        root,
                        options,
                        force_stable_downloader=force_stable,
                        aria2_validation=validation,
                    ),
                    root,
                    options,
                    lambda _message: None,
                    controller,
                )
        finally:
            downloader._run_ytdlp = old_run
    return calls, contexts


def _run_real_runner_ineligible_failure(kind: YtdlpFailureKind, text: str) -> list[list[str]]:
    with TemporaryDirectory(prefix="real_runner_ineligible_") as temp_dir:
        root = Path(temp_dir)
        runtime_paths = _runtime_paths(root, aria2=True)
        validation = downloader._Aria2RuntimeValidation(True, True, runtime_paths["aria2c.exe"])
        options = _options(root, DOWNLOAD_ENGINE_ARIA2_FAST)
        calls: list[list[str]] = []
        old_run = downloader._run_ytdlp
        try:
            def fake_run(command, _controller=None):
                calls.append(list(command))
                raise _failure([], kind, text)

            downloader._run_ytdlp = fake_run
            with _patched_runtime(runtime_paths):
                try:
                    downloader._run_media_ytdlp_with_engine_fallback(
                        lambda *, force_stable: downloader._build_video_ytdlp_command(
                            "real-ineligible",
                            root,
                            options,
                            force_stable_downloader=force_stable,
                            aria2_validation=validation,
                        ),
                        root,
                        options,
                        lambda _message: None,
                    )
                except Exception:
                    pass
                else:
                    raise AssertionError(f"{kind.value} unexpectedly succeeded")
        finally:
            downloader._run_ytdlp = old_run
    return calls


def _write_fake_infojson(command: list[str]) -> Path:
    output_template = downloader._command_option_value(command, "-o")
    _assert(output_template, "fake authenticated extraction had no output template")
    output_path = Path(output_template.replace("%(ext)s", "info.json"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('{"id": "video123", "title": "Video"}', encoding="utf-8")
    return output_path


def _failure(
    command: list[str],
    kind: YtdlpFailureKind,
    text: str,
    *,
    http_status: int | None = None,
    stage: str = YTDLP_STAGE_DOWNLOAD,
) -> YtdlpExecutionError:
    return YtdlpExecutionError(
        1,
        text,
        [text],
        combined_output=text,
        stream_interrupted=kind in {YtdlpFailureKind.NETWORK, YtdlpFailureKind.NETWORK_TIMEOUT},
        failure_kind=kind,
        fatal_lines=[text],
        http_status=http_status if http_status is not None else (403 if kind == YtdlpFailureKind.HTTP_403 else None),
        stage=stage,
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
    _assert("--external-downloader" not in command, "stable fallback retained --external-downloader")
    _assert("--downloader-args" not in command, "stable fallback retained --downloader-args")
    _assert("--external-downloader-args" not in command, "stable fallback retained --external-downloader-args")
    _assert(not _contains_aria2(command), "stable fallback retained aria2 value")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
