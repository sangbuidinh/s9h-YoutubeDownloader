from pathlib import Path
import os
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import app_settings, downloader
from core.downloader import (
    BRIDGE_COOKIE_FILE_MISSING_MESSAGE,
    BRIDGE_COOKIE_SESSION_ERROR_MESSAGE,
    COOKIE_SOURCE_BRIDGE,
    COOKIE_SOURCE_FILE,
    DownloadError,
    DownloadOptions,
    FILE_COOKIE_SESSION_ERROR_MESSAGE,
    YtdlpExecutionError,
    _base_ytdlp_command,
    _log_friendly_ytdlp_error,
    effective_cookies_path,
    is_cookie_session_error,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_raises(message: str, callback) -> None:
    try:
        callback()
    except DownloadError as exc:
        _assert(str(exc) == message, f"unexpected error message: {exc}")
        return
    raise AssertionError("DownloadError was not raised")


def _options(**kwargs) -> DownloadOptions:
    defaults = {
        "base_folder": ".",
        "channel_id": "channel",
        "channel_name": "Channel",
    }
    defaults.update(kwargs)
    return DownloadOptions(**defaults)


def _test_effective_cookies_path(root: Path) -> None:
    manual = root / "cookies.txt"
    bridge = root / "youtube_cookies.txt"
    manual.write_text("# placeholder\n", encoding="utf-8")
    bridge.write_text("# placeholder\n", encoding="utf-8")

    _assert(effective_cookies_path(_options(cookies_enabled=False, cookies_path=str(manual))) == "", "disabled cookies used a path")
    _assert(
        effective_cookies_path(
            _options(cookies_enabled=True, cookie_source=COOKIE_SOURCE_FILE, cookies_path=str(manual))
        )
        == str(manual),
        "manual cookie path was not selected",
    )
    _assert(
        effective_cookies_path(
            _options(cookies_enabled=True, cookie_source="invalid", cookies_path=str(manual))
        )
        == str(manual),
        "invalid source did not fall back to file mode",
    )
    _assert(
        effective_cookies_path(
            _options(
                cookies_enabled=True,
                cookie_source=COOKIE_SOURCE_BRIDGE,
                cookies_path="",
                bridge_cookie_path=str(bridge),
            )
        )
        == str(bridge),
        "bridge cookie path was not selected",
    )
    _assert_raises(
        "Cookies file missing",
        lambda: effective_cookies_path(
            _options(cookies_enabled=True, cookie_source=COOKIE_SOURCE_FILE, cookies_path=str(root / "missing.txt"))
        ),
    )
    _assert_raises(
        BRIDGE_COOKIE_FILE_MISSING_MESSAGE,
        lambda: effective_cookies_path(
            _options(
                cookies_enabled=True,
                cookie_source=COOKIE_SOURCE_BRIDGE,
                bridge_cookie_path=str(root / "missing_bridge.txt"),
            )
        ),
    )

    command = _base_ytdlp_command(
        _options(cookies_enabled=True, cookie_source=COOKIE_SOURCE_BRIDGE, bridge_cookie_path=str(bridge))
    )
    cookie_index = command.index("--cookies")
    _assert(command[cookie_index + 1] == str(bridge), "bridge path was not passed to yt-dlp")


def _test_cookie_session_guidance() -> None:
    bridge_logs = []
    _log_friendly_ytdlp_error(
        bridge_logs.append,
        YtdlpExecutionError(
            1,
            "bot check",
            ["Sign in to confirm you're not a bot"],
            combined_output="Sign in to confirm you're not a bot",
        ),
        _options(cookies_enabled=True, cookie_source=COOKIE_SOURCE_BRIDGE),
    )
    _assert(
        any(BRIDGE_COOKIE_SESSION_ERROR_MESSAGE in message for message in bridge_logs),
        "bridge cookie-session guidance was not logged",
    )

    file_logs = []
    _log_friendly_ytdlp_error(
        file_logs.append,
        YtdlpExecutionError(
            1,
            "cookie required",
            ["Use --cookies"],
            combined_output="Use --cookies to provide cookies",
        ),
        _options(cookies_enabled=True, cookie_source=COOKIE_SOURCE_FILE),
    )
    _assert(
        any(FILE_COOKIE_SESSION_ERROR_MESSAGE in message for message in file_logs),
        "manual cookie-session guidance was not logged",
    )

    unrelated_logs = []
    _log_friendly_ytdlp_error(
        unrelated_logs.append,
        YtdlpExecutionError(
            1,
            "network error",
            ["Temporary network error"],
            combined_output="Temporary network error",
        ),
        _options(cookies_enabled=True, cookie_source=COOKIE_SOURCE_BRIDGE),
    )
    _assert(
        not any(BRIDGE_COOKIE_SESSION_ERROR_MESSAGE in message for message in unrelated_logs),
        "bridge guidance was logged for an unrelated error",
    )
    _assert(
        not any(FILE_COOKIE_SESSION_ERROR_MESSAGE in message for message in unrelated_logs),
        "manual guidance was logged for an unrelated error",
    )


def _test_cookie_session_classifier() -> None:
    positive_cases = (
        "Sign in to continue",
        "Sign in to confirm you're not a bot",
        "Confirm you're not a bot",
        "This helps protect our community",
        "Use --cookies to provide browser cookies",
        "Use --cookies-from-browser chrome",
        "Login required",
        "Please log in",
        "Authentication required",
        "Account authentication failed",
        "cookies expired",
        "cookie invalid",
        "cookies are no longer valid",
        "HTTP Error 403: Forbidden - use --cookies",
        "HTTP Error 429: Too Many Requests - sign in to confirm",
    )
    for message in positive_cases:
        _assert(is_cookie_session_error(message), f"cookie-session classifier missed: {message}")

    negative_cases = (
        "login failed",
        "authentication failed",
        "HTTP Error 403",
        "forbidden",
        "HTTP Error 429",
        "429: too many requests",
        "cookies",
        "cookies missing",
        "fresh cookies",
        "Temporary network error",
    )
    for message in negative_cases:
        _assert(not is_cookie_session_error(message), f"cookie-session classifier was too broad: {message}")


def _test_settings(root: Path) -> None:
    original_data_dir = app_settings.data_dir
    app_settings.data_dir = lambda: root
    try:
        _assert(app_settings.load_cookie_source() == COOKIE_SOURCE_FILE, "default cookie source was not file")
        _assert(
            app_settings.load_bridge_cookie_path() == app_settings.DEFAULT_BRIDGE_COOKIE_PATH,
            "default bridge path was wrong",
        )
        _assert(app_settings.save_cookie_source(COOKIE_SOURCE_BRIDGE), "could not save bridge source")
        _assert(app_settings.load_cookie_source() == COOKIE_SOURCE_BRIDGE, "bridge source did not persist")
        custom_path = str(root / "youtube_cookies.txt")
        _assert(app_settings.save_bridge_cookie_path(custom_path), "could not save bridge path")
        _assert(app_settings.load_bridge_cookie_path() == custom_path, "bridge path did not persist")
        _assert(app_settings.save_cookie_source("label text"), "could not save invalid source")
        _assert(app_settings.load_cookie_source() == COOKIE_SOURCE_FILE, "invalid source was not normalized")
    finally:
        app_settings.data_dir = original_data_dir


def _test_bridge_refresh_retry_requires_updated_file(root: Path) -> None:
    bridge = root / "youtube_cookies.txt"
    bridge.write_text("# placeholder\n", encoding="utf-8")
    old_mtime_ns = time.time_ns() - 10_000_000_000
    os.utime(bridge, ns=(old_mtime_ns, old_mtime_ns))
    options = _options(
        cookies_enabled=True,
        cookie_source=COOKIE_SOURCE_BRIDGE,
        bridge_cookie_path=str(bridge),
    )
    command = ["yt-dlp", "--cookies", str(bridge), "https://example.test/video"]

    old_run = downloader._run_ytdlp
    old_sleep = downloader._sleep_with_cancel
    old_timeout = downloader.COOKIE_BRIDGE_REFRESH_TIMEOUT_SECONDS
    old_poll = downloader.COOKIE_BRIDGE_REFRESH_POLL_SECONDS
    try:
        calls = []
        logs = []

        def unchanged_cookie_error(run_command, _cancel_controller=None):
            calls.append(run_command)
            raise YtdlpExecutionError(
                1,
                "bot check",
                ["Sign in to confirm you're not a bot"],
                bot_check=True,
                combined_output="Sign in to confirm you're not a bot",
            )

        downloader._run_ytdlp = unchanged_cookie_error
        downloader.COOKIE_BRIDGE_REFRESH_TIMEOUT_SECONDS = 0
        try:
            downloader._run_ytdlp_with_retries(command, options, logs.append)
        except YtdlpExecutionError:
            pass
        else:
            raise AssertionError("unchanged cookie file retry was swallowed")
        _assert(len(calls) == 1, f"unchanged cookie file retried blindly: {len(calls)}")
        _assert(
            any("Cookie Bridge file was not updated before timeout" in message for message in logs),
            "unchanged cookie file did not log timeout skip",
        )

        calls.clear()
        logs.clear()
        refreshed = {"done": False}

        def refresh_cookie_once(_seconds, _cancel_controller):
            if refreshed["done"]:
                return
            refreshed["done"] = True
            previous_mtime_ns = bridge.stat().st_mtime_ns
            bridge.write_text("# refreshed placeholder\n", encoding="utf-8")
            current_mtime_ns = bridge.stat().st_mtime_ns
            fresh_mtime_ns = max(
                time.time_ns() + 1_000_000_000,
                current_mtime_ns + 1_000_000_000,
                previous_mtime_ns + 1_000_000_000,
            )
            os.utime(bridge, ns=(fresh_mtime_ns, fresh_mtime_ns))

        def fail_once_then_pass(run_command, _cancel_controller=None):
            calls.append(run_command)
            if len(calls) == 1:
                raise YtdlpExecutionError(
                    1,
                    "bot check",
                    ["Sign in to confirm you're not a bot"],
                    bot_check=True,
                    combined_output="Sign in to confirm you're not a bot",
                )
            return ""

        downloader._run_ytdlp = fail_once_then_pass
        downloader._sleep_with_cancel = refresh_cookie_once
        downloader.COOKIE_BRIDGE_REFRESH_TIMEOUT_SECONDS = 1
        downloader.COOKIE_BRIDGE_REFRESH_POLL_SECONDS = 0.01
        downloader._run_ytdlp_with_retries(command, options, logs.append)
        _assert(len(calls) == 2, f"fresh cookie retry count was wrong: {len(calls)}")
        _assert(
            any("Cookie Bridge file updated. Retrying once with refreshed cookies." in message for message in logs),
            "fresh cookie retry was not logged",
        )

        calls.clear()
        logs.clear()
        refreshed["done"] = False

        def always_cookie_error(run_command, _cancel_controller=None):
            calls.append(run_command)
            raise YtdlpExecutionError(
                1,
                "bot check",
                ["Sign in to confirm you're not a bot"],
                bot_check=True,
                combined_output="Sign in to confirm you're not a bot",
            )

        downloader._run_ytdlp = always_cookie_error
        try:
            downloader._run_ytdlp_with_retries(command, options, logs.append)
        except YtdlpExecutionError:
            pass
        else:
            raise AssertionError("second cookie failure was swallowed")
        _assert(len(calls) == 2, f"retry loop was not capped at one fresh retry: {len(calls)}")
        _assert(
            any("Cookie file was refreshed, but YouTube still rejected" in message for message in logs),
            "refreshed-cookie rejection guidance was not logged",
        )

        calls.clear()
        logs.clear()
        refreshed["done"] = False
        retry_state = downloader._CookieRefreshRetryState()
        downloader._run_ytdlp = fail_once_then_pass
        downloader._run_ytdlp_with_retries(command, options, logs.append, cookie_retry_state=retry_state)
        downloader._run_ytdlp = always_cookie_error
        try:
            downloader._run_ytdlp_with_retries(command, options, logs.append, cookie_retry_state=retry_state)
        except YtdlpExecutionError:
            pass
        else:
            raise AssertionError("per-item second cookie retry was swallowed")
        _assert(len(calls) == 3, f"per-item retry state allowed an extra retry: {len(calls)}")
    finally:
        downloader._run_ytdlp = old_run
        downloader._sleep_with_cancel = old_sleep
        downloader.COOKIE_BRIDGE_REFRESH_TIMEOUT_SECONDS = old_timeout
        downloader.COOKIE_BRIDGE_REFRESH_POLL_SECONDS = old_poll


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cookie_bridge_smoke_") as temp_dir:
        root = Path(temp_dir)
        _test_effective_cookies_path(root)
        _test_cookie_session_classifier()
        _test_cookie_session_guidance()
        _test_settings(root)
        _test_bridge_refresh_retry_requires_updated_file(root)
    print("cookie bridge options smoke passed")


if __name__ == "__main__":
    main()
