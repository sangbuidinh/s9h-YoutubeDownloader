import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

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
    MODE_VIDEO_THUMB,
    YtdlpExecutionError,
    _base_ytdlp_command,
    _log_friendly_ytdlp_error,
    _prepared_cookie_attempt,
    effective_cookies_path,
    is_cookie_session_error,
)
from ui import main_window


COOKIE_CONTENT = "# Netscape cookie placeholder\n.youtube.com\tTRUE\t/\tFALSE\t0\tSID\tSECRET_COOKIE\n"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_download_error(message: str, callback) -> None:
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


def _settings_path(root: Path) -> Path:
    return root / "app_settings.json"


def _write_settings(root: Path, data: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _settings_path(root).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_settings(root: Path) -> dict:
    return json.loads(_settings_path(root).read_text(encoding="utf-8"))


def _settings_text(root: Path) -> str:
    return _settings_path(root).read_text(encoding="utf-8") if _settings_path(root).exists() else ""


def _unexpected_save(_settings: dict) -> bool:
    raise AssertionError("loading settings attempted to save settings")


def _protected_payload() -> dict:
    return {
        "provider": "windows_dpapi_current_user",
        "version": 1,
        "ciphertext": "TEST_CIPHERTEXT",
    }


def _test_effective_cookies_path(root: Path) -> None:
    manual = root / "cookies.txt"
    bridge = root / "youtube_cookies.txt"
    manual.write_text(COOKIE_CONTENT, encoding="utf-8")
    bridge.write_text(COOKIE_CONTENT, encoding="utf-8")

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

    invalid_values = ["", str(root / "missing.txt"), "bad\x00path", 123, root, str(root)]
    for value in invalid_values:
        _assert_download_error(
            "Cookies file missing",
            lambda value=value: effective_cookies_path(
                _options(cookies_enabled=True, cookie_source=COOKIE_SOURCE_FILE, cookies_path=value)
            ),
        )
        _assert_download_error(
            BRIDGE_COOKIE_FILE_MISSING_MESSAGE,
            lambda value=value: effective_cookies_path(
                _options(cookies_enabled=True, cookie_source=COOKIE_SOURCE_BRIDGE, bridge_cookie_path=value)
            ),
        )

    command = _base_ytdlp_command(
        _options(cookies_enabled=True, cookie_source=COOKIE_SOURCE_BRIDGE, bridge_cookie_path=str(bridge))
    )
    _assert(downloader.YTDLP_COOKIES_OPTION not in command, "base yt-dlp command passed canonical cookies")

    before_text = bridge.read_text(encoding="utf-8")
    with _prepared_cookie_attempt(command, _options(cookies_enabled=True, cookie_source=COOKIE_SOURCE_BRIDGE, bridge_cookie_path=str(bridge))) as attempt:
        cookie_index = attempt.command.index(downloader.YTDLP_COOKIES_OPTION)
        temp_cookie_path = Path(attempt.command[cookie_index + 1])
        _assert(attempt.canonical_path == str(bridge), "prepared attempt lost canonical path metadata")
        _assert(temp_cookie_path != bridge, "prepared attempt used canonical cookie path")
        _assert(temp_cookie_path.exists(), "prepared attempt temp cookie was missing")
        _assert(temp_cookie_path.read_text(encoding="utf-8") == before_text, "temp cookie content mismatch")
    _assert(not temp_cookie_path.exists(), "prepared attempt temp cookie was not cleaned up")
    _assert(bridge.read_text(encoding="utf-8") == before_text, "canonical cookie file was modified")


def _test_cookie_session_guidance() -> None:
    bridge_logs = []
    _log_friendly_ytdlp_error(
        bridge_logs.append,
        YtdlpExecutionError(
            1,
            "cookie rejected",
            ["cookies are no longer valid"],
            combined_output="cookies are no longer valid",
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
            "cookie rejected",
            ["cookies are no longer valid"],
            combined_output="cookies are no longer valid",
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

    bot_logs = []
    _log_friendly_ytdlp_error(
        bot_logs.append,
        YtdlpExecutionError(
            1,
            "bot check",
            ["Sign in to confirm you're not a bot"],
            bot_check=True,
            combined_output="Sign in to confirm you're not a bot",
        ),
        _options(cookies_enabled=True, cookie_source=COOKIE_SOURCE_BRIDGE),
    )
    _assert(
        not any(BRIDGE_COOKIE_SESSION_ERROR_MESSAGE in message for message in bot_logs),
        "bot-check guidance incorrectly claimed a cookie-session rejection",
    )


def _test_cookie_session_classifier() -> None:
    positive_cases = (
        "Sign in to continue",
        "Please sign in to continue",
        "Please sign in to view this video",
        "You must be signed in to view this video",
        "Authentication is required to view this video",
        "Sign in to confirm your age",
        "Sign in to confirm your identity",
        "Sign in to confirm your account",
        "Login required",
        "Please log in",
        "Authentication required",
        "Account authentication failed",
        "cookies expired",
        "cookie invalid",
        "cookies are no longer valid",
        "The supplied browser session has expired",
        "Age-restricted video. Use --cookies to authenticate",
        "HTTP Error 403: Forbidden - cookies are no longer valid",
    )
    for message in positive_cases:
        _assert(is_cookie_session_error(message), f"cookie-session classifier missed: {message}")

    negative_cases = (
        "Sign in to confirm you're not a bot",
        "Confirm you're not a bot",
        "This helps protect our community",
        "Please verify that you're human",
        "Unusual traffic or automated requests were detected",
        "Use --cookies to provide browser cookies",
        "Use --cookies-from-browser chrome",
        "login failed",
        "authentication failed",
        "HTTP Error 403",
        "forbidden",
        "HTTP Error 429",
        "429: too many requests",
        "HTTP Error 429: Too Many Requests - sign in to confirm",
        "cookies",
        "cookies missing",
        "fresh cookies",
        "Temporary network error",
    )
    for message in negative_cases:
        _assert(not is_cookie_session_error(message), f"cookie-session classifier was too broad: {message}")


def _test_fresh_defaults(root: Path) -> None:
    with (
        _settings_env(root),
        _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", str(root / "missing_legacy.txt")),
    ):
        _assert(app_settings.load_cookie_source() == COOKIE_SOURCE_FILE, "default cookie source was not file")
        _assert(app_settings.load_cookies_path() == "", "fresh manual cookies path was not empty")
        _assert(app_settings.load_bridge_cookie_path() == "", "fresh bridge path was not empty")
        _assert(not _settings_path(root).exists(), "loading defaults created a settings file")


def _test_manual_path_persistence(root: Path) -> None:
    cookie_file = root / "Folder With Spaces" / "\u0110\u01b0\u1eddng d\u1eabn cookies.txt"
    cookie_file.parent.mkdir(parents=True)
    cookie_file.write_text(COOKIE_CONTENT, encoding="utf-8")
    payload = _protected_payload()
    with _settings_env(root):
        _write_settings(root, {"remember_api_key": True, "last_api_key_protected": payload, "future_field": "keep"})
        _assert(app_settings.save_cookies_path(f"  {cookie_file}  "), "manual cookies path save failed")
        data = _read_settings(root)
        _assert(data["cookies_path"] == str(cookie_file), "manual path was not normalized and stored")
        _assert(app_settings.load_cookies_path() == str(cookie_file), "manual path did not reload")
        _assert(data["last_api_key_protected"] == payload, "protected API payload was not preserved")
        _assert("remember_api_key" not in data, "obsolete remember field was not removed")
        _assert(data["future_field"] == "keep", "future field was not preserved")
        _assert(COOKIE_CONTENT not in _settings_text(root), "cookie contents were stored in settings")
        _assert("last_api_key" not in data, "cookie path save wrote legacy plaintext API key")


def _test_bridge_path_persistence(root: Path) -> None:
    bridge_path = root / "Bridge Folder" / "youtube_cookies.txt"
    bridge_path.parent.mkdir(parents=True)
    bridge_path.write_text(COOKIE_CONTENT, encoding="utf-8")
    with (
        _settings_env(root),
        _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", str(root / "missing_legacy.txt")),
    ):
        _write_settings(root, {"future_field": "keep"})
        _assert(app_settings.save_bridge_cookie_path(f" {bridge_path} "), "bridge path save failed")
        _assert(app_settings.load_bridge_cookie_path() == str(bridge_path), "bridge path did not reload")
        _assert(app_settings.save_bridge_cookie_path(""), "empty bridge path save failed")
        data = _read_settings(root)
        _assert("bridge_cookie_path" in data, "empty bridge path removed the explicit field")
        _assert(data["bridge_cookie_path"] == "", "empty bridge path did not store tombstone")
        _assert(data["future_field"] == "keep", "bridge path save dropped unrelated field")
        _assert(app_settings.load_bridge_cookie_path() == "", "empty bridge path restored legacy default")
        _assert(app_settings.LEGACY_BRIDGE_COOKIE_PATH not in _settings_text(root), "old D path was inserted into settings")


def _test_legacy_bridge_compatibility(root: Path) -> None:
    with _settings_env(root):
        legacy_file = root / "legacy" / "youtube_cookies.txt"
        legacy_file.parent.mkdir(exist_ok=True)
        legacy_file.write_text(COOKIE_CONTENT, encoding="utf-8")
        with (
            _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", str(legacy_file)),
            _patched_attr(app_settings, "_save_app_settings", _unexpected_save),
        ):
            before_settings = _settings_text(root)
            _assert(app_settings.load_bridge_cookie_path() == str(legacy_file), "existing legacy bridge file was not detected")
            _assert(_settings_text(root) == before_settings, "legacy bridge load mutated settings")

        with (
            _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", str(root / "missing_legacy.txt")),
            _patched_attr(app_settings, "_save_app_settings", _unexpected_save),
        ):
            before_settings = _settings_text(root)
            _assert(app_settings.load_bridge_cookie_path() == "", "missing legacy bridge path was used")
            _assert(_settings_text(root) == before_settings, "missing legacy bridge load mutated settings")

        with (
            _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", str(root)),
            _patched_attr(app_settings, "_save_app_settings", _unexpected_save),
        ):
            before_settings = _settings_text(root)
            _assert(app_settings.load_bridge_cookie_path() == "", "legacy bridge directory was used as a file")
            _assert(_settings_text(root) == before_settings, "legacy directory load mutated settings")

        original_path_class = app_settings.Path

        class RaisingPath:
            def __init__(self, _value):
                pass

            def is_file(self):
                raise ValueError("synthetic bad path")

        def fake_path(value):
            if value == "raising-legacy":
                return RaisingPath(value)
            return original_path_class(value)

        with (
            _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", "raising-legacy"),
            _patched_attr(app_settings, "Path", fake_path),
        ):
            _assert(app_settings.load_bridge_cookie_path() == "", "raising legacy inspection escaped")


def _test_present_bridge_field_blocks_legacy(root: Path) -> None:
    legacy_file = root / "present_field_legacy" / "youtube_cookies.txt"
    legacy_file.parent.mkdir(exist_ok=True)
    legacy_file.write_text(COOKIE_CONTENT, encoding="utf-8")
    custom_path = str(root / "missing custom bridge.txt")

    with _settings_env(root):
        _write_settings(root, {"bridge_cookie_path": "", "future_field": "keep"})
        before_settings = _settings_text(root)
        inspector = _collector(True)
        with (
            _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", str(legacy_file)),
            _patched_attr(app_settings, "_is_existing_regular_file", inspector),
            _patched_attr(app_settings, "_save_app_settings", _unexpected_save),
        ):
            _assert(app_settings.load_bridge_cookie_path() == "", "explicit empty bridge field used legacy fallback")
        _assert(inspector.calls == [], "legacy inspector was called for explicit empty bridge field")
        _assert(_settings_text(root) == before_settings, "explicit empty bridge load mutated settings")

        _write_settings(root, {"bridge_cookie_path": f"  {custom_path}  "})
        before_settings = _settings_text(root)
        inspector = _collector(True)
        with (
            _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", str(legacy_file)),
            _patched_attr(app_settings, "_is_existing_regular_file", inspector),
            _patched_attr(app_settings, "_save_app_settings", _unexpected_save),
        ):
            _assert(app_settings.load_bridge_cookie_path() == custom_path, "custom bridge path did not override legacy path")
        _assert(inspector.calls == [], "legacy inspector was called for custom bridge field")
        _assert(_settings_text(root) == before_settings, "custom bridge load normalized settings on disk")

        invalid_values = [
            None,
            True,
            False,
            123,
            1.5,
            [],
            {},
            "bad\x00path",
            "x" * (app_settings.COOKIE_PATH_MAX_CHARS + 1),
        ]
        for value in invalid_values:
            _write_settings(root, {"bridge_cookie_path": value, "future_field": "keep"})
            before_settings = _settings_text(root)
            inspector = _collector(True)
            with (
                _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", str(legacy_file)),
                _patched_attr(app_settings, "_is_existing_regular_file", inspector),
                _patched_attr(app_settings, "_save_app_settings", _unexpected_save),
            ):
                _assert(app_settings.load_bridge_cookie_path() == "", f"invalid bridge field used fallback: {value!r}")
            _assert(inspector.calls == [], f"legacy inspector was called for invalid bridge field: {value!r}")
            _assert(_settings_text(root) == before_settings, f"invalid bridge load mutated settings: {value!r}")


def _test_explicit_bridge_clear_survives_legacy(root: Path) -> None:
    legacy_file = root / "clear_legacy" / "youtube_cookies.txt"
    legacy_file.parent.mkdir(exist_ok=True)
    legacy_file.write_text(COOKIE_CONTENT, encoding="utf-8")

    with _settings_env(root):
        _write_settings(root, {"unknown": "keep", "cookie_source": COOKIE_SOURCE_BRIDGE})
        with _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", str(legacy_file)):
            _assert(app_settings.load_bridge_cookie_path() == str(legacy_file), "absent bridge field did not use legacy path")
            _assert(app_settings.save_bridge_cookie_path(""), "explicit bridge clear failed")

        data = _read_settings(root)
        _assert(data["bridge_cookie_path"] == "", "explicit bridge clear did not store tombstone")
        _assert(data["unknown"] == "keep", "explicit bridge clear dropped unrelated field")

        inspector = _collector(True)
        with (
            _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", str(legacy_file)),
            _patched_attr(app_settings, "_is_existing_regular_file", inspector),
            _patched_attr(app_settings, "_save_app_settings", _unexpected_save),
        ):
            for _index in range(3):
                _assert(app_settings.load_bridge_cookie_path() == "", "explicit bridge clear was not restart-stable")
        _assert(inspector.calls == [], "legacy inspector was called after explicit bridge clear")
        _assert(_read_settings(root) == data, "repeated bridge loads mutated settings")


def _test_invalid_settings_values(root: Path) -> None:
    invalid_values = [
        None,
        True,
        False,
        123,
        1.5,
        ["cookies"],
        {"path": "cookies"},
        "",
        "   ",
        "bad\x00path",
        "x" * (app_settings.COOKIE_PATH_MAX_CHARS + 1),
    ]
    with (
        _settings_env(root),
        _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", ""),
        _patched_attr(app_settings, "Path", _raising_path),
    ):
        for value in invalid_values:
            _write_settings(root, {"cookies_path": value})
            _assert(app_settings.load_cookies_path() == "", f"invalid manual path loaded: {value!r}")
            _write_settings(root, {"bridge_cookie_path": value})
            _assert(app_settings.load_bridge_cookie_path() == "", f"invalid bridge path loaded: {value!r}")


def _test_path_normalization_preserves_portable_forms(root: Path) -> None:
    values = [
        r"\\server\share\cookies.txt",
        r"\\?\C:\Cookies Folder\cookies.txt",
        r"relative folder\cookies.txt",
        r"%USERPROFILE%\cookies.txt",
        "\u0110\u01b0\u1eddng d\u1eabn unicode\\cookies.txt",
    ]
    with _settings_env(root):
        for value in values:
            _write_settings(root, {"cookies_path": f"  {value}  ", "bridge_cookie_path": f"  {value}  "})
            _assert(app_settings.load_cookies_path() == value, f"manual path form changed: {value}")
            _assert(app_settings.load_bridge_cookie_path() == value, f"bridge path form changed: {value}")


def _test_batch_save(root: Path) -> None:
    manual = r"relative manual\cookies.txt"
    bridge = r"\\server\share\youtube_cookies.txt"
    legacy_file = root / "batch_legacy" / "youtube_cookies.txt"
    legacy_file.parent.mkdir(exist_ok=True)
    legacy_file.write_text(COOKIE_CONTENT, encoding="utf-8")
    with _settings_env(root):
        _write_settings(root, {"unknown": "keep", "cookie_source": COOKIE_SOURCE_BRIDGE})
        original_save = app_settings._save_app_settings
        save_calls = 0

        def counted_save(settings):
            nonlocal save_calls
            save_calls += 1
            return original_save(settings)

        with _patched_attr(app_settings, "_save_app_settings", counted_save):
            _assert(app_settings.save_cookie_preferences("invalid", f" {manual} ", f" {bridge} "), "batch save failed")

        data = _read_settings(root)
        _assert(save_calls == 1, f"batch save write count changed: {save_calls}")
        _assert(data["cookie_source"] == COOKIE_SOURCE_FILE, "invalid source was not normalized")
        _assert(data["cookies_path"] == manual, "manual path was not stored by batch save")
        _assert(data["bridge_cookie_path"] == bridge, "bridge path was not stored by batch save")
        _assert(data["unknown"] == "keep", "batch save dropped unknown setting")

        save_calls = 0
        with _patched_attr(app_settings, "_save_app_settings", counted_save):
            _assert(app_settings.save_cookie_preferences(COOKIE_SOURCE_BRIDGE, "", ""), "empty batch save failed")
        data = _read_settings(root)
        _assert(save_calls == 1, f"empty batch save write count changed: {save_calls}")
        _assert(data["cookie_source"] == COOKIE_SOURCE_BRIDGE, "batch source was not stored")
        _assert("cookies_path" not in data, "empty manual path did not remove field")
        _assert("bridge_cookie_path" in data, "empty bridge batch save removed explicit field")
        _assert(data["bridge_cookie_path"] == "", "empty bridge batch save did not store tombstone")

        inspector = _collector(True)
        with (
            _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", str(legacy_file)),
            _patched_attr(app_settings, "_is_existing_regular_file", inspector),
        ):
            _assert(app_settings.load_bridge_cookie_path() == "", "empty bridge batch save restored legacy fallback")
        _assert(inspector.calls == [], "legacy inspector was called after empty bridge batch save")


def _test_invalid_bridge_save_values(root: Path) -> None:
    legacy_file = root / "invalid_save_legacy" / "youtube_cookies.txt"
    legacy_file.parent.mkdir(exist_ok=True)
    legacy_file.write_text(COOKIE_CONTENT, encoding="utf-8")
    with _settings_env(root):
        _write_settings(root, {"unknown": "keep"})
        _assert(app_settings.save_bridge_cookie_path("bad\x00path"), "invalid narrow bridge save failed")
        data = _read_settings(root)
        _assert(data["bridge_cookie_path"] == "", "invalid narrow bridge save did not store tombstone")
        _assert(data["unknown"] == "keep", "invalid narrow bridge save dropped unrelated field")

        inspector = _collector(True)
        with (
            _patched_attr(app_settings, "LEGACY_BRIDGE_COOKIE_PATH", str(legacy_file)),
            _patched_attr(app_settings, "_is_existing_regular_file", inspector),
        ):
            _assert(app_settings.load_bridge_cookie_path() == "", "invalid narrow bridge save restored legacy fallback")
        _assert(inspector.calls == [], "legacy inspector was called after invalid narrow bridge save")

        _assert(
            app_settings.save_cookie_preferences(COOKIE_SOURCE_BRIDGE, "manual.txt", 123),
            "invalid batch bridge save failed",
        )
        data = _read_settings(root)
        _assert(data["cookie_source"] == COOKIE_SOURCE_BRIDGE, "invalid batch bridge save lost source")
        _assert(data["cookies_path"] == "manual.txt", "invalid batch bridge save lost manual path")
        _assert(data["bridge_cookie_path"] == "", "invalid batch bridge save did not store tombstone")
        _assert(data["unknown"] == "keep", "invalid batch bridge save dropped unrelated field")


def _test_atomic_write_failure(root: Path) -> None:
    original = {"unknown": "keep", "cookie_source": COOKIE_SOURCE_FILE, "bridge_cookie_path": "existing.txt"}
    with _settings_env(root):
        _write_settings(root, original)

        def fail_dump(*_args, **_kwargs):
            raise TypeError("synthetic write failure")

        with _patched_attr(app_settings.json, "dump", fail_dump):
            _assert(
                not app_settings.save_bridge_cookie_path(""),
                "failed bridge clear reported success",
            )
        _assert(_read_settings(root) == original, "failed bridge clear changed original settings")
        _assert(list(root.glob(".app_settings_*.tmp")) == [], "failed bridge clear left temp file")


def _test_api_key_guard_regression(root: Path) -> None:
    payload = _protected_payload()
    with _settings_env(root):
        _write_settings(
            root,
            {
                "remember_api_key": True,
                "last_api_key_protected": payload,
                "last_api_key": "LEGACY_SECRET",
                "future_field": "keep",
            },
        )
        _assert(app_settings.save_bridge_cookie_path(""), "guard bridge clear failed")
        data = _read_settings(root)
        _assert(data["bridge_cookie_path"] == "", "guard bridge clear did not store tombstone")
        _assert(data["last_api_key_protected"] == payload, "protected payload was not preserved")
        _assert("remember_api_key" not in data, "obsolete remember=true field was not removed")
        _assert("last_api_key" not in data, "legacy plaintext API key was retained")
        _assert(data["future_field"] == "keep", "future field was not preserved")

        _write_settings(
            root,
            {
                "remember_api_key": False,
                "last_api_key_protected": payload,
                "last_api_key": "LEGACY_SECRET",
                "future_field": "keep",
            },
        )
        _assert(app_settings.save_cookie_preferences(COOKIE_SOURCE_FILE, "manual.txt", ""), "guard cleanup save failed")
        data = _read_settings(root)
        _assert(data["bridge_cookie_path"] == "", "guard cleanup save did not store tombstone")
        _assert(data["last_api_key_protected"] == payload, "protected payload was removed by obsolete remember=false")
        _assert("remember_api_key" not in data, "obsolete remember=false field was not removed")
        _assert("last_api_key" not in data, "legacy plaintext API key was retained")
        _assert(data["future_field"] == "keep", "future field was not preserved")


def _test_ui_status_safety(root: Path) -> None:
    window = main_window.YouTubeDownloaderWindow.__new__(main_window.YouTubeDownloaderWindow)
    valid = root / "cookies.txt"
    valid.write_text(COOKIE_CONTENT, encoding="utf-8")
    directory = root / "cookies_dir"
    directory.mkdir()

    status = main_window.YouTubeDownloaderWindow._cookie_file_metadata_status
    _assert("chưa chọn file" in status(window, "File cookie", ""), "empty path did not show unselected")
    _assert("không tìm thấy" in status(window, "File cookie", str(root / "missing.txt")), "missing path did not show missing")
    _assert("không tìm thấy" in status(window, "File cookie", "bad\x00path"), "NUL path did not show missing")
    _assert("không tìm thấy" in status(window, "File cookie", directory), "non-string path did not show missing")
    _assert("không tìm thấy" in status(window, "File cookie", str(directory)), "directory did not show missing")
    valid_status = status(window, "File cookie", f" {valid} ")
    _assert("tìm thấy" in valid_status and "byte" in valid_status, "valid file status changed")

    class BadDatetime:
        @staticmethod
        def fromtimestamp(_value):
            raise OverflowError("bad timestamp")

    with _patched_attr(main_window, "datetime", BadDatetime):
        bad_status = status(window, "File cookie", str(valid))
    _assert("không tìm thấy" in bad_status, "invalid timestamp did not show missing")
    _assert("bad timestamp" not in bad_status, "raw timestamp exception was displayed")


def _test_ui_browse_persistence(root: Path) -> None:
    manual = str(root / "manual cookies.txt")
    bridge = str(root / "youtube_cookies.txt")

    window = _browse_window()
    with (
        _patched_attr(main_window.filedialog, "askopenfilename", lambda **_kwargs: manual),
        _patched_attr(main_window, "save_cookies_path", _collector(True)) as saved,
    ):
        main_window.YouTubeDownloaderWindow.choose_cookies_file(window)
    _assert(window.cookies_path_var.get() == manual, "manual browse did not keep selected path")
    _assert(saved.calls == [manual], "manual browse did not save immediately")
    _assert(window.logs == [], "successful manual browse logged a warning")

    window = _browse_window()
    with (
        _patched_attr(main_window.filedialog, "askopenfilename", lambda **_kwargs: manual),
        _patched_attr(main_window, "save_cookies_path", _collector(False)),
    ):
        main_window.YouTubeDownloaderWindow.choose_cookies_file(window)
    _assert(window.cookies_path_var.get() == manual, "failed manual save did not keep path active")
    _assert(manual not in "\n".join(window.logs), "manual save warning exposed full path")

    window = _browse_window()
    with (
        _patched_attr(main_window.filedialog, "askopenfilename", lambda **_kwargs: bridge),
        _patched_attr(main_window, "save_bridge_cookie_path", _collector(True)) as saved,
    ):
        main_window.YouTubeDownloaderWindow.choose_bridge_cookie_file(window)
    _assert(window.bridge_cookie_path_var.get() == bridge, "bridge browse did not keep selected path")
    _assert(saved.calls == [bridge], "bridge browse did not save immediately")

    window = _browse_window()
    with (
        _patched_attr(main_window.filedialog, "askopenfilename", lambda **_kwargs: bridge),
        _patched_attr(main_window, "save_bridge_cookie_path", _collector(False)),
    ):
        main_window.YouTubeDownloaderWindow.choose_bridge_cookie_file(window)
    _assert(window.bridge_cookie_path_var.get() == bridge, "failed bridge save did not keep path active")
    _assert(bridge not in "\n".join(window.logs), "bridge save warning exposed full path")


def _test_start_download_cookie_preference_failure_does_not_block(root: Path) -> None:
    manual = root / "manual cookies.txt"
    manual.write_text(COOKIE_CONTENT, encoding="utf-8")
    window = _download_window(str(manual))

    with (
        _patched_attr(main_window, "save_cookie_preferences", _collector(False)) as saved,
        _patched_attr(main_window, "validate_download_environment", lambda _options: None),
        _patched_attr(main_window.threading, "Thread", _thread_collector(window)),
    ):
        main_window.YouTubeDownloaderWindow.start_download(window)

    _assert(saved.calls == [(COOKIE_SOURCE_FILE, str(manual), "")], "download did not batch-save cookie preferences")
    _assert(window.thread_started is True, "preference save failure blocked current download")
    _assert(str(manual) not in "\n".join(window.logs), "preference save failure warning exposed full path")


def _browse_window():
    window = main_window.YouTubeDownloaderWindow.__new__(main_window.YouTubeDownloaderWindow)
    window.downloading = False
    window.cookies_path_var = _Var("")
    window.bridge_cookie_path_var = _Var("")
    window.logs = []
    window.refresh_count = 0
    window._append_log = lambda message: window.logs.append(message)
    window._refresh_cookie_status = lambda: setattr(window, "refresh_count", window.refresh_count + 1)
    return window


def _download_window(cookies_path: str):
    video = SimpleNamespace(video_id="video-1")
    window = main_window.YouTubeDownloaderWindow.__new__(main_window.YouTubeDownloaderWindow)
    window.downloading = False
    window.shutdown_in_progress = False
    window.download_worker = None
    window.channel_info = SimpleNamespace(channel_id="channel", channel_name="Channel")
    window.videos = [video]
    window.download_stop_requested = False
    window.exit_after_download_stop = False
    window.close_requested = False
    window.cancel_download = False
    window.shutdown_started_at = None
    window.events = SimpleNamespace(put=lambda _event: None)
    window.speed_limit_var = _Var("")
    window.save_folder_var = _Var(".")
    window.cookies_enabled_var = _Var(True)
    window.cookies_path_var = _Var(cookies_path)
    window.download_mode_var = _Var(MODE_VIDEO_THUMB)
    window.bridge_cookie_path_var = _Var("")
    window.progress_current_var = _Var("")
    window.progress_detail_var = _Var("")
    window.logs = []
    window.dialogs = []
    window.thread_started = False
    window._channel_request_busy = lambda: False
    window._selected_visible_videos = lambda: [video]
    window._videos_for_snapshot_ids = lambda _ids: [video]
    window._current_cookie_source = lambda: COOKIE_SOURCE_FILE
    window._append_log = lambda message: window.logs.append(message)
    window._friendly_general_message = lambda message: message
    window._show_error_dialog = lambda message: window.dialogs.append(message)
    window._clear_progress_queue = lambda: None
    window._reset_progress_sticky = lambda: None
    window._set_download_controls_locked = lambda _locked: None
    return window


class _settings_env:
    def __init__(self, root: Path) -> None:
        self.root = root

    def __enter__(self):
        self.original_data_dir = app_settings.data_dir
        app_settings.data_dir = lambda: self.root
        if _settings_path(self.root).exists():
            _settings_path(self.root).unlink()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        app_settings.data_dir = self.original_data_dir


class _patched_attr:
    def __init__(self, target, name: str, replacement):
        self.target = target
        self.name = name
        self.replacement = replacement

    def __enter__(self):
        self.original = getattr(self.target, self.name)
        setattr(self.target, self.name, self.replacement)
        return self.replacement

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        setattr(self.target, self.name, self.original)


class _collector:
    def __init__(self, result: bool):
        self.result = result
        self.calls = []

    def __call__(self, *args, **_kwargs):
        self.calls.append(args[0] if len(args) == 1 else args)
        return self.result


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _FakeThread:
    def __init__(self, window, target, args, daemon):
        self.window = window
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        self.window.thread_started = True


class _thread_collector:
    def __init__(self, window):
        self.window = window

    def __call__(self, *, target, args, daemon):
        return _FakeThread(self.window, target, args, daemon)


def _raising_path(_value):
    raise AssertionError("settings path normalization accessed the filesystem")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cookie_bridge_smoke_") as temp_dir:
        root = Path(temp_dir)
        _test_effective_cookies_path(root)
        _test_cookie_session_classifier()
        _test_cookie_session_guidance()
        _test_fresh_defaults(root)
        _test_manual_path_persistence(root)
        _test_bridge_path_persistence(root)
        _test_legacy_bridge_compatibility(root)
        _test_present_bridge_field_blocks_legacy(root)
        _test_explicit_bridge_clear_survives_legacy(root)
        _test_invalid_settings_values(root)
        _test_path_normalization_preserves_portable_forms(root)
        _test_batch_save(root)
        _test_invalid_bridge_save_values(root)
        _test_atomic_write_failure(root)
        _test_api_key_guard_regression(root)
        _test_ui_status_safety(root)
        _test_ui_browse_persistence(root)
        _test_start_download_cookie_preference_failure_does_not_block(root)
    print("cookie bridge options smoke passed")


if __name__ == "__main__":
    main()
