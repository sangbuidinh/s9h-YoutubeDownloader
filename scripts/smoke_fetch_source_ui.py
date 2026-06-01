import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ui.main_window import FETCH_SOURCE_API, FETCH_SOURCE_NONE, YouTubeDownloaderWindow


def main() -> int:
    _configure_stdio()
    _test_default_fetch_source_is_api()
    _test_api_key_state_helper()
    _test_fetch_source_change_updates_entry_state()
    _test_unlock_respects_none_mode()
    _test_lock_disables_api_key_and_fetch_source_box()
    _test_fetch_source_bind_exists()
    print("fetch source UI smoke tests passed")
    return 0


def _test_default_fetch_source_is_api() -> None:
    window = _window()
    _assert(window.fetch_source_var.get() == FETCH_SOURCE_API, "default fetch source should be API")
    _assert(window._api_key_entry_state(False) == "normal", "API key entry should be enabled by default")


def _test_api_key_state_helper() -> None:
    window = _window()
    window.fetch_source_var.set(FETCH_SOURCE_NONE)
    _assert(window._api_key_entry_state(False) == "disabled", "None mode should disable API key entry")
    window.fetch_source_var.set(FETCH_SOURCE_API)
    _assert(window._api_key_entry_state(False) == "normal", "API mode should enable API key entry")
    _assert(window._api_key_entry_state(True) == "disabled", "locked state should disable API key entry")


def _test_fetch_source_change_updates_entry_state() -> None:
    window = _window()
    window.fetch_source_var.set(FETCH_SOURCE_NONE)
    window._on_fetch_source_changed()
    _assert(window.api_key_entry.state == "disabled", "switching to None did not disable API key entry")
    window.fetch_source_var.set(FETCH_SOURCE_API)
    window._on_fetch_source_changed()
    _assert(window.api_key_entry.state == "normal", "switching to API did not enable API key entry")


def _test_unlock_respects_none_mode() -> None:
    window = _window()
    window.fetch_source_var.set(FETCH_SOURCE_NONE)
    window._set_download_controls_locked(False)
    _assert(window.api_key_entry.state == "disabled", "unlock re-enabled API key entry in None mode")
    _assert(window.fetch_source_box.state == "readonly", "fetch source box should be readonly when unlocked")


def _test_lock_disables_api_key_and_fetch_source_box() -> None:
    window = _window()
    window._set_download_controls_locked(True)
    _assert(window.api_key_entry.state == "disabled", "lock did not disable API key entry")
    _assert(window.fetch_source_box.state == "disabled", "lock did not disable fetch source box")
    _assert(window.channel_entry.state == "disabled", "lock did not disable channel entry")


def _test_fetch_source_bind_exists() -> None:
    text = (REPO_ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    _assert(
        'self.fetch_source_box.bind("<<ComboboxSelected>>", lambda _event: self._on_fetch_source_changed())'
        in text,
        "fetch source combobox binding is missing",
    )


def _window():
    window = YouTubeDownloaderWindow.__new__(YouTubeDownloaderWindow)
    window.fetch_source_var = _Var(FETCH_SOURCE_API)
    window.fetching = False
    window.downloading = False
    for name in (
        "api_key_entry",
        "fetch_source_box",
        "channel_entry",
        "fetch_button",
        "select_by_date_button",
        "choose_folder_button",
        "cookies_check",
        "mode_box",
        "speed_limit_entry",
        "filter_box",
        "short_videos_check",
        "threshold_box",
        "download_button",
    ):
        setattr(window, name, _Widget())
    window._update_cookies_state = lambda: None
    window._update_more_button_state = lambda: None
    window._update_stop_button_state = lambda: None
    window._update_download_button_text = lambda: None
    return window


class _Var:
    def __init__(self, value: str):
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _Widget:
    def __init__(self):
        self.state = None

    def configure(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
