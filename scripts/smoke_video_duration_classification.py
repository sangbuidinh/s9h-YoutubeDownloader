import queue
import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import youtube_api
from ui import main_window


def main() -> int:
    _test_duration_parser_and_display()
    _test_duration_visibility_rule()
    _test_video_detail_construction()
    _test_unknown_and_live_count_as_visible_with_duration_filters()
    _test_both_filters_disabled_stop_early()
    _test_duration_filters_continue_safely()
    _test_maximum_scan_bound()
    _test_missing_details_do_not_count()
    _test_ui_local_filter()
    _test_success_log_counts()
    print("video duration classification smoke passed")
    return 0


def _test_duration_parser_and_display() -> None:
    _assert_duration_cases(
        {
            "PT30S": 30,
            "PT2M5S": 125,
            "PT1H2M3S": 3723,
            "P1D": 86400,
            "P1DT2H": 93600,
            "P1DT2H3M4S": 93784,
            "PT2H": 7200,
            "PT2M": 120,
            "PT2S": 2,
            "  PT30S  ": 30,
        }
    )
    _assert_duration_cases(
        {
            "P2H": None,
            "P3M": None,
            "P4S": None,
            "P1D2H": None,
            "P1D3M": None,
            "P1D4S": None,
        }
    )
    _assert_duration_cases(
        {
            "P": None,
            "PT": None,
            "P1DT": None,
            "P0DT": None,
        }
    )
    _assert_duration_cases(
        {
            "PT0S": None,
            "PT0M": None,
            "PT0H": None,
            "P0D": None,
            "P0DT0H0M0S": None,
        }
    )
    _assert_duration_cases(
        {
            "": None,
            "invalid": None,
            "T2H": None,
            "1H": None,
            "P-1D": None,
            "PT-2H": None,
            "P1Y": None,
            "P2W": None,
            "P1M": None,
            "PT1.5S": None,
            "PT 30S": None,
            "P1D T2H": None,
        }
    )
    for duration in (None, 0, 123, [], {}):
        _assert(
            youtube_api._parse_duration_seconds(duration) is None,
            f"non-string duration did not return None for {duration!r}",
        )
    _assert(youtube_api._duration_to_text("PT1M5S") == "1:05", "minute duration formatting changed")
    _assert(youtube_api._duration_to_text("PT1H2M3S") == "1:02:03", "hour duration formatting changed")
    _assert(youtube_api._duration_to_text("") == "Không rõ", "unknown duration label is wrong")
    _assert(youtube_api._duration_to_text("PT0S") == "Không rõ", "zero duration label is wrong")
    _assert(youtube_api._duration_to_text("", "live") == "Đang trực tiếp", "live duration label is wrong")
    _assert(youtube_api._duration_to_text("PT0S", "upcoming") == "Sắp phát", "upcoming duration label is wrong")


def _assert_duration_cases(cases) -> None:
    for duration, expected in cases.items():
        _assert(
            youtube_api._parse_duration_seconds(duration) == expected,
            f"duration parse mismatch for {duration!r}",
        )


