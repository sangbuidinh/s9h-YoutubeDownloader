import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[0]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ui import main_window
from smoke_channel_request_generation import (
    _activate_fetch,
    _activate_loaded_channel,
    _activate_load_more,
    _assert,
    _channel,
    _context,
    _fake_window,
    _patched_apply_statuses,
    _patched_save_last_api_key,
    _patched_thread,
    _video,
    _window_with_active_load_more,
)


def main() -> int:
    _test_context_is_frozen()
    _test_duration_text_validator()
    _test_duration_fetch_validation()
    _test_load_more_uses_current_live_duration_filter()
    _test_duration_entry_state_behavior()
    _test_fetch_worker_uses_snapshot_context_and_main_thread_key_persistence()
    _test_load_more_worker_uses_snapshot_duration_filters()
    _test_controls_lock_and_restore_for_fetch_and_load_more()
    _test_stale_terminal_events_do_not_unlock_or_persist()
    _test_thread_start_failures_restore_state()
    _test_download_blocked_during_channel_requests()
    print("channel request context smoke passed")
    return 0


def _test_context_is_frozen() -> None:
    context = _context(save_folder="snapshot")
    try:
        context.save_folder = "changed"
    except FrozenInstanceError:
        return
    raise AssertionError("channel request context is not immutable")


def _test_duration_text_validator() -> None:
    window = _fake_window()
    valid_values = ["", "0", "3", "60", "9999", "0001"]
    extra_invalid_values = [
        "00001",
        "10000",
        "9" * 5000,
        "0" * 5000,
        "1e3",
        "\uff16\uff10",
        "\u0663",
        "\u00b2",
    ]
    invalid_values = [" ", "-1", "+1", "3.5", "1,000", "abc", "3a", "@", "６０", "٣", "²"]
    for value in valid_values:
        _assert(
            main_window.YouTubeDownloaderWindow._validate_duration_filter_text(window, value),
            f"duration validator rejected {value!r}",
        )
    for value in invalid_values + extra_invalid_values:
        _assert(
            not main_window.YouTubeDownloaderWindow._validate_duration_filter_text(window, value),
            f"duration validator accepted {value!r}",
        )


