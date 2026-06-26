import sys
from pathlib import Path


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
    _assert_kind("Sign in to continue", options_with_cookies, YtdlpFailureKind.COOKIE_SESSION)
    _assert_kind("Please sign in to continue", options_with_cookies, YtdlpFailureKind.COOKIE_SESSION)
    _assert_kind("Please sign in to view this video", options_with_cookies, YtdlpFailureKind.COOKIE_SESSION)
    _assert_kind("You must be signed in to view this video", options_with_cookies, YtdlpFailureKind.COOKIE_SESSION)
    _assert_kind(
        "Authentication is required to view this video",
        options_with_cookies,
        YtdlpFailureKind.COOKIE_SESSION,
    )
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


def _test_vietnamese_friendly_errors() -> None:
    expected_titles = {
        "bot_check": "YouTube yêu cầu xác minh người dùng",
        "rate_limit": "YouTube đang giới hạn lượt tải",
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