def _test_duration_visibility_rule() -> None:
    default_cases = [
        (_video("known-1", duration_seconds=1), False),
        (_video("below-179", duration_seconds=179), False),
        (_video("lower-exact", duration_seconds=180), True),
        (_video("inside", duration_seconds=181), True),
        (_video("upper-inside", duration_seconds=3599), True),
        (_video("upper-exact", duration_seconds=3600), True),
        (_video("above-3601", duration_seconds=3601), False),
        (_video("above-7200", duration_seconds=7200), False),
        (_video("unknown", duration_seconds=None), True),
        (SimpleNamespace(video_id="missing-duration"), True),
        (SimpleNamespace(video_id="string-duration", duration_seconds="30"), True),
        (SimpleNamespace(video_id="float-duration", duration_seconds=30.5), True),
        (_video("zero", duration_seconds=0), True),
        (_video("live-zero", duration_seconds=0, live_broadcast_content="live"), True),
        (_video("upcoming-none", duration_seconds=None, live_broadcast_content="upcoming"), True),
    ]
    for video, expected in default_cases:
        _assert(
            youtube_api.is_video_visible_by_duration(video) is expected,
            f"default duration visibility mismatch for {getattr(video, 'video_id', '-')}",
        )

    lower_only = lambda video: youtube_api.is_video_visible_by_duration(
        video,
        hide_below_enabled=True,
        min_duration_seconds=180,
        hide_above_enabled=False,
        max_duration_seconds=3600,
    )
    upper_only = lambda video: youtube_api.is_video_visible_by_duration(
        video,
        hide_below_enabled=False,
        min_duration_seconds=180,
        hide_above_enabled=True,
        max_duration_seconds=3600,
    )
    disabled = lambda video: youtube_api.is_video_visible_by_duration(
        video,
        hide_below_enabled=False,
        min_duration_seconds=180,
        hide_above_enabled=False,
        max_duration_seconds=3600,
    )
    _assert(not lower_only(_video("below", duration_seconds=179)), "lower-only did not hide below threshold")
    _assert(lower_only(_video("above", duration_seconds=7200)), "lower-only hid above threshold")
    _assert(upper_only(_video("below", duration_seconds=179)), "upper-only hid below threshold")
    _assert(not upper_only(_video("above", duration_seconds=7200)), "upper-only did not hide above threshold")
    _assert(disabled(_video("below", duration_seconds=1)), "disabled filters hid below threshold")
    _assert(disabled(_video("above", duration_seconds=7200)), "disabled filters hid above threshold")
    _assert(
        youtube_api.is_video_visible_by_duration(
            _video("non-default-visible", duration_seconds=600),
            min_duration_seconds=600,
            max_duration_seconds=5400,
        ),
        "non-default lower boundary was hidden",
    )
    _assert(
        youtube_api.is_video_visible_by_duration(
            _video("non-default-upper", duration_seconds=5400),
            min_duration_seconds=600,
            max_duration_seconds=5400,
        ),
        "non-default upper boundary was hidden",
    )


def _test_video_detail_construction() -> None:
    video_ids = ["known", "unknown", "live", "upcoming", "malformed", "long"]
    items_by_id = {
        "known": _api_video_item("known", "PT1M", live_state="none"),
        "unknown": _api_video_item("unknown", None, live_state="none"),
        "live": _api_video_item("live", None, live_state="live"),
        "upcoming": _api_video_item("upcoming", "PT0S", live_state="upcoming"),
        "malformed": _api_video_item("malformed", "not-a-duration", live_state="unexpected"),
        "long": _api_video_item("long", "PT10M", live_state="none"),
    }
    returned_order = ["long", "malformed", "upcoming", "live", "unknown", "known"]
    with _patched_api_get(videos_by_id=items_by_id, returned_order=returned_order):
        videos = youtube_api._fetch_video_details(video_ids, "api-key", start_order=7)

    _assert([video.video_id for video in videos] == video_ids, "video detail order was not preserved")
    _assert([video.display_order for video in videos] == [7, 8, 9, 10, 11, 12], "display order was wrong")
    expected = {
        "known": (60, "none", "1:00"),
        "unknown": (None, "none", "Không rõ"),
        "live": (None, "live", "Đang trực tiếp"),
        "upcoming": (None, "upcoming", "Sắp phát"),
        "malformed": (None, "none", "Không rõ"),
        "long": (600, "none", "10:00"),
    }
    for video in videos:
        duration_seconds, live_state, duration_text = expected[video.video_id]
        _assert(video.duration_seconds == duration_seconds, f"{video.video_id} duration_seconds mismatch")
        _assert(video.live_broadcast_content == live_state, f"{video.video_id} live state mismatch")
        _assert(video.duration == duration_text, f"{video.video_id} duration text mismatch")


def _test_unknown_and_live_count_as_visible_with_duration_filters() -> None:
    result = _run_scanner(
        pages=[
            (["unknown", "live", "below-1", "above-1"], "page-2"),
            (["visible"], ""),
        ],
        details={
            "unknown": _video("unknown", duration_seconds=None),
            "live": _video("live", duration_seconds=None, live_broadcast_content="live"),
            "below-1": _video("below-1", duration_seconds=30),
            "above-1": _video("above-1", duration_seconds=7200),
            "visible": _video("visible", duration_seconds=600),
        },
        target_visible_count=2,
    )
    _assert(result.playlist_calls == 1, "scanner fetched another page after unknown/live reached target")
    _assert(result.next_page_token == "page-2", "next page token was not preserved after early stop")


def _test_both_filters_disabled_stop_early() -> None:
    result = _run_scanner(
        pages=[
            ([f"outside-{index}" for index in range(5)], "page-2"),
            (["visible"], ""),
        ],
        details={f"outside-{index}": _video(f"outside-{index}", duration_seconds=30) for index in range(5)},
        hide_below_enabled=False,
        hide_above_enabled=False,
        target_visible_count=3,
    )
    _assert(result.playlist_calls == 1, "disabled duration filters searched for visible-range videos")
    _assert(result.next_page_token == "page-2", "disabled duration filter next page token was wrong")
    _assert(len(result.videos) == 5, "scanner should retain the already fetched page")


