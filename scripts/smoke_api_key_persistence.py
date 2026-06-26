import json
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[0]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import app_settings
from core.youtube_api import read_api_keys
from ui import main_window


SECRET = "secret-api-key-1234567890"
PROTECTED_SECRET = "protected-api-key-0987654321"


def main() -> int:
    _test_fresh_default()
    _test_secure_save_load_and_plaintext_guard()
    _test_protect_failures_preserve_original()
    _test_atomic_write_failure_preserves_original()
    _test_obsolete_remember_field_is_ignored_and_removed()
    _test_legacy_plaintext_migrates_even_with_old_opt_out()
    _test_protected_payload_wins_over_legacy_plaintext()
    _test_invalid_legacy_cleanup_and_protected_fallback()
    _test_storage_unavailable_is_fail_closed()
    _test_protected_payload_failures_preserve_ciphertext()
    _test_unrelated_settings_writes_preserve_protected_payload()
    _test_window_startup_loads_key_without_remember_ui()
    _test_accepted_fetch_always_persists_nonempty_manual_key()
    _test_blank_or_stale_fetch_does_not_change_saved_key()
    _test_save_failure_warns_without_secret_leakage()
    _test_startup_logs_only_actionable_failures()
    _test_request_context_and_ui_have_no_remember_state()
    _test_api_key_file_still_supplies_fallback_keys()
    print("api key persistence smoke passed")
    return 0


def _test_fresh_default() -> None:
    with _settings_env() as env:
        state = app_settings.load_api_key_persistence_state()
        _assert(state.api_key == "", "fresh state loaded an API key")
        _assert(state.storage_available is True, "fake secure storage was unavailable")
        _assert(state.status == "not_remembered", f"unexpected fresh status: {state.status}")
        _assert(env.read_json() == {}, "fresh load wrote settings")


def _test_secure_save_load_and_plaintext_guard() -> None:
    with _settings_env() as env:
        env.write_json(
            {
                "remember_api_key": False,
                "last_api_key": "legacy-secret-that-must-not-survive",
                "cookie_source": app_settings.COOKIE_SOURCE_BRIDGE,
                "unknown": {"keep": True},
            }
        )
        _assert(app_settings.save_last_api_key(f"  {SECRET}  "), "secure save failed")
        data = env.read_json()
        _assert("remember_api_key" not in data, "secure save retained obsolete remember field")
        _assert("last_api_key" not in data, "secure save retained plaintext key")
        _assert(data["cookie_source"] == app_settings.COOKIE_SOURCE_BRIDGE, "secure save dropped cookie setting")
        _assert(data["unknown"] == {"keep": True}, "secure save dropped unknown field")
        payload = data.get("last_api_key_protected")
        _assert(isinstance(payload, dict), "secure save did not write protected payload")
        _assert(payload.get("provider") == app_settings.API_KEY_PROTECTION_PROVIDER, "wrong payload provider")
        _assert(payload.get("version") == app_settings.API_KEY_PROTECTION_VERSION, "wrong payload version")
        _assert(SECRET not in env.settings_text(), "settings contain raw secret")

        state = app_settings.load_api_key_persistence_state()
        _assert(state.api_key == SECRET, "saved key did not reload")
        _assert(state.status == "ok", f"unexpected protected-load status: {state.status}")


def _test_protect_failures_preserve_original() -> None:
    invalid_outputs = [
        None,
        "not-bytes",
        bytearray(b"bytes"),
        b"",
        b"x" * (app_settings.API_KEY_MAX_CIPHERTEXT_BYTES + 1),
    ]
    for protected_output in invalid_outputs:
        with _settings_env(protect=lambda _value, output=protected_output: output) as env:
            original = {"cookie_source": app_settings.COOKIE_SOURCE_FILE, "unknown": "keep"}
            env.write_json(original)
            _assert(not app_settings.save_last_api_key(SECRET), f"invalid protect output accepted: {type(protected_output)}")
            _assert(env.read_json() == original, "protect failure changed settings")
            _assert(SECRET not in env.settings_text(), "protect failure wrote raw secret")

    def fail_protect(_value: bytes) -> bytes:
        raise OSError("protect failed")

    with _settings_env(protect=fail_protect) as env:
        original = {"unknown": "keep"}
        env.write_json(original)
        _assert(not app_settings.save_last_api_key(SECRET), "protect exception reported success")
        _assert(env.read_json() == original, "protect exception changed settings")