def _test_duration_fetch_validation() -> None:
    window = _fake_window()
    window.hide_below_minutes_var.set("")
    window.hide_above_minutes_var.set("")
    _assert(main_window.YouTubeDownloaderWindow._validate_duration_filter_inputs(window), "empty fields were not normalized")
    _assert(window.hide_below_minutes_var.get() == "3", "empty lower field did not reset to 3")
    _assert(window.hide_above_minutes_var.get() == "60", "empty upper field did not reset to 60")

    window.hide_below_minutes_var.set("1")
    window.hide_above_minutes_var.set("9999")
    _assert(main_window.YouTubeDownloaderWindow._validate_duration_filter_inputs(window), "valid range was rejected")

    window.hide_below_minutes_var.set("0001")
    window.hide_above_minutes_var.set("0060")
    _assert(main_window.YouTubeDownloaderWindow._validate_duration_filter_inputs(window), "leading zero values were rejected")
    context = main_window.YouTubeDownloaderWindow._capture_channel_request_context(window)
    _assert(context.hide_below_minutes == 1, "0001 did not capture as integer 1")
    _assert(context.hide_above_minutes == 60, "0060 did not capture as integer 60")

    window.hide_below_enabled_var.set(False)
    window.hide_below_minutes_var.set("500")
    window.hide_above_enabled_var.set(True)
    window.hide_above_minutes_var.set("1")
    _assert(
        main_window.YouTubeDownloaderWindow._validate_duration_filter_inputs(window),
        "disabled lower threshold incorrectly blocked validation",
    )

    window.hide_below_enabled_var.set(False)
    window.hide_above_enabled_var.set(False)
    window.hide_below_minutes_var.set("10000")
    window.hide_above_minutes_var.set("0")
    _assert(
        main_window.YouTubeDownloaderWindow._validate_duration_filter_inputs(window),
        "both disabled filters still enforced inactive thresholds",
    )

    cases = [
        ("active zero", True, "0", True, "60"),
        ("active leading zero value zero", True, "0000", True, "60"),
        ("active lower five digits", True, "00001", True, "60"),
        ("active lower oversized", True, "9" * 5000, True, "60"),
        ("active lower oversized zero", True, "0" * 5000, True, "60"),
        ("active lower malformed", True, "3.5", True, "60"),
        ("active lower non-ascii", True, "\uff16\uff10", True, "60"),
        ("active above limit", True, "3", True, "10000"),
        ("active upper five digits", True, "3", True, "00001"),
        ("active upper oversized", True, "3", True, "9" * 5000),
        ("equal active thresholds", True, "60", True, "60"),
        ("lower greater than upper", True, "60", True, "3"),
    ]
    for _name, below_enabled, below_value, above_enabled, above_value in cases:
        window = _fake_window()
        old_context = _context(hide_below_minutes=3, hide_above_minutes=60)
        window._channel_generation = 7
        window._loaded_channel_generation = 7
        window._loaded_channel_context = old_context
        window.hide_below_enabled_var.set(below_enabled)
        window.hide_below_minutes_var.set(below_value)
        window.hide_above_enabled_var.set(above_enabled)
        window.hide_above_minutes_var.set(above_value)
        with _patched_thread() as threads, _patched_fetch_latest_video_page() as fetch_latest:
            window.start_fetch()
        _assert(threads.created == [], "invalid duration Fetch created a worker")
        _assert(window._active_fetch_request is None, "invalid duration Fetch created a request token")
        _assert(fetch_latest.calls == [], "invalid duration Fetch reached the API")
        _assert(window._loaded_channel_context is old_context, "invalid duration Fetch replaced loaded context")
        _assert(window._channel_generation == 7, "invalid duration Fetch changed generation")
        _assert(window.dialogs, "invalid duration Fetch did not show a validation dialog")

    window = _fake_window()
    window.hide_below_enabled_var.set(True)
    window.hide_below_minutes_var.set("0")
    window.hide_above_enabled_var.set(False)
    window.hide_above_minutes_var.set("0")
    with _patched_thread() as threads:
        window.start_fetch()
    _assert(threads.created == [], "active invalid lower threshold started Fetch")

    window = _fake_window()
    window.hide_below_enabled_var.set(False)
    window.hide_below_minutes_var.set("0")
    window.hide_above_enabled_var.set(True)
    window.hide_above_minutes_var.set("1")
    with _patched_thread() as threads:
        window.start_fetch()
    _assert(len(threads.created) == 1, "inactive invalid lower threshold blocked Fetch")
    token = window._active_fetch_request
    _assert(token.context.hide_below_enabled is False, "inactive lower checkbox was not captured")
    _assert(token.context.hide_below_minutes == 3, "inactive invalid lower text entered context")
    _assert(token.context.hide_above_minutes == 1, "active upper threshold was not captured")

    window = _fake_window()
    window.hide_below_enabled_var.set(True)
    window.hide_below_minutes_var.set("1")
    window.hide_above_enabled_var.set(False)
    window.hide_above_minutes_var.set("9" * 5000)
    with _patched_thread() as threads:
        window.start_fetch()
    _assert(len(threads.created) == 1, "inactive oversized upper threshold blocked Fetch")
    token = window._active_fetch_request
    _assert(token.context.hide_above_enabled is False, "inactive upper checkbox was not captured")
    _assert(token.context.hide_above_minutes == 60, "inactive invalid upper text entered context")


