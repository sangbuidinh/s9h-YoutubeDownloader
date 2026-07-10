import sys
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import downloader
from core.downloader import (
    DownloadController,
    DownloadOptions,
    YtdlpExecutionError,
    YtdlpFailureKind,
    classify_ytdlp_failure_kind,
)
from core.error_messages import classify_ytdlp_error, friendly_ytdlp_failure_kind_error


def main() -> int:
    options_with_cookies = DownloadOptions(".", "channel", "Channel", cookies_enabled=True)
    options_without_cookies = DownloadOptions(".", "channel", "Channel", cookies_enabled=False)

    _assert_kind("HTTP Error 429: Too Many Requests", options_without_cookies, YtdlpFailureKind.RATE_LIMIT)
    _assert_kind("ERROR: HTTP Error 401: Unauthorized", options_without_cookies, YtdlpFailureKind.HTTP_401)
    _assert_kind("ERROR: HTTP Error 403: Forbidden", options_without_cookies, YtdlpFailureKind.HTTP_403)
    _assert_kind("ERROR: HTTP Error 429: Too Many Requests", options_without_cookies, YtdlpFailureKind.RATE_LIMIT)
    _assert_kind(
        "Sign in to confirm you're not a bot",
        options_with_cookies,
        YtdlpFailureKind.BOT_CHECK,
        bot_check=True,
    )
    _assert_kind("Please verify that you're human", options_with_cookies, YtdlpFailureKind.BOT_CHECK)
    _assert_kind("This helps protect our community", options_with_cookies, YtdlpFailureKind.BOT_CHECK)
    _assert_kind(
        "Unusual traffic or automated requests were detected",
        options_with_cookies,
        YtdlpFailureKind.BOT_CHECK,
    )
    _assert_kind(
        "Sign in to confirm your age and verify that you're human",
        options_with_cookies,
        YtdlpFailureKind.BOT_CHECK,
    )
    _assert_not_kind("Sign in to confirm your age", options_with_cookies, YtdlpFailureKind.BOT_CHECK)
    _assert_not_kind("Sign in to confirm your identity", options_with_cookies, YtdlpFailureKind.BOT_CHECK)
    _assert_not_kind("Sign in to confirm your account", options_with_cookies, YtdlpFailureKind.BOT_CHECK)
    _assert_not_kind("Sign in to continue", options_with_cookies, YtdlpFailureKind.BOT_CHECK)
    _assert_not_kind("Please sign in to view this video", options_with_cookies, YtdlpFailureKind.BOT_CHECK)
    _assert_kind("Sign in to confirm your age", options_with_cookies, YtdlpFailureKind.COOKIE_SESSION)
    _assert_kind("Sign in to confirm your identity", options_with_cookies, YtdlpFailureKind.COOKIE_SESSION)
    _assert_kind("Sign in to confirm your account", options_with_cookies, YtdlpFailureKind.COOKIE_SESSION)
    _assert_kind("Sign in to continue", options_with_cookies, YtdlpFailureKind.LOGIN_REQUIRED)
    _assert_kind("Please sign in to continue", options_with_cookies, YtdlpFailureKind.LOGIN_REQUIRED)
    _assert_kind("Please sign in to view this video", options_with_cookies, YtdlpFailureKind.COOKIE_SESSION)
    _assert_kind("You must be signed in to view this video", options_with_cookies, YtdlpFailureKind.COOKIE_SESSION)
    _assert_kind(
        "Authentication is required to view this video",
        options_with_cookies,
        YtdlpFailureKind.LOGIN_REQUIRED,
    )
    _assert_kind("Missing required Visitor Data", options_with_cookies, YtdlpFailureKind.PO_TOKEN_OR_VISITOR_DATA)
    _assert_kind("PO Token is required for this client", options_with_cookies, YtdlpFailureKind.PO_TOKEN_OR_VISITOR_DATA)
    _assert_kind("ERROR: Requested format is not available", options_without_cookies, YtdlpFailureKind.FORMAT_UNAVAILABLE)
    _assert_kind("ERROR: No video formats found", options_without_cookies, YtdlpFailureKind.FORMAT_UNAVAILABLE)
    _assert_kind("ERROR: No suitable formats", options_without_cookies, YtdlpFailureKind.FORMAT_UNAVAILABLE)
    _assert_kind("Premiere-safe validation failed", options_without_cookies, YtdlpFailureKind.FORMAT_UNAVAILABLE)
    _assert_kind("cookies are no longer valid", options_with_cookies, YtdlpFailureKind.COOKIE_SESSION)
    _assert_kind("The supplied browser session has expired", options_with_cookies, YtdlpFailureKind.COOKIE_SESSION)
    _assert_not_kind(
        "Use --cookies to provide browser cookies",
        options_with_cookies,
        YtdlpFailureKind.BOT_CHECK,
    )
    _assert_kind(
        "Private video. Sign in if you've been granted access",
        options_with_cookies,
        YtdlpFailureKind.PERMANENT_VIDEO,
    )
    _assert_kind(
        "Video unavailable. Use --cookies-from-browser for authentication",
        options_with_cookies,
        YtdlpFailureKind.PERMANENT_VIDEO,
    )
    _assert_kind(
        "This video has been removed by the uploader",
        options_with_cookies,
        YtdlpFailureKind.PERMANENT_VIDEO,
    )
    _assert_kind(
        "This video is not available in your country",
        options_with_cookies,
        YtdlpFailureKind.PERMANENT_VIDEO,
    )
    _assert_not_kind(
        "Age-restricted video. Use --cookies to authenticate",
        options_with_cookies,
        YtdlpFailureKind.BOT_CHECK,
    )
    _assert_kind(
        "Age-restricted video. Use --cookies to authenticate",
        options_with_cookies,
        YtdlpFailureKind.COOKIE_SESSION,
    )
    _assert_kind("HTTP Error 403: Forbidden", options_with_cookies, YtdlpFailureKind.HTTP_403, http_403=True)
    _assert_kind("Private video", options_without_cookies, YtdlpFailureKind.PERMANENT_VIDEO)
    _assert_kind(
        "No supported JavaScript runtime could be found",
        options_without_cookies,
        YtdlpFailureKind.TOOL_CONFIGURATION,
        missing_js_runtime=True,
    )
    _assert_kind("100 bytes read, 200 more expected", options_without_cookies, YtdlpFailureKind.NETWORK)

    bot_friendly = classify_ytdlp_error(
        "Sign in to confirm you're not a bot",
        cookies_enabled=True,
        bot_check=True,
    )
    _assert(bot_friendly is friendly_ytdlp_failure_kind_error("bot_check"), "bot-check friendly error was not distinct")
    _assert("expired" not in bot_friendly.title.lower(), "bot-check title claimed cookies expired")

    cookie_friendly = classify_ytdlp_error("cookies are no longer valid", cookies_enabled=True)
    _assert(
        cookie_friendly is friendly_ytdlp_failure_kind_error("cookie_session"),
        "cookie-session friendly error was not distinct",
    )

    http_403_friendly = classify_ytdlp_error("HTTP Error 403: Forbidden", http_403=True)
    _assert(
        http_403_friendly is friendly_ytdlp_failure_kind_error("http_403"),
        "plain 403 friendly error was not distinct",
    )
    _test_vietnamese_friendly_errors()
    _test_permanent_video_does_not_pause()
    _test_recovered_http_403_warning_succeeds()
    _test_warning_403_then_format_fatal_is_not_403()
    _test_true_fatal_403_retries_with_fatal_logs()
    _test_cookie_media_403_uses_authenticated_infojson_fallback()
    _test_authenticated_infojson_fallback_then_standard_retries()
    _test_cookie_batch_mode_skips_repeated_cookie_media_failure()
    _test_cookie_batch_mode_probes_shorter_delay()
    _test_cookie_batch_mode_short_probe_can_lower_delay()
    _test_fatal_http_statuses()
    _test_stage_tracking()
    _test_sanitized_fatal_lines()
    _test_aria2_unknown_contextual_reclassification()
    _test_unknown_failure()

    print("yt-dlp failure classification smoke passed")
    return 0


