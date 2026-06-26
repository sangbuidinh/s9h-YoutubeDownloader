import queue
import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ui import main_window


def main() -> int:
    _test_fetch_is_blocked_during_load_more()
    _test_load_more_is_blocked_during_fetch_and_shutdown()
    _test_stale_errors_do_not_affect_current_requests()
    _test_current_fetch_result()
    _test_current_load_more_result_uses_request_context()
    _test_mismatched_load_more_snapshots()
    _test_duplicate_terminal_events()
    _test_scoped_logs_and_worker_ordering()
    _test_fetch_then_load_more_sequence()
    _test_new_fetch_installs_new_duration_context()
    _test_tokens_contain_no_secrets()
    _test_more_button_busy_states()
    print("channel request generation smoke passed")
    return 0


def _test_fetch_is_blocked_during_load_more() -> None:
    window, old_token = _window_with_active_load_more("A")
    original_generation = window._channel_generation
    original_videos = list(window.videos)
    original_page_token = window.next_page_token
    original_log_count = len(window.logs)
    with _patched_thread() as threads:
        window.channel_var.set("channel-b")
        window.start_fetch()

    _assert(threads.created == [], "Fetch started while Load More was active")
    _assert(window._channel_generation == original_generation, "blocked Fetch advanced generation")
    _assert(window._active_fetch_request is None, "blocked Fetch created active request")
    _assert(window._active_load_more_request is old_token, "blocked Fetch changed active Load More")
    _assert(window.loading_more, "blocked Fetch cleared loading_more")
    _assert(window.videos == original_videos, "blocked Fetch cleared existing videos")
    _assert(window.next_page_token == original_page_token, "blocked Fetch changed page token")
    _assert(len(window.logs) == original_log_count, "blocked Fetch logged unexpectedly")


def _test_load_more_is_blocked_during_fetch_and_shutdown() -> None:
    with _patched_thread() as threads:
        window = _fake_window()
        _activate_loaded_channel(window, "A")
        window.fetching = True
        window.start_load_more()
        _assert(threads.created == [], "Load More started while Fetch was active")
        _assert(window._active_load_more_request is None, "blocked Load More created active request")

        window = _fake_window()
        _activate_loaded_channel(window, "A")
        window.shutdown_in_progress = True
        window.start_load_more()
        _assert(threads.created == [], "Load More started during shutdown")
        _assert(window._active_load_more_request is None, "shutdown Load More created active request")


def _test_stale_errors_do_not_affect_current_requests() -> None:
    window, stale_token = _window_with_active_load_more("A")
    current = _activate_load_more(window, "A", request_id=stale_token.request_id + 1)
    window.loading_more = True
    window._refresh_interaction_control_states()
    more_count = window.more_button_updates
    button_events = len(window.fetch_button.states)

    window._handle_event(("load_more_error", stale_token, "old error"))

    _assert(window.loading_more, "stale Load More error cleared newer loading state")
    _assert(window._active_load_more_request is current, "stale Load More error cleared current token")
    _assert(window.logs == [], "stale Load More error was logged")
    _assert(window.more_button_updates == more_count, "stale Load More error updated More button")
    _assert(len(window.fetch_button.states) == button_events, "stale Load More error changed controls")

    window = _fake_window()
    stale_fetch = main_window._FetchRequestToken(1, 1, "old", _context())
    current_fetch = main_window._FetchRequestToken(2, 2, "new", _context())
    window._channel_generation = 2
    window._active_fetch_request = current_fetch
    window.fetching = True
    window._refresh_interaction_control_states()
    button_events = len(window.fetch_button.states)

    window._handle_event(("fetch_error", stale_fetch, "old fetch error"))

    _assert(window.fetching, "stale Fetch error cleared current fetching state")
    _assert(window._active_fetch_request is current_fetch, "stale Fetch error cleared current token")
    _assert(window.logs == [], "stale Fetch error was logged")
    _assert(len(window.fetch_button.states) == button_events, "stale Fetch error changed controls")