def _test_duration_filters_continue_safely() -> None:
    result = _run_scanner(
        pages=[
            (["below-1", "above-1"], "page-2"),
            (["visible", "unknown"], "page-3"),
            (["extra"], ""),
        ],
        details={
            "below-1": _video("below-1", duration_seconds=30),
            "above-1": _video("above-1", duration_seconds=7200),
            "visible": _video("visible", duration_seconds=600),
            "unknown": _video("unknown", duration_seconds=None),
            "extra": _video("extra", duration_seconds=600),
        },
        target_visible_count=2,
    )
    _assert(result.playlist_calls == 2, "duration scanner did not stop after second page reached target")
    _assert(result.next_page_token == "page-3", "duration next page token was not preserved")
    _assert([video.video_id for video in result.videos] == ["below-1", "above-1", "visible", "unknown"], "wrong videos returned")


def _test_maximum_scan_bound() -> None:
    details = {f"below-{index}": _video(f"below-{index}", duration_seconds=30) for index in range(150)}

    def page_factory(call_index: int, max_results: int):
        start = (call_index - 1) * 50
        return [f"below-{index}" for index in range(start, start + max_results)], f"page-{call_index + 1}"

    result = _run_scanner(
        pages=page_factory,
        details=details,
        target_visible_count=1,
        max_checked=120,
    )
    _assert(result.playlist_calls == 3, "max scan did not bound playlist calls")
    _assert(sum(len(ids) for ids in result.detail_calls) == 120, "scanner checked beyond max_checked")
    _assert(result.requested_page_sizes == [50, 50, 20], "scanner did not cap final page size")


def _test_missing_details_do_not_count() -> None:
    result = _run_scanner(
        pages=[
            (["missing", "visible"], "page-2"),
            (["unknown"], ""),
        ],
        details={
            "visible": _video("visible", duration_seconds=600),
            "unknown": _video("unknown", duration_seconds=None),
        },
        target_visible_count=2,
    )
    _assert(result.playlist_calls == 2, "missing detail ID was counted as visible")
    _assert([video.video_id for video in result.videos] == ["visible", "unknown"], "missing detail ID was fabricated")
    _assert(result.detail_calls[0] == ["missing", "visible"], "missing detail ID was not checked")


