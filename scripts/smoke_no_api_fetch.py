import json
import sys
from contextlib import contextmanager
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import youtube_no_api


def main() -> int:
    _configure_stdio()
    _test_command_uses_single_json()
    _test_command_has_flat_playlist_and_range_flags()
    _test_command_first_and_second_page_ranges()
    _test_command_with_cookies()
    _test_command_has_no_api_key()
    _test_parse_single_json_object_with_entries()
    _test_top_level_channel_name_and_id_are_used()
    _test_record_metadata_used_when_top_level_missing()
    _test_channel_id_fallback_order()
    _test_metadata_id_channel_id_fallback()
    _test_channel_name_fallback_order()
    _test_handle_url_input_fallback()
    _test_handle_input_fallback()
    _test_channel_url_input_fallback()
    _test_later_record_channel_id_is_used()
    _test_exact_channel_folder_regression()
    _test_input_derived_name_warning()
    _test_fetch_more_uses_page_token_as_playlist_start()
    _test_full_page_returns_next_token()
    _test_short_final_page_returns_empty_token()
    _test_missing_duration_is_safe_and_not_short()
    _test_malformed_json_is_ignored()
    _test_best_thumbnail_extraction()
    _test_upload_date_formatting()
    _test_bot_cookies_classification()
    _test_load_more_not_disabled_in_none_mode()
    print("no-api fetch smoke tests passed")
    return 0


def _test_command_uses_single_json() -> None:
    command = youtube_no_api.build_no_api_listing_command("https://example.test/channel")
    _assert("--dump-single-json" in command, "--dump-single-json missing")
    _assert("--dump-json" not in command, "--dump-json should not be used")


def _test_command_has_flat_playlist_and_range_flags() -> None:
    command = youtube_no_api.build_no_api_listing_command("https://example.test/channel")
    for flag in ("--flat-playlist", "--playlist-start", "--playlist-end"):
        _assert(flag in command, f"{flag} missing")


def _test_command_first_and_second_page_ranges() -> None:
    first_page = youtube_no_api.build_no_api_listing_command("https://example.test/channel")
    _assert(_option_value(first_page, "--playlist-start") == "1", "first page start should be 1")
    _assert(_option_value(first_page, "--playlist-end") == "100", "first page end should be 100")

    second_page = youtube_no_api.build_no_api_listing_command(
        "https://example.test/channel",
        playlist_start=101,
        playlist_end=200,
    )
    _assert(_option_value(second_page, "--playlist-start") == "101", "second page start should be 101")
    _assert(_option_value(second_page, "--playlist-end") == "200", "second page end should be 200")


def _test_command_with_cookies() -> None:
    command = youtube_no_api.build_no_api_listing_command(
        "https://example.test/channel",
        cookies_path=r"D:\cookies_export.txt",
    )
    _assert(_option_value(command, "--cookies") == r"D:\cookies_export.txt", "cookies path was not preserved")


def _test_command_has_no_api_key() -> None:
    command_text = " ".join(youtube_no_api.build_no_api_listing_command("https://example.test/channel"))
    _assert("AIzaFakeApiKey" not in command_text, "fake API key appeared in command")
    _assert("--api-key" not in command_text and "key=" not in command_text, "API key option appeared in command")


def _test_parse_single_json_object_with_entries() -> None:
    metadata, records = youtube_no_api.parse_no_api_listing_payload(
        _single_json_output(
            [
                {"id": "video1", "title": "Video 1"},
                "bad-entry",
                {"id": "video2", "title": "Video 2"},
            ],
            channel="Top Channel",
        )
    )
    _assert(metadata.get("channel") == "Top Channel", "top-level metadata was not returned")
    _assert([record["id"] for record in records] == ["video1", "video2"], "entries were not parsed safely")


def _test_top_level_channel_name_and_id_are_used() -> None:
    channel = _channel_from_output(
        _single_json_output(
            [{"id": "video1", "channel": "Record Channel", "channel_id": "UCrecord"}],
            channel="Top Channel",
            channel_id="UCtop",
        ),
        "https://www.youtube.com/@fallback/videos",
    )
    _assert(channel.channel_name == "Top Channel", "top-level channel name was not used")
    _assert(channel.channel_id == "UCtop", "top-level channel id was not used")


def _test_record_metadata_used_when_top_level_missing() -> None:
    channel = _channel_from_output(
        _single_json_output([{"id": "video1", "channel": "Record Channel", "channel_id": "UCrecord"}]),
        "https://www.youtube.com/@fallback/videos",
    )
    _assert(channel.channel_name == "Record Channel", "record channel name was not used")
    _assert(channel.channel_id == "UCrecord", "record channel id was not used")