def _test_current_fetch_result() -> None:
    window = _fake_window()
    token = _activate_fetch(window)
    channel = _channel("B")
    videos = [_video("b-1", 1), _video("b-2", 2)]

    window._handle_event(("fetch_done", token, channel, videos, "next-b"))

    _assert(window.channel_info is channel, "current Fetch did not replace channel_info")
    _assert(window.videos == videos, "current Fetch did not replace videos")
    _assert(window.next_page_token == "next-b", "current Fetch did not set page token")
    _assert(window._loaded_channel_generation == token.generation, "loaded generation not recorded")
    _assert(window._loaded_channel_context is token.context, "loaded context not recorded")
    _assert(window._active_fetch_request is None, "Fetch token not cleared")
    _assert(not window.fetching, "Fetch state not cleared")
    _assert(window.fetch_button.states[-1] == "normal", "Fetch button not re-enabled")
    _assert(window.mode_box.states[-1] == "readonly", "mode combobox did not restore readonly")
    _assert(window.hide_below_entry.states[-1] == "normal", "lower duration entry did not restore")
    _assert(window.hide_above_entry.states[-1] == "normal", "upper duration entry did not restore")
    _assert(window.more_button.states[-1] == "normal", "More button not enabled after page token")
    _assert(window.filter_count == 1, "Fetch did not apply filter once")


def _test_current_load_more_result_uses_request_context() -> None:
    context = _context(save_folder="snapshot-folder", download_mode=main_window.DOWNLOAD_MODES[1])
    window, token = _window_with_active_load_more("A", context=context)
    window.save_folder_var.set("live-folder")
    window.download_mode_var.set(main_window.DOWNLOAD_MODES[2])
    with _patched_apply_statuses() as statuses:
        window._handle_event(("load_more_done", token, [_video("a-3", 3)], "a-next-2"))

    _assert([video.video_id for video in window.videos] == ["a-1", "a-2", "a-3"], "Load More did not append once")
    _assert(window.next_page_token == "a-next-2", "Load More did not advance page token")
    _assert(not window.loading_more, "Load More state not cleared")
    _assert(window._active_load_more_request is None, "Load More token not cleared")
    _assert(len(statuses.calls) == 1, "Load More did not apply statuses once")
    _assert(statuses.calls[0].save_folder == "snapshot-folder", "Load More used live save folder")
    _assert(
        statuses.calls[0].kwargs["download_mode"] == main_window.DOWNLOAD_MODES[1],
        "Load More used live download mode",
    )
    _assert(window.filter_count == 1, "Load More did not apply filter once")
    _assert(window.more_button_updates == 1, "Load More did not update More button once")


def _test_mismatched_load_more_snapshots() -> None:
    cases = [
        ("generation", lambda window, _token: setattr(window, "_channel_generation", 99)),
        (
            "request_id",
            lambda window, token: setattr(
                window,
                "_active_load_more_request",
                main_window._LoadMoreRequestToken(
                    token.generation,
                    token.request_id + 1,
                    token.channel_id,
                    token.uploads_playlist_id,
                    token.page_token,
                    token.start_order,
                    token.context,
                ),
            ),
        ),
        ("channel_id", lambda window, _token: setattr(window.channel_info, "channel_id", "other-channel")),
        ("playlist_id", lambda window, _token: setattr(window.channel_info, "uploads_playlist_id", "other-playlist")),
        ("page_token", lambda window, _token: setattr(window, "next_page_token", "other-page")),
        ("start_order", lambda window, _token: window.videos.append(_video("extra", 99))),
    ]

    for name, mutate in cases:
        window, token = _window_with_active_load_more("A")
        mutate(window, token)
        with _patched_apply_statuses() as statuses:
            window._handle_event(("load_more_done", token, [_video(f"{name}-new", 3)], "new-token"))
        _assert([video.video_id for video in window.videos][:2] == ["a-1", "a-2"], f"{name} mismatch changed base videos")
        _assert("new-token" != window.next_page_token, f"{name} mismatch replaced page token")
        _assert(statuses.calls == [], f"{name} mismatch applied statuses")
        _assert(window.filter_count == 0, f"{name} mismatch applied filter")