def _assert_kind(
    output: str,
    options: DownloadOptions,
    expected: YtdlpFailureKind,
    bot_check: bool = False,
    http_403: bool = False,
    missing_js_runtime: bool = False,
) -> None:
    exc = YtdlpExecutionError(
        1,
        output,
        [output],
        bot_check=bot_check,
        http_403=http_403,
        missing_js_runtime=missing_js_runtime,
        combined_output=output,
    )
    actual = classify_ytdlp_failure_kind(exc, options)
    _assert(actual == expected, f"{output!r}: expected {expected}, got {actual}")


def _assert_not_kind(
    output: str,
    options: DownloadOptions,
    forbidden: YtdlpFailureKind,
) -> None:
    exc = YtdlpExecutionError(1, output, [output], combined_output=output)
    actual = classify_ytdlp_failure_kind(exc, options)
    _assert(actual != forbidden, f"{output!r}: unexpectedly classified as {forbidden}")


def _test_permanent_video_does_not_pause() -> None:
    permanent_outputs = (
        "Private video. Sign in if you've been granted access",
        "Video unavailable. Use --cookies-from-browser for authentication",
        "This video has been removed by the uploader",
        "This video is not available in your country",
    )
    old_run_ytdlp = downloader._run_ytdlp
    try:
        for index, output in enumerate(permanent_outputs, start=1):
            contexts = []
            calls = []
            controller = DownloadController(systemic_block_callback=lambda context: contexts.append(context))

            def permanent_error(command, _cancel_controller=None, text=output):
                calls.append(command)
                raise YtdlpExecutionError(
                    1,
                    "permanent video",
                    [text],
                    combined_output=text,
                )

            downloader._run_ytdlp = permanent_error
            try:
                downloader._run_ytdlp_with_retries(
                    ["yt-dlp", f"https://www.youtube.com/watch?v=private{index}"],
                    DownloadOptions(".", "channel", "Channel", cookies_enabled=False),
                    lambda _message: None,
                    controller,
                )
            except YtdlpExecutionError:
                pass
            else:
                raise AssertionError("permanent-video error was swallowed")
            _assert(len(calls) == 1, f"permanent-video call count was wrong for {output!r}: {len(calls)}")
            _assert(not contexts, f"permanent-video error entered systemic pause callback: {output!r}")
    finally:
        downloader._run_ytdlp = old_run_ytdlp


