import queue
import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.download_modes import MODE_AUDIO_THUMB, MODE_VIDEO_THUMB
from core.downloader import DownloadError, validate_file_start_number
from core.state_store import STATUS_DOWNLOADED, STATUS_MISSING_AUDIO, STATUS_MISSING_THUMB
from ui import main_window


YouTubeDownloaderWindow = main_window.YouTubeDownloaderWindow


def main() -> int:
    _test_two_successes_then_stop()
    _test_stop_before_completion()
    _test_duplicate_and_initially_complete_events()
    _test_initial_complete_capture_uses_mode_helper()
    _test_partial_and_incomplete_statuses()
    _test_two_modes_use_final_logical_status()
    _test_stale_event_and_new_run_reset()
    _test_invalid_start_values()
    _test_worker_callback_only_queues_events()
    _test_malformed_completion_events_are_ignored()
    print("file start number sync smoke passed")
    return 0


def _test_two_successes_then_stop() -> None:
    window, run_id = _window(51, {"A", "B", "C"})
    _complete(window, run_id, "A")
    _assert(window.file_start_number_var.get() == "52", "A did not advance to 52")
    _complete(window, run_id, "B")
    _assert(window.file_start_number_var.get() == "53", "B did not advance to 53")
    _finish(window)
    _assert(window.file_start_number_var.get() == "53", "Stop reset the advanced value")


def _test_stop_before_completion() -> None:
    window, _run_id = _window(51, {"A"})
    _finish(window)
    _assert(window.file_start_number_var.get() == "51", "empty run changed the value")


def _test_duplicate_and_initially_complete_events() -> None:
    window, run_id = _window(51, {"A"})
    _complete(window, run_id, "A")
    _complete(window, run_id, "A")
    _assert(window.file_start_number_var.get() == "52", "duplicate completion incremented twice")

    initial, initial_run = _window(51, {"A", "B"}, {"A"})
    _complete(initial, initial_run, "A")
    _assert(initial.file_start_number_var.get() == "51", "initially complete item advanced")
    _complete(initial, initial_run, "B")
    _assert(initial.file_start_number_var.get() == "52", "newly complete item did not advance")


def _test_initial_complete_capture_uses_mode_helper() -> None:
    entries = {
        "video-complete": {
            "video_status": STATUS_DOWNLOADED,
            "thumb_status": STATUS_DOWNLOADED,
        },
        "audio-complete": {
            "audio_status": STATUS_DOWNLOADED,
            "thumb_status": STATUS_DOWNLOADED,
        },
    }
    old_get_video_entry = main_window.get_video_entry
    try:
        main_window.get_video_entry = lambda _channel_id, video_id: entries.get(video_id)
        window, _run_id = _window(51, set())
        selected = [_video("video-complete", ""), _video("audio-complete", "")]
        video_ids = window._initial_complete_video_ids(selected, "channel", MODE_VIDEO_THUMB)
        audio_ids = window._initial_complete_video_ids(selected, "channel", MODE_AUDIO_THUMB)
    finally:
        main_window.get_video_entry = old_get_video_entry

    _assert(video_ids == {"video-complete"}, "video mode initial-complete capture was wrong")
    _assert(audio_ids == {"audio-complete"}, "audio mode initial-complete capture was wrong")


def _test_partial_and_incomplete_statuses() -> None:
    window, run_id = _window(51, {"A"})
    video = _video("A", STATUS_MISSING_THUMB)
    window._queue_download_status(video, run_id)
    _dispatch(window)
    _assert(window.file_start_number_var.get() == "51", "partial status advanced the value")
    video.status = STATUS_DOWNLOADED
    window._queue_download_status(video, run_id)
    _dispatch(window)
    _assert(window.file_start_number_var.get() == "52", "partial item completion did not advance")

    incomplete, incomplete_run = _window(51, {"A"})
    incomplete._queue_download_status(_video("A", STATUS_MISSING_AUDIO), incomplete_run)
    _dispatch(incomplete)
    _assert(incomplete.file_start_number_var.get() == "51", "incomplete item advanced")


