import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader


def main() -> int:
    _test_pattern_matching()
    _test_command_uses_aria2()
    _test_contextual_classification()
    _test_stable_command_not_reclassified()
    _test_audio_not_reclassified()
    _test_other_aria2_codes_not_reclassified()
    _test_unknown_kind_can_be_contextually_reclassified()
    _test_cookie_authenticated_code_22_triggers_infojson_fallback()
    _test_cookieless_code_22_uses_metadata_age_retry()
    _test_real_http_403_behavior_is_unchanged()
    _test_code_22_wrong_context_does_not_trigger_cookie_fallback()
    _test_code_22_logs_do_not_expose_sensitive_values()
    print("aria2 HTTP response fallback smoke passed")
    return 0


def _test_pattern_matching() -> None:
    matching = (
        "ERROR: aria2c exited with code 22",
        "aria2c exited with code 22",
        "ERROR: aria2c.exe exited with code 22",
        "ARIA2C EXITED WITH CODE 22",
    )
    for text in matching:
        _assert(
            downloader._contains_aria2_http_response_exit_error(text),
            f"aria2 code 22 did not match: {text!r}",
        )

    nonmatching = (
        "aria2c exited with code 2",
        "aria2c exited with code 23",
        "aria2c exited with code 24",
        "aria2c exited with code 220",
        "yt-dlp exited with code 22",
    )
    for text in nonmatching:
        _assert(
            not downloader._contains_aria2_http_response_exit_error(text),
            f"non-code-22 text matched: {text!r}",
        )

    _assert(
        downloader.ARIA2_HTTP_RESPONSE_EXIT_CODE == 22,
        "aria2 HTTP response exit-code constant changed",
    )


def _test_command_uses_aria2() -> None:
    cases = (
        (["yt-dlp", "--downloader", r"D:\app\data\bin\aria2c.exe"], True),
        (["yt-dlp", "--downloader", "aria2c"], True),
        (["yt-dlp", "--downloader", "aria2c.exe"], True),
        (["yt-dlp"], False),
        (["yt-dlp", "--downloader", "native"], False),
        (["yt-dlp", "--downloader", "ffmpeg"], False),
    )
    for command, expected in cases:
        _assert(
            downloader._command_uses_aria2(command) is expected,
            f"aria2 command detection was wrong: {command}",
        )


def _test_contextual_classification() -> None:
    with TemporaryDirectory(prefix="aria2_classify_") as temp_dir:
        root = Path(temp_dir)
        command = _fast_video_command(root)
        options = _options(root, downloader.DOWNLOAD_ENGINE_ARIA2_FAST)
        error = _aria2_code_22_error(command)
        _assert(error.http_status is None, "aria2 code 22 started with a fake HTTP status")
        _assert(not error.http_403, "aria2 code 22 started with an HTTP 403 flag")
        kind = downloader.classify_ytdlp_failure_kind(error, options)
        _assert(
            kind == downloader.YtdlpFailureKind.HTTP_403,
            "Fast video aria2 code 22 was not routed to the media fallback class",
        )
        _assert(
            downloader._is_aria2_http_response_media_failure(error),
            "strict aria2 media failure detector rejected the real context",
        )
        _assert(error.http_status is None, "contextual classification faked HTTP status 403")
        _assert(not error.http_403, "contextual classification mutated the HTTP 403 flag")
        _assert(
            downloader._media_access_failure_description(error)
            == "aria2 HTTP response failure (exit code 22: bad or unexpected HTTP response header)",
            "aria2 media failure description was wrong",
        )


def _test_stable_command_not_reclassified() -> None:
    with TemporaryDirectory(prefix="aria2_stable_exclusion_") as temp_dir:
        root = Path(temp_dir)
        options = _options(root, downloader.DOWNLOAD_ENGINE_STABLE)
        error = _aria2_code_22_error(_stable_video_command(root))
        _assert(
            downloader.classify_ytdlp_failure_kind(error, options)
            == downloader.YtdlpFailureKind.UNKNOWN,
            "Stable command was reclassified from aria2 text alone",
        )


def _test_audio_not_reclassified() -> None:
    with TemporaryDirectory(prefix="aria2_audio_exclusion_") as temp_dir:
        root = Path(temp_dir)
        options = _options(root, downloader.DOWNLOAD_ENGINE_ARIA2_FAST)
        error = _aria2_code_22_error(
            _fast_video_command(root),
            part=downloader.PART_AUDIO,
        )
        _assert(
            downloader.classify_ytdlp_failure_kind(error, options)
            == downloader.YtdlpFailureKind.UNKNOWN,
            "Fast audio code 22 was reclassified as video media access",
        )


