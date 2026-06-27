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
    _test_cookie_media_403_falls_back_without_delay()
    _test_cookie_media_403_fallback_then_standard_retries()
    _test_fatal_http_statuses()
    _test_stage_tracking()
    _test_sanitized_fatal_lines()
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


def _test_cookie_media_403_falls_back_without_delay() -> None:
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
                    stage="extract",
                    part="video",
                )
            return ""

        downloader._run_ytdlp = fake_run
        downloader._sleep_with_cancel = lambda seconds, _controller=None: delays.append(int(seconds))
        with TemporaryDirectory(prefix="cookie_fallback_success_") as temp_dir:
            cookie_path = Path(temp_dir) / "cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            options = DownloadOptions(
                ".",
                "channel",
                "Channel",
                cookies_enabled=True,
                cookies_path=str(cookie_path),
            )
            with _progress_phase("Video"):
                downloader._run_ytdlp_with_retries(
                    ["yt-dlp", "https://www.youtube.com/watch?v=cookie403"],
                    options,
                    logs.append,
                )
    finally:
        downloader._run_ytdlp = old_run_ytdlp
        downloader._sleep_with_cancel = old_sleep

    _assert(len(calls) == 2, f"cookie fallback call count was wrong: {len(calls)}")
    _assert(downloader._command_uses_cookies(calls[0]), "first attempt did not use cookies")
    _assert(not downloader._command_uses_cookies(calls[1]), "fallback attempt still used cookies")
    _assert(delays == [], f"cookie fallback waited before cookieless retry: {delays}")
    joined = "\n".join(logs)
    _assert("[COOKIE FALLBACK]" in joined, "cookie fallback diagnostic missing")
    _assert("Retrying immediately without cookies" in joined, "cookieless retry message missing")
    _assert("[COOKIE FALLBACK SUCCESS]" in joined, "cookie fallback success diagnostic missing")
    _assert("attempt=1 cookies=enabled" in joined, "first attempt cookie state was not logged")
    _assert("attempt=2 cookies=disabled" in joined, "fallback attempt cookie state was not logged")
    _assert("Retrying in 10 seconds" not in joined, "HTTP 403 delay ran before cookieless fallback")


def _test_cookie_media_403_fallback_then_standard_retries() -> None:
    logs: list[str] = []
    calls: list[list[str]] = []
    delays: list[int] = []
    old_run_ytdlp = downloader._run_ytdlp
    old_sleep = downloader._sleep_with_cancel
    try:
        def always_media_403(command, _cancel_controller=None):
            calls.append(list(command))
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

        downloader._run_ytdlp = always_media_403
        downloader._sleep_with_cancel = lambda seconds, _controller=None: delays.append(int(seconds))
        with TemporaryDirectory(prefix="cookie_fallback_failure_") as temp_dir:
            cookie_path = Path(temp_dir) / "cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            options = DownloadOptions(
                ".",
                "channel",
                "Channel",
                cookies_enabled=True,
                cookies_path=str(cookie_path),
            )
            with _progress_phase("Video"):
                try:
                    downloader._run_ytdlp_with_retries(
                        ["yt-dlp", "https://www.youtube.com/watch?v=cookie403fail"],
                        options,
                        logs.append,
                    )
                except downloader.DownloadCancelled:
                    pass
                else:
                    raise AssertionError("persistent cookieless HTTP 403 did not stop after retries")
    finally:
        downloader._run_ytdlp = old_run_ytdlp
        downloader._sleep_with_cancel = old_sleep

    _assert(len(calls) == 4, f"persistent fallback call count was wrong: {len(calls)}")
    _assert(downloader._command_uses_cookies(calls[0]), "initial persistent attempt did not use cookies")
    _assert(
        all(not downloader._command_uses_cookies(command) for command in calls[1:]),
        "one or more post-fallback retries re-enabled cookies",
    )
    _assert(delays == [10, 30], f"post-fallback HTTP 403 delays were wrong: {delays}")
    joined = "\n".join(logs)
    _assert(joined.count("[COOKIE FALLBACK]") == 1, "cookieless fallback ran more than once")
    _assert("[COOKIE FALLBACK SUCCESS]" not in joined, "failed fallback was logged as successful")


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