def _test_recovered_http_403_warning_succeeds() -> None:
    logs: list[str] = []
    command = _python_ytdlp_command(
        [
            "WARNING: Unable to download webpage: HTTP Error 403",
            "[youtube] Downloading another client",
            "[download] Destination: output.mp4",
        ],
        0,
    )
    with _progress_phase("Video"):
        downloader._run_ytdlp_with_retries(
            command,
            DownloadOptions(".", "channel", "Channel", cookies_enabled=False),
            logs.append,
        )

    joined = "\n".join(logs)
    _assert(
        "[YT-DLP START] part=video stage=extract attempt=1 cookies=disabled ipv4=forced fragments=1" in joined,
        "start diagnostic missing",
    )
    _assert("[YT-DLP FAILED]" not in joined, "successful recovered warning logged as failure")
    _assert("Retrying in" not in joined, "successful recovered warning triggered retry")


def _test_warning_403_then_format_fatal_is_not_403() -> None:
    logs: list[str] = []
    command = _python_ytdlp_command(
        [
            "WARNING: HTTP Error 403",
            "ERROR: Requested format is not available",
        ],
        1,
    )
    with _progress_phase("Video"):
        try:
            downloader._run_ytdlp_with_retries(
                command,
                DownloadOptions(".", "channel", "Channel", cookies_enabled=False),
                logs.append,
            )
        except YtdlpExecutionError as exc:
            actual = classify_ytdlp_failure_kind(exc, DownloadOptions(".", "channel", "Channel"))
            _assert(actual == YtdlpFailureKind.FORMAT_UNAVAILABLE, f"expected format_unavailable, got {actual}")
            _assert(not exc.http_403, "format-unavailable fatal retained sticky http_403")
        else:
            raise AssertionError("format-unavailable failure was swallowed")

    joined = "\n".join(logs)
    _assert("[YT-DLP CLASS] format_unavailable" in joined, "format class log missing")
    _assert("[YT-DLP FATAL] ERROR: Requested format is not available" in joined, "fatal format line missing")
    _assert("HTTP 403 during" not in joined, "format fatal used HTTP 403 retry message")


def _test_true_fatal_403_retries_with_fatal_logs() -> None:
    logs: list[str] = []
    delays: list[int] = []
    old_sleep = downloader._sleep_with_cancel
    try:
        downloader._sleep_with_cancel = lambda seconds, _cancel_controller=None: delays.append(seconds)
        with _progress_phase("Video"):
            try:
                downloader._run_ytdlp_with_retries(
                    _python_ytdlp_command(["ERROR: Unable to download webpage: HTTP Error 403: Forbidden"], 1),
                    DownloadOptions(".", "channel", "Channel", cookies_enabled=False),
                    logs.append,
                )
            except downloader.DownloadCancelled:
                pass
            else:
                raise AssertionError("fatal 403 without pause callback did not stop batch after retries")
    finally:
        downloader._sleep_with_cancel = old_sleep

    joined = "\n".join(logs)
    _assert(delays == [10, 30], f"HTTP 403 retry delays changed: {delays}")
    _assert(joined.count("[YT-DLP START]") == 3, "fatal 403 did not run initial attempt plus two retries")
    _assert("[YT-DLP CLASS] http_403" in joined, "HTTP 403 class log missing")
    _assert(
        "[YT-DLP FATAL] ERROR: Unable to download webpage: HTTP Error 403: Forbidden" in joined,
        "HTTP 403 fatal line missing",
    )
    first_fatal = joined.index("[YT-DLP FATAL]")
    first_retry = joined.index("Retrying in 10 seconds")
    _assert(first_fatal < first_retry, "retry was logged before fatal diagnostics")
    _assert(
        "[WARNING] HTTP 403 during video/extract. Retrying in 10 seconds (retry 1/2)." in joined,
        "first retry message changed",
    )
    _assert(
        "[WARNING] HTTP 403 during video/extract. Retrying in 30 seconds (retry 2/2)." in joined,
        "second retry message changed",
    )