def _test_channel_id_fallback_order() -> None:
    records = [
        {"id": "video1", "channel_url": "https://youtube.com/@record-url"},
        {"id": "video2", "uploader_id": "UploaderRecord"},
        {"id": "video3", "channel_id": "UCrecord"},
    ]
    channel = youtube_no_api._channel_info_from_metadata_and_records(
        {"channel_url": "https://youtube.com/@meta-url", "uploader_id": "UploaderMeta", "channel_id": "UCmeta"},
        records,
        "https://www.youtube.com/@input/videos",
    )
    _assert(channel.channel_id == "UCmeta", "channel_id did not prefer metadata channel_id")

    channel = youtube_no_api._channel_info_from_metadata_and_records(
        {"channel_url": "https://youtube.com/@meta-url", "uploader_id": "UploaderMeta"},
        records,
        "https://www.youtube.com/@input/videos",
    )
    _assert(channel.channel_id == "UploaderMeta", "channel_id did not prefer uploader_id before channel_url")

    channel = youtube_no_api._channel_info_from_metadata_and_records(
        {"channel_url": "https://youtube.com/@meta-url"},
        records,
        "https://www.youtube.com/@input/videos",
    )
    _assert(
        channel.channel_id == "https://youtube.com/@meta-url",
        "channel_id did not prefer metadata channel_url before record metadata",
    )


def _test_metadata_id_channel_id_fallback() -> None:
    channel = youtube_no_api._channel_info_from_metadata_and_records(
        {"id": "UCfrom_metadata_id"},
        [{"id": "video1"}],
        "https://www.youtube.com/@input/videos",
    )
    _assert(channel.channel_id == "UCfrom_metadata_id", "metadata id was not used as UC channel id fallback")


def _test_channel_name_fallback_order() -> None:
    records = [{"id": "video1", "channel": "Record Channel", "uploader": "Record Uploader"}]
    channel = youtube_no_api._channel_info_from_metadata_and_records(
        {"channel": "Meta Channel", "uploader": "Meta Uploader"},
        records,
        "https://www.youtube.com/@input/videos",
    )
    _assert(channel.channel_name == "Meta Channel", "channel_name did not prefer channel over uploader")

    channel = youtube_no_api._channel_info_from_metadata_and_records(
        {"uploader": "Meta Uploader"},
        records,
        "https://www.youtube.com/@input/videos",
    )
    _assert(channel.channel_name == "Meta Uploader", "channel_name did not use uploader fallback")

    channel = youtube_no_api._channel_info_from_metadata_and_records(
        {},
        [{"id": "video1"}],
        "https://www.youtube.com/@input/videos",
    )
    _assert(channel.channel_name == "input", "channel_name did not fall back to input-derived name")


def _test_handle_url_input_fallback() -> None:
    _assert(
        youtube_no_api._channel_name_from_input("https://www.youtube.com/@fallback/videos") == "fallback",
        "handle URL did not resolve to fallback",
    )


def _test_handle_input_fallback() -> None:
    _assert(youtube_no_api._channel_name_from_input("@fallback") == "fallback", "@handle did not resolve")


def _test_channel_url_input_fallback() -> None:
    _assert(
        youtube_no_api._channel_name_from_input("https://www.youtube.com/channel/UCabc/videos") == "UCabc",
        "channel URL did not resolve to UC id",
    )


def _test_later_record_channel_id_is_used() -> None:
    channel = _channel_from_output(
        _single_json_output(
            [
                {"id": "video1", "channel": "Name Only"},
                {"id": "video2", "channel_id": "UClater"},
            ]
        ),
        "https://www.youtube.com/@fallback/videos",
    )
    _assert(channel.channel_name == "Name Only", "first record channel name was not used")
    _assert(channel.channel_id == "UClater", "later record channel_id was not found")


def _test_exact_channel_folder_regression() -> None:
    original_input = "https://www.youtube.com/@real_channel/videos"
    output = _single_json_output(
        [
            {"id": "video1", "title": "Video 1", "duration": 240},
            {"id": "video2", "title": "Video 2", "duration": 240},
        ]
    )
    with _patched_runner(output):
        channel, videos, _next_page_token = youtube_no_api.fetch_latest_video_page_no_api(original_input)
    _assert(len(videos) == 2, "regression fixture did not parse videos")
    _assert(channel.channel_name == "real_channel", "handle input collapsed to the wrong channel name")
    _assert(channel.channel_name != "Channel", "handle input collapsed to Channel")
    _assert(channel.uploads_playlist_id == original_input, "original source input was not retained for Load more")


def _test_input_derived_name_warning() -> None:
    logs = []
    with _patched_runner(_single_json_output([{"id": "video1", "duration": 240}])):
        youtube_no_api.fetch_latest_video_page_no_api(
            "https://www.youtube.com/@warning_name/videos",
            progress=logs.append,
        )
    _assert(
        any("using input-derived name: warning_name" in message for message in logs),
        "input-derived channel name warning was not logged",
    )