def _test_load_more_uses_current_live_duration_filter() -> None:
    window = _fake_window()
    _activate_loaded_channel(window, "A")
    window.hide_below_minutes_var.set("10")
    window.hide_above_minutes_var.set("30")
    main_window.YouTubeDownloaderWindow._on_duration_filter_text_changed(window)

    with _patched_thread() as threads:
        window.start_load_more()

    _assert(len(threads.created) == 1, "valid live duration filter did not start Load More")
    token = window._active_load_more_request
    _assert(token.context.hide_below_minutes == 10, "Load More did not capture current live lower threshold")
    _assert(token.context.hide_above_minutes == 30, "Load More did not capture current live upper threshold")
    _assert(token.context is window._loaded_channel_context, "Load More did not use the active loaded context")

    window = _fake_window()
    _activate_loaded_channel(window, "A")
    old_context = window._loaded_channel_context
    window.hide_below_minutes_var.set("70")
    window.hide_above_minutes_var.set("60")
    main_window.YouTubeDownloaderWindow._on_duration_filter_text_changed(window)
    with _patched_thread() as threads:
        window.start_load_more()
    _assert(threads.created == [], "invalid live duration range started Load More")
    _assert(window._active_load_more_request is None, "invalid Load More created a token")
    _assert(window._loaded_channel_context is old_context, "invalid live duration range replaced loaded context")
    _assert(window.dialogs, "invalid live duration range did not show a dialog")


def _test_duration_entry_state_behavior() -> None:
    window = _fake_window()
    loaded_context = _context()
    window._loaded_channel_context = loaded_context
    _assert(window.hide_below_enabled_var.get() is True, "lower checkbox default was not enabled")
    _assert(window.hide_above_enabled_var.get() is True, "upper checkbox default was not enabled")
    _assert(window.hide_below_minutes_var.get() == "3", "lower default was not 3")
    _assert(window.hide_above_minutes_var.get() == "60", "upper default was not 60")

    window.hide_below_minutes_var.set("10")
    window.hide_above_minutes_var.set("30")
    main_window.YouTubeDownloaderWindow._on_duration_filter_text_changed(window)
    _assert(window.filter_count == 1, "duration entry edit did not reclassify loaded list")
    _assert(window._loaded_channel_context is not loaded_context, "duration entry edit did not refresh loaded context")
    _assert(window._loaded_channel_context.hide_below_minutes == 10, "live lower value was not applied")
    _assert(window._loaded_channel_context.hide_above_minutes == 30, "live upper value was not applied")

    before_invalid = window._loaded_channel_context
    window.hide_below_minutes_var.set("40")
    window.hide_above_minutes_var.set("30")
    main_window.YouTubeDownloaderWindow._on_duration_filter_text_changed(window)
    _assert(window.filter_count == 1, "invalid live range reclassified loaded list")
    _assert(window._loaded_channel_context is before_invalid, "invalid live range replaced loaded context")

    window.hide_below_minutes_var.set("15")
    window.hide_below_enabled_var.set(False)
    main_window.YouTubeDownloaderWindow._on_duration_filter_changed(window)
    _assert(window.hide_below_entry.states[-1] == "disabled", "unchecked lower did not disable lower entry")
    _assert(window.hide_above_entry.states[-1] == "normal", "unchecked lower disabled upper entry")
    _assert(window.hide_below_minutes_var.get() == "15", "unchecked lower cleared the value")
    _assert(window.filter_count == 2, "duration checkbox edit did not reclassify loaded list")
    _assert(window._loaded_channel_context.hide_below_enabled is False, "unchecked lower was not applied live")
    _assert(window._loaded_channel_context.hide_above_enabled is True, "upper checkbox changed unexpectedly")

    window.hide_below_enabled_var.set(True)
    window.hide_above_minutes_var.set("90")
    main_window.YouTubeDownloaderWindow._on_duration_filter_changed(window)
    _assert(window.hide_below_entry.states[-1] == "normal", "rechecked lower did not restore editable state")
    _assert(window.hide_below_minutes_var.get() == "15", "rechecked lower did not preserve value")
    _assert(window._loaded_channel_context.hide_below_enabled is True, "rechecked lower was not applied live")
    _assert(window._loaded_channel_context.hide_below_minutes == 15, "rechecked lower value was not preserved")

    window.hide_above_enabled_var.set(False)
    main_window.YouTubeDownloaderWindow._on_duration_filter_changed(window)
    _assert(window.hide_above_entry.states[-1] == "disabled", "unchecked upper did not disable upper entry")
    _assert(window.hide_above_minutes_var.get() == "90", "unchecked upper cleared the value")
    _assert(window._loaded_channel_context.hide_above_enabled is False, "unchecked upper was not applied live")

    filter_count = window.filter_count
    context_before_busy_edit = window._loaded_channel_context
    window.fetching = True
    window.hide_below_minutes_var.set("25")
    main_window.YouTubeDownloaderWindow._on_duration_filter_text_changed(window)
    _assert(window.filter_count == filter_count, "busy duration edit reclassified loaded list")
    _assert(window._loaded_channel_context is context_before_busy_edit, "busy duration edit changed loaded context")
    main_window.YouTubeDownloaderWindow._refresh_interaction_control_states(window)
    _assert(window.hide_below_check.states[-1] == "disabled", "lower checkbox was not locked during Fetch")
    _assert(window.hide_above_check.states[-1] == "disabled", "upper checkbox was not locked during Fetch")
    _assert(window.hide_below_entry.states[-1] == "disabled", "lower entry was not locked during Fetch")
    _assert(window.hide_above_entry.states[-1] == "disabled", "upper entry was not locked during Fetch")

    window.fetching = False
    main_window.YouTubeDownloaderWindow._refresh_interaction_control_states(window)
    _assert(window.hide_below_check.states[-1] == "normal", "lower checkbox did not restore")
    _assert(window.hide_above_check.states[-1] == "normal", "upper checkbox did not restore")
    _assert(window.hide_below_entry.states[-1] == "normal", "enabled lower entry did not restore")
    _assert(window.hide_above_entry.states[-1] == "disabled", "unchecked upper entry did not stay disabled")