def _test_cookie_media_403_uses_authenticated_infojson_fallback() -> None:
    logs: list[str] = []
    calls: list[list[str]] = []
    delays: list[int] = []
    old_run_ytdlp = downloader._run_ytdlp
    old_sleep = downloader._sleep_with_cancel
    try:
        def fake_run(command, _cancel_controller=None):
            calls.append(list(command))
            if len(calls) == 1:
                fatal = "ERROR: unable to download video data: HTTP Error 403: Forbidden"
                raise YtdlpExecutionError(
                    1,
                    "yt-dlp failed with HTTP 403",
                    [fatal],
                    combined_output=fatal,
                    failure_kind=YtdlpFailureKind.HTTP_403,
                    fatal_lines=[fatal],
                    http_status=403,
                    stage="download",
                    part="video",
                )
            if "--write-info-json" in command:
                _write_fake_infojson(command)
                return ""
            _assert("--load-info-json" in command, "media fallback did not use --load-info-json")
            return ""

        downloader._run_ytdlp = fake_run
        downloader._sleep_with_cancel = lambda seconds, _controller=None: delays.append(int(seconds))
        with TemporaryDirectory(prefix="cookie_infojson_success_") as temp_dir:
            temp_root = Path(temp_dir)
            cookie_path = temp_root / "cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            options = DownloadOptions(
                ".",
                "channel",
                "Channel",
                cookies_enabled=True,
                cookies_path=str(cookie_path),
            )
            command = [
                "yt-dlp",
                "-N",
                "1",
                "--no-write-info-json",
                "-o",
                str(temp_root / "stage" / "video.%(ext)s"),
                "https://www.youtube.com/watch?v=cookie403",
            ]
            with _progress_phase("Video"):
                downloader._run_ytdlp_with_retries(
                    command,
                    options,
                    logs.append,
                )
    finally:
        downloader._run_ytdlp = old_run_ytdlp
        downloader._sleep_with_cancel = old_sleep

    _assert(len(calls) == 3, f"authenticated infojson fallback call count was wrong: {len(calls)}")
    initial, extract, media = calls
    _assert(downloader._command_uses_cookies(initial), "first attempt did not use cookies")
    _assert(downloader._command_uses_cookies(extract), "authenticated extraction did not use cookies")
    _assert("--skip-download" in extract, "authenticated extraction did not skip media download")
    _assert("--write-info-json" in extract, "authenticated extraction did not write info JSON")
    _assert("--no-write-info-json" not in extract, "authenticated extraction kept --no-write-info-json")
    _assert(not downloader._command_uses_cookies(media), "saved-media download still used cookies")
    _assert("--load-info-json" in media, "saved-media download did not load info JSON")
    _assert(
        not any(str(value).startswith("https://www.youtube.com/") for value in media),
        "saved-media download re-ran the YouTube extractor",
    )
    _assert(delays == [], f"authenticated infojson fallback waited before media retry: {delays}")
    joined = "\n".join(logs)
    _assert("Extracting authenticated metadata" in joined, "authenticated extraction message missing")
    _assert("Downloading from saved media URLs without cookies" in joined, "saved-media message missing")
    _assert("[COOKIE FALLBACK SUCCESS]" in joined, "authenticated infojson success diagnostic missing")
    _assert("Retrying in 10 seconds" not in joined, "HTTP 403 delay ran before infojson fallback")


def _test_authenticated_infojson_fallback_then_standard_retries() -> None:
    logs: list[str] = []
    calls: list[list[str]] = []
    delays: list[int] = []
    old_run_ytdlp = downloader._run_ytdlp
    old_sleep = downloader._sleep_with_cancel
    try:
        def persistent_media_403(command, _cancel_controller=None):
            calls.append(list(command))
            if len(calls) == 1:
                fatal = "ERROR: unable to download video data: HTTP Error 403: Forbidden"
                raise _media_403_error(fatal)
            if "--write-info-json" in command:
                _write_fake_infojson(command)
                return ""
            _assert("--load-info-json" in command, "post-bootstrap retry did not use saved info JSON")
            fatal = "ERROR: unable to download video data: HTTP Error 403: Forbidden"
            raise _media_403_error(fatal)

        downloader._run_ytdlp = persistent_media_403
        downloader._sleep_with_cancel = lambda seconds, _controller=None: delays.append(int(seconds))
        with TemporaryDirectory(prefix="cookie_infojson_failure_") as temp_dir:
            temp_root = Path(temp_dir)
            cookie_path = temp_root / "cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            options = DownloadOptions(
                ".",
                "channel",
                "Channel",
                cookies_enabled=True,
                cookies_path=str(cookie_path),
            )
            command = [
                "yt-dlp",
                "-N",
                "1",
                "--no-write-info-json",
                "-o",
                str(temp_root / "stage" / "video.%(ext)s"),
                "https://www.youtube.com/watch?v=cookie403fail",
            ]
            with _progress_phase("Video"):
                try:
                    downloader._run_ytdlp_with_retries(
                        command,
                        options,
                        logs.append,
                    )
                except downloader.DownloadCancelled:
                    pass
                else:
                    raise AssertionError("persistent saved-media HTTP 403 did not stop after retries")
    finally:
        downloader._run_ytdlp = old_run_ytdlp
        downloader._sleep_with_cancel = old_sleep

    _assert(len(calls) == 7, f"persistent infojson fallback call count was wrong: {len(calls)}")
    _assert(downloader._command_uses_cookies(calls[0]), "initial persistent attempt did not use cookies")
    _assert(downloader._command_uses_cookies(calls[1]), "bootstrap extraction did not use cookies")
    _assert("--write-info-json" in calls[1], "bootstrap extraction did not write info JSON")
    for command in calls[2:]:
        _assert(not downloader._command_uses_cookies(command), "saved-media retry re-enabled cookies")
        _assert("--load-info-json" in command, "saved-media retry did not reuse info JSON")
        _assert(
            not any(str(value).startswith("https://www.youtube.com/") for value in command),
            "saved-media retry re-ran the YouTube extractor",
        )
    _assert(delays == [2, 3, 5, 20], f"post-bootstrap HTTP 403 delays were wrong: {delays}")
    joined = "\n".join(logs)
    _assert(joined.count("Extracting authenticated metadata") == 1, "bootstrap extraction ran more than once")
    _assert("[COOKIE FALLBACK SUCCESS]" not in joined, "failed saved-media fallback was logged as successful")