def _test_fetch_more_uses_page_token_as_playlist_start() -> None:
    calls = []

    def fake_runner(command):
        calls.append(command)
        return _single_json_output([{"id": "video101", "duration": 240}])

    with _patched_runner_func(fake_runner):
        videos, next_page_token = youtube_no_api.fetch_more_videos_no_api(
            "https://example.test/@channel",
            page_token="101",
            start_order=101,
        )

    _assert(len(videos) == 1, "fetch_more did not parse mocked page")
    _assert(videos[0].display_order == 101, "fetch_more did not preserve start_order")
    _assert(next_page_token == "", "single-item mocked page should be final")
    _assert(_option_value(calls[0], "--playlist-start") == "101", "fetch_more did not use page_token as start")
    _assert(_option_value(calls[0], "--playlist-end") == "200", "fetch_more did not compute page end")


def _test_full_page_returns_next_token() -> None:
    with _patched_runner(_single_json_output(_entries(100))):
        channel, videos, next_page_token = youtube_no_api.fetch_latest_video_page_no_api(
            "https://example.test/@channel"
        )
    _assert(channel.channel_name != "Channel", "full page channel name collapsed to Channel")
    _assert(len(videos) == 100, "full page did not parse 100 videos")
    _assert(next_page_token == "101", "full page did not return next token")


def _test_short_final_page_returns_empty_token() -> None:
    with _patched_runner(_single_json_output(_entries(3))):
        _channel, videos, next_page_token = youtube_no_api.fetch_latest_video_page_no_api(
            "https://example.test/@channel"
        )
    _assert(len(videos) == 3, "short page did not parse videos")
    _assert(next_page_token == "", "short final page should not return next token")


def _test_missing_duration_is_safe_and_not_short() -> None:
    video = youtube_no_api.parse_no_api_video_line(json.dumps({"id": "unknown-duration"}), display_order=1)
    _assert(video is not None, "missing duration should still produce a VideoItem")
    _assert(video.duration == "", "missing duration should stay blank")
    _assert(video.duration_seconds == 0, "missing duration should be 0 seconds")
    _assert(not youtube_no_api.is_no_api_short_video(video, 180), "unknown duration was treated as short")


def _test_malformed_json_is_ignored() -> None:
    output = "{not-json}\n" + json.dumps({"id": "valid"})
    videos = youtube_no_api.parse_no_api_listing_output(output)
    _assert(len(videos) == 1, "malformed JSON was not ignored safely")
    _assert(videos[0].video_id == "valid", "valid line was not preserved")


def _test_best_thumbnail_extraction() -> None:
    video = youtube_no_api.parse_no_api_video_line(
        json.dumps(
            {
                "id": "thumbs",
                "thumbnails": [
                    {"url": "https://example.test/small.jpg", "width": 120, "height": 90},
                    {"url": "https://example.test/large.jpg", "width": 1280, "height": 720},
                ],
            }
        ),
        display_order=1,
    )
    _assert(video is not None, "thumbnail test video missing")
    _assert(video.thumbnail_url.endswith("large.jpg"), "best thumbnail was not selected")


def _test_upload_date_formatting() -> None:
    video = youtube_no_api.parse_no_api_video_line(
        json.dumps({"id": "dated", "upload_date": "20261205"}),
        display_order=1,
    )
    _assert(video is not None, "upload date test video missing")
    _assert(video.published_at == "2026-12-05", "upload_date did not convert to YYYY-MM-DD")


def _test_bot_cookies_classification() -> None:
    message = youtube_no_api.classify_no_api_error("ERROR: Sign in to confirm you're not a bot. Use --cookies")
    lower = message.lower()
    _assert("sign in to confirm" in lower and "cookies" in lower, "bot/cookies output was not classified")


def _test_load_more_not_disabled_in_none_mode() -> None:
    text = (REPO_ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    unavailable_message = "Load more is " + "not available"
    _assert(unavailable_message not in text, "None mode still contains unavailable Load more log")
    _assert("fetch_more_videos_no_api" in text, "None mode Load more fetcher is not wired")
    start = text.index("    def _update_more_button_state")
    end = text.index("    def _update_stop_button_state", start)
    more_button_block = text[start:end]
    _assert("FETCH_SOURCE_NONE" not in more_button_block, "More button state blocks None mode")


def _channel_from_output(output: str, channel_input: str):
    with _patched_runner(output):
        channel, _videos, _next_page_token = youtube_no_api.fetch_latest_video_page_no_api(channel_input)
    return channel


def _single_json_output(entries: list, **metadata) -> str:
    payload = dict(metadata)
    payload["entries"] = entries
    return json.dumps(payload)


def _entries(count: int, first_id: int = 1) -> list[dict]:
    rows = []
    for offset in range(count):
        index = first_id + offset
        rows.append(
            {
                "id": f"video{index}",
                "title": f"Video {index}",
                "duration": 240,
            }
        )
    return rows


@contextmanager
def _patched_runner(output: str):
    with _patched_runner_func(lambda _command: output):
        yield


@contextmanager
def _patched_runner_func(func):
    old_runner = youtube_no_api.run_no_api_listing_command
    try:
        youtube_no_api.run_no_api_listing_command = func
        yield
    finally:
        youtube_no_api.run_no_api_listing_command = old_runner


def _option_value(command: list[str], option: str) -> str | None:
    try:
        return command[command.index(option) + 1]
    except (ValueError, IndexError):
        return None


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