def _test_other_aria2_codes_not_reclassified() -> None:
    with TemporaryDirectory(prefix="aria2_other_codes_") as temp_dir:
        root = Path(temp_dir)
        command = _fast_video_command(root)
        options = _options(root, downloader.DOWNLOAD_ENGINE_ARIA2_FAST)
        for code in (2, 23, 24, 28, 220):
            error = _aria2_error(command, code=code)
            _assert(
                downloader.classify_ytdlp_failure_kind(error, options)
                == downloader.YtdlpFailureKind.UNKNOWN,
                f"aria2 code {code} was incorrectly reclassified",
            )
        generic = _execution_error(
            command,
            "ERROR: aria2c failed for an unknown reason",
        )
        _assert(
            downloader.classify_ytdlp_failure_kind(generic, options)
            == downloader.YtdlpFailureKind.UNKNOWN,
            "generic aria2 failure was incorrectly reclassified",
        )


def _test_unknown_kind_can_be_contextually_reclassified() -> None:
    with TemporaryDirectory(prefix="aria2_unknown_reclassify_") as temp_dir:
        root = Path(temp_dir)
        error = _aria2_code_22_error(_fast_video_command(root))
        _assert(
            error.failure_kind == downloader.YtdlpFailureKind.UNKNOWN,
            "test error did not begin with UNKNOWN",
        )
        actual = downloader.classify_ytdlp_failure_kind(
            error,
            _options(root, downloader.DOWNLOAD_ENGINE_ARIA2_FAST),
        )
        _assert(
            actual == downloader.YtdlpFailureKind.HTTP_403,
            "stored UNKNOWN prevented contextual reclassification",
        )


def _test_cookie_authenticated_code_22_triggers_infojson_fallback() -> None:
    with TemporaryDirectory(prefix="aria2_cookie_fallback_") as temp_dir:
        root = Path(temp_dir)
        options = _options_with_cookies(root)
        command = _fast_video_command(root)
        calls: list[list[str]] = []
        logs: list[str] = []
        lookahead: list[str] = []
        state = downloader._YtdlpAttemptState(
            batch_state=downloader._YtdlpBatchState(),
            lookahead_callback=lambda: lookahead.append("started"),
        )

        def sequence(current: list[str], _controller=None):
            calls.append(list(current))
            if len(calls) == 1:
                raise _aria2_code_22_error(current)
            if len(calls) == 2:
                _write_fake_infojson(current)
                return ""
            if len(calls) == 3:
                return ""
            raise AssertionError(f"unexpected yt-dlp call {len(calls)}")

        with _patched_attr(downloader, "_run_ytdlp", sequence):
            with _progress_phase("Video"):
                downloader._run_ytdlp_with_retries(
                    command,
                    options,
                    logs.append,
                    cookie_retry_state=state,
                )

        _assert(len(calls) == 3, f"initial fallback call count was wrong: {len(calls)}")
        initial, metadata, saved_media = calls
        _assert(downloader._command_uses_aria2(initial), "initial Fast media lost aria2")
        _assert(downloader._command_uses_cookies(initial), "initial media missed isolated cookies")
        initial_cookie = downloader._command_option_value(initial, "--cookies")
        _assert(initial_cookie, "initial media had no isolated cookie path")
        _assert(
            Path(initial_cookie) != Path(options.cookies_path),
            "initial media used the canonical cookie path directly",
        )
        _assert("--skip-download" in metadata, "metadata extraction did not skip download")
        _assert("--write-info-json" in metadata, "metadata extraction did not write info JSON")
        _assert(downloader._command_uses_cookies(metadata), "metadata extraction missed cookies")
        _assert(not downloader._command_uses_aria2(metadata), "metadata extraction retained aria2")
        _assert("--load-info-json" in saved_media, "saved-media transfer missed info JSON")
        _assert(downloader._command_uses_aria2(saved_media), "saved-media transfer lost aria2")
        _assert(not downloader._command_uses_cookies(saved_media), "saved-media transfer retained cookies")
        _assert(not _contains_youtube_watch_url(saved_media), "saved-media transfer retained watch URL")
        _assert(state.authenticated_infojson_fallback_used, "fallback state was not marked used")
        _assert(lookahead == ["started"], f"lookahead start count was wrong: {lookahead}")

        joined = "\n".join(logs)
        _assert("[YT-DLP CLASS] http_403" in joined, "compatibility class log missing")
        _assert(
            "[YT-DLP CLASS DETAIL] aria2_http_response_exit_22" in joined,
            "aria2 detail log missing",
        )
        _assert(
            "[COOKIE FALLBACK] aria2 media transfer failed because the HTTP response header "
            "was bad or unexpected (exit code 22)." in joined,
            "aria2 cookie fallback log missing",
        )
        _assert(
            "Cookie-authenticated media request returned HTTP 403" not in joined,
            "code 22 was logged as an actual HTTP 403 response",
        )