def _test_duplicate_terminal_events() -> None:
    window, token = _window_with_active_load_more("A")
    event = ("load_more_done", token, [_video("a-3", 3)], "a-next-2")
    with _patched_apply_statuses() as statuses:
        window._handle_event(event)
        window._handle_event(event)

    _assert([video.video_id for video in window.videos] == ["a-1", "a-2", "a-3"], "duplicate result appended twice")
    _assert(len(statuses.calls) == 1, "duplicate result reconciled statuses twice")

    window, token = _window_with_active_load_more("A")
    event = ("load_more_error", token, "current error")
    window._handle_event(event)
    window._handle_event(event)
    _assert(window.logs == ["current error"], "duplicate error was displayed twice")

    window = _fake_window()
    token = _activate_fetch(window)
    window._active_fetch_manual_key = "manual-key"
    window._active_fetch_manual_key_request_id = token.request_id
    event = ("fetch_done", token, _channel("B"), [_video("b-1", 1)], "")
    with _patched_save_last_api_key() as saved:
        window._handle_event(event)
        window._handle_event(event)
    _assert(saved.calls == ["manual-key"], "duplicate Fetch terminal persisted key more than once")


def _test_scoped_logs_and_worker_ordering() -> None:
    window = _fake_window()
    fetch_token = _activate_fetch(window)
    window._handle_event(("channel_request_log", fetch_token, "current fetch log"))
    stale_fetch = main_window._FetchRequestToken(fetch_token.generation, fetch_token.request_id + 1, "stale", _context())
    window._handle_event(("channel_request_log", stale_fetch, "stale fetch log"))
    _assert(window.logs == ["current fetch log"], "stale Fetch log was displayed")

    window = _fake_window()
    _activate_fetch(window)
    stale_load_token = main_window._LoadMoreRequestToken(1, 99, "A-id", "A-uploads", "page", 1, _context())
    window._handle_event(("channel_request_log", stale_load_token, "[SUCCESS] stale load more"))
    _assert("[SUCCESS] stale load more" not in window.logs, "stale Load More success log displayed during Fetch")

    _assert_worker_terminal_is_last("fetch")
    _assert_worker_terminal_is_last("load_more")


def _test_fetch_then_load_more_sequence() -> None:
    window = _fake_window()
    with _patched_thread(), _patched_apply_statuses() as statuses:
        window.channel_var.set("channel-b")
        window.hide_below_minutes_var.set("3")
        window.hide_above_minutes_var.set("60")
        window.start_fetch()
        fetch_token = window._active_fetch_request
        window._handle_event(("fetch_done", fetch_token, _channel("B"), [_video("b-1", 1)], "b-next"))
        window.hide_below_minutes_var.set("10")
        window.hide_above_minutes_var.set("30")
        main_window.YouTubeDownloaderWindow._on_duration_filter_text_changed(window)
        window.hide_below_enabled_var.set(False)
        window.hide_above_enabled_var.set(False)
        main_window.YouTubeDownloaderWindow._on_duration_filter_changed(window)
        window.start_load_more()
        load_token = window._active_load_more_request
        _assert(load_token.context is window._loaded_channel_context, "Load More did not use the active live context")
        _assert(load_token.context is not fetch_token.context, "Load More incorrectly retained the original Fetch duration context")
        _assert(load_token.context.hide_below_enabled is False, "Load More ignored live lower checkbox")
        _assert(load_token.context.hide_below_minutes == 10, "Load More ignored live lower threshold")
        _assert(load_token.context.hide_above_enabled is False, "Load More ignored live upper checkbox")
        _assert(load_token.context.hide_above_minutes == 30, "Load More ignored live upper threshold")
        stale_token = main_window._LoadMoreRequestToken(
            load_token.generation,
            load_token.request_id + 1,
            load_token.channel_id,
            load_token.uploads_playlist_id,
            load_token.page_token,
            load_token.start_order,
            load_token.context,
        )
        window._handle_event(("load_more_done", stale_token, [_video("stale", 99)], "stale-next"))
        window._handle_event(("load_more_done", load_token, [_video("b-2", 2)], "b-next-2"))

    _assert([video.video_id for video in window.videos] == ["b-1", "b-2"], "Fetch then Load More sequence was wrong")
    _assert(window.next_page_token == "b-next-2", "current Load More page token not applied")
    _assert(len(statuses.calls) == 1, "only accepted Load More should apply statuses")
    _assert(not window.loading_more, "current Load More did not clear loading state")