def _test_ui_local_filter() -> None:
    window = main_window.YouTubeDownloaderWindow.__new__(main_window.YouTubeDownloaderWindow)
    loaded_context = _context(hide_below_minutes=3, hide_above_minutes=60)
    window._loaded_channel_context = loaded_context
    window.hide_below_enabled_var = _RaisingVar()
    window.hide_below_minutes_var = _RaisingVar()
    window.hide_above_enabled_var = _RaisingVar()
    window.hide_above_minutes_var = _RaisingVar()
    videos = [
        _video("five", duration_seconds=300),
        _video("forty-five", duration_seconds=2700),
        _video("ninety", duration_seconds=5400),
    ]

    visible_ids = [
        video.video_id
        for video in videos
        if main_window.YouTubeDownloaderWindow._video_allowed_by_duration_filter(window, video)
    ]
    _assert(visible_ids == ["five", "forty-five"], "loaded context duration filter hid wrong videos")

    window.fetching = False
    window.loading_more = False
    window.downloading = False
    window.shutdown_in_progress = False
    window.hide_below_enabled_var = _Var(True)
    window.hide_below_minutes_var = _Var("10")
    window.hide_above_enabled_var = _Var(True)
    window.hide_above_minutes_var = _Var("60")
    window.apply_filter_count = 0
    window.apply_filter = lambda: setattr(window, "apply_filter_count", window.apply_filter_count + 1)
    _assert(
        main_window.YouTubeDownloaderWindow._apply_live_duration_filter_if_valid(window),
        "valid live duration values were not applied",
    )
    _assert(window.apply_filter_count == 1, "live duration edit did not trigger immediate table filtering")
    _assert(window._loaded_channel_context.hide_below_minutes == 10, "live lower threshold was not installed")
    _assert(window._loaded_channel_context.hide_above_minutes == 60, "live upper threshold was not installed")
    visible_ids = [
        video.video_id
        for video in videos
        if main_window.YouTubeDownloaderWindow._video_allowed_by_duration_filter(window, video)
    ]
    _assert(visible_ids == ["forty-five"], "live duration change was not reflected immediately")

    previous_context = window._loaded_channel_context
    window.hide_below_minutes_var.set("70")
    window.hide_above_minutes_var.set("60")
    _assert(
        not main_window.YouTubeDownloaderWindow._apply_live_duration_filter_if_valid(window),
        "invalid live duration relationship was applied",
    )
    _assert(window._loaded_channel_context is previous_context, "invalid live range replaced the active context")
    _assert(window.apply_filter_count == 1, "invalid live range re-filtered the current table")

    window._loaded_channel_context = None
    _assert(
        all(main_window.YouTubeDownloaderWindow._video_allowed_by_duration_filter(window, video) for video in videos),
        "missing loaded context should not hide local videos",
    )

    table_videos = [
        _table_video("alpha five", 1, duration_seconds=300, status=main_window.STATUS_NOT_DOWNLOADED),
        _table_video("alpha forty-five", 2, duration_seconds=2700, status=main_window.STATUS_DOWNLOADED),
        _table_video("alpha ninety", 3, duration_seconds=5400, status=main_window.STATUS_NOT_DOWNLOADED),
        _table_video("beta fifteen", 4, duration_seconds=900, status=main_window.STATUS_NOT_DOWNLOADED),
    ]
    window = _table_filter_window(
        table_videos,
        context=loaded_context,
        filter_value=main_window.FILTER_NOT_DOWNLOADED,
        search_text="alpha",
    )
    main_window.YouTubeDownloaderWindow.apply_filter(window)
    _assert(window.visible_orders == [1, 4], "status filtering did not use loaded duration context")
    _assert(window.search_match_orders == [1], "search filtering did not use loaded duration context")
    _assert([row[0] for row in window.tree.inserted] == ["1", "4"], "table inserted rows outside loaded context")

    dialog = _DialogRecorder()
    main_window.YouTubeDownloaderWindow._apply_date_selection_dialog(
        window,
        dialog,
        "2026-01-01",
        "2026-01-01",
        True,
    )
    _assert(window.selected_orders == {1, 4}, "date/status selection did not use loaded duration context")
    _assert(dialog.closed_with == [True], "date selection dialog did not close after matches")

    hidden_count = main_window.YouTubeDownloaderWindow._duration_hidden_count(window, table_videos, loaded_context)
    _assert(hidden_count == 1, "hidden count did not use loaded duration context")


def _test_success_log_counts() -> None:
    videos = [
        _video("below-1", duration_seconds=30),
        _video("below-2", duration_seconds=90),
        _video("unknown", duration_seconds=None),
        _video("live", duration_seconds=None, live_broadcast_content="live"),
        _video("inside", duration_seconds=600),
        _video("above", duration_seconds=7200),
    ]
    hidden_logs, hidden_fetch_call = _run_fetch_worker_for_logs(
        videos,
        hide_below_enabled=True,
        hide_above_enabled=True,
    )
    _assert(hidden_fetch_call.hide_below_duration_enabled is True, "Fetch worker did not pass lower snapshot")
    _assert(hidden_fetch_call.hide_above_duration_enabled is True, "Fetch worker did not pass upper snapshot")
    _assert(hidden_fetch_call.min_visible_duration_seconds == 180, "Fetch worker lower threshold changed")
    _assert(hidden_fetch_call.max_visible_duration_seconds == 3600, "Fetch worker upper threshold changed")
    _assert(any(" 3 " in f" {message} " for message in hidden_logs if "[INFO]" in message), "hidden count log did not report 3")
    _assert(any(" 3 " in f" {message} " for message in hidden_logs if "[SUCCESS]" in message), "visible success log did not report 3")

    disabled_logs, disabled_fetch_call = _run_fetch_worker_for_logs(
        videos,
        hide_below_enabled=False,
        hide_above_enabled=False,
    )
    _assert(disabled_fetch_call.hide_below_duration_enabled is False, "Fetch worker did not pass disabled lower snapshot")
    _assert(disabled_fetch_call.hide_above_duration_enabled is False, "Fetch worker did not pass disabled upper snapshot")
    _assert(not any("ẩn" in message for message in disabled_logs), "disabled filters logged hidden videos")
    _assert(any(" 6 " in f" {message} " for message in disabled_logs if "[SUCCESS]" in message), "disabled success log did not report 6")