def _test_cookie_batch_mode_skips_repeated_cookie_media_failure() -> None:
    logs: list[str] = []
    calls: list[list[str]] = []
    delays: list[int] = []
    old_run_ytdlp = downloader._run_ytdlp
    old_sleep = downloader._sleep_with_cancel
    batch_state = downloader._YtdlpBatchState()

    try:
        def batch_sequence(command, _cancel_controller=None):
            calls.append(list(command))
            call_number = len(calls)
            if call_number == 1:
                raise _media_403_error(
                    "ERROR: unable to download video data: HTTP Error 403: Forbidden"
                )
            if call_number in {2, 5}:
                _write_fake_infojson(command)
                return ""
            if call_number == 3:
                raise _media_403_error(
                    "ERROR: unable to download video data: HTTP Error 403: Forbidden"
                )
            if call_number in {4, 6}:
                return ""
            raise AssertionError(f"unexpected yt-dlp call {call_number}: {command}")

        downloader._run_ytdlp = batch_sequence
        downloader._sleep_with_cancel = lambda seconds, _controller=None: delays.append(int(seconds))

        with TemporaryDirectory(prefix="cookie_batch_mode_") as temp_dir:
            temp_root = Path(temp_dir)
            cookie_path = temp_root / "cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            options = DownloadOptions(
                ".",
                "channel",
                "Channel",
                cookies_enabled=True,
                cookies_path=str(cookie_path),
            )

            first_command = [
                "yt-dlp",
                "-N",
                "1",
                "--no-write-info-json",
                "-o",
                str(temp_root / "stage1" / "video.%(ext)s"),
                "https://www.youtube.com/watch?v=first403",
            ]
            second_command = [
                "yt-dlp",
                "-N",
                "1",
                "--no-write-info-json",
                "-o",
                str(temp_root / "stage2" / "video.%(ext)s"),
                "https://www.youtube.com/watch?v=second403",
            ]

            with _progress_phase("Video"):
                downloader._run_ytdlp_with_retries(
                    first_command,
                    options,
                    logs.append,
                    cookie_retry_state=downloader._YtdlpAttemptState(
                        batch_state=batch_state,
                    ),
                )
                downloader._run_ytdlp_with_retries(
                    second_command,
                    options,
                    logs.append,
                    cookie_retry_state=downloader._YtdlpAttemptState(
                        batch_state=batch_state,
                    ),
                )
    finally:
        downloader._run_ytdlp = old_run_ytdlp
        downloader._sleep_with_cancel = old_sleep

    _assert(len(calls) == 6, f"batch-mode yt-dlp call count was wrong: {len(calls)}")
    _assert(
        downloader._command_uses_cookies(calls[0])
        and "--skip-download" not in calls[0],
        "first video did not begin with the normal cookie media attempt",
    )
    _assert(
        downloader._command_uses_cookies(calls[1])
        and "--write-info-json" in calls[1]
        and "--skip-download" in calls[1],
        "first video authenticated metadata extraction was wrong",
    )
    _assert(
        not downloader._command_uses_cookies(calls[2])
        and "--load-info-json" in calls[2],
        "first saved-media attempt did not run cookieless",
    )
    _assert(
        not downloader._command_uses_cookies(calls[3])
        and "--load-info-json" in calls[3],
        "first saved-media retry did not remain cookieless",
    )
    _assert(
        downloader._command_uses_cookies(calls[4])
        and "--write-info-json" in calls[4]
        and "--skip-download" in calls[4],
        "second video did not start directly with authenticated metadata extraction",
    )
    _assert(
        not downloader._command_uses_cookies(calls[5])
        and "--load-info-json" in calls[5],
        "second video did not use the batch cookieless media strategy",
    )
    cookie_media_attempts = [
        command
        for command in calls
        if downloader._command_uses_cookies(command)
        and "--skip-download" not in command
    ]
    _assert(
        len(cookie_media_attempts) == 1,
        f"cookie media failure was repeated after batch learning: {len(cookie_media_attempts)}",
    )
    _assert(delays == [2, 2], f"batch-mode delays were wrong: {delays}")
    _assert(batch_state.cookie_bootstrap_media_mode, "batch mode was not enabled")
    _assert(
        batch_state.media_settle_delay_seconds == 2,
        f"learned media settle delay was wrong: {batch_state.media_settle_delay_seconds}",
    )

    joined = "\n".join(logs)
    _assert(
        "[COOKIE BATCH MODE] Enabled for the remaining videos in this batch." in joined,
        "batch-mode activation log missing",
    )
    _assert(
        "[COOKIE BATCH MODE] Reusing the successful batch strategy:" in joined,
        "later video did not report direct batch strategy reuse",
    )
    _assert(
        "[COOKIE BATCH MODE] Waiting 2 seconds before the media transfer (learned metadata age; metadata age target: 2 seconds)." in joined,
        "short learned settle delay was not applied before later media transfer",
    )



