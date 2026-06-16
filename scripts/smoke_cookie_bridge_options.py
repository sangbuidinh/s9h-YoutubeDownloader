from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import app_settings
from core.downloader import (
    BRIDGE_COOKIE_FILE_MISSING_MESSAGE,
    COOKIE_SOURCE_BRIDGE,
    COOKIE_SOURCE_FILE,
    DownloadError,
    DownloadOptions,
    _base_ytdlp_command,
    effective_cookies_path,
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
            _options(cookies_enabled=True, cookie_source=COOKIE_SOURCE_BRIDGE, bridge_cookie_path=str(bridge))
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


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cookie_bridge_smoke_") as temp_dir:
        root = Path(temp_dir)
        _test_effective_cookies_path(root)
        _test_settings(root)
    print("cookie bridge options smoke passed")


if __name__ == "__main__":
    main()