def _test_fetch_worker_uses_snapshot_context_and_main_thread_key_persistence() -> None:
    api_key = "abcdefghijklmnopqrstuvwxyz123456"
    window = _fake_window()
    window.api_key_var.set(api_key)
    window.save_folder_var.set("snapshot-folder")
    window.download_mode_var.set(main_window.DOWNLOAD_MODES[1])
    window.hide_below_enabled_var.set(True)
    window.hide_below_minutes_var.set("3")
    window.hide_above_enabled_var.set(True)
    window.hide_above_minutes_var.set("60")

    with (
        _patched_thread() as threads,
        _patched_fetch_latest_video_page() as fetch_latest,
        _patched_apply_statuses() as statuses,
        _patched_save_last_api_key() as saved,
    ):
        window.start_fetch()
        _assert(len(threads.created) == 1, "Fetch did not create one worker")
        thread = threads.created[0]
        token = window._active_fetch_request
        _assert(token.context.save_folder == "snapshot-folder", "Fetch token did not snapshot save folder")
        _assert(token.context.download_mode == main_window.DOWNLOAD_MODES[1], "Fetch token did not snapshot mode")
        _assert(token.context.hide_below_enabled is True, "Fetch token did not snapshot lower checkbox")
        _assert(token.context.hide_below_minutes == 3, "Fetch token did not snapshot lower threshold")
        _assert(token.context.hide_above_enabled is True, "Fetch token did not snapshot upper checkbox")
        _assert(token.context.hide_above_minutes == 60, "Fetch token did not snapshot upper threshold")

        window.save_folder_var.set("live-folder")
        window.download_mode_var.set(main_window.DOWNLOAD_MODES[2])
        window.hide_below_minutes_var.set("10")
        window.hide_above_minutes_var.set("30")

        thread.target(*thread.args)
        events = _drain_events(window)
        _assert(saved.calls == [], "worker persisted API key before accepted terminal event")
        _assert(fetch_latest.calls[0].min_visible_duration_seconds == 180, "Fetch worker used live lower threshold")
        _assert(fetch_latest.calls[0].max_visible_duration_seconds == 3600, "Fetch worker used live upper threshold")
        _assert(fetch_latest.calls[0].hide_below_duration_enabled is True, "Fetch worker used live lower checkbox")
        _assert(fetch_latest.calls[0].hide_above_duration_enabled is True, "Fetch worker used live upper checkbox")
        _assert(statuses.calls[0].save_folder == "snapshot-folder", "Fetch worker used live save folder")
        _assert(
            statuses.calls[0].kwargs["download_mode"] == main_window.DOWNLOAD_MODES[1],
            "Fetch worker used live download mode",
        )
        _assert(api_key not in repr(events), "raw API key leaked into worker events")

        for event in events:
            window._handle_event(event)

        _assert(saved.calls == [api_key], "accepted Fetch terminal did not persist API key exactly once")
        _assert(window._active_fetch_manual_key == "", "accepted Fetch did not clear pending key")
        _assert(window._active_fetch_manual_key_request_id is None, "accepted Fetch did not clear pending key id")
        _assert(api_key not in repr(window.logs), "raw API key leaked into logs")