def _test_new_fetch_installs_new_duration_context() -> None:
    window = _fake_window()
    with _patched_thread():
        window.hide_below_minutes_var.set("3")
        window.hide_above_minutes_var.set("60")
        window.start_fetch()
        first_token = window._active_fetch_request
        window._handle_event(("fetch_done", first_token, _channel("A"), [_video("a-1", 1)], "a-next"))
        _assert(window._loaded_channel_context is first_token.context, "first Fetch did not install loaded context")

        window.hide_below_minutes_var.set("10")
        window.hide_above_minutes_var.set("30")
        main_window.YouTubeDownloaderWindow._on_duration_filter_text_changed(window)
        _assert(window._loaded_channel_context is not first_token.context, "live duration edit did not update current list context")
        _assert(window._loaded_channel_context.hide_below_minutes == 10, "live lower threshold was not installed")
        _assert(window._loaded_channel_context.hide_above_minutes == 30, "live upper threshold was not installed")
        live_context = window._loaded_channel_context

        window.start_fetch()
        second_token = window._active_fetch_request
        _assert(second_token.context.hide_below_minutes == 10, "new Fetch did not capture live lower threshold")
        _assert(second_token.context.hide_above_minutes == 30, "new Fetch did not capture live upper threshold")
        _assert(window._loaded_channel_context is live_context, "pending new Fetch changed the active live context")

        window._handle_event(("fetch_done", second_token, _channel("B"), [_video("b-1", 1)], "b-next"))
        _assert(window._loaded_channel_context is second_token.context, "accepted new Fetch did not install new context")
        _assert(window._loaded_channel_context.hide_below_minutes == 10, "installed lower threshold was wrong")
        _assert(window._loaded_channel_context.hide_above_minutes == 30, "installed upper threshold was wrong")


def _test_tokens_contain_no_secrets() -> None:
    fetch_fields = set(main_window._FetchRequestToken.__dataclass_fields__)
    load_fields = set(main_window._LoadMoreRequestToken.__dataclass_fields__)
    context_fields = set(main_window._ChannelRequestContext.__dataclass_fields__)
    forbidden = {"api_key", "cookie_path", "cookie_value", "cookies", "videos", "channel_info", "channel"}
    _assert(fetch_fields.isdisjoint(forbidden), f"Fetch token has forbidden fields: {fetch_fields & forbidden}")
    _assert(load_fields.isdisjoint(forbidden), f"Load More token has forbidden fields: {load_fields & forbidden}")
    _assert(context_fields.isdisjoint(forbidden), f"request context has forbidden fields: {context_fields & forbidden}")


def _test_more_button_busy_states() -> None:
    window = _fake_window()
    _activate_loaded_channel(window, "A")
    main_window.YouTubeDownloaderWindow._update_more_button_state(window)
    _assert(window.more_button.states[-1] == "normal", "More button baseline should be enabled")
    window.fetching = True
    main_window.YouTubeDownloaderWindow._update_more_button_state(window)
    _assert(window.more_button.states[-1] == "disabled", "More button enabled during Fetch")
    window.fetching = False
    window.loading_more = True
    main_window.YouTubeDownloaderWindow._update_more_button_state(window)
    _assert(window.more_button.states[-1] == "disabled", "More button enabled during Load More")
    window.loading_more = False
    window.shutdown_in_progress = True
    main_window.YouTubeDownloaderWindow._update_more_button_state(window)
    _assert(window.more_button.states[-1] == "disabled", "More button enabled during shutdown")