def _test_cookie_batch_mode_probes_shorter_delay() -> None:
    logs: list[str] = []
    calls: list[list[str]] = []
    delays: list[int] = []
    old_run_ytdlp = downloader._run_ytdlp
    old_sleep = downloader._sleep_with_cancel
    batch_state = downloader._YtdlpBatchState(
        cookie_bootstrap_media_mode=True,
        media_settle_delay_seconds=10,
        media_videos_since_probe=downloader.COOKIE_MEDIA_PROBE_INTERVAL_VIDEOS,
    )

    try:
        def probe_sequence(command, _cancel_controller=None):
            calls.append(list(command))
            call_number = len(calls)
            if call_number in {1, 4}:
                _write_fake_infojson(command)
                return ""
            if call_number == 2:
                raise _media_403_error(
                    "ERROR: unable to download video data: HTTP Error 403: Forbidden"
                )
            if call_number in {3, 5}:
                return ""
            raise AssertionError(f"unexpected yt-dlp call {call_number}: {command}")

        downloader._run_ytdlp = probe_sequence
        downloader._sleep_with_cancel = lambda seconds, _controller=None: delays.append(int(seconds))

        with TemporaryDirectory(prefix="cookie_batch_probe_") as temp_dir:
            temp_root = Path(temp_dir)
            cookie_path = temp_root / "cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\\n", encoding="utf-8")
            options = DownloadOptions(
                ".",
                "channel",
                "Channel",
                cookies_enabled=True,
                cookies_path=str(cookie_path),
            )

            first_command = [
                "yt-dlp",
                "-N",
                "1",
                "--no-write-info-json",
                "-o",
                str(temp_root / "probe1" / "video.%(ext)s"),
                "https://www.youtube.com/watch?v=probe1",
            ]
            second_command = [
                "yt-dlp",
                "-N",
                "1",
                "--no-write-info-json",
                "-o",
                str(temp_root / "probe2" / "video.%(ext)s"),
                "https://www.youtube.com/watch?v=probe2",
            ]

            with _progress_phase("Video"):
                downloader._run_ytdlp_with_retries(
                    first_command,
                    options,
                    logs.append,
                    cookie_retry_state=downloader._YtdlpAttemptState(
                        batch_state=batch_state,
                    ),
                )
                downloader._run_ytdlp_with_retries(
                    second_command,
                    options,
                    logs.append,
                    cookie_retry_state=downloader._YtdlpAttemptState(
                        batch_state=batch_state,
                    ),
                )
    finally:
        downloader._run_ytdlp = old_run_ytdlp
        downloader._sleep_with_cancel = old_sleep

    _assert(len(calls) == 5, f"adaptive probe yt-dlp call count was wrong: {len(calls)}")
    _assert(delays == [2, 3, 5], f"adaptive probe delays were wrong: {delays}")
    _assert(
        batch_state.media_settle_delay_seconds == 5,
        f"adaptive retry did not learn the smaller stable delay: {batch_state.media_settle_delay_seconds}",
    )
    _assert(
        batch_state.media_videos_since_probe == 2,
        f"probe interval counter was wrong: {batch_state.media_videos_since_probe}",
    )

    joined = "\\n".join(logs)
    _assert(
        "Waiting 2 seconds before the media transfer (short adaptive probe; metadata age target: 2 seconds)." in joined,
        "short adaptive probe was not attempted",
    )
    _assert(
        "Short delay probe still received HTTP 403." in joined,
        "failed short probe was not reported",
    )
    _assert(
        "Waiting 5 seconds before the media transfer (learned metadata age; metadata age target: 5 seconds)." in joined,
        "stable learned delay was not reused after the failed probe",
    )