def _test_atomic_write_failure_preserves_original() -> None:
    with _settings_env() as env:
        original = {"unknown": "keep", "cookie_source": app_settings.COOKIE_SOURCE_BRIDGE}
        env.write_json(original)

        def fail_dump(*_args, **_kwargs):
            raise TypeError("synthetic write failure")

        with _patched_attr(app_settings.json, "dump", fail_dump):
            _assert(not app_settings.save_last_api_key(SECRET), "failed atomic write reported success")
        _assert(env.read_json() == original, "failed atomic write changed original settings")
        _assert(list(env.root.glob(".app_settings_*.tmp")) == [], "failed atomic write left temp file")


def _test_obsolete_remember_field_is_ignored_and_removed() -> None:
    payload = _protected_payload_for_key(PROTECTED_SECRET)
    remember_values = [True, False, None, "true", 1, [], {}]
    for value in remember_values:
        with _settings_env() as env:
            env.write_json(
                {
                    "remember_api_key": value,
                    "last_api_key_protected": payload,
                    "unknown": "keep",
                }
            )
            state = app_settings.load_api_key_persistence_state()
            _assert(state.api_key == PROTECTED_SECRET, f"obsolete remember value blocked protected key: {value!r}")
            data = env.read_json()
            _assert("remember_api_key" not in data, f"obsolete remember field was not removed: {value!r}")
            _assert(data["last_api_key_protected"] == payload, "obsolete field cleanup changed protected payload")
            _assert(data["unknown"] == "keep", "obsolete field cleanup dropped unknown field")

    with _settings_env() as env:
        env.write_json({"remember_api_key": False, "unknown": "keep"})
        state = app_settings.load_api_key_persistence_state()
        _assert(state.api_key == "", "obsolete opt-out without payload loaded a key")
        _assert(state.status == "not_remembered", f"unexpected no-payload status: {state.status}")
        _assert(env.read_json() == {"unknown": "keep"}, "obsolete opt-out field was not removed")


def _test_legacy_plaintext_migrates_even_with_old_opt_out() -> None:
    for old_preference in (True, False, None, "false"):
        with _settings_env() as env:
            env.write_json(
                {
                    "remember_api_key": old_preference,
                    "last_api_key": SECRET,
                    "unknown": "keep",
                }
            )
            state = app_settings.load_api_key_persistence_state()
            _assert(state.api_key == SECRET, f"legacy key did not migrate for old preference {old_preference!r}")
            _assert(state.status == "legacy_migrated", f"unexpected migration status: {state.status}")
            data = env.read_json()
            _assert("remember_api_key" not in data, "migration retained obsolete remember field")
            _assert("last_api_key" not in data, "migration retained plaintext")
            _assert("last_api_key_protected" in data, "migration did not create protected payload")
            _assert(data["unknown"] == "keep", "migration dropped unknown field")
            _assert(SECRET not in env.settings_text(), "migration wrote raw secret")


def _test_protected_payload_wins_over_legacy_plaintext() -> None:
    payload = _protected_payload_for_key(PROTECTED_SECRET)
    with _settings_env() as env:
        env.write_json(
            {
                "remember_api_key": False,
                "last_api_key": SECRET,
                "last_api_key_protected": payload,
                "unknown": "keep",
            }
        )
        state = app_settings.load_api_key_persistence_state()
        _assert(state.api_key == PROTECTED_SECRET, "legacy plaintext overrode protected payload")
        _assert(state.status == "legacy_plaintext_removed", f"unexpected precedence status: {state.status}")
        data = env.read_json()
        _assert("last_api_key" not in data, "protected precedence retained plaintext")
        _assert("remember_api_key" not in data, "protected precedence retained obsolete field")
        _assert(data["last_api_key_protected"] == payload, "protected precedence changed payload")


def _test_invalid_legacy_cleanup_and_protected_fallback() -> None:
    invalid_values = [None, "", "   ", 123, [], {}, "x" * (app_settings.API_KEY_MAX_BYTES + 1)]
    payload = _protected_payload_for_key(PROTECTED_SECRET)
    for legacy_value in invalid_values:
        with _settings_env() as env:
            env.write_json(
                {
                    "remember_api_key": False,
                    "last_api_key": legacy_value,
                    "last_api_key_protected": payload,
                    "unknown": "keep",
                }
            )
            state = app_settings.load_api_key_persistence_state()
            _assert(state.api_key == PROTECTED_SECRET, f"invalid legacy value blocked protected payload: {type(legacy_value)}")
            data = env.read_json()
            _assert("last_api_key" not in data, "invalid legacy plaintext was not removed")
            _assert("remember_api_key" not in data, "invalid legacy cleanup retained obsolete field")
            _assert(data["last_api_key_protected"] == payload, "invalid legacy cleanup changed payload")