def _test_load_more_worker_uses_snapshot_duration_filters() -> None:
    window = _fake_window()
    context = _context(
        hide_below_enabled=False,
        hide_below_minutes=5,
        hide_above_enabled=True,
        hide_above_minutes=90,
    )
    token = main_window._LoadMoreRequestToken(
        generation=1,
        request_id=1,
        channel_id="A-id",
        uploads_playlist_id="A-uploads",
        page_token="page-A",
        start_order=3,
        context=context,
    )
    window.hide_below_enabled_var.set(True)
    window.hide_below_minutes_var.set("1")
    window.hide_above_enabled_var.set(False)
    window.hide_above_minutes_var.set("30")

    with _patched_fetch_more_videos() as fetch_more:
        window._load_more_worker(token, "")

    _assert(fetch_more.calls[0].hide_below_duration_enabled is False, "Load More worker used live lower checkbox")
    _assert(fetch_more.calls[0].min_visible_duration_seconds == 300, "Load More worker used live lower threshold")
    _assert(fetch_more.calls[0].hide_above_duration_enabled is True, "Load More worker used live upper checkbox")
    _assert(fetch_more.calls[0].max_visible_duration_seconds == 5400, "Load More worker used live upper threshold")


def _test_controls_lock_and_restore_for_fetch_and_load_more() -> None:
    window = _fake_window()
    with _patched_thread():
        window.start_fetch()
        _assert_semantic_controls(window, "disabled", "disabled")
        _assert(window.more_button.states[-1] == "disabled", "More button enabled during Fetch")
        fetch_token = window._active_fetch_request
        window._handle_event(("fetch_done", fetch_token, _channel("B"), [_video("b-1", 1)], "b-next"))
        _assert_semantic_controls(window, "normal", "readonly")
        _assert(window.more_button.states[-1] == "normal", "More button did not restore after Fetch")

        window.start_load_more()
        _assert_semantic_controls(window, "disabled", "disabled")
        _assert(window.more_button.states[-1] == "disabled", "More button enabled during Load More")
        load_token = window._active_load_more_request
        window._handle_event(("load_more_error", load_token, "load error"))
        _assert_semantic_controls(window, "normal", "readonly")