def _run_fetch_worker_for_logs(videos, *, hide_below_enabled: bool, hide_above_enabled: bool):
    window = main_window.YouTubeDownloaderWindow.__new__(main_window.YouTubeDownloaderWindow)
    window.events = queue.Queue()
    token = main_window._FetchRequestToken(
        generation=1,
        request_id=1,
        channel_input="channel",
        context=main_window._ChannelRequestContext(
            save_folder=".",
            download_mode=main_window.MODE_VIDEO_THUMB,
            hide_below_enabled=hide_below_enabled,
            hide_below_minutes=3,
            hide_above_enabled=hide_above_enabled,
            hide_above_minutes=60,
        ),
    )
    with _patched_main_window_fetch(videos) as fetch_call:
        window._fetch_worker(token, "")

    messages = []
    while not window.events.empty():
        event = window.events.get_nowait()
        if event[0] == "channel_request_log":
            messages.append(event[2])
    return messages, fetch_call.calls[0]


def _context(
    *,
    hide_below_enabled: bool = True,
    hide_below_minutes: int = 3,
    hide_above_enabled: bool = True,
    hide_above_minutes: int = 60,
):
    return main_window._ChannelRequestContext(
        save_folder=".",
        download_mode=main_window.MODE_VIDEO_THUMB,
        hide_below_enabled=hide_below_enabled,
        hide_below_minutes=hide_below_minutes,
        hide_above_enabled=hide_above_enabled,
        hide_above_minutes=hide_above_minutes,
    )


def _table_video(title: str, order: int, *, duration_seconds: int, status: str):
    video = _video(title, duration_seconds=duration_seconds)
    video.title = title
    video.display_order = order
    video.status = status
    video.published_at = "2026-01-01"
    return video


def _table_filter_window(videos, *, context, filter_value: str, search_text: str):
    window = main_window.YouTubeDownloaderWindow.__new__(main_window.YouTubeDownloaderWindow)
    window.videos = list(videos)
    window.selected_orders = set()
    window.visible_orders = []
    window.search_match_orders = []
    window.current_search_match_index = -1
    window._loaded_channel_context = context
    window.hide_below_enabled_var = _RaisingVar()
    window.hide_below_minutes_var = _RaisingVar()
    window.hide_above_enabled_var = _RaisingVar()
    window.hide_above_minutes_var = _RaisingVar()
    window.filter_var = _Var(filter_value)
    window.search_var = _Var(search_text)
    window.search_status_var = _Var("")
    window.tree = _FakeTree()
    window.logs = []
    window._append_log = lambda message: window.logs.append(message)
    window._destroy_status_editor = lambda: None
    window._update_header_checkbox = lambda: None
    window._update_download_button_text = lambda: None
    window._schedule_tree_column_fit = lambda: None
    window._show_error_dialog = lambda message: (_ for _ in ()).throw(AssertionError(message))
    return window


def _run_scanner(
    *,
    pages,
    details: dict[str, youtube_api.VideoItem],
    target_visible_count: int,
    max_checked: int = youtube_api.MAX_UPLOADS_SCAN_LIMIT,
    hide_below_enabled: bool = True,
    hide_above_enabled: bool = True,
):
    with _patched_scanner_dependencies(pages, details) as scanner:
        videos, next_page_token = youtube_api._fetch_video_items_until_visible_count(
            "playlist",
            "api-key",
            start_order=1,
            target_visible_count=target_visible_count,
            max_checked=max_checked,
            hide_below_duration_enabled=hide_below_enabled,
            min_visible_duration_seconds=180,
            hide_above_duration_enabled=hide_above_enabled,
            max_visible_duration_seconds=3600,
        )
    return SimpleNamespace(
        videos=videos,
        next_page_token=next_page_token,
        playlist_calls=len(scanner.playlist_calls),
        detail_calls=scanner.detail_calls,
        requested_page_sizes=[call["maxResults"] for call in scanner.playlist_calls],
    )


def _video(video_id: str, *, duration_seconds, live_broadcast_content: str = "none") -> youtube_api.VideoItem:
    return youtube_api.VideoItem(
        video_id=video_id,
        title=video_id,
        duration="",
        published_at="2026-01-01",
        thumbnail_url="",
        display_order=0,
        duration_seconds=duration_seconds,
        live_broadcast_content=live_broadcast_content,
    )