def _test_storage_unavailable_is_fail_closed() -> None:
    payload = _protected_payload_for_key(PROTECTED_SECRET)
    with _settings_env(available=False) as env:
        env.write_json(
            {
                "remember_api_key": False,
                "last_api_key": SECRET,
                "last_api_key_protected": payload,
                "unknown": "keep",
            }
        )
        state = app_settings.load_api_key_persistence_state()
        _assert(state.api_key == "", "storage-unavailable startup loaded a key")
        _assert(state.status == "secure_storage_unavailable", f"unexpected unavailable status: {state.status}")
        data = env.read_json()
        _assert("last_api_key" not in data, "storage-unavailable cleanup retained plaintext")
        _assert("remember_api_key" not in data, "storage-unavailable cleanup retained obsolete field")
        _assert(data["last_api_key_protected"] == payload, "storage-unavailable cleanup removed ciphertext")

    with _settings_env(available=False) as env:
        _assert(not app_settings.save_last_api_key(SECRET), "storage-unavailable save reported success")
        _assert(env.read_json() == {}, "storage-unavailable save wrote settings")


def _test_protected_payload_failures_preserve_ciphertext() -> None:
    invalid_payloads = [None, "bad", {}, {"provider": "other", "version": 1, "ciphertext": "QQ=="}]
    for payload in invalid_payloads:
        with _settings_env() as env:
            env.write_json({"last_api_key_protected": payload, "unknown": "keep"})
            state = app_settings.load_api_key_persistence_state()
            _assert(state.api_key == "", "invalid protected payload loaded a key")
            _assert(state.status == "unsupported_payload", f"unexpected invalid payload status: {state.status}")
            _assert(env.read_json()["last_api_key_protected"] == payload, "invalid payload was modified")

    payload = _protected_payload_for_key(PROTECTED_SECRET)

    def fail_unprotect(_value: bytes) -> bytes:
        raise OSError("decrypt failed")

    with _settings_env(unprotect=fail_unprotect) as env:
        env.write_json({"last_api_key_protected": payload})
        state = app_settings.load_api_key_persistence_state()
        _assert(state.api_key == "", "decrypt failure loaded a key")
        _assert(state.status == "decrypt_failed", f"unexpected decrypt status: {state.status}")
        _assert(env.read_json()["last_api_key_protected"] == payload, "decrypt failure changed payload")


def _test_unrelated_settings_writes_preserve_protected_payload() -> None:
    payload = _protected_payload_for_key(PROTECTED_SECRET)
    for old_preference in (True, False, None, "false"):
        with _settings_env() as env:
            env.write_json(
                {
                    "remember_api_key": old_preference,
                    "last_api_key": "legacy-secret",
                    "last_api_key_protected": payload,
                    "unknown": "keep",
                }
            )
            _assert(app_settings.save_bridge_cookie_path(""), "unrelated settings write failed")
            data = env.read_json()
            _assert(data["last_api_key_protected"] == payload, "unrelated write removed protected payload")
            _assert("last_api_key" not in data, "unrelated write retained plaintext")
            _assert("remember_api_key" not in data, "unrelated write retained obsolete remember field")
            _assert(data["unknown"] == "keep", "unrelated write dropped unknown field")


def _test_window_startup_loads_key_without_remember_ui() -> None:
    state = app_settings.ApiKeyPersistenceState(PROTECTED_SECRET, True, "ok")
    startup = _instantiate_window_for_api_key_state(state)
    _assert(startup.loader_calls == 1, "window startup called API-key loader more than once")
    _assert(startup.window.api_key_var.get() == PROTECTED_SECRET, "window did not restore protected key")
    _assert(not hasattr(startup.window, "remember_api_key_var"), "window still created remember variable")


def _test_accepted_fetch_always_persists_nonempty_manual_key() -> None:
    window = _window_harness(manual_key=SECRET)
    token = _fetch_token()
    with _patched_attr(main_window, "save_last_api_key", _collector(True)) as saved:
        window._persist_accepted_fetch_manual_key(token)
    _assert(saved.calls == [SECRET], "accepted Fetch did not save the manual key exactly once")
    _assert(window._active_fetch_manual_key == "", "accepted Fetch did not clear pending key")
    _assert(window._active_fetch_manual_key_request_id is None, "accepted Fetch did not clear pending key id")
    _assert(window.logs == [], "successful API-key persistence emitted process log")