def _test_cookieless_code_22_uses_metadata_age_retry() -> None:
    with TemporaryDirectory(prefix="aria2_age_retry_") as temp_dir:
        root = Path(temp_dir)
        options = _options_with_cookies(root)
        command = _fast_video_command(root)
        calls: list[list[str]] = []
        delays: list[int] = []
        logs: list[str] = []
        lookahead: list[str] = []
        batch_state = downloader._YtdlpBatchState()
        state = downloader._YtdlpAttemptState(
            batch_state=batch_state,
            lookahead_callback=lambda: lookahead.append("started"),
        )

        def sequence(current: list[str], _controller=None):
            calls.append(list(current))
            if len(calls) == 1:
                raise _aria2_code_22_error(current)
            if len(calls) == 2:
                _write_fake_infojson(current)
                return ""
            if len(calls) == 3:
                raise _aria2_code_22_error(current)
            if len(calls) == 4:
                return ""
            raise AssertionError(f"unexpected yt-dlp call {len(calls)}")

        with _patched_attr(downloader, "_run_ytdlp", sequence), _patched_attr(
            downloader,
            "_sleep_with_cancel",
            lambda seconds, _controller=None: delays.append(int(seconds)),
        ):
            with _progress_phase("Video"):
                downloader._run_ytdlp_with_retries(
                    command,
                    options,
                    logs.append,
                    cookie_retry_state=state,
                )

        _assert(len(calls) == 4, f"offline attempt sequence was wrong: {len(calls)}")
        expected_target = downloader.COOKIE_MEDIA_RETRY_TARGET_SECONDS[0]
        _assert(delays == [expected_target], f"metadata-age retry delays were wrong: {delays}")
        metadata_calls = [current for current in calls if "--write-info-json" in current]
        _assert(len(metadata_calls) == 1, "authenticated metadata extraction ran more than once")
        media_calls = [calls[0], calls[2], calls[3]]
        for current in media_calls:
            _assert(
                downloader._command_uses_aria2(current),
                "code-22 recovery switched media transfer to Stable",
            )
            _assert(
                downloader._command_option_value(current, "--downloader-args")
                == downloader.ARIA2_FAST_DOWNLOADER_ARGS,
                "code-22 recovery changed the aria2 profile",
            )
        for current in calls[2:4]:
            _assert(not downloader._command_uses_cookies(current), "saved-media retry re-enabled cookies")
            _assert("--load-info-json" in current, "saved-media retry lost info-json handoff")
            _assert(not _contains_youtube_watch_url(current), "saved-media retry restored watch URL")
        _assert(state.authenticated_infojson_fallback_used, "fallback state was not retained")
        _assert(batch_state.cookie_bootstrap_media_mode, "batch success was not recorded")
        _assert(lookahead == ["started"], f"lookahead was not preserved: {lookahead}")
        joined = "\n".join(logs)
        _assert(
            "aria2 HTTP response failure during cookieless media transfer (exit code 22)" in joined,
            "cookieless aria2 retry warning missing",
        )
        _assert("full transcode" not in joined.lower(), "full transcode was reintroduced")


def _test_real_http_403_behavior_is_unchanged() -> None:
    with TemporaryDirectory(prefix="aria2_real_403_") as temp_dir:
        root = Path(temp_dir)
        options = _options_with_cookies(root, engine=downloader.DOWNLOAD_ENGINE_STABLE)
        command = _stable_video_command(root)
        error = _real_http_403_error(command)
        _assert(
            downloader.classify_ytdlp_failure_kind(error, options)
            == downloader.YtdlpFailureKind.HTTP_403,
            "real HTTP 403 classification changed",
        )
        _assert(downloader._is_video_data_http_403(error), "real video-data HTTP 403 was not strict")

        calls: list[list[str]] = []
        logs: list[str] = []

        def sequence(current: list[str], _controller=None):
            calls.append(list(current))
            if len(calls) == 1:
                raise _real_http_403_error(current)
            if len(calls) == 2:
                _write_fake_infojson(current)
                return ""
            if len(calls) == 3:
                return ""
            raise AssertionError(f"unexpected real-403 call {len(calls)}")

        with _patched_attr(downloader, "_run_ytdlp", sequence):
            with _progress_phase("Video"):
                downloader._run_ytdlp_with_retries(command, options, logs.append)

        joined = "\n".join(logs)
        exact_message = (
            "[COOKIE FALLBACK] Cookie-authenticated media request returned HTTP 403. "
            "Extracting authenticated metadata, then downloading the saved media URLs "
            "without cookies."
        )
        _assert(exact_message in joined, "existing real-HTTP-403 fallback log changed")
        _assert("aria2_http_response_exit_22" not in joined, "real HTTP 403 received aria2 detail")