def _assert_worker_terminal_is_last(kind: str) -> None:
    window = _fake_window()
    events = []
    window.events.put = events.append
    original_fetch_latest = main_window.fetch_latest_video_page
    original_fetch_more = main_window.fetch_more_videos
    original_apply_statuses = main_window.apply_statuses
    try:
        if kind == "fetch":
            token = main_window._FetchRequestToken(1, 1, "channel", _context())

            def fetch_latest(_channel_input, _manual_key, progress=None, **_kwargs):
                if progress:
                    progress("[INFO] api progress")
                return _channel("A"), [_video("a-1", 1)], ""

            main_window.fetch_latest_video_page = fetch_latest
            main_window.apply_statuses = lambda *_args, **_kwargs: None
            window._fetch_worker(token, "")
            terminal_kind = "fetch_done"
        else:
            token = main_window._LoadMoreRequestToken(1, 1, "A", "uploads-A", "page-A", 1, _context())

            def fetch_more(_playlist, _page, _start, _manual_key, progress=None, **_kwargs):
                if progress:
                    progress("[INFO] api progress")
                return [_video("a-1", 1)], ""

            main_window.fetch_more_videos = fetch_more
            window._load_more_worker(token, "")
            terminal_kind = "load_more_done"
    finally:
        main_window.fetch_latest_video_page = original_fetch_latest
        main_window.fetch_more_videos = original_fetch_more
        main_window.apply_statuses = original_apply_statuses

    terminal_indexes = [index for index, event in enumerate(events) if event[0] == terminal_kind]
    _assert(terminal_indexes == [len(events) - 1], f"{kind} terminal event was not queued last: {events}")
    _assert(all(event[0] == "channel_request_log" for event in events[:-1]), f"{kind} queued non-log before terminal")


def _activate_fetch(window, generation: int = 1, request_id: int = 1, context=None):
    token = main_window._FetchRequestToken(generation, request_id, "channel", context or _context())
    window._channel_generation = generation
    window._active_fetch_request = token
    window.fetching = True
    return token


def _activate_loaded_channel(window, suffix: str) -> None:
    window._channel_generation = 1
    window._loaded_channel_generation = 1
    window._loaded_channel_context = _context()
    window.channel_info = _channel(suffix)
    window.videos = [_video(f"{suffix.lower()}-1", 1), _video(f"{suffix.lower()}-2", 2)]
    window.next_page_token = f"{suffix}-page"


def _activate_load_more(window, suffix: str, request_id: int | None = None, context=None):
    request_id = request_id if request_id is not None else window._next_channel_request_id()
    token = main_window._LoadMoreRequestToken(
        window._loaded_channel_generation,
        request_id,
        f"{suffix}-id",
        f"{suffix}-uploads",
        window.next_page_token,
        len(window.videos) + 1,
        context or _context(),
    )
    window._active_load_more_request = token
    return token


def _window_with_active_load_more(suffix: str, context=None):
    window = _fake_window()
    _activate_loaded_channel(window, suffix)
    if context is not None:
        window._loaded_channel_context = context
    window.loading_more = True
    token = _activate_load_more(window, suffix, context=context)
    return window, token


def _context(
    save_folder: str = ".",
    download_mode: str = main_window.MODE_VIDEO_THUMB,
    hide_below_enabled: bool = True,
    hide_below_minutes: int = 3,
    hide_above_enabled: bool = True,
    hide_above_minutes: int = 60,
):
    return main_window._ChannelRequestContext(
        save_folder=save_folder,
        download_mode=download_mode,
        hide_below_enabled=hide_below_enabled,
        hide_below_minutes=hide_below_minutes,
        hide_above_enabled=hide_above_enabled,
        hide_above_minutes=hide_above_minutes,
    )