def _test_blank_or_stale_fetch_does_not_change_saved_key() -> None:
    window = _window_harness(manual_key="   ")
    with _patched_attr(main_window, "save_last_api_key", _collector(True)) as saved:
        window._persist_accepted_fetch_manual_key(_fetch_token())
    _assert(saved.calls == [], "blank manual key triggered a settings write")
    _assert(window.logs == [], "blank manual key emitted a log")

    window = _window_harness(manual_key=SECRET)
    stale = main_window._FetchRequestToken(1, 2, "channel", _context())
    with _patched_attr(main_window, "save_last_api_key", _collector(True)) as saved:
        window._persist_accepted_fetch_manual_key(stale)
    _assert(saved.calls == [], "stale terminal persisted API key")
    _assert(window._active_fetch_manual_key == SECRET, "stale terminal cleared pending key")


def _test_save_failure_warns_without_secret_leakage() -> None:
    window = _window_harness(manual_key=SECRET)
    with _patched_attr(main_window, "save_last_api_key", _collector(False)) as saved:
        window._persist_accepted_fetch_manual_key(_fetch_token())
    _assert(saved.calls == [SECRET], "failed persistence did not attempt save")
    _assert(len(window.logs) == 1 and "WARNING" in window.logs[0], "failed persistence did not warn once")
    _assert(SECRET not in repr(window.logs), "failed persistence leaked raw key")


def _test_startup_logs_only_actionable_failures() -> None:
    quiet_statuses = ["", "ok", "not_remembered", "legacy_migrated", "legacy_plaintext_removed", "invalid_legacy_plaintext_removed"]
    for status in quiet_statuses:
        window = _startup_log_harness(status, storage_available=True)
        main_window.YouTubeDownloaderWindow._log_api_key_persistence_startup_status(window)
        _assert(window.logs == [], f"normal startup status emitted process log: {status}")

    for status in ("decrypt_failed", "unsupported_payload", "settings_write_failed"):
        window = _startup_log_harness(status, storage_available=True)
        main_window.YouTubeDownloaderWindow._log_api_key_persistence_startup_status(window)
        _assert(len(window.logs) == 1 and "WARNING" in window.logs[0], f"failure status did not warn once: {status}")

    window = _startup_log_harness("secure_storage_unavailable", storage_available=False)
    main_window.YouTubeDownloaderWindow._log_api_key_persistence_startup_status(window)
    _assert(len(window.logs) == 1, "storage unavailable warning was duplicated")


def _test_request_context_and_ui_have_no_remember_state() -> None:
    context_fields = set(main_window._ChannelRequestContext.__dataclass_fields__)
    _assert("remember_api_key" not in context_fields, "request context still carries remember preference")
    _assert(not hasattr(main_window.YouTubeDownloaderWindow, "_on_remember_api_key_changed"), "remember checkbox handler still exists")

    source = Path(main_window.__file__).read_text(encoding="utf-8")
    forbidden = (
        "Ghi nhớ API key bằng bảo vệ Windows",
        "remember_api_key_var",
        "remember_api_key_check",
        "save_remember_api_key",
        "Đã tắt ghi nhớ API key",
        "API key sẽ được ghi nhớ",
    )
    for text in forbidden:
        _assert(text not in source, f"obsolete remember UI/logic remains: {text}")


def _test_api_key_file_still_supplies_fallback_keys() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        api_key_file = Path(temp_dir) / "api key.txt"
        api_key_file.write_text("file-key\nmanual-ui-key\n", encoding="utf-8")
        keys = read_api_keys("manual-ui-key", api_key_file=api_key_file)
        _assert(keys == ["manual-ui-key", "file-key"], "API-key file fallback order changed")
        _assert(api_key_file.read_text(encoding="utf-8") == "file-key\nmanual-ui-key\n", "API-key file was modified")


def _window_harness(*, manual_key: str):
    window = main_window.YouTubeDownloaderWindow.__new__(main_window.YouTubeDownloaderWindow)
    window._active_fetch_manual_key = manual_key
    window._active_fetch_manual_key_request_id = 1
    window.logs = []
    window._append_log = lambda message: window.logs.append(message)
    window._clear_pending_fetch_manual_key = main_window.YouTubeDownloaderWindow._clear_pending_fetch_manual_key.__get__(
        window,
        type(window),
    )
    window._persist_accepted_fetch_manual_key = (
        main_window.YouTubeDownloaderWindow._persist_accepted_fetch_manual_key.__get__(window, type(window))
    )
    return window


def _context():
    return main_window._ChannelRequestContext(
        save_folder=".",
        download_mode=main_window.MODE_VIDEO_THUMB,
        hide_below_enabled=True,
        hide_below_minutes=3,
        hide_above_enabled=True,
        hide_above_minutes=60,
    )