def _test_cookie_batch_mode_short_probe_can_lower_delay() -> None:
    logs: list[str] = []
    calls: list[list[str]] = []
    delays: list[int] = []
    old_run_ytdlp = downloader._run_ytdlp
    old_sleep = downloader._sleep_with_cancel
    batch_state = downloader._YtdlpBatchState(
        cookie_bootstrap_media_mode=True,
        media_settle_delay_seconds=10,
        media_videos_since_probe=downloader.COOKIE_MEDIA_PROBE_INTERVAL_VIDEOS,
    )

    try:
        def successful_probe_sequence(command, _cancel_controller=None):
            calls.append(list(command))
            if len(calls) == 1:
                _write_fake_infojson(command)
                return ""
            if len(calls) == 2:
                return ""
            raise AssertionError(f"unexpected yt-dlp call {len(calls)}: {command}")

        downloader._run_ytdlp = successful_probe_sequence
        downloader._sleep_with_cancel = lambda seconds, _controller=None: delays.append(int(seconds))

        with TemporaryDirectory(prefix="cookie_batch_probe_success_") as temp_dir:
            temp_root = Path(temp_dir)
            cookie_path = temp_root / "cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\\n", encoding="utf-8")
            options = DownloadOptions(
                ".",
                "channel",
                "Channel",
                cookies_enabled=True,
                cookies_path=str(cookie_path),
            )
            command = [
                "yt-dlp",
                "-N",
                "1",
                "--no-write-info-json",
                "-o",
                str(temp_root / "probe" / "video.%(ext)s"),
                "https://www.youtube.com/watch?v=probe-success",
            ]

            with _progress_phase("Video"):
                downloader._run_ytdlp_with_retries(
                    command,
                    options,
                    logs.append,
                    cookie_retry_state=downloader._YtdlpAttemptState(
                        batch_state=batch_state,
                    ),
                )
    finally:
        downloader._run_ytdlp = old_run_ytdlp
        downloader._sleep_with_cancel = old_sleep

    _assert(len(calls) == 2, f"successful short probe call count was wrong: {len(calls)}")
    _assert(delays == [2], f"successful short probe delay was wrong: {delays}")
    _assert(
        batch_state.media_settle_delay_seconds == 2,
        f"successful short probe did not lower delay: {batch_state.media_settle_delay_seconds}",
    )
    _assert(
        "Short delay probe succeeded." in "\\n".join(logs),
        "successful short probe was not reported",
    )