def _test_stale_terminal_events_do_not_unlock_or_persist() -> None:
    window = _fake_window()
    current = _activate_fetch(window, generation=2, request_id=2)
    window._active_fetch_manual_key = "manual-key"
    window._active_fetch_manual_key_request_id = current.request_id
    window._refresh_interaction_control_states()
    stale = main_window._FetchRequestToken(2, 3, "stale", _context())

    with _patched_save_last_api_key() as saved:
        window._handle_event(("fetch_done", stale, _channel("S"), [_video("s-1", 1)], "s-next"))

    _assert(window.fetching, "stale Fetch terminal cleared fetching")
    _assert(window._active_fetch_request is current, "stale Fetch terminal cleared active token")
    _assert(window._active_fetch_manual_key == "manual-key", "stale Fetch terminal cleared pending key")
    _assert(saved.calls == [], "stale Fetch terminal persisted API key")
    _assert(window.fetch_button.states[-1] == "disabled", "stale Fetch terminal unlocked controls")
    _assert(window.filter_count == 0, "stale Fetch terminal applied filter")
    _assert(window.logs == [], "stale Fetch terminal logged")

    window = _fake_window()
    old_context = _context(hide_below_minutes=3, hide_above_minutes=60)
    window._channel_generation = 4
    window._loaded_channel_generation = 4
    window._loaded_channel_context = old_context
    window.videos = [_video("old-1", 1)]
    window.next_page_token = "old-next"
    current = _activate_fetch(window, generation=5, request_id=5, context=_context(hide_below_minutes=10, hide_above_minutes=30))
    window._handle_event(("fetch_error", current, "current fetch error"))
    _assert(not window.fetching, "current Fetch error left fetching true")
    _assert(window._active_fetch_request is None, "current Fetch error left active token")
    _assert(window._channel_generation == 4, "current Fetch error did not restore loaded generation")
    _assert(window._loaded_channel_context is old_context, "current Fetch error replaced loaded context")
    _assert(window.next_page_token == "old-next", "current Fetch error cleared old page token")
    _assert(window.filter_count == 0, "current Fetch error reclassified loaded list")

    window, current = _window_with_active_load_more("A")
    window._refresh_interaction_control_states()
    stale = main_window._LoadMoreRequestToken(
        current.generation,
        current.request_id + 1,
        current.channel_id,
        current.uploads_playlist_id,
        current.page_token,
        current.start_order,
        current.context,
    )
    with _patched_apply_statuses() as statuses:
        window._handle_event(("load_more_done", stale, [_video("stale", 99)], "stale-next"))

    _assert(window.loading_more, "stale Load More terminal cleared loading_more")
    _assert(window._active_load_more_request is current, "stale Load More terminal cleared active token")
    _assert(statuses.calls == [], "stale Load More terminal applied statuses")
    _assert(window.fetch_button.states[-1] == "disabled", "stale Load More terminal unlocked controls")
    _assert(window.filter_count == 0, "stale Load More terminal applied filter")


def _test_thread_start_failures_restore_state() -> None:
    window = _fake_window()
    old_context = _context(hide_below_minutes=3, hide_above_minutes=60)
    old_videos = [_video("old-1", 1)]
    window._channel_generation = 4
    window._loaded_channel_generation = 4
    window._loaded_channel_context = old_context
    window.videos = list(old_videos)
    window.next_page_token = "old-next"
    window.api_key_var.set("manual-key")
    with _patched_thread_start_failure():
        window.start_fetch()

    _assert(not window.fetching, "Fetch start failure left fetching true")
    _assert(window._active_fetch_request is None, "Fetch start failure left active token")
    _assert(window._channel_generation == 4, "Fetch start failure did not restore loaded generation")
    _assert(window._loaded_channel_context is old_context, "Fetch start failure replaced loaded context")
    _assert(window.videos == old_videos, "Fetch start failure cleared loaded videos")
    _assert(window.next_page_token == "old-next", "Fetch start failure cleared old page token")
    _assert(window._active_fetch_manual_key == "", "Fetch start failure left pending API key")
    _assert(window._active_fetch_manual_key_request_id is None, "Fetch start failure left pending API key id")
    _assert_semantic_controls(window, "normal", "readonly")
    _assert(window.logs and window.dialogs, "Fetch start failure did not surface error")

    window = _fake_window()
    _activate_loaded_channel(window, "A")
    with _patched_thread_start_failure():
        window.start_load_more()

    _assert(not window.loading_more, "Load More start failure left loading_more true")
    _assert(window._active_load_more_request is None, "Load More start failure left active token")
    _assert_semantic_controls(window, "normal", "readonly")
    _assert(window.logs and window.dialogs, "Load More start failure did not surface error")