def _api_video_item(video_id: str, duration: str | None, *, live_state: str) -> dict:
    content_details = {}
    if duration is not None:
        content_details["duration"] = duration
    return {
        "id": video_id,
        "snippet": {
            "title": f"Title {video_id}",
            "publishedAt": "2026-01-02T03:04:05Z",
            "thumbnails": {"default": {"url": f"https://example.test/{video_id}.jpg"}},
            "liveBroadcastContent": live_state,
        },
        "contentDetails": content_details,
    }


class _patched_api_get:
    def __init__(self, *, videos_by_id: dict[str, dict], returned_order: list[str]):
        self.videos_by_id = videos_by_id
        self.returned_order = returned_order

    def __enter__(self):
        self.original = youtube_api._api_get

        def fake_api_get(endpoint, params, _api_key):
            _assert(endpoint == "videos", f"unexpected endpoint {endpoint}")
            requested = set((params.get("id") or "").split(","))
            return {"items": [self.videos_by_id[video_id] for video_id in self.returned_order if video_id in requested]}

        youtube_api._api_get = fake_api_get
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        youtube_api._api_get = self.original


class _patched_scanner_dependencies:
    def __init__(self, pages, details):
        self.pages = pages
        self.details = details
        self.playlist_calls = []
        self.detail_calls = []

    def __enter__(self):
        self.original_api_get = youtube_api._api_get
        self.original_fetch_video_details = youtube_api._fetch_video_details

        def fake_api_get(endpoint, params, _api_key):
            _assert(endpoint == "playlistItems", f"unexpected endpoint {endpoint}")
            call_index = len(self.playlist_calls) + 1
            max_results = int(params["maxResults"])
            self.playlist_calls.append({"pageToken": params.get("pageToken", ""), "maxResults": max_results})
            if callable(self.pages):
                ids, next_page_token = self.pages(call_index, max_results)
            else:
                index = call_index - 1
                ids, next_page_token = self.pages[index] if index < len(self.pages) else ([], "")
                ids = ids[:max_results]
            return {
                "nextPageToken": next_page_token,
                "items": [{"contentDetails": {"videoId": video_id}} for video_id in ids],
            }

        def fake_fetch_video_details(video_ids, _api_key, start_order=1):
            self.detail_calls.append(list(video_ids))
            videos = []
            for video_id in video_ids:
                template = self.details.get(video_id)
                if template is None:
                    continue
                videos.append(
                    youtube_api.VideoItem(
                        video_id=template.video_id,
                        title=template.title,
                        duration=template.duration,
                        published_at=template.published_at,
                        thumbnail_url=template.thumbnail_url,
                        display_order=start_order + len(videos),
                        duration_seconds=template.duration_seconds,
                        live_broadcast_content=template.live_broadcast_content,
                    )
                )
            return videos

        youtube_api._api_get = fake_api_get
        youtube_api._fetch_video_details = fake_fetch_video_details
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        youtube_api._api_get = self.original_api_get
        youtube_api._fetch_video_details = self.original_fetch_video_details


class _patched_main_window_fetch:
    def __init__(self, videos):
        self.videos = videos
        self.calls = []

    def __enter__(self):
        self.original_fetch = main_window.fetch_latest_video_page
        self.original_apply_statuses = main_window.apply_statuses

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
            return SimpleNamespace(channel_name="Channel", channel_id="channel-id"), list(self.videos), ""

        main_window.fetch_latest_video_page = fake_fetch
        main_window.apply_statuses = lambda *_args, **_kwargs: None
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        main_window.fetch_latest_video_page = self.original_fetch
        main_window.apply_statuses = self.original_apply_statuses


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _RaisingVar:
    def get(self):
        raise AssertionError("live duration variable was read")

    def set(self, _value) -> None:
        raise AssertionError("live duration variable was written")


class _FakeTree:
    def __init__(self) -> None:
        self.inserted = []
        self._focus = ""

    def get_children(self):
        return [row_id for row_id, _values in self.inserted]

    def delete(self, row_id) -> None:
        self.inserted = [row for row in self.inserted if row[0] != row_id]

    def insert(self, _parent, _index, iid=None, values=None) -> None:
        self.inserted.append((str(iid), values))

    def exists(self, row_id) -> bool:
        return any(existing == str(row_id) for existing, _values in self.inserted)

    def focus(self, row_id=None):
        if row_id is not None:
            self._focus = str(row_id)
        return self._focus

    def selection_set(self, row_id) -> None:
        self._focus = str(row_id)

    def see(self, _row_id) -> None:
        return None


class _DialogRecorder:
    def __init__(self) -> None:
        self.closed_with = []

    def close(self, value) -> None:
        self.closed_with.append(value)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