def _write_fake_infojson(command: list[str]) -> Path:
    output_template = downloader._command_option_value(command, "-o")
    _assert(output_template, "fake authenticated extraction had no output template")
    output_path = Path(output_template.replace("%(ext)s", "info.json"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('{"id": "video123", "title": "Video"}', encoding="utf-8")
    return output_path


def _media_403_error(fatal: str) -> YtdlpExecutionError:
    return YtdlpExecutionError(
        1,
        "yt-dlp failed with HTTP 403",
        [fatal],
        combined_output=fatal,
        failure_kind=YtdlpFailureKind.HTTP_403,
        fatal_lines=[fatal],
        http_status=403,
        stage="download",
        part="video",
    )
def _test_fatal_http_statuses() -> None:
    cases = (
        ("ERROR: HTTP Error 401: Unauthorized", YtdlpFailureKind.HTTP_401, 401),
        ("ERROR: HTTP Error 429: Too Many Requests", YtdlpFailureKind.RATE_LIMIT, 429),
    )
    for output, expected_kind, expected_status in cases:
        exc = _run_ytdlp_exception([output])
        actual = classify_ytdlp_failure_kind(exc, DownloadOptions(".", "channel", "Channel"))
        _assert(actual == expected_kind, f"{output!r}: expected {expected_kind}, got {actual}")
        _assert(exc.http_status == expected_status, f"{output!r}: status was {exc.http_status}")
        _assert(not exc.http_403, f"{output!r}: mislabeled as http_403")


def _test_stage_tracking() -> None:
    extract = _run_ytdlp_exception(["ERROR: HTTP Error 401: Unauthorized"], phase="Video")
    _assert(extract.stage == "extract", f"extract-stage failure reported {extract.stage}")
    _assert(extract.part == "video", f"video part was not captured: {extract.part}")

    download = _run_ytdlp_exception(
        [
            "[download] Destination: output.mp4",
            "ERROR: Unable to download webpage: HTTP Error 403: Forbidden",
        ],
        phase="Video",
    )
    _assert(download.stage == "download", f"download-stage failure reported {download.stage}")

    media_data = _run_ytdlp_exception(
        ["ERROR: unable to download video data: HTTP Error 403: Forbidden"],
        phase="Video",
    )
    _assert(
        media_data.stage == "download",
        f"video-data HTTP 403 was not recognized as download stage: {media_data.stage}",
    )


def _test_sanitized_fatal_lines() -> None:
    cookie_path = r"C:\Users\admin\secret\youtube_cookies.txt"
    signed_url = (
        "https://rr1---sn.googlevideo.com/videoplayback?"
        "expire=123&token=abc123&access_token=secret-token&sig=signed-value&foo=ok"
    )
    exc = _run_ytdlp_exception(
        [
            f"ERROR: Unable to download {signed_url} --cookies {cookie_path}",
            "cookie: SID=super-secret",
            "access_token=standalone-secret",
            "api_key=AIzaSy012345678901234567890123456789",
        ]
    )
    joined = "\n".join([*exc.fatal_lines, exc.combined_output])
    forbidden_fragments = (
        cookie_path,
        "SID=super-secret",
        "standalone-secret",
        "AIzaSy",
        "token=abc123",
        "access_token=secret-token",
        "googlevideo.com",
        "videoplayback",
    )
    for fragment in forbidden_fragments:
        _assert(fragment not in joined, f"sanitized diagnostics leaked {fragment!r}: {joined}")
    _assert("<signed-media-url-redacted>" in joined, "signed media URL was not redacted")
    _assert("<cookies-arg-redacted>" in joined, "cookies argument was not redacted")
    _assert("<cookie-redacted>" in joined, "cookie line was not redacted")
    _assert("access_token=***" in joined, "standalone access token was not redacted")


def _test_aria2_unknown_contextual_reclassification() -> None:
    line = "ERROR: aria2c exited with code 22"
    command = [
        "yt-dlp",
        "--downloader",
        r"D:\app\data\bin\aria2c.exe",
        "--downloader-args",
        downloader.ARIA2_FAST_DOWNLOADER_ARGS,
        "https://www.youtube.com/watch?v=video123",
    ]
    exc = YtdlpExecutionError(
        1,
        "nonzero yt-dlp exit code",
        [line],
        combined_output=line,
        failure_kind=YtdlpFailureKind.UNKNOWN,
        fatal_lines=[line],
        stage=downloader.YTDLP_STAGE_DOWNLOAD,
        part=downloader.PART_VIDEO,
        command=command,
    )
    _assert(exc.failure_kind == YtdlpFailureKind.UNKNOWN, "aria2 regression did not begin as UNKNOWN")
    actual = classify_ytdlp_failure_kind(
        exc,
        DownloadOptions(".", "channel", "Channel", download_engine=downloader.DOWNLOAD_ENGINE_ARIA2_FAST),
    )
    _assert(actual == YtdlpFailureKind.HTTP_403, "stored UNKNOWN blocked aria2 contextual classification")
    _assert(exc.http_status is None, "aria2 contextual classification faked HTTP status 403")


def _test_unknown_failure() -> None:
    exc = _run_ytdlp_exception(["final unexplained extractor failure"])
    actual = classify_ytdlp_failure_kind(exc, DownloadOptions(".", "channel", "Channel"))
    _assert(actual == YtdlpFailureKind.UNKNOWN, f"unknown failure classified as {actual}")
    _assert("final unexplained extractor failure" in "\n".join(exc.fatal_lines), "unknown fatal line missing")


def _run_ytdlp_exception(
    output_lines: list[str],
    exit_code: int = 1,
    phase: str = "Video",
) -> YtdlpExecutionError:
    with _progress_phase(phase):
        try:
            downloader._run_ytdlp(_python_ytdlp_command(output_lines, exit_code))
        except YtdlpExecutionError as exc:
            return exc
    raise AssertionError("yt-dlp command unexpectedly succeeded")


def _python_ytdlp_command(output_lines: list[str], exit_code: int) -> list[str]:
    script_lines = ["import sys"]
    for line in output_lines:
        script_lines.append(f"print({line!r}, flush=True)")
    script_lines.append(f"sys.exit({int(exit_code)})")
    return [sys.executable, "-c", "\n".join(script_lines), "--force-ipv4", "-N", "1"]


class _progress_phase:
    def __init__(self, phase: str):
        self.phase = phase
        self.previous = None

    def __enter__(self):
        self.previous = downloader._set_progress_context(
            lambda _event: None,
            SimpleNamespace(video_id="video123", title="Video", sanitized_filename_base="video"),
            1,
            1,
            self.phase,
        )
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        downloader._restore_progress_context(self.previous)


def _test_vietnamese_friendly_errors() -> None:
    expected_titles = {
        "bot_check": "YouTube yêu cầu xác minh người dùng",
        "rate_limit_429": "YouTube đang giới hạn lượt tải",
        "cookie_session": "Phiên đăng nhập không còn hợp lệ",
        "http_403": "YouTube liên tục từ chối truy cập",
    }
    for kind, title in expected_titles.items():
        error = friendly_ytdlp_failure_kind_error(kind)
        _assert(error.title == title, f"{kind} title was not Vietnamese: {error.title!r}")

    refreshed = friendly_ytdlp_failure_kind_error("cookie_session", refreshed_rejected=True)
    _assert(
        refreshed.title == "Cookie mới vẫn bị YouTube từ chối",
        f"refreshed-cookie title was not Vietnamese: {refreshed.title!r}",
    )

    english_titles = (
        "YouTube requires bot verification",
        "YouTube is rate limiting downloads",
        "YouTube rejected the current cookie session",
        "YouTube repeatedly returned HTTP 403",
        "Refreshed cookies were still rejected",
    )
    all_text = "\n".join(
        [
            *(friendly_ytdlp_failure_kind_error(kind).title for kind in expected_titles),
            *(friendly_ytdlp_failure_kind_error(kind).reason for kind in expected_titles),
            refreshed.title,
            refreshed.reason,
        ]
    )
    for english_title in english_titles:
        _assert(english_title not in all_text, f"English friendly text remained: {english_title}")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