def _fake_window():
    window = main_window.YouTubeDownloaderWindow.__new__(main_window.YouTubeDownloaderWindow)
    window.events = queue.Queue()
    window.channel_info = None
    window.videos = []
    window.selected_orders = set()
    window.visible_orders = []
    window.next_page_token = ""
    window.fetching = False
    window.loading_more = False
    window.downloading = False
    window.shutdown_in_progress = False
    window.download_stop_requested = False
    window._channel_generation = 0
    window._channel_request_sequence = 0
    window._active_fetch_request = None
    window._active_load_more_request = None
    window._loaded_channel_generation = None
    window._loaded_channel_context = None
    window._active_fetch_manual_key = ""
    window._active_fetch_manual_key_request_id = None
    window._api_key_storage_available = True
    window.logs = []
    window.dialogs = []
    window.filter_count = 0
    window.more_button_updates = 0
    window.resolved_channels = []
    window.api_key_entry = _Widget()
    window.channel_entry = _Widget()
    window.fetch_button = _Widget()
    window.choose_folder_button = _Widget()
    window.mode_box = _Widget()
    window.hide_below_check = _Widget()
    window.hide_below_entry = _Widget()
    window.hide_above_check = _Widget()
    window.hide_above_entry = _Widget()
    window.download_button = _Widget()
    window.more_button = _Widget()
    window.select_by_date_button = _Widget()
    window.cookies_check = _Widget()
    window.speed_limit_entry = _Widget()
    window.filter_box = _Widget()
    window.stop_button = _Widget()
    window.channel_var = _Var("channel-a")
    window.api_key_var = _Var("")
    window.save_folder_var = _Var(".")
    window.download_mode_var = _Var(main_window.MODE_VIDEO_THUMB)
    window.hide_below_enabled_var = _Var(True)
    window.hide_below_minutes_var = _Var("3")
    window.hide_above_enabled_var = _Var(True)
    window.hide_above_minutes_var = _Var("60")
    window._append_log = lambda message: window.logs.append(message)
    window._show_error_dialog = lambda *args, **kwargs: window.dialogs.append((args, kwargs))
    window._friendly_general_message = lambda message: f"friendly:{message}"
    window.apply_filter = lambda: setattr(window, "filter_count", window.filter_count + 1)

    def update_more_button_state():
        window.more_button_updates += 1
        main_window.YouTubeDownloaderWindow._update_more_button_state(window)

    window._update_more_button_state = update_more_button_state
    window._update_cookies_state = lambda: None
    window._set_resolved_channel_display = lambda channel: window.resolved_channels.append(channel)
    return window


def _channel(suffix: str):
    return SimpleNamespace(
        channel_id=f"{suffix}-id",
        channel_name=f"Channel {suffix}",
        uploads_playlist_id=f"{suffix}-uploads",
    )


def _video(video_id: str, order: int):
    return SimpleNamespace(
        video_id=video_id,
        title=video_id,
        display_order=order,
        status="not downloaded",
        duration_seconds=600,
    )


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _Widget:
    def __init__(self) -> None:
        self.states = []
        self.configs = []

    def configure(self, **kwargs) -> None:
        self.configs.append(kwargs)
        if "state" in kwargs:
            self.states.append(kwargs["state"])


class _FakeThread:
    def __init__(self, target=None, args=(), daemon=None):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False

    def start(self) -> None:
        self.started = True


class _patched_thread:
    def __enter__(self):
        self.original = main_window.threading.Thread
        self.created = []

        def factory(*args, **kwargs):
            thread = _FakeThread(*args, **kwargs)
            self.created.append(thread)
            return thread

        main_window.threading.Thread = factory
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        main_window.threading.Thread = self.original


class _patched_apply_statuses:
    def __enter__(self):
        self.original = main_window.apply_statuses
        self.calls = []

        def fake_apply(videos, save_folder, channel_name, channel_id, **kwargs):
            self.calls.append(
                SimpleNamespace(
                    videos=list(videos),
                    save_folder=save_folder,
                    channel_name=channel_name,
                    channel_id=channel_id,
                    kwargs=kwargs,
                )
            )

        main_window.apply_statuses = fake_apply
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        main_window.apply_statuses = self.original


class _patched_save_last_api_key:
    def __enter__(self):
        self.original = main_window.save_last_api_key
        self.calls = []

        def fake_save(api_key):
            self.calls.append(api_key)
            return True

        main_window.save_last_api_key = fake_save
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        main_window.save_last_api_key = self.original


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