def _test_code_22_wrong_context_does_not_trigger_cookie_fallback() -> None:
    with TemporaryDirectory(prefix="aria2_wrong_context_") as temp_dir:
        root = Path(temp_dir)
        options = _options_with_cookies(root)
        fast = _fast_video_command(root)
        stable = _stable_video_command(root)
        attempt_info = downloader._PreparedCookieAttempt(
            command=fast,
            cookies_used=True,
        )
        cases = (
            (_aria2_code_22_error(fast, part=downloader.PART_AUDIO), downloader.YtdlpFailureKind.UNKNOWN),
            (_aria2_code_22_error(stable), downloader.YtdlpFailureKind.UNKNOWN),
            (_aria2_error(fast, code=23), downloader.YtdlpFailureKind.UNKNOWN),
            (
                _execution_error(fast, "ERROR: Requested format is not available"),
                downloader.YtdlpFailureKind.FORMAT_UNAVAILABLE,
            ),
            (
                _execution_error(fast, "ERROR: unable to open output file: Access is denied"),
                downloader.YtdlpFailureKind.OUTPUT_PATH,
            ),
        )
        for error, expected_kind in cases:
            actual = downloader.classify_ytdlp_failure_kind(error, options)
            _assert(actual == expected_kind, f"wrong-context classification changed: {actual}")
            _assert(
                not downloader._should_use_authenticated_infojson_fallback(
                    error,
                    actual,
                    options,
                    attempt_info,
                    downloader._YtdlpAttemptState(),
                ),
                f"wrong context triggered authenticated fallback: {actual}",
            )


def _test_code_22_logs_do_not_expose_sensitive_values() -> None:
    with TemporaryDirectory(prefix="aria2_log_redaction_") as temp_dir:
        root = Path(temp_dir)
        command = _fast_video_command(root)
        signed_url = (
            "https://rr1---sn.googlevideo.com/videoplayback?"
            "expire=123&token=signed-secret&sig=signature-secret"
        )
        lines = [
            "ERROR: aria2c exited with code 22",
            signed_url,
            "cookie: SID=session-secret; SAPISID=account-secret",
            "authorization token=bearer-secret",
            "api_key=secret-api-key",
        ]
        error = downloader.YtdlpExecutionError(
            exit_code=1,
            message="nonzero yt-dlp exit code",
            output_lines=lines,
            combined_output="\n".join(lines),
            failure_kind=downloader.YtdlpFailureKind.UNKNOWN,
            fatal_lines=lines,
            stage=downloader.YTDLP_STAGE_DOWNLOAD,
            part=downloader.PART_VIDEO,
            command=command,
        )
        logs: list[str] = []
        kind = downloader.classify_ytdlp_failure_kind(
            error,
            _options(root, downloader.DOWNLOAD_ENGINE_ARIA2_FAST),
        )
        downloader._log_ytdlp_attempt_failure(logs.append, error, kind, 1)
        if downloader._is_aria2_http_response_media_failure(error):
            logs.append("[YT-DLP CLASS DETAIL] aria2_http_response_exit_22")
        joined = "\n".join(logs)
        for secret in (
            "signed-secret",
            "signature-secret",
            "session-secret",
            "account-secret",
            "bearer-secret",
            "secret-api-key",
            "googlevideo.com",
            "videoplayback",
        ):
            _assert(secret not in joined, f"aria2 diagnostics leaked {secret!r}")
        _assert("aria2_http_response_exit_22" in joined, "redaction removed aria2 detail")