def _fetch_token():
    return main_window._FetchRequestToken(1, 1, "channel", _context())


def _startup_log_harness(status: str, storage_available: bool = True):
    window = main_window.YouTubeDownloaderWindow.__new__(main_window.YouTubeDownloaderWindow)
    window._api_key_persistence_status = status
    window._api_key_storage_available = storage_available
    window.logs = []
    window._append_log = lambda message: window.logs.append(message)
    return window


def _instantiate_window_for_api_key_state(state: app_settings.ApiKeyPersistenceState):
    loader_calls = 0

    def fake_loader():
        nonlocal loader_calls
        loader_calls += 1
        return state

    with ExitStack() as stack:
        stack.enter_context(_patched_attr(main_window, "load_api_key_persistence_state", fake_loader))
        stack.enter_context(_patched_attr(main_window, "load_cookie_source", lambda: app_settings.COOKIE_SOURCE_FILE))
        stack.enter_context(_patched_attr(main_window, "load_cookies_path", lambda: ""))
        stack.enter_context(_patched_attr(main_window, "load_bridge_cookie_path", lambda: "bridge-cookie-path"))
        stack.enter_context(_patched_attr(main_window.tk, "StringVar", _TkVar))
        stack.enter_context(_patched_attr(main_window.tk, "BooleanVar", _TkVar))
        for method_name in (
            "_apply_window_icon",
            "_build_ui",
            "_log_api_key_persistence_startup_status",
            "_update_channel_input_display",
            "_update_cookies_state",
            "_refresh_cookie_status",
            "_update_download_button_text",
            "_update_more_button_state",
            "_update_stop_button_state",
            "_refresh_interaction_control_states",
            "_poll_cookie_status",
            "_process_events",
            "_poll_progress_queue",
        ):
            stack.enter_context(_patched_attr(main_window.YouTubeDownloaderWindow, method_name, lambda self: None))
        root = _FakeRoot()
        window = main_window.YouTubeDownloaderWindow(root)
    return SimpleNamespace(window=window, loader_calls=loader_calls, root=root)


class _settings_env:
    def __init__(self, *, available: bool = True, protect=None, unprotect=None) -> None:
        self.available = available
        self.protect = protect or _fake_protect
        self.unprotect = unprotect or _fake_unprotect

    def __enter__(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_data_dir = app_settings.data_dir
        self.original_available = app_settings._secure_api_key_storage_available
        self.original_protect = app_settings._protect_api_key_bytes
        self.original_unprotect = app_settings._unprotect_api_key_bytes
        app_settings.data_dir = lambda: self.root
        app_settings._secure_api_key_storage_available = lambda: self.available
        app_settings._protect_api_key_bytes = self.protect
        app_settings._unprotect_api_key_bytes = self.unprotect
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        app_settings.data_dir = self.original_data_dir
        app_settings._secure_api_key_storage_available = self.original_available
        app_settings._protect_api_key_bytes = self.original_protect
        app_settings._unprotect_api_key_bytes = self.original_unprotect
        self.temp_dir.cleanup()

    def settings_path(self) -> Path:
        return self.root / "app_settings.json"

    def write_json(self, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings_path().write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def read_json(self) -> dict:
        if not self.settings_path().exists():
            return {}
        return json.loads(self.settings_path().read_text(encoding="utf-8"))

    def settings_text(self) -> str:
        if not self.settings_path().exists():
            return ""
        return self.settings_path().read_text(encoding="utf-8")


def _protected_payload_for_key(api_key: str) -> dict:
    return app_settings._protected_payload_from_bytes(_fake_protect(api_key.encode("utf-8")))


def _fake_protect(value: bytes) -> bytes:
    return bytes(byte ^ 0xA5 for byte in value) + b".fake"


def _fake_unprotect(value: bytes) -> bytes:
    if not value.endswith(b".fake"):
        raise OSError("bad fake payload")
    return bytes(byte ^ 0xA5 for byte in value[:-5])


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

    def __call__(self, *args, **kwargs):
        self.calls.append(args[0] if len(args) == 1 and not kwargs else args)
        return self.result


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _TkVar(_Var):
    def __init__(self, value=None, **_kwargs):
        super().__init__(value)
        self.traces = []

    def trace_add(self, mode, callback) -> None:
        self.traces.append((mode, callback))


class _FakeRoot:
    def title(self, *_args, **_kwargs):
        return None

    def geometry(self, *_args, **_kwargs):
        return None

    def minsize(self, *_args, **_kwargs):
        return None

    def protocol(self, *_args, **_kwargs):
        return None

    def after(self, *_args, **_kwargs):
        return None


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