def _test_two_modes_use_final_logical_status() -> None:
    for mode, incomplete_status in (
        (MODE_VIDEO_THUMB, STATUS_MISSING_THUMB),
        (MODE_AUDIO_THUMB, STATUS_MISSING_AUDIO),
    ):
        window, run_id = _window(70, {"A"})
        window.download_mode = mode
        window._queue_download_status(_video("A", incomplete_status), run_id)
        _dispatch(window)
        _assert(window.file_start_number_var.get() == "70", f"{mode} part status advanced")
        window._queue_download_status(_video("A", STATUS_DOWNLOADED), run_id)
        _dispatch(window)
        _assert(window.file_start_number_var.get() == "71", f"{mode} final status did not advance")


def _test_stale_event_and_new_run_reset() -> None:
    window, run_1 = _window(10, {"A"})
    _complete(window, run_1, "A")
    _assert(window.file_start_number_var.get() == "11", "run 1 did not advance")

    window.file_start_number_var.set("100")
    run_2 = window._begin_download_run_numbering(100, {"B"}, set())
    _complete(window, run_1, "A")
    _assert(window.file_start_number_var.get() == "100", "stale run 1 event changed run 2")
    _complete(window, run_2, "B")
    _assert(window.file_start_number_var.get() == "101", "new run did not use manual override")
    _assert(window._download_run_completed_ids == {"B"}, "new run did not reset completion IDs")


def _test_invalid_start_values() -> None:
    for value in ("", "0", "-1", "1.5", "abc"):
        try:
            validate_file_start_number(value)
        except DownloadError:
            continue
        raise AssertionError(f"invalid start value was accepted: {value!r}")


def _test_worker_callback_only_queues_events() -> None:
    window, run_id = _window(51, {"A"})
    before_sets = window.file_start_number_var.set_calls
    window._queue_download_status(_video("A", STATUS_DOWNLOADED), run_id)
    _assert(window.file_start_number_var.get() == "51", "worker callback changed StringVar directly")
    _assert(window.file_start_number_var.set_calls == before_sets, "worker callback called StringVar.set")
    events = list(window.events.queue)
    _assert(events[0] == ("status_update", 1, STATUS_DOWNLOADED), "status event shape changed")
    _assert(
        events[1] == ("download_video_completed_for_numbering", run_id, "A"),
        "completion event shape was wrong",
    )

    malformed = SimpleNamespace(display_order=2, status=STATUS_DOWNLOADED)
    window._queue_download_status(malformed, run_id)
    _assert(window.events.qsize() == 3, "malformed numbering payload changed status callback behavior")


def _test_malformed_completion_events_are_ignored() -> None:
    window, run_id = _window(51, {"A"})
    for event in (
        ("download_video_completed_for_numbering",),
        ("download_video_completed_for_numbering", run_id, ""),
        ("download_video_completed_for_numbering", run_id, "not-selected"),
    ):
        window._handle_event(event)
    _assert(window.file_start_number_var.get() == "51", "malformed event changed the value")


def _window(start: int, selected_ids: set[str], initial_ids: set[str] | None = None):
    window = YouTubeDownloaderWindow.__new__(YouTubeDownloaderWindow)
    window.events = queue.Queue()
    window.videos = [_video(video_id, "") for video_id in sorted(selected_ids)]
    window.apply_filter = lambda: None
    window.file_start_number_var = _Var(str(start))
    window.logs = []
    window._append_log = window.logs.append
    window._download_run_sequence = 0
    window._active_download_run_id = None
    window._download_run_start_number = None
    window._download_run_selected_ids = set()
    window._download_run_initial_complete_ids = set()
    window._download_run_completed_ids = set()
    run_id = window._begin_download_run_numbering(start, selected_ids, initial_ids or set())
    return window, run_id


def _video(video_id: str, status: str):
    return SimpleNamespace(video_id=video_id, display_order=1, status=status)


def _complete(window, run_id: int, video_id: str) -> None:
    window._handle_event(("download_video_completed_for_numbering", run_id, video_id))


def _dispatch(window) -> None:
    while not window.events.empty():
        window._handle_event(window.events.get_nowait())


def _finish(window) -> None:
    window.shutdown_in_progress = False
    window.exit_after_download_stop = False
    window.downloading = True
    window.download_worker = None
    window.download_controller = None
    window.download_stop_requested = True
    window.cancel_download = True
    window.close_requested = False
    window._download_terminal_received = True
    window._download_terminal_outcome = "completed"
    window._download_terminal_message = ""
    window._set_download_controls_locked = lambda _locked: None
    window._finish_download_ui()


class _Var:
    def __init__(self, value: str) -> None:
        self.value = value
        self.set_calls = 0

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value
        self.set_calls += 1


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