def _fast_video_command(root: Path) -> list[str]:
    options = downloader.DownloadOptions(
        base_folder=str(root),
        channel_id="channel",
        channel_name="Channel",
        cookies_enabled=True,
        cookies_path=str(root / "cookies.txt"),
        download_mode=downloader.MODE_VIDEO_THUMB,
        download_engine=downloader.DOWNLOAD_ENGINE_ARIA2_FAST,
        file_start_number=1,
    )
    aria2_path = root / "data" / "bin" / "aria2c.exe"
    aria2_validation = downloader._Aria2RuntimeValidation(
        requested=True,
        available=True,
        path=aria2_path,
    )

    def fake_runtime_file(filename: str) -> Path:
        return root / "data" / "bin" / filename

    with _patched_attr(downloader, "runtime_file", fake_runtime_file):
        return downloader._build_fast_video_ytdlp_command(
            "video-id",
            root,
            options,
            aria2_validation,
        )


def _stable_video_command(root: Path) -> list[str]:
    return [
        "yt-dlp",
        "-N",
        "1",
        "-f",
        downloader.PREMIERE_SAFE_VIDEO_FORMAT,
        "--no-write-info-json",
        "-o",
        str(root / "video.%(ext)s"),
        "https://www.youtube.com/watch?v=video-id",
    ]


def _aria2_code_22_error(
    command: list[str],
    *,
    part: str = downloader.PART_VIDEO,
    stage: str = downloader.YTDLP_STAGE_DOWNLOAD,
) -> downloader.YtdlpExecutionError:
    return _aria2_error(command, code=22, part=part, stage=stage)


def _aria2_error(
    command: list[str],
    *,
    code: int,
    part: str = downloader.PART_VIDEO,
    stage: str = downloader.YTDLP_STAGE_DOWNLOAD,
) -> downloader.YtdlpExecutionError:
    line = f"ERROR: aria2c exited with code {code}"
    return downloader.YtdlpExecutionError(
        exit_code=1,
        message="nonzero yt-dlp exit code",
        output_lines=[line],
        combined_output=line,
        failure_kind=downloader.YtdlpFailureKind.UNKNOWN,
        fatal_lines=[line],
        stage=stage,
        part=part,
        command=command,
    )


def _real_http_403_error(command: list[str]) -> downloader.YtdlpExecutionError:
    line = "ERROR: unable to download video data: HTTP Error 403: Forbidden"
    return downloader.YtdlpExecutionError(
        exit_code=1,
        message="yt-dlp failed with HTTP 403",
        output_lines=[line],
        combined_output=line,
        failure_kind=downloader.YtdlpFailureKind.HTTP_403,
        fatal_lines=[line],
        http_status=403,
        stage=downloader.YTDLP_STAGE_DOWNLOAD,
        part=downloader.PART_VIDEO,
        command=command,
    )


def _execution_error(command: list[str], line: str) -> downloader.YtdlpExecutionError:
    return downloader.YtdlpExecutionError(
        exit_code=1,
        message="nonzero yt-dlp exit code",
        output_lines=[line],
        combined_output=line,
        failure_kind=downloader.YtdlpFailureKind.UNKNOWN,
        fatal_lines=[line],
        stage=downloader.YTDLP_STAGE_DOWNLOAD,
        part=downloader.PART_VIDEO,
        command=command,
    )


def _options(
    root: Path,
    engine: str,
    *,
    cookies_enabled: bool = False,
    cookies_path: str = "",
) -> downloader.DownloadOptions:
    return downloader.DownloadOptions(
        base_folder=str(root),
        channel_id="channel",
        channel_name="Channel",
        cookies_enabled=cookies_enabled,
        cookies_path=cookies_path,
        download_mode=downloader.MODE_VIDEO_THUMB,
        download_engine=engine,
        file_start_number=1,
    )


def _options_with_cookies(
    root: Path,
    *,
    engine: str = downloader.DOWNLOAD_ENGINE_ARIA2_FAST,
) -> downloader.DownloadOptions:
    cookie_path = root / "cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    return _options(
        root,
        engine,
        cookies_enabled=True,
        cookies_path=str(cookie_path),
    )


def _write_fake_infojson(command: list[str]) -> Path:
    output_template = downloader._command_option_value(command, "-o")
    _assert(output_template, "authenticated extraction had no output template")
    output_path = Path(output_template.replace("%(ext)s", "info.json"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('{"id": "video-id", "title": "Video"}', encoding="utf-8")
    return output_path


def _contains_youtube_watch_url(command: list[str]) -> bool:
    return any(
        str(value).startswith(("https://www.youtube.com/", "https://youtu.be/"))
        for value in command
    )


@contextmanager
def _patched_attr(target, name: str, value):
    previous = getattr(target, name)
    setattr(target, name, value)
    try:
        yield
    finally:
        setattr(target, name, previous)


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