def _test_download_blocked_during_channel_requests() -> None:
    window = _fake_window()
    window.fetching = True
    window.start_download()
    _assert(len(window.logs) == 1, "Download blocked during Fetch did not log once")
    _assert(getattr(window, "download_worker", None) is None, "Download started during Fetch")

    window = _fake_window()
    window.loading_more = True
    window.start_download()
    _assert(len(window.logs) == 1, "Download blocked during Load More did not log once")
    _assert(getattr(window, "download_worker", None) is None, "Download started during Load More")

    window = _fake_window()
    window.downloading = True
    window.start_download()
    _assert(window.logs == [], "Download blocked during active Download should not use channel-request log")
    _assert(getattr(window, "download_worker", None) is None, "Download started during active Download")

    window = _fake_window()
    window.shutdown_in_progress = True
    window.start_download()
    _assert(window.logs == [], "Download blocked during shutdown should not use channel-request log")
    _assert(getattr(window, "download_worker", None) is None, "Download started during shutdown")


def _assert_semantic_controls(window, normal_state: str, combo_state: str) -> None:
    for attr_name in (
        "api_key_entry",
        "channel_entry",
        "fetch_button",
        "choose_folder_button",
        "hide_below_check",
        "hide_above_check",
        "download_button",
    ):
        widget = getattr(window, attr_name)
        _assert(widget.states[-1] == normal_state, f"{attr_name} state was {widget.states[-1]}")
    _assert(window.mode_box.states[-1] == combo_state, f"mode_box state was {window.mode_box.states[-1]}")
    expected_entry_state = "disabled" if normal_state == "disabled" else "normal"
    _assert(window.hide_below_entry.states[-1] == expected_entry_state, "lower duration entry state changed")
    _assert(window.hide_above_entry.states[-1] == expected_entry_state, "upper duration entry state changed")


def _drain_events(window):
    events = []
    while not window.events.empty():
        events.append(window.events.get_nowait())
    return events


class _patched_fetch_latest_video_page:
    def __enter__(self):
        self.original = main_window.fetch_latest_video_page
        self.calls = []

        def fake_fetch(
            channel_input,
            manual_key,
            progress=None,
            hide_below_duration_enabled=True,
            min_visible_duration_seconds=None,
            hide_above_duration_enabled=True,
            max_visible_duration_seconds=None,
        ):
            self.calls.append(
                SimpleNamespace(
                    channel_input=channel_input,
                    manual_key=manual_key,
                    hide_below_duration_enabled=hide_below_duration_enabled,
                    min_visible_duration_seconds=min_visible_duration_seconds,
                    hide_above_duration_enabled=hide_above_duration_enabled,
                    max_visible_duration_seconds=max_visible_duration_seconds,
                )
            )
            if progress:
                progress("[INFO] api progress")
            return _channel("B"), [_video("b-1", 1)], "b-next"

        main_window.fetch_latest_video_page = fake_fetch
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        main_window.fetch_latest_video_page = self.original


class _patched_fetch_more_videos:
    def __enter__(self):
        self.original = main_window.fetch_more_videos
        self.calls = []

        def fake_fetch_more(
            uploads_playlist_id,
            page_token,
            start_order,
            manual_key,
            progress=None,
            hide_below_duration_enabled=True,
            min_visible_duration_seconds=None,
            hide_above_duration_enabled=True,
            max_visible_duration_seconds=None,
        ):
            self.calls.append(
                SimpleNamespace(
                    uploads_playlist_id=uploads_playlist_id,
                    page_token=page_token,
                    start_order=start_order,
                    manual_key=manual_key,
                    hide_below_duration_enabled=hide_below_duration_enabled,
                    min_visible_duration_seconds=min_visible_duration_seconds,
                    hide_above_duration_enabled=hide_above_duration_enabled,
                    max_visible_duration_seconds=max_visible_duration_seconds,
                )
            )
            if progress:
                progress("[INFO] api progress")
            return [_video("a-3", 3)], ""

        main_window.fetch_more_videos = fake_fetch_more
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        main_window.fetch_more_videos = self.original


class _patched_thread_start_failure:
    def __enter__(self):
        self.original = main_window.threading.Thread

        def factory(*args, **kwargs):
            return _FailingThread(*args, **kwargs)

        main_window.threading.Thread = factory
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        main_window.threading.Thread = self.original


class _FailingThread:
    def __init__(self, target=None, args=(), daemon=None):
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self) -> None:
        raise RuntimeError("thread start failed")


if __name__ == "__main__":
    raise SystemExit(main())
